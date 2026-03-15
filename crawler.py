import json
import random
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from llm_client import summarize_generic_section
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
                fetched_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_rss_items_module_fetched_at ON rss_items(module, fetched_at DESC)"
        )
        conn.commit()


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
                fetched_at,
            )
        )

    if not rows:
        return

    with sqlite3.connect(SQLITE_FILE) as conn:
        conn.executemany(
            """
            INSERT INTO rss_items(module, source, title, url, snippet, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(url) DO UPDATE SET
                module=excluded.module,
                source=excluded.source,
                title=excluded.title,
                snippet=excluded.snippet,
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


def get_report(modules: list[str] | None = None, limit: int = 3, randomize: bool = True) -> str:
    enabled_modules = _normalize_modules(modules)
    if not enabled_modules:
        raise ReportGenerationError("未指定有效板块，请先通过 /rss 添加信息源")

    section_payloads: list[dict[str, Any]] = []
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
        summaries, focus = _summarize_generic(module, rss_items)
        section_payloads.append(
            {
                "module": module,
                "module_title": get_module_title(module),
                "type": item_type,
                "items": rss_items,
                "summaries": summaries,
                "focus": focus,
            }
        )

    if not section_payloads:
        raise ReportGenerationError("当前没有可推送内容，请稍后重试或检查数据源")

    lines = ["🧾 今日推送", f"板块：{', '.join(enabled_modules)}", ""]

    for section in section_payloads:
        module = str(section.get("module") or "")
        module_title = str(section.get("module_title") or module)
        item_type = str(section.get("type") or "rss")
        items = section.get("items") if isinstance(section.get("items"), list) else []
        summaries = section.get("summaries") if isinstance(section.get("summaries"), list) else []
        focus = str(section.get("focus") or "")

        lines.append(f"📰 {module_title}")
        lines.append("")
        for idx, item in enumerate(items, 1):
            lines.append(f"{idx}. [{item.get('source') or module}] {item.get('title') or ''}")
            lines.append(f"链接：{item.get('url') or ''}")
            if idx - 1 < len(summaries):
                lines.append(f"AI 总结：{summaries[idx - 1]}")
            lines.append("")

        lines.append(f"🔎 {module_title} 焦点：{focus}")
        lines.append("")

    return "\n".join(lines)


def get_smart_report(query: str, limit: int = 3) -> str:
    modules = pick_modules_by_query(query, max_count=2)
    return get_report(modules=modules, limit=limit, randomize=True)