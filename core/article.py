from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any

CN_TZ = timezone(timedelta(hours=8))


@dataclass(slots=True)
class Article:
    module: str
    source_id: str
    source_name: str
    title: str
    url: str
    snippet: str = ""
    fetched_at: str = field(default_factory=lambda: datetime.now(CN_TZ).isoformat())
    embedding: list[float] = field(default_factory=list)
    embedding_model: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "module": self.module,
            "source_id": self.source_id,
            "source_name": self.source_name,
            "title": self.title,
            "url": self.url,
            "snippet": self.snippet,
            "fetched_at": self.fetched_at,
            "embedding": list(self.embedding),
            "embedding_model": self.embedding_model,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Article":
        embedding = data.get("embedding") if isinstance(data.get("embedding"), list) else []
        metadata = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
        return cls(
            module=str(data.get("module") or ""),
            source_id=str(data.get("source_id") or ""),
            source_name=str(data.get("source_name") or ""),
            title=str(data.get("title") or ""),
            url=str(data.get("url") or ""),
            snippet=str(data.get("snippet") or ""),
            fetched_at=str(data.get("fetched_at") or datetime.now(CN_TZ).isoformat()),
            embedding=[float(x) for x in embedding if isinstance(x, (int, float))],
            embedding_model=str(data.get("embedding_model") or ""),
            metadata={str(k): v for k, v in metadata.items()},
        )
