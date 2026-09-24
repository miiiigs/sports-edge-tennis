from __future__ import annotations

"""Explicit validation mode for the production live pipeline.

``TENNIS_VALIDATION_MODE=1`` keeps the exact live provider/model configuration
but suppresses irreversible or user-facing side effects: pick/bet publication,
bet creation, notifications, and settlement mutation. It wraps the existing
pipeline rather than introducing a second runner.
"""

import os
from collections.abc import Mapping

VALIDATION_MODE_ENV = "TENNIS_VALIDATION_MODE"
_TRUTHY = frozenset({"1", "true", "yes", "on"})


def validation_mode_enabled(env: Mapping[str, str] | None = None) -> bool:
    values = os.environ if env is None else env
    return str(values.get(VALIDATION_MODE_ENV) or "").strip().casefold() in _TRUTHY


__all__ = ["VALIDATION_MODE_ENV", "validation_mode_enabled"]
