import os
import tempfile
from pathlib import Path
from core.article import Article
from storage.article_store import ArticleStore

def test_article_store_upsert_and_fetch():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        store = ArticleStore(db_path)
        
        articles = [
            Article(
                module="test",
                source_id="1",
                source_name="Test Source",
                title="Test Title 1",
                url="http://example.com/test1",
                snippet="test 1",
                embedding=[0.1, 0.2]
            ),
            Article(
                module="test",
                source_id="2",
                source_name="Test Source",
                title="Test Title 2",
                url="http://example.com/test2",
                snippet="test 2"
            )
        ]
        
        count = store.upsert_articles(articles)
        assert count == 2
        
        with store._connect() as conn:
            cursor = conn.execute("SELECT title, url FROM articles ORDER BY url")
            rows = cursor.fetchall()
            assert len(rows) == 2
            assert rows[0][0] == "Test Title 1"
            assert rows[1][0] == "Test Title 2"

        # Test duplicate urls will be updated
        articles[0].title = "Updated Title 1"
        count = store.upsert_articles(articles)
        assert count == 2
        
        with store._connect() as conn:
            cursor = conn.execute("SELECT title FROM articles WHERE url='http://example.com/test1'")
            row = cursor.fetchone()
            assert row[0] == "Updated Title 1"
