import requests
from bs4 import BeautifulSoup

from sections.common import build_web_headers, clean_text, fetch_rss_entries, merge_items

MIT_TECH_REVIEW_RSS = "https://www.technologyreview.com/feed/"

def get_tech_section(limit: int = 5) -> list[dict[str, str]]:
    try:
        mit = fetch_rss_entries(MIT_TECH_REVIEW_RSS, "MIT Tech Review", limit=5)
    except Exception:
        mit = []

    return mit
