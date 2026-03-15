from __future__ import annotations

from core.article import Article
from . import clean, enrich_embedding


def run(article: Article) -> Article:
    processed = clean.run(article)
    processed = enrich_embedding.run(processed)
    return processed


def run_batch(articles: list[Article]) -> list[Article]:
    return [run(article) for article in articles]
