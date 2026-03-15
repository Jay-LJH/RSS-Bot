from __future__ import annotations

from typing import Any
import re
import xml.etree.ElementTree as ET

import requests
from bs4 import BeautifulSoup

from core.article import Article
from .catalog import get_module_sources

USER_AGENT = "Mozilla/5.0 (compatible; article-bot/2.0)"


def _build_headers() -> dict[str, str]:
    return {"User-Agent": USER_AGENT}


def _clean_text(value: str) -> str:
    raw = (value or "").strip()
    if raw.startswith("http://") or raw.startswith("https://"):
        plain = raw
    else:
        plain = BeautifulSoup(raw, "html.parser").get_text(" ", strip=True)
    plain = re.sub(r"\s+", " ", plain)
    return plain.strip()


def fetch_rss_articles(module: str, source: dict[str, Any], limit: int = 5) -> list[Article]:
    url = str(source.get("url") or "").strip()
    if not url:
        return []

    source_name = str(source.get("name") or module)
    source_id = str(source.get("id") or source_name)
    response = requests.get(url, headers=_build_headers(), timeout=20)
    response.raise_for_status()

    root = ET.fromstring(response.content)
    channel = root.find("channel")
    if channel is None:
        return []

    output: list[Article] = []
    for item in channel.findall("item"):
        title = _clean_text(item.findtext("title", default=""))
        link = _clean_text(item.findtext("link", default=""))
        desc = _clean_text(item.findtext("description", default=""))
        if not title or not link:
            continue

        output.append(
            Article(
                module=module,
                source_id=source_id,
                source_name=source_name,
                title=title,
                url=link,
                snippet=desc,
            )
        )
        if len(output) >= max(1, int(limit)):
            break

    return output


def fetch_module_articles(module: str, limit: int = 5) -> list[Article]:
    articles: list[Article] = []
    seen_urls: set[str] = set()
    for source in get_module_sources(module, source_type="rss"):
        source_limit = int(source.get("limit", limit) or limit)
        try:
            source_articles = fetch_rss_articles(module=module, source=source, limit=max(1, source_limit))
        except Exception:
            source_articles = []

        for article in source_articles:
            if article.url in seen_urls:
                continue
            seen_urls.add(article.url)
            articles.append(article)
            if len(articles) >= max(1, int(limit)):
                return articles
    return articles
