from sports_edge_tennis.validation.defaulted_result_repair import (
    CanonicalIdentity,
    DefaultedRepairError,
    verify_fixture,
)


IDENTITY = CanonicalIdentity(
    match_id=131712,
    event_key="12168226",
    player_1_source_id="p1",
    player_2_source_id="p2",
    tournament_source_id="t1",
)


def fixture(**overrides):
    row = {
        "event_key": "12168226",
        "first_player_key": "p1",
        "second_player_key": "p2",
        "tournament_key": "t1",
        "event_status": "Awarded",
        "event_winner": "First Player",
        "event_final_result": "-",
    }
    row.update(overrides)
    return row


def test_exact_awarded_identity_produces_deterministic_defaulted_proof():
    a = verify_fixture(IDENTITY, fixture())
    b = verify_fixture(IDENTITY, fixture())
    assert a["terminal"].outcome_status == "DEFAULTED"
    assert a["terminal"].winner_side == 1
    assert a["proof_sha256"] == b["proof_sha256"]
    assert len(a["proof_sha256"]) == 64


def test_identity_or_nondefault_status_fails_closed():
    import pytest
    with pytest.raises(DefaultedRepairError, match="player_1"):
        verify_fixture(IDENTITY, fixture(first_player_key="wrong"))
    with pytest.raises(DefaultedRepairError, match="not_verified_defaulted"):
        verify_fixture(IDENTITY, fixture(event_status="Finished"))
