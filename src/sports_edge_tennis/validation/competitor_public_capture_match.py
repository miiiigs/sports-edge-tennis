"""Match a frozen public competitor capture to point-in-time Sports Edge fixtures.

This is read-only. It never fetches a vendor, changes predictions, settles
results or modifies model rules. Public capture time is the evidence cutoff.
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import psycopg

from sports_edge_tennis.data.normalize import normalize_person_lookup
from sports_edge_tennis.validation.competitor_production_export import run as export_original
from sports_edge_tennis.validation.competitor_forward_audit import utc


class PublicCaptureMatchError(RuntimeError):
    pass


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _candidate_rows(conn: psycopg.Connection, *, start: datetime, captured_at: datetime):
    return conn.execute(
        """
        SELECT m.match_id, mv.player_1_id, mv.player_2_id,
               p1.canonical_name, p2.canonical_name, mv.match_start_at,
               mv.tour_scope::text, mv.qualifying_flag, te.tournament_id
        FROM matches m
        JOIN tournament_editions te
          ON te.tournament_edition_id=m.tournament_edition_id
        JOIN LATERAL (
            SELECT player_1_id, player_2_id, match_start_at, tour_scope, qualifying_flag
            FROM match_versions
            WHERE match_id=m.match_id
              AND as_of <= %(captured_at)s
              AND source_observed_at <= %(captured_at)s
              AND ingested_at <= %(captured_at)s
            ORDER BY as_of DESC, source_observed_at DESC, match_version_id DESC
            LIMIT 1
        ) mv ON TRUE
        JOIN players p1 ON p1.player_id=mv.player_1_id
        JOIN players p2 ON p2.player_id=mv.player_2_id
        WHERE mv.match_start_at BETWEEN %(lo)s AND %(hi)s
          AND mv.tour_scope IN ('ATP_MAIN','CHALLENGER')
        ORDER BY abs(extract(epoch from (mv.match_start_at-%(start)s))), m.match_id
        """,
        {
            "captured_at": captured_at,
            "start": start,
            "lo": start - timedelta(hours=3),
            "hi": start + timedelta(hours=3),
        },
    ).fetchall()


def _identity_candidates(
    conn: psycopg.Connection,
    *,
    row: dict[str, Any],
    as_of: datetime,
) -> list[tuple[tuple[Any, ...], int]]:
    start = utc(str(row["scheduled_start_utc"]))
    n1 = normalize_person_lookup(str(row["player_1_name"]))
    n2 = normalize_person_lookup(str(row["player_2_name"]))
    candidates: list[tuple[tuple[Any, ...], int]] = []
    for c in _candidate_rows(conn, start=start, captured_at=as_of):
        cp1 = normalize_person_lookup(str(c[3]))
        cp2 = normalize_person_lookup(str(c[4]))
        orientation = None
        if (cp1, cp2) == (n1, n2):
            orientation = 1
        elif (cp1, cp2) == (n2, n1):
            orientation = -1
        if orientation is not None:
            candidates.append((c, orientation))
    return candidates


def _match_one(
    conn: psycopg.Connection,
    row: dict[str, Any],
    captured_at: datetime,
    evaluation_as_of: datetime | None = None,
) -> dict[str, Any]:
    start = utc(str(row["scheduled_start_utc"]))
    evaluation_as_of = evaluation_as_of or captured_at
    if evaluation_as_of < captured_at:
        raise PublicCaptureMatchError("evaluation_as_of_precedes_capture")

    candidates = _identity_candidates(conn, row=row, as_of=captured_at)
    visible_at_capture = len(candidates) == 1
    identity_match_mode = "POINT_IN_TIME_CAPTURE"

    if len(candidates) > 1:
        raise PublicCaptureMatchError(
            f"capture_match_ambiguous_at_capture names={row['player_1_name']}|{row['player_2_name']} count={len(candidates)}"
        )
    if not candidates and evaluation_as_of > captured_at:
        candidates = _identity_candidates(conn, row=row, as_of=evaluation_as_of)
        identity_match_mode = "POST_CAPTURE_IDENTITY_LINK"

    if len(candidates) != 1:
        raise PublicCaptureMatchError(
            f"capture_match_not_unique names={row['player_1_name']}|{row['player_2_name']} count={len(candidates)}"
        )

    c, orientation = candidates[0]
    match_id, p1_id, p2_id, p1_name, p2_name, frozen_start, tour_scope, qualifying, tournament_id = c
    if abs((frozen_start - start).total_seconds()) > 60 * 60:
        raise PublicCaptureMatchError("canonical_start_diff_exceeds_one_hour")

    picked = normalize_person_lookup(str(row["picked_name"]))
    prob = float(row["picked_probability"])
    if not 0 < prob < 1:
        raise PublicCaptureMatchError("invalid_probability")
    if picked == normalize_person_lookup(str(p1_name)):
        p_player1 = prob
    elif picked == normalize_person_lookup(str(p2_name)):
        p_player1 = 1.0 - prob
    else:
        raise PublicCaptureMatchError("picked_player_not_canonical_participant")

    return {
        "source": "tennisdataapp_public_web",
        "match_id": str(match_id),
        "player1_id": str(p1_id),
        "player2_id": str(p2_id),
        "p_player1": f"{p_player1:.6f}",
        "published_at_utc": _iso(captured_at),
        "captured_at_utc": _iso(captured_at),
        "rights": "authorized_public_export",
        "model_version": str(row["model_version"]),
        "canonical_player_1": str(p1_name),
        "canonical_player_2": str(p2_name),
        "frozen_start_utc": _iso(frozen_start),
        "tour": "ATP_TOUR" if str(tour_scope) == "ATP_MAIN" else "CHALLENGER",
        "draw": "QUALIFYING" if bool(qualifying) else "MAIN_DRAW",
        "tournament_id": str(tournament_id),
        "source_url": str(row["source_url"]),
        "orientation_from_capture": orientation,
        "sports_edge_fixture_visible_at_competitor_capture": visible_at_capture,
        "identity_match_mode": identity_match_mode,
        "identity_link_as_of_utc": _iso(evaluation_as_of),
    }


def match_capture(
    conn: psycopg.Connection,
    payload: dict[str, Any],
    *,
    evaluation_as_of: datetime | None = None,
) -> dict[str, Any]:
    captured_at = utc(str(payload["captured_at_utc"]))
    evaluation_as_of = evaluation_as_of or captured_at
    if evaluation_as_of < captured_at:
        raise PublicCaptureMatchError("evaluation_as_of_precedes_capture")
    if payload.get("rights") != "authorized_public_export":
        raise PublicCaptureMatchError("capture_rights_not_authorized_public_export")
    evidence = payload.get("evidence")
    if not isinstance(evidence, list) or not evidence:
        raise PublicCaptureMatchError("capture_has_no_evidence")
    matched = [
        _match_one(conn, row, captured_at, evaluation_as_of=evaluation_as_of)
        for row in evidence
    ]
    if len({row["match_id"] for row in matched}) != len(matched):
        raise PublicCaptureMatchError("duplicate_canonical_match")

    starts = [utc(row["frozen_start_utc"]) for row in matched]
    baseline = export_original(
        conn,
        as_of=evaluation_as_of,
        start_at=min(starts) - timedelta(hours=1),
        end_at=max(starts) + timedelta(hours=1),
    )
    ids = {row["match_id"] for row in matched}
    original = [row for row in baseline["forecasts"] if row["match_id"] in ids]
    return {
        "capture_version": payload["capture_version"],
        "captured_at_utc": _iso(captured_at),
        "evaluation_as_of_utc": _iso(evaluation_as_of),
        "rights": payload["rights"],
        "matched_competitor_forecasts": matched,
        "sports_edge_original_pre_match_forecasts": original,
        "matched_competitor_count": len(matched),
        "sports_edge_same_fixture_count": len(original),
        "post_capture_identity_link_count": sum(
            row["identity_match_mode"] == "POST_CAPTURE_IDENTITY_LINK" for row in matched
        ),
        "ready_for_future_settlement_scoring": True,
        "no_database_writes": True,
        "interpretation": (
            "Competitor probabilities stay frozen at first public capture. "
            "A later canonical identity link does not change publication time or probability. "
            "Comparative performance remains pending until authoritative results are observed."
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture_json", type=Path)
    parser.add_argument("--dsn", default=os.environ.get("TENNIS_DATABASE_URL"))
    parser.add_argument(
        "--evaluation-as-of-utc",
        help="Optional later point-in-time used only to link canonical identity and read Sports Edge first-issued forecasts.",
    )
    args = parser.parse_args(argv)
    if not args.dsn:
        parser.error("TENNIS_DATABASE_URL required")
    payload = json.loads(args.capture_json.read_text(encoding="utf-8"))
    evaluation_as_of = (
        utc(args.evaluation_as_of_utc)
        if args.evaluation_as_of_utc
        else utc(str(payload["captured_at_utc"]))
    )
    with psycopg.connect(args.dsn, autocommit=True, connect_timeout=10) as conn:
        report = match_capture(conn, payload, evaluation_as_of=evaluation_as_of)
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
