"""Inactive adapter from persisted canonical official result observations.

No provider polling and no bookmaker grading. Callers must independently
approve a source/dataset and its canonical winner-side orientation contract.
The scheduled-start guard is conservative; it is not proof of actual start.
"""
import json

from sports_edge_tennis.validation.forward_evidence_outbox import (
    EvidenceConflict, _require_idle, digest,
)
from sports_edge_tennis.validation.forward_evidence_settlement import (
    OUTCOMES, append_settlement, sporting_grade,
)
from sports_edge_tennis.validation.forward_receipt_store import _instant

RESULT_SQL = """
SELECT r.match_result_observation_id, r.outcome_status::text, r.winner_side,
       r.as_of, r.source_observed_at, r.ingested_at,
       i.source_system, i.source_dataset, i.source_version,
       v.match_version_id, v.player_1_id, v.player_2_id
FROM match_result_observations r
LEFT JOIN ingest_runs i USING (ingest_run_id)
LEFT JOIN LATERAL (
    SELECT mv.match_version_id, mv.player_1_id, mv.player_2_id
    FROM match_versions mv
    WHERE mv.match_id=r.match_id
      AND mv.as_of<=r.as_of AND mv.source_observed_at<=r.source_observed_at
      AND mv.ingested_at<=r.ingested_at
    ORDER BY mv.as_of DESC, mv.source_observed_at DESC, mv.match_version_id DESC
    LIMIT 1
) v ON TRUE
WHERE r.match_id=%s AND r.as_of<=%s
  AND r.source_observed_at<=%s AND r.ingested_at<=%s
ORDER BY r.as_of DESC,r.source_observed_at DESC,r.match_result_observation_id DESC
LIMIT 1
"""


def settle_stored_official_result(conn, version, match_id, *, cutoff,
                                  approved_sources):
    """Settle a single existing original, using the latest visible result.

    approved_sources maps (source_system, source_dataset) to a reviewed
    evidence reference explicitly covering official authority and canonical
    participant ordering. Never fall back to an older result when the latest
    observation is unapproved or inconsistent. No market outcome is inferred.
    """
    _require_idle(conn)
    if not approved_sources:
        raise EvidenceConflict("approved official source contract required")
    if cutoff.utcoffset() is None:
        raise ValueError("timezone-aware cutoff required")
    with conn.transaction():
        # The same original lock serializes all revisions with append_settlement.
        original = conn.execute(
            """SELECT payload,payload_sha256 FROM forward_originals
               WHERE experiment_version=%s AND match_id=%s FOR UPDATE""",
            (version, match_id),
        ).fetchone()
        if original is None or digest(bytes(original[0])) != original[1]:
            raise EvidenceConflict("missing or damaged original")
        payload = bytes(original[0])
        envelope = json.loads(payload)
        if (envelope.get("classification") == "SYNTHETIC_REHEARSAL_ONLY"
                or envelope.get("experiment_version") != version
                or envelope.get("canonical_match_id") != match_id):
            raise EvidenceConflict("original not eligible for official adapter")
        if cutoff > conn.execute("SELECT clock_timestamp()").fetchone()[0]:
            raise ValueError("future settlement cutoff")
        result = conn.execute(RESULT_SQL, (match_id, cutoff, cutoff, cutoff)).fetchone()
        if result is None:
            return {"status": "NO_VISIBLE_RESULT", "settlement_id": None}
        (rid, kind, side, as_of, observed, ingested, source, dataset,
         source_version, version_id, p1, p2) = result
        reference = approved_sources.get((source, dataset))
        if not isinstance(reference, str) or not reference.strip():
            raise EvidenceConflict("latest result source not approved")
        if kind not in OUTCOMES or version_id is None or p1 == p2:
            raise EvidenceConflict("unsupported or unoriented result")
        if side not in (1, 2, None):
            raise EvidenceConflict("invalid canonical result side")
        issued = _instant(envelope["original_issued_at_utc"])
        start = _instant(envelope["scheduled_start_at_utc"])
        # Cancellation may legitimately be announced before scheduled start,
        # but never grade a forecast from an earlier-known result.
        minimum = issued if kind in ("CANCELLED", "WALKOVER", "UNRESOLVED") else max(issued, start)
        if observed < minimum or max(as_of, observed, ingested) > cutoff:
            raise EvidenceConflict("result violates original timing boundary")
        winner = (p1 if side == 1 else p2 if side == 2 else None)
        grade = sporting_grade(payload, outcome_kind=kind,
                               source_participants=[p1,p2], winner_player_id=winner)
        evidence = {
            "outcome_kind": kind, "source_participants": [p1,p2],
            "winner_player_id": winner, "source_reference": reference,
            "adapter": "stored_official_result_v1", "result_observation_id": rid,
            "match_version_id": version_id, "source_system": source,
            "source_dataset": dataset, "source_version": source_version,
            "result_as_of": as_of.isoformat(), "source_observed_at": observed.isoformat(),
            "ingested_at": ingested.isoformat(),
            "actual_start_verified": False,
        }
        raw = json.dumps(evidence, sort_keys=True, separators=(",", ":")).encode()
        key = f"stored-official:{rid}:{version_id}"
        previous = conn.execute(
            """SELECT settlement_id,evidence_key,evidence,supersedes_id
               FROM forward_settlement_events WHERE experiment_version=%s
               AND match_id=%s AND pathway='SPORTING'
               AND market_key='official_match_winner'
               ORDER BY settlement_id DESC LIMIT 1""", (version, match_id),
        ).fetchone()
        predecessor = None
        if previous:
            if previous[1] == key:
                predecessor = previous[3]
            else:
                old = json.loads(bytes(previous[2]))
                if old.get("adapter") != "stored_official_result_v1":
                    raise EvidenceConflict("manual predecessor requires reviewed reconciliation")
                old_order = (_instant(old["result_as_of"]),
                             _instant(old["source_observed_at"]),
                             old["result_observation_id"])
                if (as_of, observed, rid) <= old_order:
                    raise EvidenceConflict("stale or conflicting result revision")
                predecessor = previous[0]
        settlement_id = append_settlement(
            conn, version, match_id, pathway="SPORTING",
            market_key="official_match_winner", evidence_key=key,
            evidence_bytes=raw, supersedes_id=predecessor)
        return {"status": grade, "settlement_id": settlement_id,
                "result_observation_id": rid, "can_promote": False,
                "actual_start_verified": False}
