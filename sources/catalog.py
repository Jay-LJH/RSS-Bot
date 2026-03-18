from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any
import json
import re
import xml.etree.ElementTree as ET

import requests
import yaml
from bs4 import BeautifulSoup

from core.article import Article
from llm import classify_rss_feed
from llm import cosine_similarity, embed_text

BASE_DIR = Path(__file__).resolve().parent.parent
SOURCES_FILE = BASE_DIR / "sources" / "sources.yml"
CACHE_FILE = BASE_DIR / "data" / "content_cache.json"
USER_AGENT = "Mozilla/5.0 (compatible; article-bot/2.0)"

MODULE_TITLES = {
    "ai": "人工智能",
    "finance": "财经",
    "sports": "体育",
    "science": "科学",
    "security": "安全",
    "gaming": "游戏",
    "global_news": "国际新闻",
}

MODULE_KEYWORDS: dict[str, list[str]] = {
    "ai": ["tech", "ai", "software", "芯片", "人工智能", "科技"],
    "finance": ["finance", "market", "财经", "股市", "经济"],
    "sports": ["sports", "football", "nba", "体育", "足球", "篮球"],
    "science": ["science", "space", "科学", "太空", "医学"],
    "security": ["security", "cyber", "安全", "漏洞", "攻击"],
    "gaming": ["game", "gaming", "游戏", "电竞"],
    "global_news": ["news", "world", "国际", "新闻", "时政"],
}


def _clean_text(value: str) -> str:
    plain = BeautifulSoup((value or "").strip(), "html.parser").get_text(" ", strip=True)
    plain = re.sub(r"\s+", " ", plain)
    return plain.strip()


def _build_headers() -> dict[str, str]:
    return {"User-Agent": USER_AGENT}


def normalize_module_key(module: str) -> str:
    key = re.sub(r"[^a-z0-9_\-\u4e00-\u9fff]+", "_", (module or "").strip().lower()).strip("_")
    return key or "misc"


@lru_cache(maxsize=1)
def load_source_catalog() -> dict[str, Any]:
    if not SOURCES_FILE.exists():
        return {"version": 1, "modules": {}}

    data = yaml.safe_load(SOURCES_FILE.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return {"version": 1, "modules": {}}

    modules = data.get("modules")
    if not isinstance(modules, dict):
        data["modules"] = {}
        return data

    normalized: dict[str, Any] = {}
    for module, raw_cfg in modules.items():
        key = normalize_module_key(str(module))
        cfg = raw_cfg if isinstance(raw_cfg, dict) else {}
        sources = cfg.get("sources", []) if isinstance(cfg.get("sources"), list) else []
        normalized[key] = {
            "title": str(cfg.get("title") or MODULE_TITLES.get(key) or key),
            "sources": [s for s in sources if isinstance(s, dict)],
        }
    data["modules"] = normalized
    return data


def reload_source_catalog() -> dict[str, Any]:
    load_source_catalog.cache_clear()
    return load_source_catalog()


def _save_source_catalog(data: dict[str, Any]) -> None:
    SOURCES_FILE.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    load_source_catalog.cache_clear()


def list_modules() -> list[str]:
    modules = load_source_catalog().get("modules", {})
    return [str(k) for k in modules.keys()] if isinstance(modules, dict) else []


def get_default_modules(max_count: int = 3) -> list[str]:
    return list_modules()[: max(1, int(max_count))]


def get_module_title(module: str) -> str:
    key = normalize_module_key(module)
    modules = load_source_catalog().get("modules", {})
    cfg = modules.get(key, {}) if isinstance(modules, dict) else {}
    return str(cfg.get("title") or MODULE_TITLES.get(key) or key)


def get_module_sources(module: str, source_type: str | None = None) -> list[dict[str, Any]]:
    key = normalize_module_key(module)
    modules = load_source_catalog().get("modules", {})
    cfg = modules.get(key, {}) if isinstance(modules, dict) else {}
    sources = cfg.get("sources") if isinstance(cfg.get("sources"), list) else []
    out: list[dict[str, Any]] = []
    for source in sources:
        if not bool(source.get("enabled", True)):
            continue
        if source_type and str(source.get("type") or "").lower() != source_type.lower():
            continue
        out.append(source)
    return out


def build_unified_source_list(modules: list[str] | None = None) -> list[dict[str, Any]]:
    selected = [normalize_module_key(x) for x in modules] if modules else list_modules()
    result: list[dict[str, Any]] = []
    for module in selected:
        for source in get_module_sources(module):
            result.append(
                {
                    "module": module,
                    "source_id": str(source.get("id") or ""),
                    "type": str(source.get("type") or ""),
                    "name": str(source.get("name") or ""),
                    "url": str(source.get("url") or ""),
                    "limit": int(source.get("limit", 3) or 3),
                }
            )
    return result


def _inspect_rss(url: str, limit: int = 8) -> dict[str, Any]:
    response = requests.get(url, headers=_build_headers(), timeout=20)
    response.raise_for_status()
    root = ET.fromstring(response.content)
    channel = root.find("channel")
    if channel is None:
        return {"feed_title": "", "items": []}

    feed_title = _clean_text(channel.findtext("title", default=""))
    items: list[dict[str, str]] = []
    for item in channel.findall("item")[:limit]:
        title = _clean_text(item.findtext("title", default=""))
        desc = _clean_text(item.findtext("description", default=""))
        link = _clean_text(item.findtext("link", default=""))
        if title or desc:
            items.append({"title": title, "desc": desc, "url": link})
    return {"feed_title": feed_title, "items": items}


def _score_module(module: str, text: str) -> int:
    content = text.lower()
    return sum(1 for kw in MODULE_KEYWORDS.get(module, []) if kw.lower() in content)


def _cache_signature() -> int:
    if not CACHE_FILE.exists():
        return -1
    return int(CACHE_FILE.stat().st_mtime_ns)


@lru_cache(maxsize=4)
def _load_cached_articles(cache_sig: int) -> tuple[Article, ...]:
    if cache_sig < 0 or not CACHE_FILE.exists():
        return ()

    try:
        data = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return ()

    modules = data.get("modules") if isinstance(data, dict) else {}
    if not isinstance(modules, dict):
        return ()

    articles: list[Article] = []
    for module, items in modules.items():
        if not isinstance(items, list):
            continue
        module_key = normalize_module_key(str(module))
        for raw in items:
            if not isinstance(raw, dict):
                continue
            try:
                article = Article.from_dict(raw)
            except Exception:
                continue
            article.module = module_key
            if article.url:
                articles.append(article)

    return tuple(articles)


def _semantic_module_scores(query: str, min_similarity: float = 0.2) -> dict[str, float]:
    payload = embed_text(query)
    q_vec = [float(x) for x in payload.get("vector", []) if isinstance(x, (int, float))]
    if not q_vec:
        return {}

    candidates = _load_cached_articles(_cache_signature())
    if not candidates:
        return {}

    per_module_hits: dict[str, list[float]] = {}
    for article in candidates:
        candidate_vec = article.embedding
        if not candidate_vec:
            text = " ".join([article.title, article.snippet, article.source_name]).strip()
            if not text:
                continue
            payload = embed_text(text)
            candidate_vec = [float(x) for x in payload.get("vector", []) if isinstance(x, (int, float))]
            if not candidate_vec:
                continue

        sim = cosine_similarity(q_vec, candidate_vec)
        if sim < float(min_similarity):
            continue
        per_module_hits.setdefault(article.module, []).append(sim)

    scores: dict[str, float] = {}
    for module, sims in per_module_hits.items():
        ordered = sorted(sims, reverse=True)
        top = ordered[0]
        top3_avg = sum(ordered[:3]) / min(3, len(ordered))
        hit_bonus = min(len(ordered), 4) * 0.03
        scores[module] = top * 0.7 + top3_avg * 0.3 + hit_bonus
    return scores


def classify_rss_module(url: str, source_name: str = "") -> tuple[str, str]:
    inspected = _inspect_rss(url)
    feed_title = str(inspected.get("feed_title") or "")
    items = inspected.get("items") if isinstance(inspected.get("items"), list) else []

    try:
        llm_result = classify_rss_feed(source_name=source_name, feed_title=feed_title, samples=items[:6])
        module_key = normalize_module_key(str(llm_result.get("module_key") or ""))
        module_title = str(llm_result.get("module_title") or "").strip()
        if module_key and module_key != "misc":
            return module_key, (module_title or MODULE_TITLES.get(module_key) or module_key)
    except Exception:
        pass

    corpus = [source_name, feed_title]
    for item in items[:6]:
        corpus.extend([str(item.get("title") or ""), str(item.get("desc") or "")])
    text = " ".join(corpus)

    best_module, best_score = "global_news", -1
    for module in MODULE_KEYWORDS:
        score = _score_module(module, text)
        if score > best_score:
            best_module, best_score = module, score
    return best_module, MODULE_TITLES.get(best_module, best_module)


def add_rss_source(url: str, source_name: str = "", per_source_limit: int = 5) -> dict[str, Any]:
    clean_url = (url or "").strip()
    if not clean_url.startswith("http"):
        raise ValueError("RSS 地址必须是 http/https URL")

    module, module_title = classify_rss_module(clean_url, source_name=source_name)
    inspected = _inspect_rss(clean_url)
    feed_title = str(inspected.get("feed_title") or "").strip()
    final_name = (source_name or feed_title or module).strip()

    data = reload_source_catalog()
    modules = data.get("modules") if isinstance(data.get("modules"), dict) else {}
    if not isinstance(modules, dict):
        modules = {}

    module_cfg = modules.get(module) if isinstance(modules.get(module), dict) else {}
    module_cfg["title"] = str(module_cfg.get("title") or module_title)
    module_sources = module_cfg.get("sources") if isinstance(module_cfg.get("sources"), list) else []

    for source in module_sources:
        if not isinstance(source, dict):
            continue
        if str(source.get("url") or "").strip() == clean_url:
            source["name"] = final_name
            source["enabled"] = True
            source["limit"] = int(source.get("limit", per_source_limit) or per_source_limit)
            module_cfg["sources"] = module_sources
            modules[module] = module_cfg
            data["modules"] = modules
            _save_source_catalog(data)
            return {
                "module": module,
                "module_title": module_cfg["title"],
                "source_name": final_name,
                "source_url": clean_url,
                "source_id": str(source.get("id") or ""),
                "updated": True,
            }

    next_index = len([s for s in module_sources if isinstance(s, dict)]) + 1
    source_id = re.sub(r"[^a-z0-9]+", "_", final_name.lower()).strip("_") or f"rss_{next_index}"
    source_id = f"{source_id}_{next_index}"
    module_sources.append(
        {
            "id": source_id,
            "type": "rss",
            "name": final_name,
            "url": clean_url,
            "enabled": True,
            "limit": max(1, int(per_source_limit)),
        }
    )
    module_cfg["sources"] = module_sources
    modules[module] = module_cfg
    data["modules"] = modules
    _save_source_catalog(data)

    return {
        "module": module,
        "module_title": module_cfg["title"],
        "source_name": final_name,
        "source_url": clean_url,
        "source_id": source_id,
        "updated": False,
    }


def pick_modules_by_query(query: str, max_count: int = 2) -> list[str]:
    text = (query or "").strip().lower()
    if not text:
        return get_default_modules(max_count=max_count)

    modules = list_modules()
    if not modules:
        return []

    scored: list[tuple[str, int]] = []
    for module in modules:
        score = 0
        title = get_module_title(module).lower()
        if module.lower() in text:
            score += 3
        if title and title in text:
            score += 3
        score += sum(1 for kw in MODULE_KEYWORDS.get(module, []) if kw.lower() in text)
        for source in get_module_sources(module):
            name = str(source.get("name") or "").lower()
            if name and name in text:
                score += 2
        scored.append((module, score))

    scored.sort(key=lambda x: x[1], reverse=True)
    chosen = [m for m, s in scored if s > 0][: max(1, int(max_count))]
    return chosen or get_default_modules(max_count=max_count)


def match_modules_by_rules(query: str, max_count: int = 3, min_score: int = 2) -> list[str]:
    text = (query or "").strip().lower()
    if not text:
        return []

    semantic_scores = _semantic_module_scores(text, min_similarity=0.2)
    scored: list[tuple[str, int]] = []
    for module in list_modules():
        score = 0
        if module.lower() in text:
            score += 4
        title = get_module_title(module).lower()
        if title and title in text:
            score += 4
        score += sum(1 for kw in MODULE_KEYWORDS.get(module, []) if kw.lower() in text)
        for source in get_module_sources(module):
            name = str(source.get("name") or "").lower().strip()
            if name and name in text:
                score += 5

        semantic = float(semantic_scores.get(module, 0.0))
        semantic_bonus = int(round(semantic * 10))
        total_score = score + semantic_bonus

        if total_score >= max(1, int(min_score)):
            scored.append((module, total_score))

    scored.sort(key=lambda x: x[1], reverse=True)
    return [module for module, _ in scored[: max(1, int(max_count))]]
