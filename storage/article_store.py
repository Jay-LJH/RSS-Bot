from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from core.article import Article
from llm import cosine_similarity, embed_text

CN_TZ = timezone(timedelta(hours=8))


class ArticleStore:
    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._db_path)

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS articles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    module TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    source_name TEXT,
                    title TEXT,
                    url TEXT NOT NULL UNIQUE,
                    snippet TEXT,
                    embedding TEXT,
                    embedding_model TEXT,
                    fetched_at TEXT NOT NULL
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_articles_module_time ON articles(module, fetched_at DESC)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_articles_time ON articles(fetched_at DESC)")
            conn.commit()

    def upsert_articles(self, articles: list[Article]) -> int:
        if not articles:
            return 0

        rows = [
            (
                article.module,
                article.source_id,
                article.source_name,
                article.title,
                article.url,
                article.snippet,
                json.dumps(article.embedding, ensure_ascii=False),
                article.embedding_model,
                article.fetched_at,
            )
            for article in articles
            if article.url
        ]
        if not rows:
            return 0

        with self._connect() as conn:
            conn.executemany(
                """
                INSERT INTO articles(module, source_id, source_name, title, url, snippet, embedding, embedding_model, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(url) DO UPDATE SET
                    module=excluded.module,
                    source_id=excluded.source_id,
                    source_name=excluded.source_name,
                    title=excluded.title,
                    snippet=excluded.snippet,
                    embedding=excluded.embedding,
                    embedding_model=excluded.embedding_model,
                    fetched_at=excluded.fetched_at
                """,
                rows,
            )
            conn.commit()
        return len(rows)

    def purge_old(self, max_age_hours: int = 24) -> int:
        threshold = datetime.now(CN_TZ) - timedelta(hours=max(1, int(max_age_hours)))
        deleted = 0
        with self._connect() as conn:
            cursor = conn.execute("SELECT id, fetched_at FROM articles")
            to_delete: list[int] = []
            for row_id, fetched_at in cursor.fetchall():
                try:
                    ts = datetime.fromisoformat(str(fetched_at))
                except ValueError:
                    to_delete.append(int(row_id))
                    continue
                if ts < threshold:
                    to_delete.append(int(row_id))

            if to_delete:
                conn.executemany("DELETE FROM articles WHERE id = ?", [(x,) for x in to_delete])
                conn.commit()
                deleted = len(to_delete)
        return deleted

    def list_recent(self, modules: list[str] | None = None, limit: int = 20) -> list[Article]:
        q_limit = max(1, int(limit))
        with self._connect() as conn:
            if modules:
                placeholders = ",".join("?" for _ in modules)
                cursor = conn.execute(
                    f"""
                    SELECT module, source_id, source_name, title, url, snippet, embedding, embedding_model, fetched_at
                    FROM articles
                    WHERE module IN ({placeholders})
                    ORDER BY fetched_at DESC, id DESC
                    LIMIT ?
                    """,
                    [*modules, q_limit],
                )
            else:
                cursor = conn.execute(
                    """
                    SELECT module, source_id, source_name, title, url, snippet, embedding, embedding_model, fetched_at
                    FROM articles
                    ORDER BY fetched_at DESC, id DESC
                    LIMIT ?
                    """,
                    (q_limit,),
                )
            rows = cursor.fetchall()

        articles: list[Article] = []
        for row in rows:
            try:
                emb_raw = json.loads(str(row[6] or "[]"))
            except Exception:
                emb_raw = []
            article = Article(
                module=str(row[0] or ""),
                source_id=str(row[1] or ""),
                source_name=str(row[2] or ""),
                title=str(row[3] or ""),
                url=str(row[4] or ""),
                snippet=str(row[5] or ""),
                embedding=[float(x) for x in emb_raw if isinstance(x, (int, float))],
                embedding_model=str(row[7] or ""),
                fetched_at=str(row[8] or ""),
            )
            articles.append(article)
        return articles

    def semantic_search(
        self,
        query: str,
        top_k: int = 5,
        min_similarity: float = 0.25,
        modules: list[str] | None = None,
    ) -> list[tuple[Article, float]]:
        payload = embed_text(query)
        q_vec = [float(x) for x in payload.get("vector", []) if isinstance(x, (int, float))]
        if not q_vec:
            return []

        candidates = self.list_recent(modules=modules, limit=500)
        hits: list[tuple[Article, float]] = []
        for article in candidates:
            if not article.embedding:
                continue
            sim = cosine_similarity(q_vec, article.embedding)
            if sim < float(min_similarity):
                continue
            hits.append((article, sim))

        hits.sort(key=lambda x: x[1], reverse=True)
        return hits[: max(1, int(top_k))]
