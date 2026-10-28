"""Agent agreement must use original selection identity."""
from datetime import date
from research.tennis_aligned_oct_replay_20261028.replay import Pick, RULES, parse_ledger


def test_agreement_rejects_opposite_side_and_missing_agent_values():
    base = dict(
        match_id=1, day=date(2026, 10, 24), probability=0.78,
        surface="HARD", pick_type="MANAGER_CONSENSUS",
        outcome="LOST", picked_at="2026-10-24T00:00:00Z",
        selected_side=1, a1_side=1, a2_side=1,
        a1_prob=0.78, a2_prob=0.80,
    )
    narrow = next(x for x in RULES if x.key == "a1_a2_agreement_lt_5pct")
    combined = next(x for x in RULES if x.key == "aligned_prob70_a1_a2_lt10pct")
    assert narrow.permits(Pick(**base))
    assert combined.permits(Pick(**base))
    assert not narrow.permits(Pick(**dict(base, a2_prob=0.85)))
    assert not narrow.permits(Pick(**dict(base, a2_side=2)))
    assert not narrow.permits(Pick(**dict(base, a2_prob=None)))
    assert not combined.permits(Pick(**dict(base, probability=0.65)))


def test_agent_agreement_uses_canonical_original_fields():
    row = {
        "match_id": 1, "manila_date": "2026-10-24",
        "picked_at": "2026-10-23T23:00:00+00:00",
        "aligned_pick_type": "A1_A2_WIDE_SPREAD",
        "aligned_probability": 0.81, "surface": "HARD",
        "result_status": "WON", "selected_side": 2,
        "a1_side": 2, "a2_side": 2, "a1_prob": 0.85,
        "a2_prob": 0.84,
    }
    narrow = next(x for x in RULES if x.key == "a1_a2_agreement_lt_5pct")
    assert narrow.permits(parse_ledger({"data": [row]})[0])
    row["a1_prob"] = None
    assert not narrow.permits(parse_ledger({"data": [row]})[0])
