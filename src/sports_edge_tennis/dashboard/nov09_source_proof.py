"""First-issued, source-backed November 9 match ledger for Brian.

Used by the protected dashboard JSON route and private operator export.
Never changes results or substitutes missing provider observations. Query
and aggregate verification use the same read-only REPEATABLE READ snapshot.
"""
from __future__ import annotations

from collections import Counter
from datetime import date, datetime
from decimal import Decimal

from sports_edge_tennis.dashboard.aligned_model_picks import (
    load_aligned_day_picks,
)
from sports_edge_tennis.dashboard.qualified_history import ComparisonDay
from sports_edge_tennis.dashboard.qualified_parallel import CONFIDENCE_FLOOR

DAY = date(2026, 11, 9)
STATES = ("WON", "LOST", "VOID", "PENDING")


def _state(result_status: str | None, winner_side: int | None,
           selected_side: int) -> str:
    if result_status == "COMPLETED" and winner_side in (1, 2):
        return "WON" if winner_side == selected_side else "LOST"
    if result_status in ("RETIRED", "WALKOVER", "CANCELLED"):
        return "VOID"
    return "PENDING"


def _query_details(conn, picks: list, *, as_of: datetime):
    if not picks:
        return {}
    ids = [p.match_id for p in picks]
    issued_at = [p.picked_at for p in picks]
    rows = conn.execute(
        """
        SELECT a.match_id, p1.canonical_name AS player_1,
               p2.canonical_name AS player_2,
               r.outcome_status, r.winner_side, r.as_of AS result_as_of,
               r.source_observed_at AS result_observed_at,
               r.ingested_at AS result_ingested_at,
               f.match_status::text AS current_fixture_status,
               f.match_start_at AS latest_match_start_at,
               f.source_observed_at AS last_fixture_source_observed_at
        FROM unnest(%s::bigint[],%s::timestamptz[]) AS a(match_id,issued_at)
        LEFT JOIN LATERAL (
            SELECT mv.player_1_id, mv.player_2_id
            FROM match_versions mv
            WHERE mv.match_id = a.match_id
              AND mv.as_of <= a.issued_at
              AND mv.ingested_at <= a.issued_at
              AND mv.source_observed_at <= a.issued_at
            ORDER BY mv.as_of DESC, mv.source_observed_at DESC,
                     mv.match_version_id DESC LIMIT 1
        ) names ON TRUE
        LEFT JOIN players p1 ON p1.player_id = names.player_1_id
        LEFT JOIN players p2 ON p2.player_id = names.player_2_id
        LEFT JOIN LATERAL (
            SELECT mro.outcome_status::text AS outcome_status,
                   mro.winner_side, mro.as_of,
                   mro.source_observed_at, mro.ingested_at
            FROM match_result_observations mro
            WHERE mro.match_id = a.match_id
              AND mro.outcome_status IN (
                  'COMPLETED','RETIRED','WALKOVER','CANCELLED'
              )
              AND mro.as_of <= %s
              AND mro.source_observed_at <= %s
              AND mro.ingested_at <= %s
            ORDER BY mro.as_of DESC, mro.source_observed_at DESC,
                     mro.match_result_observation_id DESC LIMIT 1
        ) r ON TRUE
        LEFT JOIN LATERAL (
            SELECT mv.match_status, mv.match_start_at, mv.source_observed_at
            FROM match_versions mv
            WHERE mv.match_id = a.match_id
              AND mv.as_of <= %s
              AND mv.source_observed_at <= %s
              AND mv.ingested_at <= %s
            ORDER BY mv.as_of DESC, mv.source_observed_at DESC,
                     mv.match_version_id DESC LIMIT 1
        ) f ON TRUE
        ORDER BY a.match_id
        """,
        (ids,issued_at,
         as_of,as_of,as_of,
         as_of,as_of,as_of),
    ).fetchall()
    if len(rows) != len(ids):
        raise ValueError("unexpected duplicate/missing result detail rows")
    return {int(row[0]):row for row in rows}


def verify_nov9_rows(picks, result_rows: dict, daily: ComparisonDay,
                     *, as_of: datetime) -> dict:
    """Strict parity with original and >=70% daily W/L/VOID/PENDING."""
    if daily.day != DAY:
        raise ValueError("wrong reporting date")
    result = []
    ids = set()
    for p in picks:
        if p.match_id in ids:
            raise ValueError("duplicate canonical match ID")
        ids.add(p.match_id)
        row = result_rows.get(p.match_id)
        if row is None or int(row[0]) != p.match_id:
            raise ValueError("missing match result/provenance row")
        (mid,player1,player2,terminal,winner,result_at,observed,ingested,
         fixture_status,latest_start,fixture_observed) = row
        prob = Decimal(p.aligned_probability)
        if not prob.is_finite() or not Decimal(0) <= prob <= Decimal(1):
            raise ValueError("invalid original first-issued confidence")
        if p.picked_at > as_of or p.selected_side not in (1, 2):
            raise ValueError("invalid first-issued time or winner")
        for t in (result_at,observed,ingested,fixture_observed):
            if t is not None and t > as_of:
                raise ValueError("future observation leaked into match result")
        state = _state(terminal,winner,p.selected_side)
        result.append({
            "match_id":p.match_id,
            "player_1":player1,"player_2":player2,
            "original_issued_at":p.picked_at.isoformat(),
            "pit_scheduled_start": (
                p.match_start_at.isoformat() if p.match_start_at else None
            ),
            "issued_before_pit_start": (
                p.picked_at < p.match_start_at
                if p.match_start_at else None
            ),
            "selected_side":p.selected_side,
            "original_probability":str(prob),
            "aligned_type":p.aligned_pick_type,
            "qualified_ge70": prob >= CONFIDENCE_FLOOR,
            "status":state,"terminal_type":terminal,
            "winner_side":winner,
            "terminal_source_observed_at": (
                observed.isoformat() if observed else None
            ),
            "terminal_ingested_at":ingested.isoformat() if ingested else None,
            "fixture_status_stored":fixture_status,
            "latest_start_stored":(
                latest_start.isoformat() if latest_start else None
            ),
            "last_fixture_source_observed_at":(
                fixture_observed.isoformat() if fixture_observed else None
            ),
            "provider_independently_rechecked_now":False,
        })
    original=Counter(r["status"] for r in result)
    qualified=Counter(r["status"] for r in result if r["qualified_ge70"])
    expected_orig={
        "WON":daily.original_won,"LOST":daily.original_lost,
        "VOID":daily.original_void,"PENDING":daily.original_pending,
    }
    expected_selected={
        "WON":daily.qualified_won,"LOST":daily.qualified_lost,
        "VOID":daily.qualified_void,"PENDING":daily.qualified_pending,
    }
    if len(result)!=daily.original or {
        x:original[x] for x in STATES
    }!=expected_orig:
        raise ValueError("Nov9 original daily vs match-ID result mismatch")
    if sum(r["qualified_ge70"] for r in result)!=daily.qualified or {
        x:qualified[x] for x in STATES
    }!=expected_selected:
        raise ValueError("Nov9 >=70 daily vs match-ID result mismatch")
    unverified=[r["match_id"] for r in result
                if r["issued_before_pit_start"] is not True]
    return {
        "report":"NOV09_ORIGINAL_MATCH_LEVEL_PROOF",
        "data_read_at":as_of.isoformat(),
        "match_day":DAY.isoformat(),
        "original_total":len(result),
        "qualified_ge70_total":sum(r["qualified_ge70"] for r in result),
        "original_counts":expected_orig,
        "qualified_counts":expected_selected,
        "pit_unverified_match_ids":unverified,
        "status":(
            "PIT_REVIEW_REQUIRED" if unverified
            else "SOURCE_RESULT_PROVENANCE_PENDING_EXTERNAL_RECHECK"
        ),
        "records":result,
    }


def load_nov9_proof(conn, *, as_of: datetime, daily: ComparisonDay) -> dict:
    picks=load_aligned_day_picks(
        conn, decision_date=DAY, timezone_name="Asia/Manila",
    )
    details=_query_details(
        conn, list(picks), as_of=as_of,
    )
    return verify_nov9_rows(picks,details,daily,as_of=as_of)
