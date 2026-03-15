USER_AGENT = "Mozilla/5.0 (compatible; trending-bot/1.0)"


def build_web_headers() -> dict[str, str]:
    return {"User-Agent": USER_AGENT}
