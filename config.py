from pathlib import Path


_ENV_CACHE: dict[str, str] | None = None


def _parse_env_file() -> dict[str, str]:
    env_path = Path(__file__).resolve().parent / ".env"
    if not env_path.exists():
        return {}

    values: dict[str, str] = {}
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")

    return values


def get_env(key: str, default: str = "") -> str:
    global _ENV_CACHE
    if _ENV_CACHE is None:
        _ENV_CACHE = _parse_env_file()
    return _ENV_CACHE.get(key, default)


def get_required_env(key: str) -> str:
    value = get_env(key).strip()
    if not value:
        raise RuntimeError(f"请先在 .env 文件中设置 {key}")
    return value
