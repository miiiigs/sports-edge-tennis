from datetime import UTC, datetime

from sports_edge_tennis.validation.competitor_public_capture_match import (
    PublicCaptureMatchError,
    _match_one,
)


class FakeConn:
    def __init__(self, rows):
        self.rows = rows

    class Result:
        def __init__(self, rows):
            self.rows = rows
        def fetchall(self):
            return self.rows

    def execute(self, *args, **kwargs):
        return self.Result(self.rows)


def base_capture(**overrides):
    row = {
        "source_url": "https://example.test/prediction",
        "player_1_name": "Ben Shelton",
        "player_2_name": "Rafael Jodar",
        "scheduled_start_utc": "2026-11-19T13:00:00Z",
        "picked_name": "Ben Shelton",
        "picked_probability": 0.64,
        "model_version": "public-v1",
    }
    row.update(overrides)
    return row


def test_public_capture_matches_point_in_time_fixture_and_orients_probability():
    rows=[(
        9001, 11, 12, "Ben Shelton", "Rafael Jodar",
        datetime(2026,11,19,13,0,tzinfo=UTC), "ATP_MAIN", False, 77
    )]
    out=_match_one(FakeConn(rows),base_capture(),datetime(2026,11,19,6,18,43,tzinfo=UTC))
    assert out["match_id"]=="9001"
    assert out["p_player1"]=="0.640000"
    assert out["rights"]=="authorized_public_export"


def test_reversed_canonical_orientation_complements_probability():
    rows=[(
        9002, 12, 11, "Rafael Jodar", "Ben Shelton",
        datetime(2026,11,19,13,0,tzinfo=UTC), "ATP_MAIN", False, 77
    )]
    out=_match_one(FakeConn(rows),base_capture(),datetime(2026,11,19,6,18,43,tzinfo=UTC))
    assert out["p_player1"]=="0.360000"
    assert out["orientation_from_capture"]==-1


def test_ambiguous_identity_match_fails_closed():
    import pytest
    row=(
        9001, 11, 12, "Ben Shelton", "Rafael Jodar",
        datetime(2026,11,19,13,0,tzinfo=UTC), "ATP_MAIN", False, 77
    )
    with pytest.raises(PublicCaptureMatchError,match="not_unique"):
        _match_one(FakeConn([row,row]),base_capture(),datetime(2026,11,19,6,18,43,tzinfo=UTC))
