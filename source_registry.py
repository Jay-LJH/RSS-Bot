from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any
import re
import xml.etree.ElementTree as ET

import requests
import yaml

from llm_client import classify_rss_feed
from common import build_web_headers, clean_text

BASE_DIR = Path(__file__).resolve().parent
SOURCES_FILE = BASE_DIR / "sources.yml"

MODULE_TITLES = {
    "ai": "人工智能",
    "finance": "财经",
    "sports": "体育",
    "science": "科学",
    "security": "安全",
    "gaming": "游戏",
}

MODULE_KEYWORDS: dict[str, list[str]] = {
    "ai": [
        "tech", "technology", "ai", "software", "chip", "cloud", "openai", "google", "apple", "microsoft",
        "科技", "技术", "人工智能", "芯片", "互联网", "软件",
    ],
    "opensource": ["github", "repo", "repository", "open source", "opensource", "开源", "代码", "仓库", "趋势"],
    "finance": ["finance", "market", "stock", "economy", "财经", "股市", "经济", "投资", "货币"],
    "sports": ["sports", "football", "nba", "fifa", "体育", "足球", "篮球", "网球", "奥运"],
    "science": ["science", "nature", "space", "physics", "chemistry", "生物", "科学", "太空", "医学"],
    "security": ["security", "cyber", "vulnerability", "hack", "安全", "漏洞", "攻击", "威胁"],
    "gaming": ["game", "gaming", "steam", "esports", "游戏", "电竞", "主机"],
    "global_news": ["news", "world", "global", "politics", "breaking", "新闻", "国际", "时政", "快讯"],
}


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
        sources = cfg.get("sources", []) if isinstance(cfg, dict) else []
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
    SOURCES_FILE.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    load_source_catalog.cache_clear()


def normalize_module_key(module: str) -> str:
    key = (module or "").strip().lower()
    if not key:
        return "misc"
    key = re.sub(r"[^a-z0-9_\-\u4e00-\u9fff]+", "_", key).strip("_")
    return key or "misc"


def list_modules() -> list[str]:
    modules = load_source_catalog().get("modules", {})
    if not isinstance(modules, dict):
        return []
    return [str(k) for k in modules.keys()]


def get_default_modules(max_count: int = 3) -> list[str]:
    existing = list_modules()
    return existing[:max_count]


def get_module_sources(module: str, source_type: str | None = None) -> list[dict[str, Any]]:
    key = normalize_module_key(module)

    modules = load_source_catalog().get("modules", {})
    cfg = modules.get(key, {}) if isinstance(modules, dict) else {}
    sources = cfg.get("sources", []) if isinstance(cfg, dict) else []

    result: list[dict[str, Any]] = []
    for source in sources:
        if not bool(source.get("enabled", True)):
            continue
        if source_type and str(source.get("type", "")).lower() != source_type.lower():
            continue
        result.append(source)
    return result


def build_unified_source_list(modules: list[str] | None = None) -> list[dict[str, Any]]:
    selected = [normalize_module_key(x) for x in modules] if modules else list_modules()
    output: list[dict[str, Any]] = []

    for module in selected:
        key = normalize_module_key(module)
        for source in get_module_sources(key):
            output.append(
                {
                    "module": key,
                    "source_id": str(source.get("id") or ""),
                    "type": str(source.get("type") or ""),
                    "name": str(source.get("name") or ""),
                    "url": str(source.get("url") or ""),
                    "limit": int(source.get("limit", 3) or 3),
                }
            )

    return output


def get_module_title(module: str) -> str:
    key = normalize_module_key(module)
    modules = load_source_catalog().get("modules", {})
    cfg = modules.get(key, {}) if isinstance(modules, dict) else {}
    return str(cfg.get("title") or MODULE_TITLES.get(key) or key)


def _inspect_rss(url: str, limit: int = 8) -> dict[str, Any]:
    response = requests.get(url, headers=build_web_headers(), timeout=20)
    response.raise_for_status()

    root = ET.fromstring(response.content)
    channel = root.find("channel")
    if channel is None:
        return {"feed_title": "", "items": []}

    feed_title = clean_text(channel.findtext("title", default=""))
    items: list[dict[str, str]] = []
    for item in channel.findall("item")[:limit]:
        title = clean_text(item.findtext("title", default=""))
        desc = clean_text(item.findtext("description", default=""))
        link = clean_text(item.findtext("link", default=""))
        if not title and not desc:
            continue
        items.append({"title": title, "desc": desc, "url": link})

    return {"feed_title": feed_title, "items": items}


def _score_module(module: str, text: str) -> int:
    score = 0
    content = text.lower()
    for kw in MODULE_KEYWORDS.get(module, []):
        if kw.lower() in content:
            score += 1
    return score


def classify_rss_module(url: str, source_name: str = "") -> tuple[str, str]:
    inspected = _inspect_rss(url)
    feed_title = str(inspected.get("feed_title") or "")
    items = inspected.get("items") if isinstance(inspected.get("items"), list) else []

    try:
        llm_result = classify_rss_feed(
            source_name=source_name,
            feed_title=feed_title,
            samples=[x for x in items if isinstance(x, dict)][:6],
        )
        module_key = normalize_module_key(str(llm_result.get("module_key") or ""))
        module_title = str(llm_result.get("module_title") or "").strip()
        if module_key and module_key != "misc":
            return module_key, (module_title or MODULE_TITLES.get(module_key) or module_key)
    except Exception:
        pass

    corpus = [source_name, feed_title]
    for item in items[:6]:
        if not isinstance(item, dict):
            continue
        corpus.append(str(item.get("title") or ""))
        corpus.append(str(item.get("desc") or ""))
    text = " ".join(corpus)

    best_module = "global_news"
    best_score = -1
    for module in MODULE_KEYWORDS.keys():
        score = _score_module(module, text)
        if score > best_score:
            best_score = score
            best_module = module

    return best_module, MODULE_TITLES.get(best_module, best_module.replace("_", " "))


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
        for kw in MODULE_KEYWORDS.get(module, []):
            if kw.lower() in text:
                score += 1

        for source in get_module_sources(module):
            name = str(source.get("name") or "").lower()
            if name and name in text:
                score += 2

        scored.append((module, score))

    scored.sort(key=lambda x: x[1], reverse=True)
    chosen = [m for m, s in scored if s > 0][:max_count]
    if chosen:
        return chosen
    return get_default_modules(max_count=max_count)


def match_modules_by_rules(query: str, max_count: int = 3, min_score: int = 2) -> list[str]:
    text = (query or "").strip().lower()
    if not text:
        return []

    modules = list_modules()
    if not modules:
        return []

    scored: list[tuple[str, int]] = []
    for module in modules:
        score = 0
        module_lc = module.lower()
        title_lc = get_module_title(module).lower()

        if module_lc and module_lc in text:
            score += 4
        if title_lc and title_lc in text:
            score += 4

        for kw in MODULE_KEYWORDS.get(module, []):
            if kw.lower() in text:
                score += 1

        for source in get_module_sources(module):
            name = str(source.get("name") or "").lower().strip()
            if name and name in text:
                score += 5

        if score >= max(1, int(min_score)):
            scored.append((module, score))

    scored.sort(key=lambda x: x[1], reverse=True)
    return [module for module, _ in scored[: max(1, int(max_count))]]
