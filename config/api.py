from __future__ import annotations

from .store import get_env_cache


def get_env(key: str, default: str = "") -> str:
    return get_env_cache().get(key, default)


def get_required_env(key: str) -> str:
    value = get_env(key).strip()
    if not value:
        raise RuntimeError(f"请先在 .env 文件中设置 {key}")
    return value
