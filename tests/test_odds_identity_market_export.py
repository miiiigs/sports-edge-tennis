"""Regression checks for stored evidence and the read-only export boundary."""
import json
from pathlib import Path

from sports_edge_tennis.validation.odds_identity_market_export import digest, raw_event, run


ROOT = Path(__file__).resolve().parents[1]


def test_real_archived_cohort_overlap():
    future = json.loads((ROOT / "docs/research/evidence/oddspapi_future_quota_dryrun_20261123.json").read_text())["result"]
    history = json.loads((ROOT / "docs/research/forward_captures/original_preissue_odds_gap_20261122.json").read_text())
    cold = sorted(row["match_id"] for row in future["missing_or_invalid_cache_sample"])
    assert len(cold) == len(set(cold)) == 69
    assert future["counts"]["internal_eligible_main_singles"] == 71
    assert future["verified_warm_fixture_count"] == 2
    overlap = sorted([r["match_id"], r["oddspapi_id_count"], r["odds_quote_count"], r["category"]]
                     for r in history["unmatched_sample_rows"] if r["match_id"] in cold)
    assert len(overlap) == 13
    assert [r[0] for r in overlap if r[1]] == [134190, 134191]
    assert all(r[2] == 0 for r in overlap)
    assert digest(cold) == "b93694b54e1cb0b2dc89ad305e1d60917df8d839b1062d83c2b0eb361f715135"
    assert digest(overlap) == "da4d992b78a2ce23abccf7036221a533cea2073b4207f9f22e4cffe0080326ff"


def test_raw_fixture_requires_actual_matching_provider_identity():
    from datetime import datetime, timezone
    now = datetime(2026, 11, 23, 12, tzinfo=timezone.utc)
    row = (1, "event1", now, now, now, "RECEIVE_TIME", "hash",
           "different_event", "1", "2026-11-23T14:00:00Z", "a", "b", "123", "12")
    assert raw_event(None, row, now) is None


def test_read_only_guard_precedes_any_data_read():
    from contextlib import nullcontext
    from datetime import datetime, timezone
    import pytest

    class Connection:
        def __init__(self):
            self.commands = []
        def transaction(self):
            return nullcontext()
        def execute(self, sql):
            self.commands.append(sql)
            return self
        def fetchone(self):
            return ("off",)
    conn = Connection()
    with pytest.raises(RuntimeError, match="read-only guard failed"):
        run(conn, as_of=datetime(2026, 11, 23, 12, tzinfo=timezone.utc), mode="future")
    assert conn.commands == ["SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY",
                             "SET LOCAL statement_timeout='60s'", "SHOW transaction_read_only"]
