"""Inactive PostgreSQL producer and delivery journal for forward evidence.

Use a fresh autocommit connection to make the enclosing transaction the commit
boundary. No production runner or external publisher calls this module.
"""
from __future__ import annotations

import hashlib
import json
from datetime import timedelta

from sports_edge_tennis.runner.full_slate import run_full_slate


def digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


class EvidenceConflict(RuntimeError):
    pass


def _freeze(conn, version, freeze):
    if not isinstance(freeze, bytes) or not freeze or not version.strip():
        raise ValueError("explicit frozen experiment bytes required")
    parsed = json.loads(freeze)
    for field in ("model_versions", "configuration_versions", "eligibility_policy",
                  "selection_policy", "candidate"):
        if not isinstance(parsed, dict) or not parsed.get(field):
            raise ValueError("missing freeze field: " + field)
    conn.execute(
        """INSERT INTO forward_experiment_freezes VALUES (%s,%s,%s)
           ON CONFLICT DO NOTHING""", (version, freeze, digest(freeze)),
    )
    prior = conn.execute(
        "SELECT freeze_bytes FROM forward_experiment_freezes WHERE experiment_version=%s",
        (version,),
    ).fetchone()
    if bytes(prior[0]) != freeze:
        raise EvidenceConflict("experiment freeze changed")


def _append(conn, version, match_id, channel, state, evidence=None):
    conn.execute(
        """INSERT INTO forward_delivery_events
           (experiment_version,match_id,channel,state,evidence) VALUES (%s,%s,%s,%s,%s)""",
        (version, match_id, channel, state, evidence),
    )


def _original(conn, version, match_id, run_id, payload, issued):
    result = conn.execute(
        """INSERT INTO forward_originals VALUES (%s,%s,%s,%s,%s,%s)
           ON CONFLICT DO NOTHING RETURNING match_id""",
        (version, match_id, run_id, payload, digest(payload), issued),
    ).fetchone()
    if result is None:
        previous = conn.execute(
            """SELECT prediction_run_id,payload,issued_at FROM forward_originals
               WHERE experiment_version=%s AND match_id=%s""", (version, match_id),
        ).fetchone()
        if previous != (run_id, payload, issued):
            raise EvidenceConflict("conflicting original forecast")
        return
    _append(conn, version, match_id, "ARCHIVE", "PENDING")


def run_with_originals(conn, request, *, freeze_bytes: bytes):
    """Opt-in rehearsal wrapper; commit matrix, feature lineage, Manager and
    immutable envelope together. Not a production or corrected-model runner.

    Capture happens only at the end of a successful transaction; no backfill
    of previously committed predictions. A duplicate experiment/fixture fails
    closed rather than changing the original or issuing a second forecast.
    """
    if not conn.autocommit or not request.experiment_id or not request.persist_identity_resolutions:
        raise ValueError("fresh autocommit connection and explicit research provenance required")
    version = request.experiment_id
    with conn.transaction():
        _freeze(conn, version, freeze_bytes)
        outcome = run_full_slate(conn, request)
        if outcome.runner_status not in ("COMPLETED", "EMPTY_SLATE"):
            raise EvidenceConflict("runner did not complete: " + outcome.runner_status)
        run_id = outcome.prediction_run_id
        for match_id in request.match_ids:
            row = conn.execute(
                """SELECT mv.match_start_at, mv.tour_scope::text, mv.qualifying_flag,
                          to_jsonb(mv)::text, to_jsonb(fs)::text
                   FROM prediction_feature_snapshots fs
                   JOIN match_versions mv ON mv.match_version_id=fs.match_version_id
                   WHERE fs.prediction_run_id=%s AND fs.match_id=%s
                     AND fs.agent_code='AGENT_1_SURFACE_ELO'""", (run_id, match_id),
            ).fetchone()
            if row is None:
                raise EvidenceConflict("missing original feature snapshot")
            start, tour, qualifying, fixture, features = row
            issued = conn.execute("SELECT clock_timestamp()").fetchone()[0]
            if tour not in ("ATP_MAIN", "CHALLENGER") or qualifying or issued > start - timedelta(minutes=30):
                raise EvidenceConflict("out of scope or missed issuance deadline")
            agents = conn.execute(
                """SELECT to_jsonb(p)::text, to_jsonb(c)::text FROM predictions p
                   JOIN agent_config_versions c USING (agent_config_version_id)
                   WHERE p.prediction_run_id=%s AND p.match_id=%s ORDER BY p.agent_code""",
                (run_id, match_id),
            ).fetchall()
            manager = conn.execute(
                """SELECT to_jsonb(m)::text, to_jsonb(v)::text
                   FROM manager_consensus_results m JOIN manager_policy_versions v
                   USING (manager_policy_version_id)
                   WHERE m.prediction_run_id=%s AND m.match_id=%s""", (run_id, match_id),
            ).fetchone()
            identities = conn.execute(
                """SELECT to_jsonb(i)::text FROM identity_resolution_provenance i
                   WHERE i.prediction_run_id=%s AND i.match_id=%s ORDER BY i.original_player_id""",
                (run_id, match_id),
            ).fetchall()
            if len(agents) != 3 or manager is None or len(identities) != 2:
                raise EvidenceConflict("incomplete agent, Manager or identity evidence")
            # PostgreSQL JSON text is retained as text, not parsed into binary floats.
            record = {
                "schema": "forward_original_v1", "experiment_version": version,
                "canonical_match_id": match_id, "first_prediction_run_id": run_id,
                "original_issued_at_utc": issued.isoformat(),
                "scheduled_start_at_utc": start.isoformat(), "tour": tour,
                "fixture_json": fixture, "feature_snapshot_json": features,
                "feature_sha256": digest(features.encode()),
                "agents_json": agents, "manager_json": manager,
                "identity_provenance_json": [i[0] for i in identities],
                "freeze_json": freeze_bytes.decode(), "freeze_sha256": digest(freeze_bytes),
                "independent_receipt_verified": False, "not_a_published_model_pick": True,
            }
            payload = json.dumps(record, sort_keys=True, separators=(",", ":"),
                                 allow_nan=False).encode()
            _original(conn, version, match_id, run_id, payload, issued)
    return outcome


def _lock_and_read(conn, version, match_id, channel):
    row = conn.execute(
        """SELECT payload,payload_sha256 FROM forward_originals
           WHERE experiment_version=%s AND match_id=%s FOR UPDATE""",
        (version, match_id),
    ).fetchone()
    if row is None:
        raise EvidenceConflict("unknown original")
    last = conn.execute(
        """SELECT state FROM forward_delivery_events WHERE experiment_version=%s
           AND match_id=%s AND channel=%s ORDER BY event_id DESC LIMIT 1""",
        (version, match_id, channel),
    ).fetchone()
    return bytes(row[0]), row[1], last[0] if last else None


def claim_delivery(conn, version, match_id, channel):
    """Commit the uncertainty marker BEFORE invoking any transport.
    Archive is idempotent by exact bytes; Telegram is not.
    No Telegram event is automatically enqueued by the shadow runner.
    """
    if not conn.autocommit or channel not in ("ARCHIVE", "TELEGRAM"):
        raise ValueError("fresh autocommit connection and supported channel required")
    with conn.transaction():
        payload, sha, state = _lock_and_read(conn, version, match_id, channel)
        if state != "PENDING" and not (channel == "ARCHIVE" and state == "DELIVERY_UNCERTAIN"):
            return None
        if digest(payload) != sha:
            raise EvidenceConflict("original digest mismatch")
        _append(conn, version, match_id, channel, "DELIVERY_UNCERTAIN")
    return payload, sha


def confirm_delivery(conn, version, match_id, channel, *, state, evidence):
    """Evidence is an externally verified message ID/receipt or definite rejection.
    Ambiguous transport exceptions must leave the claim uncertain.
    """
    if not conn.autocommit or state not in ("DELIVERED", "CONFIRMED_FAILURE", "RECONCILED"):
        raise ValueError("unsupported confirmation")
    if not isinstance(evidence, str) or not evidence.strip():
        raise ValueError("confirmation evidence required")
    with conn.transaction():
        _, _, previous = _lock_and_read(conn, version, match_id, channel)
        if previous != "DELIVERY_UNCERTAIN":
            raise EvidenceConflict("no outstanding uncertainty to reconcile")
        _append(conn, version, match_id, channel, state, evidence)
