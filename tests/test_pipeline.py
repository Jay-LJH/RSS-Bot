from unittest.mock import patch, MagicMock
from core.article import Article
from pipeline import clean, enrich_embedding, runner

def test_clean_pipeline():
    article = Article(
        module=" TEST ",
        source_id="1",
        source_name="  Test   Source  ",
        title="  Test    Title  ",
        url="http://example.com/test",
        snippet="  Test   Snippet  \n  Line 2 ",
    )
    processed = clean.run(article)
    assert processed.module == "test"
    assert processed.source_name == "Test Source"
    assert processed.title == "Test Title"
    assert processed.snippet == "Test Snippet Line 2"

@patch("pipeline.enrich_embedding.embed_article")
def test_runner(mock_embed):
    def mock_embed_run(article):
        article.embedding = [0.1, 0.2, 0.3]
        return article
    
    mock_embed.side_effect = mock_embed_run
    
    article = Article(
        module=" TEST ",
        source_id="1",
        source_name="  Test   Source  ",
        title="  Test    Title  ",
        url="http://example.com/test",
        snippet="  Test   Snippet  ",
    )
    
    processed = runner.run(article)
    assert processed.module == "test"
    assert processed.title == "Test Title"
    assert processed.embedding == [0.1, 0.2, 0.3]
