import re
import xml.etree.ElementTree as ET

import requests
from bs4 import BeautifulSoup

USER_AGENT = "Mozilla/5.0 (compatible; trending-bot/1.0)"


def build_web_headers() -> dict[str, str]:
    return {"User-Agent": USER_AGENT}


def clean_text(value: str) -> str:
    raw = (value or "").strip()
    if raw.startswith("http://") or raw.startswith("https://"):
        plain = raw
    else:
        plain = BeautifulSoup(raw, "html.parser").get_text(" ", strip=True)
    plain = re.sub(r"\s+", " ", plain)
    return plain.strip()


def fetch_rss_entries(url: str, source: str, limit: int = 3) -> list[dict[str, str]]:
    response = requests.get(url, headers=build_web_headers(), timeout=20)
    response.raise_for_status()

    root = ET.fromstring(response.content)
    channel = root.find("channel")
    if channel is None:
        return []

    items: list[dict[str, str]] = []
    for item in channel.findall("item"):
        title = clean_text(item.findtext("title", default=""))
        link = clean_text(item.findtext("link", default=""))
        desc = clean_text(item.findtext("description", default=""))
        if not title or not link:
            continue

        items.append(
            {
                "source": source,
                "title": title,
                "url": link,
                "snippet": desc,
            }
        )
        if len(items) >= limit:
            break

    return items


def merge_items(*groups: list[dict[str, str]], limit: int = 5) -> list[dict[str, str]]:
    merged: list[dict[str, str]] = []
    seen: set[str] = set()
    for group in groups:
        for item in group:
            url = item.get("url", "")
            if not url or url in seen:
                continue
            seen.add(url)
            merged.append(item)
            if len(merged) >= limit:
                return merged
    return merged
