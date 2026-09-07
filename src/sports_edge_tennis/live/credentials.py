"""Safe credential loading for the Live MVP providers.

Keys are loaded from the normal process environment first, then from the local
Codex environment file (``~/.codex/.env``) if present. Values are never printed,
logged, or persisted by this module.
"""

from __future__ import annotations

import os
from pathlib import Path


class MissingCredentialError(RuntimeError):
    """Raised when a required provider credential is unavailable."""


def _codex_env_path() -> Path | None:
    path = Path(os.path.expanduser("~/.codex/.env"))
    return path if path.is_file() else None


def load_secret(name: str) -> str | None:
    value = os.environ.get(name)
    if value and value.strip():
        return value.strip()

    path = _codex_env_path()
    if path is not None:
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            if key.strip() != name:
                continue
            val = val.strip()
            if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
                val = val[1:-1]
            return val or None
    return None


def require_api_tennis_key() -> str:
    key = load_secret("API_TENNIS_API_KEY")
    if not key:
        raise MissingCredentialError("API_TENNIS_API_KEY is not configured")
    return key


def require_pinnapi_key() -> str:
    key = load_secret("PINNAPI_KEY")
    if not key:
        raise MissingCredentialError("PINNAPI_KEY is not configured")
    return key
