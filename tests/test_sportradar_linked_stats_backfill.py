from __future__ import annotations

import inspect
from datetime import UTC, date, datetime, timedelta

import pytest

import sports_edge_tennis.validation.sportradar_linked_stats_backfill as backfill
from sports_edge_tennis.data.contracts import MatchStatLine
from sports_edge_tennis.live.sportradar_fixtures import SportradarFixtureCandidate
from sports_edge_tennis.live.sportradar_history_refresh import ParsedResult, ParsedStat
from sports_edge_tennis.live.transport import TransportError
from sports_edge_tennis.validation.sportradar_linked_stats_backfill import (
    AUTH_ENV,
    AUTH_TOKEN,
    ENABLED_ENV,
    HASH_ENV,
    BackfillCandidate,
    _artifact_hash,
    _result_detail_conflict,
    _same_candidate,
    _validate_window,
    apply_linked_stats_backfill,
)


RECEIVED = datetime(2026, 9, 23, 1, 0, tzinfo=UTC)

def _fixture() -> SportradarFixtureCandidate:
    return SportradarFixtureCandidate(
        source_match_id="sr:sport_event:1",
        competition_source_id="sr:competition:1",
        competition_name="ATP Challenger Test Men Singles",
        season_source_id="sr:season:1",
        player_1_source_id="sr:p1",
        player_1_name="Player One",
        player_2_source_id="sr:p2",
        player_2_name="Player Two",
        match_start_at=datetime(2026, 9, 21, 10, 0, tzinfo=UTC),
        match_status="COMPLETED",
        tour_scope="CHALLENGER",
        competition_tier="CHALLENGER",
        surface="HARD",
        round_code="R32",
        best_of_sets=3,
        qualifying_flag=False,
        received_at=RECEIVED,
    )


def _result() -> ParsedResult:
    return ParsedResult(
        outcome_status="COMPLETED",
        winner_side=1,
        retired_side=None,
        sets_won_player_1=2,
        sets_won_player_2=0,
        games_won_player_1=12,
        games_won_player_2=7,
        scoreline_text="2-0",
    )


def _stat(provider_id: str, digest: str) -> ParsedStat:
    return ParsedStat(
        provider_player_id=provider_id,
        line=MatchStatLine(
            first_serves_in=30,
            first_serve_points_won=20,
            second_serve_points_won=10,
            service_points_played=50,
            return_points_won=18,
            aces=5,
            double_faults=2,
            break_points_saved=3,
            break_points_faced=4,
        ),
        record_hash=digest,
    )


def _candidate(
    *,
    received_at: datetime = RECEIVED,
    target_date: date = date(2026, 9, 21),
) -> BackfillCandidate:
    fixture = _fixture()
    fixture = SportradarFixtureCandidate(
        **{
            **fixture.__dict__,
            "received_at": received_at,
        }
    )
    return BackfillCandidate(
        target_date=target_date,
        received_at=received_at,
        fixture=fixture,
        match_id=101,
        player_1_id=11,
        player_2_id=22,
        result=_result(),
        stats=(_stat("sr:p1", "hash-a"), _stat("sr:p2", "hash-b")),
        player_map={"sr:p1": 11, "sr:p2": 22},
    )


def test_backfill_window_is_strictly_bounded() -> None:
    _validate_window(date(2026, 9, 1), date(2026, 9, 14))
    with pytest.raises(ValueError, match="1..14"):
        _validate_window(date(2026, 9, 1), date(2026, 9, 15))
    with pytest.raises(ValueError, match="on or after"):
        _validate_window(date(2026, 9, 2), date(2026, 9, 1))


def test_artifact_hash_ignores_fetch_time_but_keeps_semantic_evidence() -> None:
    first = _candidate(received_at=RECEIVED)
    second = _candidate(received_at=RECEIVED + timedelta(hours=1))
    assert _artifact_hash((first,)) == _artifact_hash((second,))
    assert _same_candidate(first, second) is True

    changed = BackfillCandidate(
        **{
            **second.__dict__,
            "stats": (_stat("sr:p1", "hash-a"), _stat("sr:p2", "hash-c")),
        }
    )
    assert _artifact_hash((first,)) != _artifact_hash((changed,))
    assert _same_candidate(first, changed) is False


def test_terminal_score_conflict_fails_closed() -> None:
    current = ("COMPLETED", 1, None, 2, 0, 12, 7)
    assert _result_detail_conflict(current, _result()) is None

    conflicting = ParsedResult(
        outcome_status="COMPLETED",
        winner_side=1,
        retired_side=None,
        sets_won_player_1=2,
        sets_won_player_2=0,
        games_won_player_1=12,
        games_won_player_2=8,
        scoreline_text="2-0",
    )
    assert (
        _result_detail_conflict(current, conflicting)
        == "terminal_score_conflict:games_player_2:7:8"
    )


def test_apply_is_blocked_before_any_database_access_without_enable_gate() -> None:
    with pytest.raises(RuntimeError, match=ENABLED_ENV):
        apply_linked_stats_backfill(
            dsn="postgresql://not-used",
            api_key="not-used",
            start_date=date(2026, 9, 21),
            end_date=date(2026, 9, 21),
            env={},
        )


def test_apply_requires_exact_authorization_and_reviewed_hash() -> None:
    with pytest.raises(RuntimeError, match=AUTH_ENV):
        apply_linked_stats_backfill(
            dsn="postgresql://not-used",
            api_key="not-used",
            start_date=date(2026, 9, 21),
            end_date=date(2026, 9, 21),
            env={ENABLED_ENV: "true"},
        )

    with pytest.raises(RuntimeError, match=HASH_ENV):
        apply_linked_stats_backfill(
            dsn="postgresql://not-used",
            api_key="not-used",
            start_date=date(2026, 9, 21),
            end_date=date(2026, 9, 21),
            env={
                ENABLED_ENV: "true",
                AUTH_ENV: AUTH_TOKEN,
            },
        )

def test_collect_keeps_fully_existing_candidate_in_reviewed_artifact(monkeypatch) -> None:
    fixture = _fixture()
    stats = (_stat("sr:p1", "hash-a"), _stat("sr:p2", "hash-b"))

    monkeypatch.setattr(backfill, "parse_summary", lambda *args, **kwargs: fixture)
    monkeypatch.setattr(
        backfill,
        "_linked_match_context",
        lambda *args, **kwargs: (101, 11, 22),
    )
    monkeypatch.setattr(
        backfill,
        "_parse_terminal_result",
        lambda *args, **kwargs: _result(),
    )
    monkeypatch.setattr(
        backfill,
        "_sportradar_player_map",
        lambda *args, **kwargs: {"sr:p1": 11, "sr:p2": 22},
    )
    monkeypatch.setattr(
        backfill,
        "_latest_result_semantic_key",
        lambda *args, **kwargs: ("COMPLETED", 1, None, 2, 0, 12, 7),
    )
    monkeypatch.setattr(
        backfill,
        "_parse_final_stats",
        lambda *args, **kwargs: stats,
    )
    monkeypatch.setattr(
        backfill,
        "_existing_stat_hashes",
        lambda *args, **kwargs: {"hash-a", "hash-b"},
    )

    collection = backfill.collect_candidates(
        object(),
        api_key="unused",
        start_date=date(2026, 9, 21),
        end_date=date(2026, 9, 21),
        fetch_rows=lambda *args, **kwargs: (RECEIVED, [{"summary": 1}]),
    )

    assert collection.counts["already_backfilled"] == 1
    assert collection.counts["candidate_matches"] == 1
    assert collection.counts["candidate_stat_rows"] == 2
    assert len(collection.candidates) == 1
    assert collection.artifact_sha256 == _artifact_hash((_candidate(),))


class _RecordingConn:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    def execute(self, sql, params=None):
        self.calls.append((str(sql), tuple(params or ())))
        return self

    def fetchone(self):
        return None


def test_repository_inserts_accept_explicit_ingested_at() -> None:
    from sports_edge_tennis.data.repository import HistoricalRepository

    received = RECEIVED + timedelta(minutes=5)

    conn = _RecordingConn()
    repository = HistoricalRepository(conn, 7)
    assert repository.insert_match_result(
        match_id=101,
        outcome_status="COMPLETED",
        winner_side=1,
        retired_side=None,
        sets_won_player_1=2,
        sets_won_player_2=0,
        games_won_player_1=12,
        games_won_player_2=7,
        scoreline_text="2-0",
        source_observed_at=received,
        as_of=received,
        ingested_at=received,
    ) is True
    result_sql = conn.calls[-1][0]
    assert "ingested_at" in result_sql
    assert "COALESCE(%s::timestamptz, clock_timestamp())" in result_sql
    assert conn.calls[-1][1][-2] == received

    stat_conn = _RecordingConn()
    stat_repository = HistoricalRepository(stat_conn, 7)
    assert stat_repository.insert_match_stat(
        match_id=101,
        player_id=11,
        source_system="SPORTRADAR",
        source_record_id="sr:sport_event:1",
        source_record_hash="hash-a",
        line=_stat("sr:p1", "hash-a").line,
        source_observed_at=received,
        as_of=received,
        ingested_at=received,
    ) is True
    stat_sql = stat_conn.calls[-1][0]
    assert "ingested_at" in stat_sql
    assert "COALESCE(%s::timestamptz, clock_timestamp())" in stat_sql
    assert stat_conn.calls[-1][1][-2] == received


def test_repository_defaults_ingested_at_to_statement_clock() -> None:
    from sports_edge_tennis.data.repository import HistoricalRepository

    received = RECEIVED + timedelta(minutes=5)
    conn = _RecordingConn()
    repository = HistoricalRepository(conn, 7)
    repository.insert_match_result(
        match_id=101,
        outcome_status="COMPLETED",
        winner_side=1,
        retired_side=None,
        sets_won_player_1=2,
        sets_won_player_2=0,
        games_won_player_1=12,
        games_won_player_2=7,
        scoreline_text="2-0",
        source_observed_at=received,
        as_of=received,
    )
    result_sql = conn.calls[-1][0]
    assert "clock_timestamp()" in result_sql
    assert "CURRENT_TIMESTAMP" not in result_sql
    # No explicit ingestion time: the database statement clock is used instead.
    assert conn.calls[-1][1][-2] is None


def test_backfill_uses_database_clock_for_ingested_at() -> None:
    source = inspect.getsource(backfill.apply_linked_stats_backfill)
    # The backfill must not conflate provider receipt time with ingestion time.
    assert "ingested_at=" not in source


class _RecordingPacer:
    def __init__(self) -> None:
        self.waits = 0
        self.slept: list[float] = []

    def wait(self) -> None:
        self.waits += 1

    def sleep_for(self, seconds: float) -> None:
        self.slept.append(seconds)


def test_collect_retries_transient_transport_error(monkeypatch) -> None:
    fixture = _fixture()
    stats = (_stat("sr:p1", "hash-a"), _stat("sr:p2", "hash-b"))

    monkeypatch.setattr(backfill, "parse_summary", lambda *args, **kwargs: fixture)
    monkeypatch.setattr(
        backfill, "_linked_match_context", lambda *args, **kwargs: (101, 11, 22)
    )
    monkeypatch.setattr(
        backfill, "_parse_terminal_result", lambda *args, **kwargs: _result()
    )
    monkeypatch.setattr(
        backfill,
        "_sportradar_player_map",
        lambda *args, **kwargs: {"sr:p1": 11, "sr:p2": 22},
    )
    monkeypatch.setattr(
        backfill,
        "_latest_result_semantic_key",
        lambda *args, **kwargs: ("COMPLETED", 1, None, 2, 0, 12, 7),
    )
    monkeypatch.setattr(
        backfill, "_parse_final_stats", lambda *args, **kwargs: stats
    )
    monkeypatch.setattr(
        backfill, "_existing_stat_hashes", lambda *args, **kwargs: set()
    )

    attempts = {"n": 0}

    def fetch_rows(api_key, target_date):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise TransportError("HTTP_429", "rate limited")
        return RECEIVED, [{"summary": 1}]

    pacer = _RecordingPacer()
    collection = backfill.collect_candidates(
        object(),
        api_key="unused",
        start_date=date(2026, 9, 21),
        end_date=date(2026, 9, 21),
        fetch_rows=fetch_rows,
        pacer=pacer,
    )

    assert attempts["n"] == 2
    assert pacer.slept == [backfill.TRIAL_429_RETRY_SECONDS]
    assert collection.counts["provider_requests"] == 1
    assert collection.counts["candidate_matches"] == 1


def test_collect_rejects_non_transient_transport_error(monkeypatch) -> None:
    attempts = {"n": 0}

    def fetch_rows(api_key, target_date):
        attempts["n"] += 1
        raise TransportError("HTTP_403", "forbidden")

    with pytest.raises(TransportError):
        backfill.collect_candidates(
            object(),
            api_key="unused",
            start_date=date(2026, 9, 21),
            end_date=date(2026, 9, 21),
            fetch_rows=fetch_rows,
            pacer=_RecordingPacer(),
        )

    assert attempts["n"] == 1

