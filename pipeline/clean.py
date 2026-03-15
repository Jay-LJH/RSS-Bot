from __future__ import annotations

import re

from core.article import Article


def run(article: Article) -> Article:
    article.title = re.sub(r"\s+", " ", (article.title or "").strip())
    article.snippet = re.sub(r"\s+", " ", (article.snippet or "").strip())
    article.source_name = re.sub(r"\s+", " ", (article.source_name or "").strip())
    article.module = re.sub(r"\s+", "_", (article.module or "").strip().lower())
    return article
