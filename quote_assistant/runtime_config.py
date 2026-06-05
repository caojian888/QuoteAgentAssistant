from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SYSTEM_ENV_FILE = Path("/etc/quote-agent-assistant/.env")
SYSTEM_DATA_DIR = Path("/var/lib/quote-agent-assistant/data")

_loaded_env_file: Path | None = None
_env_loaded = False


def _clean_env_value(value: str | None) -> str:
    return (value or "").strip()


def _absolute_path(path: str | Path, *, base: Path = PROJECT_ROOT) -> Path:
    resolved = Path(path).expanduser()
    if not resolved.is_absolute():
        resolved = base / resolved
    return resolved.resolve()


def load_runtime_env() -> Path | None:
    """Load the runtime .env once, using production paths before local fallback."""
    global _env_loaded, _loaded_env_file
    if _env_loaded:
        return _loaded_env_file

    _env_loaded = True
    explicit = _clean_env_value(os.getenv("QUOTE_ENV_FILE"))
    if explicit:
        candidate = _absolute_path(explicit)
        if candidate.exists():
            load_dotenv(candidate, override=False)
            _loaded_env_file = candidate
        return _loaded_env_file

    for candidate in (SYSTEM_ENV_FILE, PROJECT_ROOT / ".env"):
        candidate = candidate.resolve()
        if candidate.exists():
            load_dotenv(candidate, override=False)
            _loaded_env_file = candidate
            return _loaded_env_file

    return None


def runtime_path(
    value: str | Path | None,
    *,
    env_name: str | None = None,
    default: str | Path,
    base: Path = PROJECT_ROOT,
) -> Path:
    load_runtime_env()
    raw_value: str | Path | None = value
    if raw_value is None and env_name:
        raw_value = _clean_env_value(os.getenv(env_name))
    if raw_value is None or str(raw_value).strip() == "":
        raw_value = default
    return _absolute_path(raw_value, base=base)


def runtime_data_dir() -> Path:
    load_runtime_env()
    default = SYSTEM_DATA_DIR if _loaded_env_file == SYSTEM_ENV_FILE.resolve() else PROJECT_ROOT / "data"
    return runtime_path(None, env_name="QUOTE_DATA_DIR", default=default)
