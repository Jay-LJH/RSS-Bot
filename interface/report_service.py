from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

from core.article import Article
from pipeline import run_batch
from sources import fetch_module_articles, get_default_modules, get_module_title, list_modules, normalize_module_key, pick_modules_by_query
from storage import ArticleStore, CacheStore

CN_TZ = timezone(timedelta(hours=8))
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)


class ReportGenerationError(RuntimeError):
    pass


_store = ArticleStore(DATA_DIR / "rss_items.db")
_cache = CacheStore(DATA_DIR / "content_cache.json")


def _now_iso() -> str:
    return datetime.now(CN_TZ).isoformat()


def _normalize_modules(modules: list[str] | None) -> list[str]:
    if not modules:
        return get_default_modules()

    valid = set(list_modules())
    out: list[str] = []
    for m in modules:
        key = normalize_module_key(m)
        if key in valid and key not in out:
            out.append(key)
    return out


def get_available_modules() -> list[str]:
    return list_modules()


def refresh_content_cache(modules: list[str] | None = None, limit: int = 5) -> dict[str, object]:
    selected_modules = _normalize_modules(modules)
    if not selected_modules:
        selected_modules = list_modules()

    module_articles: dict[str, list[Article]] = {}
    for module in selected_modules:
        raw_articles = fetch_module_articles(module=module, limit=max(1, int(limit)))
        processed = run_batch(raw_articles)
        _store.upsert_articles(processed)
        module_articles[module] = processed

    _store.purge_old(max_age_hours=24)
    _cache.save_modules(module_articles, updated_at=_now_iso())
    return {
        "updated_at": _now_iso(),
        "modules": {module: [article.to_dict() for article in articles] for module, articles in module_articles.items()},
    }


def _ensure_recent_articles(modules: list[str] | None, limit: int) -> list[Article]:
    selected = _normalize_modules(modules)
    recent = _store.list_recent(modules=selected or None, limit=max(1, int(limit)))
    if recent:
        return recent

    refresh_content_cache(modules=selected or None, limit=max(10, int(limit)))
    return _store.list_recent(modules=selected or None, limit=max(1, int(limit)))


def _format_article_push(items: list[Article], title: str) -> str:
    if not items:
        raise ReportGenerationError("当前没有可推送内容，请稍后重试或检查数据源")

    lines = [title, ""]
    for idx, article in enumerate(items, 1):
        module_title = get_module_title(article.module)
        lines.append(f"{idx}. [{module_title} | {article.source_name}] {article.title}")
        lines.append(f"链接：{article.url}")
        if article.snippet:
            lines.append(f"摘要：{article.snippet[:220]}")
        if "similarity" in article.metadata:
            lines.append(f"相似度：{float(article.metadata['similarity']):.3f}")
        lines.append("")
    return "\n".join(lines)


def get_report(modules: list[str] | None = None, limit: int = 3, randomize: bool = True) -> str:
    selected = _normalize_modules(modules)
    if not selected:
        raise ReportGenerationError("未指定有效板块，请先通过 /rss 添加信息源")

    articles = _ensure_recent_articles(selected, limit=max(10, int(limit) * 3))
    module_filtered = [a for a in articles if a.module in selected]
    if not module_filtered:
        raise ReportGenerationError("当前没有可推送内容，请稍后重试或检查数据源")

    if randomize and len(module_filtered) > int(limit):
        picked = random.sample(module_filtered, k=max(1, int(limit)))
    else:
        picked = module_filtered[: max(1, int(limit))]

    return _format_article_push(picked, title=f"🧾 文章推送（Article Centered）\n范围：{', '.join(selected)}")


def get_semantic_report(query: str, top_k: int = 5, min_similarity: float = 0.25) -> str:
    query_text = (query or "").strip()
    if not query_text:
        raise ReportGenerationError("查询不能为空")

    hits = _store.semantic_search(query=query_text, top_k=top_k, min_similarity=min_similarity)
    if not hits:
        refresh_content_cache(modules=list_modules(), limit=max(10, int(top_k) * 3))
        hits = _store.semantic_search(query=query_text, top_k=top_k, min_similarity=min_similarity)

    if not hits:
        raise ReportGenerationError("未找到相似度达标的文章，请尝试更具体的关键词")

    picked: list[Article] = []
    for article, sim in hits:
        article.metadata["similarity"] = sim
        picked.append(article)
    return _format_article_push(picked, title=f"🧠 语义检索推送\n查询：{query_text}")


def get_smart_report(query: str, limit: int = 3) -> str:
    query_text = (query or "").strip()
    semantic = _store.semantic_search(query=query_text, top_k=max(1, int(limit)), min_similarity=0.25)
    if semantic:
        picked: list[Article] = []
        for article, sim in semantic:
            article.metadata["similarity"] = sim
            picked.append(article)
        return _format_article_push(picked, title=f"🧠 语义检索推送\n查询：{query_text}")

    modules = pick_modules_by_query(query_text, max_count=3)
    return get_report(modules=modules, limit=limit, randomize=True)
