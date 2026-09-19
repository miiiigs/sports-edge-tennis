from __future__ import annotations

from contextlib import nullcontext
from datetime import UTC, datetime

import pytest

from sports_edge_tennis.live.goalserve import GoalserveTennisMatchEnvelope
from sports_edge_tennis.live.goalserve_reconciliation import (
    GoalserveEventResolution,
    GoalserveReconciliationError,
    apply_goalserve_identity_bridge,
    reconcile_goalserve_event,
)


AS_OF = datetime(2026, 9, 19, 12, 0, tzinfo=UTC)


class _Result:
    def __init__(self, *, row=None, rows=()):
        self.row = row
        self.rows = rows

    def fetchone(self):
        return self.row

    def fetchall(self):
        return list(self.rows)


class _ReconConn:
    def __init__(self, *, existing=None, rows=()):
        self.existing = existing
        self.rows = rows

    def execute(self, sql, params=()):
        normalized = " ".join(str(sql).split()).lower()
        if "from match_source_ids" in normalized:
            return _Result(row=self.existing)
        if "with latest as" in normalized:
            return _Result(rows=self.rows)
        raise AssertionError(normalized)


def _envelope():
    return GoalserveTennisMatchEnvelope(
        category_id="21788",
        category_name="Atp - Singles: Acapulco (Mexico), Hard",
        provider_event_id="567872",
        match_start_at=datetime(2026, 9, 19, 13, 30, tzinfo=UTC),
        player_1_id="1716",
        player_1_name="M. Copil",
        player_2_id="11483",
        player_2_name="D. Schwartzman",
        match_payload={"id": "567872"},
    )


def test_goalserve_reconciliation_matches_unique_candidate_and_orientation() -> None:
    result = reconcile_goalserve_event(
        _ReconConn(
            rows=(
                (
                    10,
                    100,
                    1,
                    2,
                    datetime(2026, 9, 19, 13, 20, tzinfo=UTC),
                    "Marius Copil",
                    "Diego Schwartzman",
                ),
            )
        ),
        envelope=_envelope(),
        candidate_match_ids=(10,),
        as_of=AS_OF,
    )
    assert result.status == "EXACT"
    assert result.match_id == 10
    assert result.candidate.provider_orientation == "DIRECT"
    assert result.candidate.start_delta_minutes == 10


def test_goalserve_reconciliation_preserves_reversed_canonical_orientation() -> None:
    result = reconcile_goalserve_event(
        _ReconConn(
            rows=(
                (
                    11,
                    101,
                    2,
                    1,
                    datetime(2026, 9, 19, 13, 30, tzinfo=UTC),
                    "D. Schwartzman",
                    "M. Copil",
                ),
            )
        ),
        envelope=_envelope(),
        candidate_match_ids=(11,),
        as_of=AS_OF,
    )
    assert result.status == "EXACT"
    assert result.candidate.provider_orientation == "REVERSED"


def test_goalserve_reconciliation_fails_ambiguous_instead_of_picking_closest() -> None:
    rows = (
        (
            10,
            100,
            1,
            2,
            datetime(2026, 9, 19, 13, 20, tzinfo=UTC),
            "M. Copil",
            "D. Schwartzman",
        ),
        (
            12,
            102,
            1,
            2,
            datetime(2026, 9, 19, 13, 35, tzinfo=UTC),
            "M. Copil",
            "D. Schwartzman",
        ),
    )
    result = reconcile_goalserve_event(
        _ReconConn(rows=rows),
        envelope=_envelope(),
        candidate_match_ids=(10, 12),
        as_of=AS_OF,
    )
    assert result.status == "AMBIGUOUS"
    assert result.match_id is None
    assert result.candidate_count == 2


class _ApplyConn:
    def __init__(self, *, player_links=None, event_link=None):
        self.player_links = dict(player_links or {})
        self.event_link = event_link
        self.inserts = []

    def transaction(self):
        return nullcontext()

    def execute(self, sql, params=()):
        normalized = " ".join(str(sql).split()).lower()
        if "select player_id from player_source_ids" in normalized:
            source_id = params[1]
            player = self.player_links.get(source_id)
            return _Result(row=(player,) if player is not None else None)
        if "insert into player_source_ids" in normalized:
            player_id, _, source_id = params
            self.player_links[source_id] = player_id
            self.inserts.append(("player", player_id, source_id))
            return _Result()
        if "select match_id from match_source_ids" in normalized:
            return _Result(row=(self.event_link,) if self.event_link is not None else None)
        if "insert into match_source_ids" in normalized:
            match_id, version_id, _, event_id = params
            self.event_link = match_id
            self.inserts.append(("match", match_id, version_id, event_id))
            return _Result()
        raise AssertionError(normalized)


def _exact_resolution(orientation="DIRECT"):
    from sports_edge_tennis.live.goalserve_reconciliation import (
        GoalserveCanonicalCandidate,
    )
    candidate = GoalserveCanonicalCandidate(
        match_id=10,
        match_version_id=100,
        canonical_player_1_id=1,
        canonical_player_2_id=2,
        canonical_player_1_name="M. Copil",
        canonical_player_2_name="D. Schwartzman",
        start_delta_minutes=0,
        provider_orientation=orientation,
    )
    return GoalserveEventResolution("EXACT", 10, candidate, 1)


def test_goalserve_bridge_inserts_player_and_event_ids_idempotently() -> None:
    conn = _ApplyConn()
    status = apply_goalserve_identity_bridge(
        conn,
        envelope=_envelope(),
        resolution=_exact_resolution(),
    )
    assert status == "INSERTED"
    assert conn.player_links == {"1716": 1, "11483": 2}
    assert conn.event_link == 10

    status = apply_goalserve_identity_bridge(
        conn,
        envelope=_envelope(),
        resolution=GoalserveEventResolution("EXISTING", 10, None, 1),
    )
    assert status == "EXISTING"


def test_goalserve_bridge_rejects_conflicting_player_source_id() -> None:
    conn = _ApplyConn(player_links={"1716": 999})
    with pytest.raises(GoalserveReconciliationError, match="player source ID conflicts"):
        apply_goalserve_identity_bridge(
            conn,
            envelope=_envelope(),
            resolution=_exact_resolution(),
        )
