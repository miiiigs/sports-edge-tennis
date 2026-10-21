"""Read-only reconciliation of forward-issued Tennis Manager picks.

The package consumes the frozen JSON Lines dataset produced by the read-only
production extractor and never writes to the database, the Manager, or the
dashboard. It is analysis code only.
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "1.0.0"
