from __future__ import annotations

from datetime import UTC, datetime

from sports_edge_tennis.live.sportradar_fixtures import SportradarFixtureCandidate
from sports_edge_tennis.live.sportradar_reconciliation import (
    resolve_sportradar_event_overlap,
)


class _Result:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return list(self.rows)


class _Conn:
    def __init__(self, *, event_rows=(), p1_rows=(), p2_rows=(), alias_rows=None, match_rows=()):
        self.event_rows = event_rows
        self.p1_rows = p1_rows
        self.p2_rows = p2_rows
        self.alias_rows = alias_rows or {}
        self.match_rows = match_rows

    def execute(self, sql, params=()):
        normalized = " ".join(str(sql).split()).lower()
        if "from match_source_ids" in normalized:
            return _Result(self.event_rows)
        if "from player_source_ids" in normalized:
            source_id = params[0]
            if source_id == "sr:p1":
                return _Result(self.p1_rows)
            if source_id == "sr:p2":
                return _Result(self.p2_rows)
            return _Result(())
        if "from player_aliases" in normalized:
            keys = tuple(params[0])
            rows = set()
            for key in keys:
                rows.update(self.alias_rows.get(key, ()))
            return _Result(tuple((row,) for row in sorted(rows)))
        if "with latest as" in normalized:
            return _Result(self.match_rows)
        raise AssertionError(normalized)


def _fixture():
    return SportradarFixtureCandidate(
        source_match_id="sr:event:1",
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
        surface=None,
        round_code="round_of_32",
        best_of_sets=3,
        qualifying_flag=False,
        received_at=datetime(2026, 9, 19, 12, 0, tzinfo=UTC),
    )


def test_existing_sportradar_event_id_wins_without_fuzzy_match() -> None:
    result = resolve_sportradar_event_overlap(
        _Conn(event_rows=((55,),), p1_rows=((1,),), p2_rows=((2,),)),
        fixture=_fixture(),
    )
    assert result.status == "SOURCE_ID_RESOLVED"
    assert result.match_id == 55


def test_unique_existing_pair_in_time_window_reconciles_to_same_match() -> None:
    result = resolve_sportradar_event_overlap(
        _Conn(
            p1_rows=((1,),),
            p2_rows=((2,),),
            match_rows=(
                (
                    77,
                    1,
                    2,
                    datetime(2026, 9, 19, 13, 20, tzinfo=UTC),
                ),
            ),
        ),
        fixture=_fixture(),
    )
    assert result.status == "EXACT_EXISTING_MATCH"
    assert result.match_id == 77
    assert result.candidates[0].orientation == "DIRECT"
    assert result.candidates[0].start_delta_minutes == 10


def test_reversed_existing_pair_is_reconciled_without_reordering_canonical_match() -> None:
    result = resolve_sportradar_event_overlap(
        _Conn(
            p1_rows=((1,),),
            p2_rows=((2,),),
            match_rows=(
                (
                    78,
                    2,
                    1,
                    datetime(2026, 9, 19, 13, 25, tzinfo=UTC),
                ),
            ),
        ),
        fixture=_fixture(),
    )
    assert result.status == "EXACT_EXISTING_MATCH"
    assert result.match_id == 78
    assert result.candidates[0].orientation == "REVERSED"


def test_multiple_pair_candidates_fail_closed() -> None:
    result = resolve_sportradar_event_overlap(
        _Conn(
            p1_rows=((1,),),
            p2_rows=((2,),),
            match_rows=(
                (77, 1, 2, datetime(2026, 9, 19, 13, 20, tzinfo=UTC)),
                (79, 1, 2, datetime(2026, 9, 19, 13, 40, tzinfo=UTC)),
            ),
        ),
        fixture=_fixture(),
    )
    assert result.status == "AMBIGUOUS_EXISTING_MATCH"
    assert result.match_id is None
    assert len(result.candidates) == 2


def test_new_player_does_not_get_forced_onto_existing_identity() -> None:
    result = resolve_sportradar_event_overlap(
        _Conn(
            p1_rows=(),
            p2_rows=((2,),),
            alias_rows={},
        ),
        fixture=_fixture(),
    )
    assert result.status == "NEW_CANONICAL_EVENT"
    assert result.player_1.status == "NEW_PLAYER"
    assert result.match_id is None


def test_ambiguous_name_identity_blocks_event_reconciliation() -> None:
    result = resolve_sportradar_event_overlap(
        _Conn(
            p1_rows=(),
            p2_rows=((2,),),
            alias_rows={
                "darwin blanch": (1, 3),
                "d blanch": (1, 3),
            },
        ),
        fixture=_fixture(),
    )
    assert result.status == "PLAYER_IDENTITY_UNRESOLVED"
    assert result.player_1.status == "AMBIGUOUS_PLAYER"
