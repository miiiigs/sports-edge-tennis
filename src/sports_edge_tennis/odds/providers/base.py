from __future__ import annotations

from typing import Protocol

from sports_edge_tennis.odds.contracts import ProviderObservation


class LiveOddsProvider(Protocol):
    """Boundary for future concrete adapters; no transport is implemented in V1 core."""

    provider_code: str
    adapter_version: str

    def observations(self) -> tuple[ProviderObservation, ...]:
        """Return already-fetched provider-neutral observations."""

