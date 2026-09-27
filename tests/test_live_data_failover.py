"""Failover classification tests (Sportradar TransportError kinds)."""

from __future__ import annotations

from sports_edge_tennis.live.orchestrator import (
    sportradar_error_is_failover_eligible as eligible,
)


def test_eligible_kinds_allow_fallback():
    for kind in (
        "TIMEOUT",
        "NETWORK",
        "HTTP_401",
        "HTTP_403",
        "HTTP_429",
        "HTTP_500",
        "HTTP_502",
        "HTTP_503",
        "HTTP_504",
        "PROVIDER_ERROR",
    ):
        assert eligible(kind) is True, kind


def test_ineligible_kinds_fail_closed():
    for kind in ("PARSE", "HTTP_400", "HTTP_404", "HTTP_418", "WEIRD", "", "ValueError"):
        assert eligible(kind) is False, kind
