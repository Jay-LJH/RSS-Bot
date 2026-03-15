from __future__ import annotations

from pathlib import Path

from .env_parser import parse_env_file

_ENV_CACHE: dict[str, str] | None = None


def get_env_cache() -> dict[str, str]:
    global _ENV_CACHE
    if _ENV_CACHE is None:
        env_path = Path(__file__).resolve().parent.parent / ".env"
        _ENV_CACHE = parse_env_file(env_path)
    return _ENV_CACHE
