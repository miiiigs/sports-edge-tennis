from datetime import UTC, datetime

from sports_edge_tennis.validation.competitor_public_capture_match import (
    PublicCaptureMatchError,
    _match_one,
    match_capture,
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
    with pytest.raises(PublicCaptureMatchError,match="ambiguous_at_capture"):
        _match_one(FakeConn([row,row]),base_capture(),datetime(2026,11,19,6,18,43,tzinfo=UTC))


class TimeAwareConn(FakeConn):
    def __init__(self, row, capture_cutoff):
        self.row = row
        self.capture_cutoff = capture_cutoff

    def execute(self, _sql, params):
        rows = [] if params["captured_at"] <= self.capture_cutoff else [self.row]
        return self.Result(rows)


def test_post_capture_identity_link_preserves_frozen_prediction_timestamp():
    captured=datetime(2026,11,19,6,18,43,tzinfo=UTC)
    evaluated=datetime(2026,11,19,6,30,0,tzinfo=UTC)
    row=(
        9003, 11, 12, "Ben Shelton", "Rafael Jodar",
        datetime(2026,11,19,13,0,tzinfo=UTC), "ATP_MAIN", False, 77
    )
    out=_match_one(
        TimeAwareConn(row,captured),
        base_capture(),
        captured,
        evaluation_as_of=evaluated,
    )
    assert out["match_id"]=="9003"
    assert out["captured_at_utc"]=="2026-11-19T06:18:43Z"
    assert out["identity_match_mode"]=="POST_CAPTURE_IDENTITY_LINK"
    assert out["sports_edge_fixture_visible_at_competitor_capture"] is False



class AlwaysEmptyConn(FakeConn):
    def __init__(self):
        super().__init__([])


def test_zero_candidate_capture_is_retained_pending_and_never_scored():
    captured=datetime(2026,11,19,6,18,43,tzinfo=UTC)
    payload={
        "capture_version":"PUBLIC_WEB_CAPTURE_V1",
        "captured_at_utc":"2026-11-19T06:18:43Z",
        "rights":"authorized_public_export",
        "evidence":[base_capture()],
    }
    report=match_capture(
        AlwaysEmptyConn(),
        payload,
        evaluation_as_of=datetime(2026,11,19,6,33,27,tzinfo=UTC),
    )
    assert report["captured_competitor_count"]==1
    assert report["matched_competitor_count"]==0
    assert report["canonical_fixture_pending_count"]==1
    assert report["sports_edge_same_fixture_count"]==0
    assert report["ready_for_future_settlement_scoring"] is False
    pending=report["canonical_fixture_pending_forecasts"][0]
    assert pending["capture_status"]=="CANONICAL_FIXTURE_PENDING"
    assert pending["published_at_utc"]=="2026-11-19T06:18:43Z"
    assert pending["scoring_eligible"] is False


def test_unmatched_capture_after_scheduled_start_fails_closed():
    import pytest
    payload={
        "capture_version":"PUBLIC_WEB_CAPTURE_V1",
        "captured_at_utc":"2026-11-19T14:00:00Z",
        "rights":"authorized_public_export",
        "evidence":[base_capture()],
    }
    with pytest.raises(PublicCaptureMatchError,match="not_pre_match"):
        match_capture(AlwaysEmptyConn(),payload)
