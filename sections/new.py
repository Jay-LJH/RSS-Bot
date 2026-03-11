from sections.common import fetch_rss_entries, merge_items

BBC_WORLD_RSS = "https://feeds.bbci.co.uk/news/world/rss.xml"
NYT_HOME_RSS = "https://rss.nytimes.com/services/xml/rss/nyt/HomePage.xml"


def get_new_section(limit: int = 5) -> list[dict[str, str]]:
    try:
        bbc = fetch_rss_entries(BBC_WORLD_RSS, "BBC", limit=3)
    except Exception:
        bbc = []

    try:
        nyt = fetch_rss_entries(NYT_HOME_RSS, "New York Times", limit=3)
    except Exception:
        nyt = []

    return merge_items(bbc, nyt, limit=limit)
