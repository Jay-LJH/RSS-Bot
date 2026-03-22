from __future__ import annotations
import logging

from core.article import Article
from . import clean, enrich_embedding

logger = logging.getLogger(__name__)

def run(article: Article) -> Article:
    try:
        logger.info(f"Pipeline started for article: {article.title}")
        processed = clean.run(article)
        processed = enrich_embedding.run(processed)
        logger.info(f"Pipeline finished for article: {article.title}")
        return processed
    except Exception as e:
        logger.error(f"Pipeline failed for article {article.title}: {e}", exc_info=True)
        raise


def run_batch(articles: list[Article]) -> list[Article]:
    logger.info(f"Starting batch pipeline for {len(articles)} articles")
    results = []
    for article in articles:
        try:
            results.append(run(article))
        except Exception:
            # Skip failed articles in batch but continue
            continue
    logger.info(f"Batch pipeline complete, {len(results)}/{len(articles)} succeeded")
    return results
