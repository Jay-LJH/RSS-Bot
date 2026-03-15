import re

from bs4 import BeautifulSoup


def clean_text(value: str) -> str:
    raw = (value or "").strip()
    if raw.startswith("http://") or raw.startswith("https://"):
        plain = raw
    else:
        plain = BeautifulSoup(raw, "html.parser").get_text(" ", strip=True)
    plain = re.sub(r"\s+", " ", plain)
    return plain.strip()
