import xml.etree.ElementTree as ET

import requests

from .http import build_web_headers
from .text import clean_text


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
