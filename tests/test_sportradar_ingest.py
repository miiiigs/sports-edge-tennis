from __future__ import annotations

from contextlib import nullcontext
from datetime import UTC, datetime
from types import SimpleNamespace

import sports_edge_tennis.live.sportradar_ingest as ingest
from sports_edge_tennis.data.repository import MatchLocator
from sports_edge_tennis.live.sportradar_fixtures import (
    SportradarFixtureCandidate,
    SportradarFixtureParseOutcome,
)
from sports_edge_tennis.live.sportradar_reconciliation import (
    ExistingPlayerResolution,
)


RECEIVED = datetime(2026, 9, 19, 12, 0, tzinfo=UTC)


class _Conn:
    def transaction(self):
        return nullcontext()


def _fixture(event_id="sr:event:1"):
    return SportradarFixtureCandidate(
        source_match_id=event_id,
        competition_source_id="sr:competition:1",
        competition_name="ATP Challenger Rennes Men Singles",
        season_source_id="sr:season:1",
        player_1_source_id="sr:p1",
        player_1_name="Blanch, Darwin",
        player_2_source_id="sr:p2",
        player_2_name="Nakashima, Brandon",
        match_start_at=datetime(2026, 9, 19, 13, 30, tzinfo=UTC),
        match_status="SCHEDULED",
        tour_scope="CHALLENGER",
        competition_tier="CHALLENGER",
        surface="HARD",
        round_code="round_of_32",
        best_of_sets=3,
        qualifying_flag=False,
        received_at=RECEIVED,
    )


def _parsed(*fixtures, rejected=()):
    return SportradarFixtureParseOutcome(
        rows_seen=len(fixtures) + sum(count for _, count in rejected),
        rows_accepted=len(fixtures),
        rows_rejected=sum(count for _, count in rejected),
        fixtures=tuple(fixtures),
        rejection_reasons=tuple(rejected),
    )


def _new_overlap():
    return SimpleNamespace(
        status="NEW_CANONICAL_EVENT",
        match_id=None,
        player_1=ExistingPlayerResolution("NEW_PLAYER", None),
        player_2=ExistingPlayerResolution("NEW_PLAYER", None),
    )


def _exact_overlap():
    return SimpleNamespace(
        status="EXACT_EXISTING_MATCH",
        match_id=10,
        player_1=ExistingPlayerResolution("SOURCE_ID_RESOLVED", 1, (1,)),
        player_2=ExistingPlayerResolution("SOURCE_ID_RESOLVED", 2, (2,)),
    )


def _patch_common(monkeypatch, parsed):
    monkeypatch.setattr(ingest, "parse_daily_summaries", lambda *a, **k: parsed)
    monkeypatch.setattr(ingest, "_start_ingest_run", lambda *a, **k: 99)
    monkeypatch.setattr(ingest, "HistoricalRepository", lambda *a, **k: object())
    finished = []
    monkeypatch.setattr(
        ingest,
        "_finish_ingest_run",
        lambda *a, **k: finished.append(k),
    )
    return finished


def test_overlap_mode_defers_new_events_without_any_persistence(monkeypatch) -> None:
    finished = _patch_common(monkeypatch, _parsed(_fixture()))
    monkeypatch.setattr(
        ingest,
        "resolve_sportradar_event_overlap",
        lambda *a, **k: _new_overlap(),
    )
    persisted = []
    monkeypatch.setattr(
        ingest,
        "_persist_fixture",
        lambda *a, **k: persisted.append(k),
    )

    result = ingest.ingest_sportradar_daily_summaries(
        _Conn(),
        {"summaries": []},
        received_at=RECEIVED,
        allow_new_events=False,
    )

    assert result.rows_ingested == 0
    assert result.rows_deferred_new == 1
    assert result.rows_rejected == 1
    assert result.status == "FAILED"
    assert persisted == []
    assert finished[0]["rows_inserted"] == 0


def test_exact_overlap_persists_one_match(monkeypatch) -> None:
    finished = _patch_common(monkeypatch, _parsed(_fixture()))
    monkeypatch.setattr(
        ingest,
        "resolve_sportradar_event_overlap",
        lambda *a, **k: _exact_overlap(),
    )
    monkeypatch.setattr(
        ingest,
        "_persist_fixture",
        lambda *a, **k: MatchLocator(
            match_id=10,
            match_version_id=20,
            player_1_id=1,
            player_2_id=2,
        ),
    )

    result = ingest.ingest_sportradar_daily_summaries(
        _Conn(),
        {"summaries": []},
        received_at=RECEIVED,
    )

    assert result.rows_seen == 1
    assert result.rows_ingested == 1
    assert result.rows_rejected == 0
    assert result.rows_deferred_new == 0
    assert result.status == "COMPLETED"
    assert result.matches[0].match_id == 10
    assert result.matches[0].overlap_status == "EXACT_EXISTING_MATCH"
    assert finished[0]["rows_inserted"] == 1


def test_ambiguous_overlap_is_rejected_without_persisting(monkeypatch) -> None:
    finished = _patch_common(monkeypatch, _parsed(_fixture()))
    monkeypatch.setattr(
        ingest,
        "resolve_sportradar_event_overlap",
        lambda *a, **k: SimpleNamespace(
            status="AMBIGUOUS_EXISTING_MATCH",
            match_id=None,
            player_1=ExistingPlayerResolution("SOURCE_ID_RESOLVED", 1, (1,)),
            player_2=ExistingPlayerResolution("SOURCE_ID_RESOLVED", 2, (2,)),
        ),
    )
    persisted = []
    monkeypatch.setattr(
        ingest,
        "_persist_fixture",
        lambda *a, **k: persisted.append(k),
    )

    result = ingest.ingest_sportradar_daily_summaries(
        _Conn(),
        {"summaries": []},
        received_at=RECEIVED,
    )

    assert result.rows_ingested == 0
    assert result.rows_rejected == 1
    assert "AMBIGUOUS_EXISTING_MATCH" in str(result.rejected_rows)
    assert persisted == []
    assert finished[0]["rows_rejected"] == 1


def test_parse_rejections_remain_accounted_for_in_dry_run(monkeypatch) -> None:
    parsed = _parsed(_fixture(), rejected=(("out_of_scope_event_type", 3),))
    monkeypatch.setattr(ingest, "parse_daily_summaries", lambda *a, **k: parsed)

    result = ingest.ingest_sportradar_daily_summaries(
        _Conn(),
        {"summaries": []},
        received_at=RECEIVED,
        dry_run=True,
    )

    assert result.rows_seen == 4
    assert result.rows_ingested == 0
    assert result.rows_rejected == 3
    assert result.status == "FAILED"
