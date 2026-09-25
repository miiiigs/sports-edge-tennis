from __future__ import annotations

from contextlib import nullcontext
from dataclasses import replace
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
        "_latest_terminal_outcome",
        lambda *a, **k: None,
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


def test_existing_terminal_truth_blocks_conflicting_provider_status(monkeypatch) -> None:
    fixture = replace(_fixture(), match_status="COMPLETED")
    finished = _patch_common(monkeypatch, _parsed(fixture))
    monkeypatch.setattr(
        ingest,
        "resolve_sportradar_event_overlap",
        lambda *a, **k: _exact_overlap(),
    )
    monkeypatch.setattr(
        ingest,
        "_latest_terminal_outcome",
        lambda *a, **k: "RETIRED",
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
    assert "terminal_truth_conflict:RETIRED:COMPLETED" in str(result.rejected_rows)
    assert persisted == []
    assert finished[0]["rows_inserted"] == 0


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


def _promote(**kwargs):
    return ingest._oriented_participants_after_provider_resolution(
        overlap_status="SOURCE_ID_RESOLVED", **kwargs
    )


def test_both_placeholder_slots_promote_to_provider_orientation() -> None:
    assert _promote(
        canonical_p1=10,
        canonical_p1_name="Qf1",
        canonical_p2=20,
        canonical_p2_name="Qf2",
        incoming_p1=101,
        incoming_p1_name="Real One",
        incoming_p2=202,
        incoming_p2_name="Real Two",
    ) == (101, 202)


def test_retained_real_player_keeps_its_canonical_side_when_side_two() -> None:
    assert _promote(
        canonical_p1=10,
        canonical_p1_name="Qf1",
        canonical_p2=20,
        canonical_p2_name="Real Two",
        incoming_p1=303,
        incoming_p1_name="Real New",
        incoming_p2=20,
        incoming_p2_name="Real Two",
    ) == (303, 20)


def test_retained_real_player_keeps_its_canonical_side_when_side_one() -> None:
    assert _promote(
        canonical_p1=10,
        canonical_p1_name="Real One",
        canonical_p2=20,
        canonical_p2_name="Qf2",
        incoming_p1=10,
        incoming_p1_name="Real One",
        incoming_p2=404,
        incoming_p2_name="Real New",
    ) == (10, 404)


def test_retained_placeholder_side_is_preserved() -> None:
    assert _promote(
        canonical_p1=10,
        canonical_p1_name="Qf1",
        canonical_p2=20,
        canonical_p2_name="Qf2",
        incoming_p1=10,
        incoming_p1_name="Qf1",
        incoming_p2=505,
        incoming_p2_name="Real New",
    ) == (10, 505)


def test_real_participant_substitution_fails_closed() -> None:
    assert _promote(
        canonical_p1=10,
        canonical_p1_name="Real One",
        canonical_p2=20,
        canonical_p2_name="Real Two",
        incoming_p1=10,
        incoming_p1_name="Real One",
        incoming_p2=606,
        incoming_p2_name="Real Three",
    ) is None


def test_incoming_placeholder_does_not_promote() -> None:
    assert _promote(
        canonical_p1=10,
        canonical_p1_name="Qf1",
        canonical_p2=20,
        canonical_p2_name="Qf2",
        incoming_p1=707,
        incoming_p1_name="Real New",
        incoming_p2=808,
        incoming_p2_name="Qf9",
    ) is None


def test_no_promotion_without_stable_provider_event_identity() -> None:
    assert (
        ingest._oriented_participants_after_provider_resolution(
            overlap_status="EXACT_EXISTING_MATCH",
            canonical_p1=10,
            canonical_p1_name="Qf1",
            canonical_p2=20,
            canonical_p2_name="Qf2",
            incoming_p1=707,
            incoming_p1_name="Real New",
            incoming_p2=808,
            incoming_p2_name="Real Two",
        )
        is None
    )


def test_identical_participant_pair_is_not_a_promotion() -> None:
    assert _promote(
        canonical_p1=10,
        canonical_p1_name="Qf1",
        canonical_p2=20,
        canonical_p2_name="Qf2",
        incoming_p1=10,
        incoming_p1_name="Qf1",
        incoming_p2=20,
        incoming_p2_name="Qf2",
    ) is None
