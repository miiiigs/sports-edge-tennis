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
from sports_edge_tennis.data.identity_provenance import (
    IDENTITY_PROVENANCE_SCHEMA_VERSION,
    resolve_with_provenance,
)

VERSION = "A1_IDENTITY_BRIDGE_FORWARD_SHADOW_V2"
MINIMUM_START_LEAD = timedelta(minutes=30)
CAPTURE_SCHEMA_VERSION = "a1_identity_bridge_forward_shadow_record_v2"


class ShadowValidationError(ValueError):
    """Raised when a shadow capture cannot be trusted as pre-match evidence."""


@dataclass(frozen=True)
class ShadowTarget:
    prediction_run_id: int
    match_id: int
    prediction_id: int
    prediction_as_of: datetime
    prediction_finalized_at: datetime
    match_version_id: int
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
    is_earliest_issued: bool = False
    prior_a1_prediction_count: int = 0
    main_draw_eligible: bool = False
    player_1_resolution_method: str = "UNKNOWN"
    player_2_resolution_method: str = "UNKNOWN"
    player_1_candidate_count: int = 0
    player_2_candidate_count: int = 0
    player_1_matched_alias: str | None = None
    player_2_matched_alias: str | None = None
    player_1_bridge_event_id: int | None = None
    player_2_bridge_event_id: int | None = None
    player_1_visible_from: datetime | None = None
    player_2_visible_from: datetime | None = None
    player_1_evidence: str = "none"
    player_2_evidence: str = "none"
    player_1_name_source: str = "NONE"
    player_2_name_source: str = "NONE"
    player_1_observed_at: datetime | None = None
    player_2_observed_at: datetime | None = None
    identity_reference_sha256: str | None = None
    identity_reference_policy: str | None = None
    identity_reference_row_counts: dict[str, int] | None = None

    def __post_init__(self) -> None:
        if self.prediction_as_of.tzinfo is None or self.match_start_at.tzinfo is None:
            raise ShadowValidationError("shadow timestamps must be timezone-aware")
        if self.prediction_finalized_at.tzinfo is None:
            raise ShadowValidationError("prediction_finalized_at must be timezone-aware")
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


DISCOVERY_SQL = """
SELECT pr.prediction_run_id, pr.as_of, pr.prediction_finalized_at
FROM prediction_runs pr
WHERE pr.run_status = 'COMPLETED'
  AND pr.prediction_contract_version = 'predictive_agent_result_v1'
  AND pr.prediction_finalized_at IS NOT NULL
  AND pr.experiment_id IS NULL
  AND pr.prediction_finalized_at >= %(since)s
  AND EXISTS (
      SELECT 1
      FROM predictions p
      WHERE p.prediction_run_id = pr.prediction_run_id
        AND p.agent_code = 'AGENT_1_SURFACE_ELO'
        AND p.prediction_status = 'PREDICTED'
        AND p.selection_side IN (1, 2)
        AND p.win_probability IS NOT NULL
        AND p.experiment_id IS NULL
  )
ORDER BY pr.prediction_finalized_at, pr.prediction_run_id
LIMIT %(limit)s
"""


def discover_completed_runs(
    conn: psycopg.Connection,
    *,
    since: datetime,
    limit: int = 25,
) -> list[int]:
    """List completed production runs that carry at least one first-party A1 result.

    Discovery is deliberately read-only and excludes experiment runs. The caller
    still has to pass ``require_first_issue`` for the capture gate; discovery only
    bounds the amount of work.
    """
    since = _utc(since, field_name="since")
    if limit <= 0:
        return []
    rows = conn.execute(DISCOVERY_SQL, {"since": since, "limit": int(limit)}).fetchall()
    return [int(row[0]) for row in rows]


def archived_run_ids(path: str) -> set[int]:
    """Return run ids already present in an append-only capture archive."""
    import json
    from pathlib import Path

    target = Path(path)
    if not target.exists():
        return set()
    run_ids: set[int] = set()
    for line in target.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        if record.get("record_type") == "summary":
            run_ids.update(int(run_id) for run_id in record.get("run_ids", []))
    return run_ids


def _canonical_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _sha256_json(payload: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


TARGET_SQL = """
WITH completed_runs AS (
    SELECT pr.prediction_run_id, pr.as_of, pr.prediction_finalized_at
    FROM prediction_runs pr
    WHERE pr.run_status = 'COMPLETED'
      AND pr.prediction_contract_version = 'predictive_agent_result_v1'
      AND pr.prediction_finalized_at IS NOT NULL
      AND pr.experiment_id IS NULL
      AND pr.prediction_run_id = ANY(%(run_ids)s)
),
earliest AS (
    SELECT DISTINCT ON (p.match_id)
           p.match_id,
           p.prediction_id AS earliest_prediction_id,
           p.prediction_run_id AS earliest_run_id
    FROM predictions p
    JOIN prediction_runs pr USING (prediction_run_id)
    WHERE p.agent_code = 'AGENT_1_SURFACE_ELO'
      AND p.prediction_status = 'PREDICTED'
      AND p.selection_side IN (1, 2)
      AND p.win_probability IS NOT NULL
      AND p.experiment_id IS NULL
      AND pr.prediction_contract_version = 'predictive_agent_result_v1'
      AND pr.run_status = 'COMPLETED'
      AND pr.prediction_finalized_at IS NOT NULL
      AND pr.experiment_id IS NULL
    ORDER BY p.match_id, pr.prediction_finalized_at, p.as_of, p.prediction_id
)
SELECT a1.prediction_id, a1.prediction_run_id, a1.match_id, a1.as_of,
       a1.prediction_finalized_at,
       a1.selection_side, a1.win_probability,
       mv.match_version_id,
       mv.player_1_id, mv.player_2_id, mv.surface::text,
       mv.tour_scope::text, mv.competition_tier::text,
       mv.best_of_sets, mv.qualifying_flag, mv.match_start_at,
       (a1.prediction_id = earliest.earliest_prediction_id) AS is_earliest_issued,
       (
           SELECT count(*)
           FROM predictions prior_p
           JOIN prediction_runs prior_pr USING (prediction_run_id)
           WHERE prior_p.match_id = a1.match_id
             AND prior_p.agent_code = 'AGENT_1_SURFACE_ELO'
             AND prior_p.prediction_status = 'PREDICTED'
             AND prior_p.experiment_id IS NULL
             AND prior_pr.experiment_id IS NULL
             AND prior_pr.run_status = 'COMPLETED'
             AND prior_pr.prediction_finalized_at < a1.prediction_finalized_at
       ) AS prior_a1_prediction_count
FROM (
    SELECT DISTINCT ON (p.match_id, p.prediction_run_id)
           p.prediction_id, p.prediction_run_id, p.match_id, p.as_of,
           p.selection_side, p.win_probability,
           pr.prediction_finalized_at
    FROM predictions p
    JOIN completed_runs pr USING (prediction_run_id)
    WHERE p.agent_code = 'AGENT_1_SURFACE_ELO'
      AND p.prediction_status = 'PREDICTED'
      AND p.selection_side IN (1, 2)
      AND p.win_probability IS NOT NULL
      AND p.experiment_id IS NULL
    ORDER BY p.match_id, p.prediction_run_id, p.prediction_id
) a1
JOIN prediction_run_targets prt
  ON prt.prediction_run_id = a1.prediction_run_id
 AND prt.match_id = a1.match_id
JOIN earliest ON earliest.match_id = a1.match_id
JOIN LATERAL (
    SELECT mv0.match_version_id, mv0.player_1_id, mv0.player_2_id,
           mv0.surface, mv0.tour_scope, mv0.competition_tier,
           mv0.best_of_sets, mv0.qualifying_flag, mv0.match_start_at
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

    player_ids = {int(row[8]) for row in rows} | {int(row[9]) for row in rows}
    # One bulk PIT resolution, exactly the live target resolver's semantics, frozen
    # against the reference-state digest it used.
    as_of_values = {_utc(row[3], field_name="prediction_as_of") for row in rows}
    if len(as_of_values) != 1:
        raise ShadowValidationError(
            "one shadow capture must contain a single prediction as_of"
        )
    prediction_as_of = next(iter(as_of_values))
    resolutions, reference = resolve_with_provenance(
        conn, player_ids=player_ids, as_of=prediction_as_of
    )

    targets: list[ShadowTarget] = []
    for row in rows:
        (
            prediction_id,
            prediction_run_id,
            match_id,
            as_of,
            finalized_at,
            side,
            probability,
            match_version_id,
            live_p1,
            live_p2,
            surface,
            tour,
            tier,
            best_of,
            qualifying,
            start_at,
            is_earliest,
            prior_count,
        ) = row
        start_at = _utc(start_at, field_name="match_start_at")
        as_of = _utc(as_of, field_name="prediction_as_of")
        finalized_at = _utc(finalized_at, field_name="prediction_finalized_at")
        if start_at <= capture_at + MINIMUM_START_LEAD:
            # The observer must only capture genuinely future fixtures. A late
            # post-hoc run or a retry must never be presented as a fresh trial.
            continue
        res_1 = resolutions[int(live_p1)].resolution
        res_2 = resolutions[int(live_p2)].resolution
        targets.append(
            ShadowTarget(
                prediction_run_id=int(prediction_run_id),
                match_id=int(match_id),
                prediction_id=int(prediction_id),
                prediction_as_of=as_of,
                prediction_finalized_at=finalized_at,
                match_version_id=int(match_version_id),
                match_start_at=start_at,
                live_player_1_id=int(live_p1),
                live_player_2_id=int(live_p2),
                model_player_1_id=res_1.resolved_player_id,
                model_player_2_id=res_2.resolved_player_id,
                surface=str(surface) if surface is not None else None,
                tour_scope=str(tour),
                competition_tier=str(tier),
                best_of_sets=int(best_of),
                qualifying_flag=bool(qualifying),
                original_selection_side=int(side),
                original_win_probability=float(probability),
                is_earliest_issued=bool(is_earliest),
                prior_a1_prediction_count=int(prior_count),
                main_draw_eligible=(
                    str(tour) in ("ATP_MAIN", "CHALLENGER") and not bool(qualifying)
                ),
                player_1_resolution_method=res_1.method,
                player_2_resolution_method=res_2.method,
                player_1_candidate_count=res_1.candidate_count,
                player_2_candidate_count=res_2.candidate_count,
                player_1_matched_alias=res_1.matched_alias,
                player_2_matched_alias=res_2.matched_alias,
                player_1_bridge_event_id=res_1.bridge_event_id,
                player_2_bridge_event_id=res_2.bridge_event_id,
                player_1_visible_from=res_1.visible_from,
                player_2_visible_from=res_2.visible_from,
                player_1_evidence=res_1.evidence,
                player_2_evidence=res_2.evidence,
                player_1_name_source=res_1.name_source,
                player_2_name_source=res_2.name_source,
                player_1_observed_at=res_1.observed_at,
                player_2_observed_at=res_2.observed_at,
                identity_reference_sha256=reference.sha256,
                identity_reference_policy=reference.policy_version,
                identity_reference_row_counts=dict(reference.row_counts),
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
        "record_type": "capture",
        "observer_version": VERSION,
        "captured_at": capture_at.isoformat(),
        "source_revision": source_revision,
        "prediction_run_id": target.prediction_run_id,
        "prediction_id": target.prediction_id,
        "match_id": target.match_id,
        "match_version_id": target.match_version_id,
        "prediction_as_of": target.prediction_as_of.isoformat(),
        "prediction_finalized_at": target.prediction_finalized_at.isoformat(),
        "match_start_at": target.match_start_at.isoformat(),
        "is_earliest_issued": target.is_earliest_issued,
        "prior_a1_prediction_count": target.prior_a1_prediction_count,
        "main_draw_eligible": target.main_draw_eligible,
        "coverage_scope": (
            "ATP_MAIN_MAIN_DRAW"
            if target.tour_scope == "ATP_MAIN" and not target.qualifying_flag
            else "CHALLENGER_MAIN_DRAW"
            if target.tour_scope == "CHALLENGER" and not target.qualifying_flag
            else "QUALIFYING"
            if target.qualifying_flag
            else "OTHER_SCOPE"
        ),
        "tour_scope": target.tour_scope,
        "competition_tier": target.competition_tier,
        "surface": target.surface,
        "best_of_sets": target.best_of_sets,
        "qualifying_flag": target.qualifying_flag,
        "live_player_1_id": target.live_player_1_id,
        "live_player_2_id": target.live_player_2_id,
        "model_player_1_id": target.model_player_1_id,
        "model_player_2_id": target.model_player_2_id,
        "identity_reference": {
            "schema_version": IDENTITY_PROVENANCE_SCHEMA_VERSION,
            "policy_version": target.identity_reference_policy,
            "reference_sha256": target.identity_reference_sha256,
            "row_counts": target.identity_reference_row_counts,
            "observed_at": target.prediction_as_of.isoformat(),
        },
        "player_1_resolution": {
            "method": target.player_1_resolution_method,
            "evidence": target.player_1_evidence,
            "name_source": target.player_1_name_source,
            "candidate_count": target.player_1_candidate_count,
            "matched_alias": target.player_1_matched_alias,
            "bridge_event_id": target.player_1_bridge_event_id,
            "visible_from": (
                target.player_1_visible_from.isoformat()
                if target.player_1_visible_from is not None
                else None
            ),
            "observed_at": (
                target.player_1_observed_at.isoformat()
                if target.player_1_observed_at is not None
                else target.prediction_as_of.isoformat()
            ),
        },
        "player_2_resolution": {
            "method": target.player_2_resolution_method,
            "evidence": target.player_2_evidence,
            "name_source": target.player_2_name_source,
            "candidate_count": target.player_2_candidate_count,
            "matched_alias": target.player_2_matched_alias,
            "bridge_event_id": target.player_2_bridge_event_id,
            "visible_from": (
                target.player_2_visible_from.isoformat()
                if target.player_2_visible_from is not None
                else None
            ),
            "observed_at": (
                target.player_2_observed_at.isoformat()
                if target.player_2_observed_at is not None
                else target.prediction_as_of.isoformat()
            ),
        },
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
        "settlement_key": f"{target.match_id}:{target.prediction_id}",
        "grading_contract": "official_completed_result_visible_after_start_v1",
    }
    record["record_sha256"] = _sha256_json(record)
    return record


def append_archive(
    path: str,
    *,
    records: list[dict[str, Any]],
    summary: dict[str, Any],
) -> dict[str, Any]:
    """Append the frozen capture plus its summary to an operator-owned JSONL file.

    The file is opened with ``O_APPEND`` and never truncated. Each line is a complete
    canonical JSON object. Returns the bytes written and the final file size so the
    caller can independently attest the archive.
    """
    import os
    from pathlib import Path

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        _canonical_json(record) + "\n"
        for record in [*records, {**summary, "record_type": "summary"}]
    ]
    payload = "".join(lines).encode("utf-8")
    fd = os.open(target, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
    try:
        os.write(fd, payload)
        os.fsync(fd)
        size = os.fstat(fd).st_size
    finally:
        os.close(fd)
    return {
        "path": str(target),
        "bytes_appended": len(payload),
        "file_size": size,
        "record_lines": len(lines),
        "append_only": True,
    }


def run(
    conn: psycopg.Connection,
    *,
    run_ids: Iterable[int],
    capture_at: datetime | None = None,
    archive_path: str | None = None,
    require_first_issue: bool = False,
    log: Callable[[str], None] = print,
) -> dict[str, Any]:
    """Capture the PIT-safe bridged A1 counterfactual for already-issued runs."""
    capture_at = capture_at or datetime.now(UTC)
    capture_at = _utc(capture_at, field_name="capture_at")
    conn.execute("SET default_transaction_read_only=on")
    targets = load_targets(conn, run_ids=run_ids, capture_at=capture_at)
    non_first_issue_filtered = 0
    if require_first_issue:
        kept = [target for target in targets if target.is_earliest_issued]
        non_first_issue_filtered = len(targets) - len(kept)
        targets = kept
    summary: dict[str, Any] = {
        "observer_version": VERSION,
        "captured_at": capture_at.isoformat(),
        "run_ids": sorted({int(run_id) for run_id in run_ids if run_id is not None}),
        "targets": len(targets),
        "non_first_issue_filtered": non_first_issue_filtered,
        "require_first_issue": require_first_issue,
        "captured": 0,
        "failed": 0,
        "selection_changes": 0,
        "main_draw_targets": sum(1 for target in targets if target.main_draw_eligible),
        "earliest_issued_targets": sum(
            1 for target in targets if target.is_earliest_issued
        ),
        "scope_counts": {
            scope: sum(
                1
                for target in targets
                if (
                    "ATP_MAIN_MAIN_DRAW"
                    if target.tour_scope == "ATP_MAIN" and not target.qualifying_flag
                    else "CHALLENGER_MAIN_DRAW"
                    if target.tour_scope == "CHALLENGER" and not target.qualifying_flag
                    else "QUALIFYING"
                    if target.qualifying_flag
                    else "OTHER_SCOPE"
                )
                == scope
            )
            for scope in (
                "ATP_MAIN_MAIN_DRAW",
                "CHALLENGER_MAIN_DRAW",
                "QUALIFYING",
                "OTHER_SCOPE",
            )
        },
        "modified_production": False,
        "outcome_included": False,
        "can_promote": False,
    }
    if not targets:
        if archive_path:
            summary["archive"] = append_archive(
                archive_path, records=[], summary=summary
            )
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
    if targets:
        summary["identity_reference"] = {
            "policy_version": targets[0].identity_reference_policy,
            "reference_sha256": targets[0].identity_reference_sha256,
            "row_counts": targets[0].identity_reference_row_counts,
        }
    summary["record_set_sha256"] = hashlib.sha256(
        "\n".join(record["record_sha256"] for record in records).encode("utf-8")
    ).hexdigest()
    if archive_path:
        summary["archive"] = append_archive(
            archive_path, records=records, summary=summary
        )
    log("TENNIS_A1_BRIDGE_FORWARD_SUMMARY " + _canonical_json(summary))
    return summary


def run_discovered(
    conn: psycopg.Connection,
    *,
    since: datetime,
    capture_at: datetime | None = None,
    archive_path: str | None = None,
    require_first_issue: bool = True,
    max_runs: int = 25,
    log: Callable[[str], None] = print,
) -> dict[str, Any]:
    """Capture newly completed runs once, in finalized order.

    Archive idempotency is by summary ``run_ids``: a run is never appended twice to the
    same archive. The observer remains read-only and still writes no prediction,
    decision, Manager, odds, publication or settlement row.
    """
    capture_at = _utc(capture_at or datetime.now(UTC), field_name="capture_at")
    run_ids = discover_completed_runs(conn, since=since, limit=max_runs)
    already = archived_run_ids(archive_path) if archive_path else set()
    pending = [run_id for run_id in run_ids if run_id not in already]
    summaries: list[dict[str, Any]] = []
    for run_id in pending:
        summary = run(
            conn,
            run_ids=[run_id],
            capture_at=capture_at,
            archive_path=archive_path,
            require_first_issue=require_first_issue,
            log=log,
        )
        summaries.append(
            {
                "prediction_run_id": run_id,
                "targets": summary["targets"],
                "captured": summary["captured"],
                "failed": summary["failed"],
                "selection_changes": summary["selection_changes"],
            }
        )
    aggregate = {
        "observer_version": VERSION,
        "captured_at": capture_at.isoformat(),
        "since": _utc(since, field_name="since").isoformat(),
        "discovered_runs": len(run_ids),
        "already_archived_runs": len(run_ids) - len(pending),
        "captured_runs": len(pending),
        "targets": sum(item["targets"] for item in summaries),
        "captured": sum(item["captured"] for item in summaries),
        "failed": sum(item["failed"] for item in summaries),
        "selection_changes": sum(item["selection_changes"] for item in summaries),
        "runs": summaries,
        "modified_production": False,
        "outcome_included": False,
        "can_promote": False,
    }
    log("TENNIS_A1_BRIDGE_FORWARD_CYCLE " + _canonical_json(aggregate))
    return aggregate


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--run-id",
        action="append",
        type=int,
        help="prediction run id; may be repeated",
    )
    parser.add_argument(
        "--discover-since",
        help="discover completed runs finalized at or after this ISO-8601 timestamp",
    )
    parser.add_argument(
        "--max-runs",
        type=int,
        default=25,
        help="maximum runs to discover in one cycle",
    )
    parser.add_argument("--as-of", help="ISO-8601 capture clock; defaults to now UTC")
    parser.add_argument(
        "--archive-jsonl",
        help="append the frozen capture and summary to this operator-owned JSONL file",
    )
    parser.add_argument(
        "--require-first-issue",
        action="store_true",
        help="keep only matches where this run holds the earliest original A1 prediction",
    )
    args = parser.parse_args(argv)
    if not args.run_id and not args.discover_since:
        parser.error("one of --run-id or --discover-since is required")

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
        if args.discover_since:
            run_discovered(
                conn,
                since=datetime.fromisoformat(args.discover_since).astimezone(UTC),
                capture_at=capture_at,
                archive_path=args.archive_jsonl,
                require_first_issue=args.require_first_issue,
                max_runs=args.max_runs,
            )
        else:
            run(
                conn,
                run_ids=args.run_id or [],
                capture_at=capture_at,
                archive_path=args.archive_jsonl,
                require_first_issue=args.require_first_issue,
            )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
