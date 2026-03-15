from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from core.article import Article


class CacheStore:
    def __init__(self, cache_path: Path) -> None:
        self._cache_path = cache_path

    def save_modules(self, module_articles: dict[str, list[Article]], updated_at: str) -> None:
        payload: dict[str, Any] = {"updated_at": updated_at, "modules": {}}
        for module, articles in module_articles.items():
            payload["modules"][module] = [article.to_dict() for article in articles]
        self._cache_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def load_modules(self) -> dict[str, list[Article]]:
        if not self._cache_path.exists():
            return {}
        try:
            data = json.loads(self._cache_path.read_text(encoding="utf-8"))
        except Exception:
            return {}

        modules = data.get("modules") if isinstance(data, dict) else {}
        if not isinstance(modules, dict):
            return {}

        result: dict[str, list[Article]] = {}
        for module, items in modules.items():
            if not isinstance(items, list):
                continue
            result[str(module)] = [Article.from_dict(x) for x in items if isinstance(x, dict)]
        return result
