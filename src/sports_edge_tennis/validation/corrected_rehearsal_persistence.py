"""Atomic B/C/D evidence persistence for isolated synthetic rehearsals only.

Uses the existing PostgreSQL original/outbox tables. The synthetic namespace
is intentionally distinct from all formal prospective experiment identifiers.
No production runner, provider or external transport is called here.
"""
import json
from datetime import timedelta

from sports_edge_tennis.validation.corrected_candidate_rehearsal import (
    rehearse_corrected_candidates,
)
from sports_edge_tennis.validation.forward_evidence_outbox import (
    EvidenceConflict, _freeze, _original, _require_idle, digest,
)
from sports_edge_tennis.validation.prospective_policy_registry import POLICIES

REHEARSAL_RUN = "synthetic-corrected-rehearsal-v1"
PREFIX = REHEARSAL_RUN + ":"


def _bytes(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode()


def persist_corrected_rehearsal(conn, *, run_id, match_id, request,
                                scheduled_start, history, identity_map,
                                other_agents):
    """Execute and atomically persist three explicitly synthetic decisions.

    The anchor must be an existing completed synthetic research run with a
    matching target and cutoff. It is not a claim that B/C/D ran in that
    original runner. The payload retains the real rehearsal completion clock.
    Exact retry is idempotent; changed input or a partial set fails closed.
    """
    _require_idle(conn)
    with conn.transaction():
        anchor = conn.execute(
            """SELECT r.as_of,r.experiment_id,r.run_status
               FROM prediction_runs r JOIN prediction_run_targets t
               USING (prediction_run_id)
               WHERE r.prediction_run_id=%s AND t.match_id=%s FOR UPDATE OF r""",
            (run_id, match_id),
        ).fetchone()
        if anchor != (request.as_of, REHEARSAL_RUN, "COMPLETED"):
            raise EvidenceConflict("completed synthetic rehearsal anchor required")
        fixture = conn.execute(
            """SELECT to_jsonb(v)::text FROM match_versions v
               WHERE match_id=%s AND as_of<=%s AND source_observed_at<=%s
                 AND ingested_at<=%s
               ORDER BY as_of DESC,source_observed_at DESC,match_version_id DESC
               LIMIT 1""",
            (match_id, request.as_of, request.as_of, request.as_of),
        ).fetchone()
        if fixture is None:
            raise EvidenceConflict("missing cutoff-visible fixture")
        from sports_edge_tennis.validation.forward_receipt_store import _instant
        actual = json.loads(fixture[0])
        if ((actual["player_1_id"], actual["player_2_id"]) !=
                (request.player_1_id, request.player_2_id)
                or _instant(actual["match_start_at"]) != scheduled_start
                or actual["tour_scope"] != request.tour_scope
                or actual["competition_tier"] != request.competition_tier
                or actual["surface"] != request.surface
                or actual["best_of_sets"] != request.best_of_sets
                or actual["qualifying_flag"] != request.qualifying_flag
                or actual["match_status"] != "SCHEDULED"):
            raise EvidenceConflict("fixture orientation or feature mismatch")

        result = rehearse_corrected_candidates(
            match_id=match_id, request=request, scheduled_start=scheduled_start,
            history=history, identity_map=identity_map, other_agents=other_agents)
        versions = [PREFIX + POLICIES[c].experiment_id for c in ("B", "C", "D")]
        prior = conn.execute(
            """SELECT experiment_version,issued_at,payload FROM forward_originals
               WHERE match_id=%s AND experiment_version=ANY(%s)
               ORDER BY experiment_version""", (match_id, versions),
        ).fetchall()
        if prior and (len(prior) != 3 or len({p[1] for p in prior}) != 1):
            raise EvidenceConflict("partial or inconsistent rehearsal set")
        issued = prior[0][1] if prior else conn.execute(
            "SELECT clock_timestamp()").fetchone()[0]
        if not request.as_of <= issued <= scheduled_start - timedelta(minutes=30):
            raise EvidenceConflict("rehearsal completion missed cutoff")
        feature = _bytes(result).decode()
        for candidate, version in zip(("B", "C", "D"), versions):
            policy = POLICIES[candidate]
            freeze = _bytes({
                "candidate": candidate, "classification": "SYNTHETIC_REHEARSAL_ONLY",
                "model_versions": {"A1": result["model_version"]},
                "configuration_versions": {"A1": result["configuration_sha256"]},
                "model_source_sha256": result["model_source_sha256"],
                "policy_source_sha256": result["policy_source_sha256"],
                "eligibility_policy": "atp_challenger_main_draw_30m_v1",
                "selection_policy": policy.selection_policy,
                "threshold": policy.threshold,
                "other_agents_executed": False,
            })
            payload = _bytes({
                "schema": "forward_original_v1", "classification": "SYNTHETIC_REHEARSAL_ONLY",
                "experiment_version": version, "canonical_match_id": match_id,
                "first_prediction_run_id": run_id, "candidate": candidate,
                "candidate_decision": result["decisions"][candidate],
                "original_issued_at_utc": issued.isoformat(),
                "scheduled_start_at_utc": scheduled_start.isoformat(),
                "tour": request.tour_scope, "fixture_json": fixture[0],
                "feature_snapshot_json": feature,
                "feature_sha256": digest(feature.encode()),
                "freeze_json": freeze.decode(), "freeze_sha256": digest(freeze),
                "independent_receipt_verified": False,
                "eligible_for_prospective_evidence": False,
                "not_a_published_model_pick": True, "can_promote": False,
            })
            _freeze(conn, version, freeze)
            _original(conn, version, match_id, run_id, payload, issued)
        return {"versions": versions, "candidate_count": 3,
                "synthetic_only": True, "can_promote": False,
                "evidence_sha256": result["evidence_sha256"]}
