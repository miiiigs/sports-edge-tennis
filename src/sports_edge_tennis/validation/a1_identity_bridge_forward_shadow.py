"""Point-in-time forward shadow for the Agent 1 live-to-history identity bridge.

The production Agent 1 loader does not bridge live-provider player IDs into the
historical model namespace. Shadows on already-issued history have therefore had
to resolve bridges "as of today", which is not a forward test.

This module is a deliberately separate, read-only observer:

* it reads one already-finalised production prediction run;
* it rebuilds Agent 1 history with ``bridge_model_identities=True`` inside the
  run's own ``as_of`` boundary, including an ``ingested_at`` guard;
* it re-runs the unchanged Agent 1 implementation for the run's targets;
* it logs the original and bridged probabilities side by side.

It never writes a prediction, decision, Manager result, Model Pick, odds value or
outcome. It cannot suppress or alter live publication. It records no outcome, so
it is a true pre-match capture for later independent grading.

This is a forward *measurement* path, not an activation. The production flag that
would change Agent 1 remains off until the pre-registered sample gate in
``docs/research/A1_IDENTITY_BRIDGE_FORWARD_SHADOW_20261124.md`` passes.
"""
from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg

from sports_edge_tennis.agents.agent1_surface_elo import (
    DEFAULT_CONFIG,
    PredictionRequest,
    PredictionResult,
    predict_from_replay,
    replay_history,
)
from sports_edge_tennis.data.agent1_history import load_agent1_history
from sports_edge_tennis.data.model_identity import resolve_model_player_ids

VERSION = "A1_IDENTITY_BRIDGE_FORWARD_SHADOW_V1"
MINIMUM_START_LEAD = timedelta(minutes=30)
CAPTURE_SCHEMA_VERSION = "a1_identity_bridge_forward_shadow_record_v1"


class ShadowValidationError(ValueError):
    """Raised when a shadow capture cannot be trusted as pre-match evidence."""


@dataclass(frozen=True)
class ShadowTarget:
    prediction_run_id: int
    match_id: int
    prediction_id: int
    prediction_as_of: datetime
    match_start_at: datetime
    live_player_1_id: int
    live_player_2_id: int
    model_player_1_id: int
    model_player_2_id: int
    surface: str | None
    tour_scope: str
    competition_tier: str
    best_of_sets: int
    qualifying_flag: bool
    original_selection_side: int
    original_win_probability: float

    def __post_init__(self) -> None:
        if self.prediction_as_of.tzinfo is None or self.match_start_at.tzinfo is None:
            raise ShadowValidationError("shadow timestamps must be timezone-aware")
        if self.match_start_at <= self.prediction_as_of + MINIMUM_START_LEAD:
            raise ShadowValidationError(
                "shadow target must start at least 30 minutes after prediction as_of"
            )
        if self.original_selection_side not in (1, 2):
            raise ShadowValidationError("original selection side must be 1 or 2")
        if not (0.5 <= self.original_win_probability <= 1.0):
            raise ShadowValidationError(
                "original selected probability must be within [0.5, 1.0]"
            )


def _utc(value: datetime, *, field_name: str) -> datetime:
    if value.tzinfo is None:
        raise ShadowValidationError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


def _canonical_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _sha256_json(payload: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


TARGET_SQL = """
WITH completed_runs AS (
    SELECT pr.prediction_run_id, pr.as_of
    FROM prediction_runs pr
    WHERE pr.run_status = 'COMPLETED'
      AND pr.prediction_contract_version = 'predictive_agent_result_v1'
      AND pr.prediction_finalized_at IS NOT NULL
      AND pr.experiment_id IS NULL
      AND pr.prediction_run_id = ANY(%(run_ids)s)
)
SELECT a1.prediction_id, a1.prediction_run_id, a1.match_id, a1.as_of,
       a1.selection_side, a1.win_probability,
       mv.player_1_id, mv.player_2_id, mv.surface::text,
       mv.tour_scope::text, mv.competition_tier::text,
       mv.best_of_sets, mv.qualifying_flag, mv.match_start_at
FROM (
    SELECT DISTINCT ON (p.match_id, p.prediction_run_id)
           p.prediction_id, p.prediction_run_id, p.match_id, p.as_of,
           p.selection_side, p.win_probability
    FROM predictions p
    JOIN completed_runs cr USING (prediction_run_id)
    WHERE p.agent_code = 'AGENT_1_SURFACE_ELO'
      AND p.prediction_status = 'PREDICTED'
      AND p.selection_side IN (1, 2)
      AND p.win_probability IS NOT NULL
      AND p.experiment_id IS NULL
    ORDER BY p.match_id, p.prediction_run_id, p.prediction_id
) a1
JOIN LATERAL (
    SELECT mv0.player_1_id, mv0.player_2_id, mv0.surface, mv0.tour_scope,
           mv0.competition_tier, mv0.best_of_sets, mv0.qualifying_flag,
           mv0.match_start_at
    FROM match_versions mv0
    WHERE mv0.match_id = a1.match_id
      AND mv0.as_of <= a1.as_of
      AND mv0.source_observed_at <= a1.as_of
      AND mv0.ingested_at <= a1.as_of
    ORDER BY mv0.as_of DESC, mv0.source_observed_at DESC,
             mv0.ingested_at DESC, mv0.match_version_id DESC
    LIMIT 1
) mv ON TRUE
ORDER BY mv.match_start_at, a1.match_id
"""


def load_targets(
    conn: psycopg.Connection,
    *,
    run_ids: Iterable[int],
    capture_at: datetime,
) -> list[ShadowTarget]:
    """Load one immutable, pre-match A1 target per match for the given runs."""
    ids = sorted({int(run_id) for run_id in run_ids if run_id is not None})
    if not ids or any(run_id <= 0 for run_id in ids):
        return []
    capture_at = _utc(capture_at, field_name="capture_at")
    rows = conn.execute(TARGET_SQL, {"run_ids": ids}).fetchall()
    if not rows:
        return []

    player_ids = {int(row[6]) for row in rows} | {int(row[7]) for row in rows}
    # One bulk PIT resolution, exactly the live target resolver's semantics.
    as_of_values = {_utc(row[3], field_name="prediction_as_of") for row in rows}
    if len(as_of_values) != 1:
        raise ShadowValidationError(
            "one shadow capture must contain a single prediction as_of"
        )
    prediction_as_of = next(iter(as_of_values))
    resolved = resolve_model_player_ids(
        conn, player_ids=player_ids, as_of=prediction_as_of
    )

    targets: list[ShadowTarget] = []
    for row in rows:
        (
            prediction_id,
            prediction_run_id,
            match_id,
            as_of,
            side,
            probability,
            live_p1,
            live_p2,
            surface,
            tour,
            tier,
            best_of,
            qualifying,
            start_at,
        ) = row
        start_at = _utc(start_at, field_name="match_start_at")
        as_of = _utc(as_of, field_name="prediction_as_of")
        if start_at <= capture_at + MINIMUM_START_LEAD:
            # The observer must only capture genuinely future fixtures. A late
            # post-hoc run or a retry must never be presented as a fresh trial.
            continue
        targets.append(
            ShadowTarget(
                prediction_run_id=int(prediction_run_id),
                match_id=int(match_id),
                prediction_id=int(prediction_id),
                prediction_as_of=as_of,
                match_start_at=start_at,
                live_player_1_id=int(live_p1),
                live_player_2_id=int(live_p2),
                model_player_1_id=int(resolved.get(int(live_p1), int(live_p1))),
                model_player_2_id=int(resolved.get(int(live_p2), int(live_p2))),
                surface=str(surface) if surface is not None else None,
                tour_scope=str(tour),
                competition_tier=str(tier),
                best_of_sets=int(best_of),
                qualifying_flag=bool(qualifying),
                original_selection_side=int(side),
                original_win_probability=float(probability),
            )
        )
    return targets


def build_record(
    *,
    target: ShadowTarget,
    bridged: PredictionResult,
    history_rows: int,
    eligible_history_matches: int,
    capture_at: datetime,
    source_revision: str | None,
) -> dict[str, Any]:
    """Build the immutable, outcome-free research record for one fixture."""
    if bridged.prediction_status != "PREDICTED" or bridged.win_probability is None:
        raise ShadowValidationError("bridged Agent 1 must produce a prediction")
    capture_at = _utc(capture_at, field_name="capture_at")
    bridged_p1 = float(bridged.player_1_probability)
    bridged_selected = (
        bridged_p1 if bridged.selection_side == 1 else 1.0 - bridged_p1
    )
    record: dict[str, Any] = {
        "schema_version": CAPTURE_SCHEMA_VERSION,
        "observer_version": VERSION,
        "captured_at": capture_at.isoformat(),
        "source_revision": source_revision,
        "prediction_run_id": target.prediction_run_id,
        "prediction_id": target.prediction_id,
        "match_id": target.match_id,
        "prediction_as_of": target.prediction_as_of.isoformat(),
        "match_start_at": target.match_start_at.isoformat(),
        "tour_scope": target.tour_scope,
        "competition_tier": target.competition_tier,
        "surface": target.surface,
        "best_of_sets": target.best_of_sets,
        "qualifying_flag": target.qualifying_flag,
        "live_player_1_id": target.live_player_1_id,
        "live_player_2_id": target.live_player_2_id,
        "model_player_1_id": target.model_player_1_id,
        "model_player_2_id": target.model_player_2_id,
        "player_1_bridged": target.model_player_1_id != target.live_player_1_id,
        "player_2_bridged": target.model_player_2_id != target.live_player_2_id,
        "original_selection_side": target.original_selection_side,
        "original_win_probability": target.original_win_probability,
        "bridged_selection_side": int(bridged.selection_side),
        "bridged_win_probability": bridged_selected,
        "bridged_player_1_probability": bridged_p1,
        "selection_changed": int(bridged.selection_side)
        != target.original_selection_side,
        "history_rows": history_rows,
        "eligible_history_matches": eligible_history_matches,
        "agent_model_version": bridged.model_version,
        "agent_feature_version": bridged.feature_version,
        "outcome_included": False,
        "production_changed": False,
        "can_promote": False,
    }
    record["record_sha256"] = _sha256_json(record)
    return record


def run(
    conn: psycopg.Connection,
    *,
    run_ids: Iterable[int],
    capture_at: datetime | None = None,
    log: Callable[[str], None] = print,
) -> dict[str, Any]:
    """Capture the PIT-safe bridged A1 counterfactual for already-issued runs."""
    capture_at = capture_at or datetime.now(UTC)
    capture_at = _utc(capture_at, field_name="capture_at")
    conn.execute("SET default_transaction_read_only=on")
    targets = load_targets(conn, run_ids=run_ids, capture_at=capture_at)
    summary: dict[str, Any] = {
        "observer_version": VERSION,
        "captured_at": capture_at.isoformat(),
        "run_ids": sorted({int(run_id) for run_id in run_ids if run_id is not None}),
        "targets": len(targets),
        "captured": 0,
        "failed": 0,
        "selection_changes": 0,
        "modified_production": False,
        "outcome_included": False,
        "can_promote": False,
    }
    if not targets:
        log("TENNIS_A1_BRIDGE_FORWARD_SUMMARY " + _canonical_json(summary))
        return summary

    as_of_values = {target.prediction_as_of for target in targets}
    if len(as_of_values) != 1:
        raise ShadowValidationError("one capture cannot mix prediction as_of values")
    as_of = next(iter(as_of_values))

    history = load_agent1_history(
        conn,
        cutoff=as_of,
        bridge_model_identities=True,
        ingested_by=as_of,
    )
    replay = replay_history(history, as_of, DEFAULT_CONFIG)
    source_revision = os.environ.get("RAILWAY_GIT_COMMIT_SHA") or None
    records: list[dict[str, Any]] = []
    for target in targets:
        try:
            request = PredictionRequest(
                player_1_id=target.model_player_1_id,
                player_2_id=target.model_player_2_id,
                surface=target.surface,  # type: ignore[arg-type]
                tour_scope=target.tour_scope,  # type: ignore[arg-type]
                competition_tier=target.competition_tier,  # type: ignore[arg-type]
                best_of_sets=target.best_of_sets,
                qualifying_flag=target.qualifying_flag,
                as_of=as_of,
            )
            bridged = predict_from_replay(request, replay, DEFAULT_CONFIG)
            record = build_record(
                target=target,
                bridged=bridged,
                history_rows=len(history),
                eligible_history_matches=replay.eligible_history_match_count,
                capture_at=capture_at,
                source_revision=source_revision,
            )
        except Exception as exc:  # noqa: BLE001 - observer must never break live work
            summary["failed"] += 1
            log(
                "TENNIS_A1_BRIDGE_FORWARD_FAILED "
                + _canonical_json(
                    {
                        "match_id": target.match_id,
                        "prediction_run_id": target.prediction_run_id,
                        "reason": type(exc).__name__,
                    }
                )
            )
            continue
        records.append(record)
        summary["selection_changes"] += int(record["selection_changed"])
        log("TENNIS_A1_BRIDGE_FORWARD_RECORD " + _canonical_json(record))

    summary["captured"] = len(records)
    summary["record_set_sha256"] = hashlib.sha256(
        "\n".join(record["record_sha256"] for record in records).encode("utf-8")
    ).hexdigest()
    log("TENNIS_A1_BRIDGE_FORWARD_SUMMARY " + _canonical_json(summary))
    return summary


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--run-id", action="append", type=int, required=True, help="prediction run id"
    )
    parser.add_argument("--as-of", help="ISO-8601 capture clock; defaults to now UTC")
    args = parser.parse_args(argv)

    capture_at = (
        datetime.fromisoformat(args.as_of).astimezone(UTC)
        if args.as_of
        else datetime.now(UTC)
    )
    dsn = os.environ.get("TENNIS_DATABASE_URL")
    if not dsn:
        if os.environ.get("PGHOST") and os.environ.get("PGPASSWORD"):
            dsn = psycopg.conninfo.make_conninfo(
                host=os.environ["PGHOST"],
                port=os.environ.get("PGPORT", "5432"),
                user=os.environ["PGUSER"],
                password=os.environ["PGPASSWORD"],
                dbname=os.environ["PGDATABASE"],
                sslmode="require",
            )
        else:
            raise SystemExit("TENNIS_DATABASE_URL or PG* variables are required")
    with psycopg.connect(dsn, autocommit=True, connect_timeout=30) as conn:
        run(conn, run_ids=args.run_id, capture_at=capture_at)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
