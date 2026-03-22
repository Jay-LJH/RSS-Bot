import tempfile
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from core.article import Article
from storage.article_store import ArticleStore
from pipeline import runner

def test_full_pipeline_flow():
    # End-to-end integration test of parsing -> mapping -> processing -> storing
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        store = ArticleStore(db_path)
        
        # Step 1: Simulate source fetch
        raw_article = Article(
            module="test_module ",
            source_id="source_1",
            source_name=" Test Source ",
            title=" A   Great Title ",
            url="https://example.com/great-title",
            snippet=" Some   Description   here  "
        )
        
        # Step 2: Run pipeline (clean -> embed)
        with patch("pipeline.enrich_embedding.embed_article") as mock_embed:
            def mock_embed_run(article):
                article.embedding = [0.1, 0.5, 0.9]
                return article
            mock_embed.side_effect = mock_embed_run
            
            processed = runner.run(raw_article)
            
            assert processed.module == "test_module"
            assert processed.source_name == "Test Source"
            assert processed.title == "A Great Title"
            assert processed.snippet == "Some Description here"
            assert processed.embedding == [0.1, 0.5, 0.9]
            
            # Step 3: Upsert into store
            count = store.upsert_articles([processed])
            assert count == 1
            
            # Step 4: Retrieve from store (simulate API read)
            with store._connect() as conn:
                cursor = conn.execute("SELECT module, title, embedding FROM articles WHERE url='https://example.com/great-title'")
                row = cursor.fetchone()
                assert row[0] == "test_module"
                assert row[1] == "A Great Title"
                assert "[0.1" in row[2]
