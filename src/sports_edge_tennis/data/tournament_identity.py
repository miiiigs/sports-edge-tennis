"""Deterministic canonical tournament alias resolution.

Provider event names do not always match the canonical tournament name. For
example API-Tennis labels the Shanghai Masters simply ``"Shanghai"``, which
must not create a second canonical tournament alongside the existing
``"Shanghai Masters"`` (``MASTERS_1000``).

This module carries a small, reviewed, evidence-proven alias table. It is
deliberately NOT a fuzzy matcher: only exact provider-name entries are mapped,
keyed by ``(source_system, tour_scope)``. Any ambiguity at the caller (multiple
canonical candidates) fails closed.

No model, Manager, Market, Veto, staking, selection, or settlement behavior is
defined here.
"""

from __future__ import annotations

#: Reviewed, production-proven provider-name -> canonical-name aliases.
#: Keyed by (source_system, tour_scope). Every target name must already exist
#: as a canonical tournament row; the caller only reuses when exactly one
#: candidate matches, otherwise it fails closed.
CANONICAL_TOURNAMENT_ALIASES: dict[tuple[str, str], dict[str, str]] = {
    ("API_TENNIS", "ATP_MAIN"): {
        "SHANGHAI": "Shanghai Masters",
        "CINCINNATI": "Cincinnati Masters",
        "MADRID": "Madrid Masters",
        "MIAMI": "Miami Masters",
        "MONTE CARLO": "Monte Carlo Masters",
        "ROME": "Rome Masters",
    },
}


def normalize_tournament_name(value: str | None) -> str:
    """Upper-case, whitespace-collapsed comparison key for provider names."""
    if not value:
        return ""
    return " ".join(str(value).strip().upper().split())


def resolve_canonical_tournament_name(
    source_system: str,
    canonical_name: str,
    tour_scope: str,
) -> str:
    """Return the reviewed canonical name for a provider name, or the input.

    This is a pure lookup: it never invents a name, never does fuzzy matching,
    and never maps across tour scopes.
    """
    table = CANONICAL_TOURNAMENT_ALIASES.get((source_system, tour_scope))
    if not table:
        return canonical_name
    return table.get(normalize_tournament_name(canonical_name), canonical_name)


__all__ = [
    "CANONICAL_TOURNAMENT_ALIASES",
    "normalize_tournament_name",
    "resolve_canonical_tournament_name",
]
