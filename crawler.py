import json
import math
import random
import sqlite3
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from llm_client import cosine_similarity, embed_text, summarize_generic_section
from source_registry import (
    build_unified_source_list,
    get_default_modules,
    get_module_sources,
    get_module_title,
    list_modules,
    normalize_module_key,
    pick_modules_by_query,
)
from common import fetch_rss_entries, merge_items


class ReportGenerationError(RuntimeError):
    pass


CACHE_FILE = Path(__file__).resolve().parent / "content_cache.json"
SQLITE_FILE = Path(__file__).resolve().parent / "rss_items.db"
CACHE_TTL_MINUTES = 120
SQLITE_RETENTION_HOURS = 24
CN_TZ = timezone(timedelta(hours=8))


def _init_sqlite() -> None:
    with sqlite3.connect(SQLITE_FILE) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS rss_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                module TEXT NOT NULL,
                source TEXT,
                title TEXT,
                url TEXT NOT NULL UNIQUE,
                snippet TEXT,
                embedding TEXT,
                embedding_model TEXT,
                embedding_dim INTEGER DEFAULT 0,
                embedding_norm REAL DEFAULT 0,
                fetched_at TEXT NOT NULL
            )
            """
        )

        existing_cols = {
            str(row[1])
            for row in conn.execute("PRAGMA table_info(rss_items)").fetchall()
            if len(row) >= 2
        }
        if "embedding" not in existing_cols:
            conn.execute("ALTER TABLE rss_items ADD COLUMN embedding TEXT")
        if "embedding_model" not in existing_cols:
            conn.execute("ALTER TABLE rss_items ADD COLUMN embedding_model TEXT")
        if "embedding_dim" not in existing_cols:
            conn.execute("ALTER TABLE rss_items ADD COLUMN embedding_dim INTEGER DEFAULT 0")
        if "embedding_norm" not in existing_cols:
            conn.execute("ALTER TABLE rss_items ADD COLUMN embedding_norm REAL DEFAULT 0")

        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_rss_items_module_fetched_at ON rss_items(module, fetched_at DESC)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_rss_items_fetched_at ON rss_items(fetched_at DESC)"
        )
        conn.commit()


def _vector_norm(vector: list[float]) -> float:
    if not vector:
        return 0.0
    return math.sqrt(sum(x * x for x in vector))


def _build_embedding_text(module: str, item: dict[str, str]) -> str:
    return "\n".join(
        [
            f"module: {module}",
            f"source: {item.get('source') or module}",
            f"title: {item.get('title') or ''}",
            f"snippet: {item.get('snippet') or ''}",
            f"url: {item.get('url') or ''}",
        ]
    ).strip()


def _embed_item(module: str, item: dict[str, str]) -> tuple[str, str, int, float]:
    try:
        payload = embed_text(_build_embedding_text(module, item))
        vector = payload.get("vector") if isinstance(payload.get("vector"), list) else []
        model = str(payload.get("model") or "")
        vec = [float(x) for x in vector]
        return json.dumps(vec, ensure_ascii=False), model, len(vec), _vector_norm(vec)
    except Exception:
        return "[]", "", 0, 0.0


def _save_items_to_sqlite(module: str, items: list[dict[str, str]]) -> None:
    if not items:
        return

    _init_sqlite()
    fetched_at = _now_iso()
    rows = []
    for item in items:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        if not url:
            continue
        rows.append(
            (
                module,
                str(item.get("source") or module),
                str(item.get("title") or ""),
                url,
                str(item.get("snippet") or ""),
                *(_embed_item(module, item)),
                fetched_at,
            )
        )

    if not rows:
        return

    with sqlite3.connect(SQLITE_FILE) as conn:
        conn.executemany(
            """
            INSERT INTO rss_items(module, source, title, url, snippet, embedding, embedding_model, embedding_dim, embedding_norm, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(url) DO UPDATE SET
                module=excluded.module,
                source=excluded.source,
                title=excluded.title,
                snippet=excluded.snippet,
                embedding=excluded.embedding,
                embedding_model=excluded.embedding_model,
                embedding_dim=excluded.embedding_dim,
                embedding_norm=excluded.embedding_norm,
                fetched_at=excluded.fetched_at
            """,
            rows,
        )
        conn.commit()


def purge_old_sqlite_items(max_age_hours: int = SQLITE_RETENTION_HOURS) -> int:
    _init_sqlite()
    threshold = datetime.now(CN_TZ) - timedelta(hours=max(1, int(max_age_hours)))

    to_delete: list[int] = []
    with sqlite3.connect(SQLITE_FILE) as conn:
        cursor = conn.execute("SELECT id, fetched_at FROM rss_items")
        for row in cursor.fetchall():
            row_id = int(row[0])
            fetched_at = str(row[1] or "")
            try:
                ts = datetime.fromisoformat(fetched_at)
            except ValueError:
                to_delete.append(row_id)
                continue
            if ts < threshold:
                to_delete.append(row_id)

        if to_delete:
            conn.executemany("DELETE FROM rss_items WHERE id = ?", [(x,) for x in to_delete])
            conn.commit()

    return len(to_delete)


def _load_items_from_sqlite(module: str, limit: int = 5) -> list[dict[str, str]]:
    purge_old_sqlite_items()
    _init_sqlite()
    with sqlite3.connect(SQLITE_FILE) as conn:
        cursor = conn.execute(
            """
            SELECT source, title, url, snippet
            FROM rss_items
            WHERE module = ?
            ORDER BY fetched_at DESC, id DESC
            LIMIT ?
            """,
            (module, max(1, int(limit))),
        )
        rows = cursor.fetchall()

    return [
        {
            "source": str(row[0] or module),
            "title": str(row[1] or ""),
            "url": str(row[2] or ""),
            "snippet": str(row[3] or ""),
        }
        for row in rows
        if str(row[2] or "").strip()
    ]


def get_unified_sources(modules: list[str] | None = None) -> list[dict[str, Any]]:
    return build_unified_source_list(modules)


def get_available_modules() -> list[str]:
    return list_modules()


def _now_iso() -> str:
    return datetime.now(CN_TZ).isoformat()


def _load_cache() -> dict[str, Any]:
    if not CACHE_FILE.exists():
        return {"updated_at": "", "modules": {}}

    try:
        data = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"updated_at": "", "modules": {}}

    if not isinstance(data, dict):
        return {"updated_at": "", "modules": {}}

    modules = data.get("modules")
    if not isinstance(modules, dict):
        data["modules"] = {}
    return data


def _save_cache(cache: dict[str, Any]) -> None:
    CACHE_FILE.write_text(
        json.dumps(cache, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _is_fresh(updated_at: str, max_age_minutes: int) -> bool:
    if not updated_at:
        return False
    try:
        ts = datetime.fromisoformat(updated_at)
    except ValueError:
        return False
    return (datetime.now(CN_TZ) - ts) <= timedelta(minutes=max_age_minutes)


def _fetch_module_bundle(module: str, limit: int) -> dict[str, Any]:
    sources = get_module_sources(module)
    if not sources:
        return {"type": "rss", "items": []}

    groups: list[list[dict[str, str]]] = []
    for source in sources:
        if str(source.get("type") or "").lower() != "rss":
            continue
        url = str(source.get("url") or "").strip()
        name = str(source.get("name") or module).strip()
        source_limit = int(source.get("limit", limit) or limit)
        if not url:
            continue
        try:
            groups.append(fetch_rss_entries(url=url, source=name, limit=max(1, source_limit)))
        except Exception:
            groups.append([])

    items = merge_items(*groups, limit=limit)
    _save_items_to_sqlite(module, items)
    return {"type": "rss", "items": items}


def refresh_content_cache(modules: list[str] | None = None, limit: int = 5) -> dict[str, Any]:
    purge_old_sqlite_items()
    normalized = _normalize_modules(modules)
    cache = _load_cache()
    mod_map = cache.get("modules", {}) if isinstance(cache.get("modules"), dict) else {}

    for module in normalized:
        try:
            bundle = _fetch_module_bundle(module, limit=limit)
            items = bundle.get("items") if isinstance(bundle.get("items"), list) else []
            item_type = str(bundle.get("type") or "rss")
        except Exception:
            old_cfg = mod_map.get(module, {}) if isinstance(mod_map.get(module), dict) else {}
            old_items = old_cfg.get("items") if isinstance(old_cfg.get("items"), list) else []
            items = [x for x in old_items if isinstance(x, dict)]
            item_type = str(old_cfg.get("type") or "rss")
        mod_map[module] = {
            "updated_at": _now_iso(),
            "type": item_type,
            "items": items,
        }

    cache["updated_at"] = _now_iso()
    cache["modules"] = mod_map
    cache["sources"] = get_unified_sources()
    _save_cache(cache)
    return cache


def _get_module_bundle(module: str, limit: int, max_age_minutes: int = CACHE_TTL_MINUTES) -> dict[str, Any]:
    db_items = _load_items_from_sqlite(module, limit=limit)
    if db_items:
        return {"type": "rss", "items": db_items}

    cache = _load_cache()
    mod_map = cache.get("modules", {}) if isinstance(cache.get("modules"), dict) else {}
    cfg = mod_map.get(module, {}) if isinstance(mod_map.get(module), dict) else {}
    updated_at = str(cfg.get("updated_at") or "")
    item_type = str(cfg.get("type") or "rss")
    cached_items = cfg.get("items") if isinstance(cfg.get("items"), list) else []

    if cached_items and _is_fresh(updated_at, max_age_minutes=max_age_minutes):
        return {
            "type": item_type,
            "items": [item for item in cached_items if isinstance(item, dict)][:limit],
        }

    try:
        bundle = _fetch_module_bundle(module, limit=limit)
        fresh_items = bundle.get("items") if isinstance(bundle.get("items"), list) else []
        item_type = str(bundle.get("type") or "rss")
    except Exception:
        db_items = _load_items_from_sqlite(module, limit=limit)
        if db_items:
            return {"type": "rss", "items": db_items}
        if cached_items:
            return {
                "type": item_type,
                "items": [item for item in cached_items if isinstance(item, dict)][:limit],
            }
        raise
    mod_map[module] = {
        "updated_at": _now_iso(),
        "type": item_type,
        "items": fresh_items,
    }
    cache["updated_at"] = _now_iso()
    cache["modules"] = mod_map
    cache["sources"] = get_unified_sources()
    _save_cache(cache)
    return {"type": item_type, "items": fresh_items[:limit]}


def _normalize_summary_list(raw: Any, size: int) -> list[str]:
    if not isinstance(raw, list):
        raw = []
    result = [str(x) for x in raw][:size]
    return result


def _normalize_modules(modules: list[str] | None) -> list[str]:
    if not modules:
        return get_default_modules()

    normalized: list[str] = []
    valid = set(list_modules())
    for item in modules:
        key = normalize_module_key(item)
        if key in valid and key not in normalized:
            normalized.append(key)

    return normalized


def _summarize_generic(module: str, items: list[dict[str, str]]) -> tuple[list[str], str]:
    module_title = get_module_title(module)
    try:
        data = summarize_generic_section(module=module, module_title=module_title, items=items)
        default_focus = f"{module_title} 板块保持持续更新。"

        summaries = _normalize_summary_list(data.get("summaries"), len(items))
        focus = str(data.get("focus") or default_focus)
    except Exception:
        summaries = []
        focus = f"{module_title} 板块保持持续更新。"

    while len(summaries) < len(items):
        item = items[len(summaries)]
        summaries.append(f"{item.get('title') or '未命名内容'}：{item.get('snippet') or '暂无摘要。'}")
    return summaries, focus


def _pick_items_for_push(items: list[dict[str, str]], limit: int, randomize: bool = True) -> list[dict[str, str]]:
    filtered = [x for x in items if isinstance(x, dict)]
    if not filtered:
        return []

    size = min(len(filtered), max(1, int(limit)))
    if not randomize or len(filtered) <= size:
        return filtered[:size]
    return random.sample(filtered, k=size)


def _summarize_sections_parallel(sections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if len(sections) <= 1:
        return sections

    max_workers = min(4, len(sections))
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_map = {
            executor.submit(
                _summarize_generic,
                str(section.get("module") or ""),
                section.get("items") if isinstance(section.get("items"), list) else [],
            ): idx
            for idx, section in enumerate(sections)
        }

        for future in as_completed(future_map):
            idx = future_map[future]
            module = str(sections[idx].get("module") or "")
            module_title = str(sections[idx].get("module_title") or module)
            items = sections[idx].get("items") if isinstance(sections[idx].get("items"), list) else []
            try:
                summaries, focus = future.result()
            except Exception:
                summaries = [
                    f"{item.get('title') or '未命名内容'}：{item.get('snippet') or '暂无摘要。'}"
                    for item in items
                    if isinstance(item, dict)
                ]
                focus = f"{module_title} 板块保持持续更新。"

            sections[idx]["summaries"] = summaries
            sections[idx]["focus"] = focus

    return sections


def _load_articles_from_sqlite(modules: list[str] | None = None, limit: int = 200) -> list[dict[str, Any]]:
    _init_sqlite()
    purge_old_sqlite_items()

    q_limit = max(1, int(limit))
    with sqlite3.connect(SQLITE_FILE) as conn:
        if modules:
            placeholders = ",".join("?" for _ in modules)
            cursor = conn.execute(
                f"""
                SELECT module, source, title, url, snippet, embedding, embedding_model, fetched_at
                FROM rss_items
                WHERE module IN ({placeholders})
                ORDER BY fetched_at DESC, id DESC
                LIMIT ?
                """,
                [*modules, q_limit],
            )
        else:
            cursor = conn.execute(
                """
                SELECT module, source, title, url, snippet, embedding, embedding_model, fetched_at
                FROM rss_items
                ORDER BY fetched_at DESC, id DESC
                LIMIT ?
                """,
                (q_limit,),
            )
        rows = cursor.fetchall()

    output: list[dict[str, Any]] = []
    for row in rows:
        embedding_raw = str(row[5] or "[]")
        try:
            embedding_list = json.loads(embedding_raw)
        except Exception:
            embedding_list = []
        vec = [float(x) for x in embedding_list if isinstance(x, (int, float))]

        output.append(
            {
                "module": str(row[0] or ""),
                "source": str(row[1] or ""),
                "title": str(row[2] or ""),
                "url": str(row[3] or ""),
                "snippet": str(row[4] or ""),
                "embedding": vec,
                "embedding_model": str(row[6] or ""),
                "fetched_at": str(row[7] or ""),
            }
        )
    return output


def _format_article_push(items: list[dict[str, Any]], title: str = "🧾 文章推送") -> str:
    if not items:
        raise ReportGenerationError("当前没有可推送内容，请稍后重试或检查数据源")

    lines = [title, ""]
    for idx, item in enumerate(items, 1):
        module = str(item.get("module") or "")
        module_title = get_module_title(module) if module else "未分类"
        source = str(item.get("source") or module_title)
        lines.append(f"{idx}. [{module_title} | {source}] {item.get('title') or ''}")
        lines.append(f"链接：{item.get('url') or ''}")
        snippet = str(item.get("snippet") or "").strip()
        if snippet:
            lines.append(f"摘要：{snippet[:220]}")
        sim = item.get("similarity")
        if isinstance(sim, (int, float)):
            lines.append(f"相似度：{float(sim):.3f}")
        lines.append("")
    return "\n".join(lines)


def _ensure_embeddings_for_recent_items(limit: int = 300) -> None:
    _init_sqlite()
    with sqlite3.connect(SQLITE_FILE) as conn:
        rows = conn.execute(
            """
            SELECT id, module, source, title, url, snippet
            FROM rss_items
            WHERE embedding IS NULL OR embedding = '' OR embedding = '[]'
            ORDER BY fetched_at DESC, id DESC
            LIMIT ?
            """,
            (max(1, int(limit)),),
        ).fetchall()

        if not rows:
            return

        updates: list[tuple[str, str, int, float, int]] = []
        for row in rows:
            row_id = int(row[0])
            item = {
                "source": str(row[2] or ""),
                "title": str(row[3] or ""),
                "url": str(row[4] or ""),
                "snippet": str(row[5] or ""),
            }
            emb_json, emb_model, emb_dim, emb_norm = _embed_item(str(row[1] or ""), item)
            updates.append((emb_json, emb_model, emb_dim, emb_norm, row_id))

        conn.executemany(
            """
            UPDATE rss_items
            SET embedding = ?, embedding_model = ?, embedding_dim = ?, embedding_norm = ?
            WHERE id = ?
            """,
            updates,
        )
        conn.commit()


def query_articles_by_embedding(
    query: str,
    top_k: int = 5,
    min_similarity: float = 0.25,
    modules: list[str] | None = None,
) -> list[dict[str, Any]]:
    user_query = (query or "").strip()
    if not user_query:
        return []

    _ensure_embeddings_for_recent_items(limit=500)
    query_payload = embed_text(user_query)
    query_vec = query_payload.get("vector") if isinstance(query_payload.get("vector"), list) else []
    qvec = [float(x) for x in query_vec if isinstance(x, (int, float))]
    if not qvec:
        return []

    normalized_modules = _normalize_modules(modules) if modules else None
    candidates = _load_articles_from_sqlite(modules=normalized_modules, limit=500)

    picked: list[dict[str, Any]] = []
    threshold = float(min_similarity)
    for item in candidates:
        ivec = item.get("embedding") if isinstance(item.get("embedding"), list) else []
        vec = [float(x) for x in ivec if isinstance(x, (int, float))]
        if not vec:
            continue
        sim = cosine_similarity(qvec, vec)
        if sim < threshold:
            continue
        item_copy = dict(item)
        item_copy["similarity"] = sim
        picked.append(item_copy)

    picked.sort(key=lambda x: float(x.get("similarity") or 0), reverse=True)
    return picked[: max(1, int(top_k))]


def get_report(modules: list[str] | None = None, limit: int = 3, randomize: bool = True) -> str:
    enabled_modules = _normalize_modules(modules)
    if not enabled_modules:
        raise ReportGenerationError("未指定有效板块，请先通过 /rss 添加信息源")

    articles: list[dict[str, Any]] = []
    for module in enabled_modules:
        try:
            bundle = _get_module_bundle(module, limit=max(10, limit * 3))
        except Exception as exc:
            raise ReportGenerationError(f"抓取 {module} 板块失败：{exc}") from exc

        items = bundle.get("items") if isinstance(bundle.get("items"), list) else []
        item_type = str(bundle.get("type") or "rss")
        if not items:
            continue

        rss_items = _pick_items_for_push([x for x in items if isinstance(x, dict)], limit=limit, randomize=randomize)
        _ = item_type
        for it in rss_items:
            article = dict(it)
            article["module"] = module
            articles.append(article)

    if not articles:
        raise ReportGenerationError("当前没有可推送内容，请稍后重试或检查数据源")

    if randomize and len(articles) > limit:
        picked = random.sample(articles, k=max(1, int(limit)))
    else:
        picked = articles[: max(1, int(limit))]

    return _format_article_push(
        picked,
        title=f"🧾 文章推送（跨源）\n范围：{', '.join(enabled_modules)}",
    )

def get_smart_report(query: str, limit: int = 3) -> str:
    semantic_hits = query_articles_by_embedding(
        query=query,
        top_k=max(1, int(limit)),
        min_similarity=0.25,
        modules=None,
    )
    if semantic_hits:
        return _format_article_push(
            semantic_hits,
            title=f"🧠 语义检索推送\n查询：{query}",
        )

    modules = pick_modules_by_query(query, max_count=3)
    return get_report(modules=modules, limit=limit, randomize=True)


def get_semantic_report(query: str, top_k: int = 5, min_similarity: float = 0.25) -> str:
    semantic_hits = query_articles_by_embedding(
        query=query,
        top_k=top_k,
        min_similarity=min_similarity,
        modules=None,
    )
    if not semantic_hits:
        try:
            refresh_content_cache(modules=list_modules(), limit=max(10, int(top_k) * 3))
            semantic_hits = query_articles_by_embedding(
                query=query,
                top_k=top_k,
                min_similarity=min_similarity,
                modules=None,
            )
        except Exception:
            semantic_hits = []
    if not semantic_hits:
        raise ReportGenerationError("未找到相似度达标的文章，请尝试更具体的关键词")
    return _format_article_push(
        semantic_hits,
        title=f"🧠 语义检索推送\n查询：{query}",
    )