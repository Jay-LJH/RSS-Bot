from __future__ import annotations

from core.article import Article
from llm import embed_article


def run(article: Article) -> Article:
    if article.embedding:
        return article
    return embed_article(article)
