"""Scope tests for the intentionally narrow tournament identity normalizer.

``normalize_tournament_identity`` strips at most one trailing `` CH`` token, which
the historical corpus appends for Challenger editions. It must never alter a name
where ``CH`` is part of the real tournament name, and it must not strip other
provider/edition labels.
"""

from __future__ import annotations

import pytest

from sports_edge_tennis.data.normalize import normalize_tournament_identity


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Columbus CH", "COLUMBUS"),
        ("Mouilleron-Le-Captif CH", "MOUILLERON LE CAPTIF"),
        ("Mouilleron Le Captif CH", "MOUILLERON LE CAPTIF"),
        ("columbus ch", "COLUMBUS"),
        ("Columbus", "COLUMBUS"),
        ("  Columbus   CH  ", "COLUMBUS"),
        # "CH" at the beginning is real name text and must remain intact.
        ("CH London", "CH LONDON"),
        ("Challenger Trophy", "CHALLENGER TROPHY"),
        # Internal "CH" (including a word ending in CH) must remain intact.
        ("Munich", "MUNICH"),
        ("Chennai Open", "CHENNAI OPEN"),
        ("Cheshire Open", "CHESHIRE OPEN"),
        # Unrelated suffixes must not be removed.
        ("Columbus 2", "COLUMBUS 2"),
        ("Columbus ATP", "COLUMBUS ATP"),
        ("Oeiras 2 CH", "OEIRAS 2"),
        # ATP/WTA/ITF labels are not stripped unless explicitly supported.
        ("ATP Challenger", "ATP CHALLENGER"),
        ("WTA 125", "WTA 125"),
        ("ITF World Tennis Tour", "ITF WORLD TENNIS TOUR"),
        # Only one trailing token is removed.
        ("X CH CH", "X CH"),
        # A bare "CH" is preserved.
        ("CH", "CH"),
        ("", ""),
    ],
)
def test_normalize_tournament_identity_scope(raw: str, expected: str) -> None:
    assert normalize_tournament_identity(raw) == expected


def test_live_and_corpus_names_collapse_to_the_same_identity() -> None:
    assert normalize_tournament_identity("Columbus CH") == normalize_tournament_identity(
        "Columbus"
    )
    assert normalize_tournament_identity(
        "Mouilleron-Le-Captif CH"
    ) == normalize_tournament_identity("Mouilleron-Le-Captif")


def test_only_the_single_documented_suffix_token_is_removed() -> None:
    # "X CH CH" loses exactly one documented trailing token.
    assert normalize_tournament_identity("X CH CH") == "X CH"
