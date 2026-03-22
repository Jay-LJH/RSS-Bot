from core.article import Article

def test_article_creation():
    article = Article(
        module="test",
        source_id="1",
        source_name="Test Source",
        title="Test Title",
        url="http://example.com/test",
    )
    assert article.module == "test"
    assert article.source_id == "1"
    assert article.title == "Test Title"
    assert article.url == "http://example.com/test"
    assert article.snippet == ""
    assert article.embedding == []

