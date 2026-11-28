"""Run B/C/D against stored, isolated runner inputs rather than injected agents.

Only an explicitly synthetic anchor is accepted. Reads one consistent DB
snapshot, hashes exact inputs, runs existing frozen policies, and stores local
originals in the existing synthetic namespace. No provider calls, production
publication, or assertion of commercial rights. The subsequent persistence
transaction rechecks the anchor and cutoff-visible fixture.
"""
import json
from dataclasses import asdict
from datetime import timedelta

from sports_edge_tennis.agents.agent1_surface_elo import PredictionRequest
from sports_edge_tennis.data.agent1_history import load_agent1_history
from sports_edge_tennis.data.identity_provenance import resolve_with_provenance
from sports_edge_tennis.validation import corrected_rehearsal_persistence as persistence
from sports_edge_tennis.validation.forward_evidence_outbox import EvidenceConflict, _require_idle, digest
from sports_edge_tennis.validation.forward_receipt_store import _instant
from sports_edge_tennis.validation.manager_abc_first_issue_ablation import CODES


def _hash(value):
    return digest(json.dumps(value, sort_keys=True, separators=(",", ":"),
                             default=str, allow_nan=False).encode())


def run_isolated_corrected_from_database(conn, *, run_id, match_id):
    _require_idle(conn)
    with conn.transaction():
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        row = conn.execute(
            """SELECT r.as_of,r.prediction_finalized_at,r.experiment_id,r.run_status
               FROM prediction_runs r JOIN prediction_run_targets t USING(prediction_run_id)
               WHERE r.prediction_run_id=%s AND t.match_id=%s""", (run_id,match_id)
        ).fetchone()
        if row is None or row[2:] != (persistence.REHEARSAL_RUN, "COMPLETED"):
            raise EvidenceConflict("isolated completed synthetic anchor required")
        cutoff, finalized = row[:2]
        fixture_row = conn.execute(
            """SELECT to_jsonb(v)::text FROM match_versions v WHERE match_id=%s
               AND as_of<=%s AND source_observed_at<=%s AND ingested_at<=%s
               ORDER BY as_of DESC,source_observed_at DESC,match_version_id DESC LIMIT 1""",
            (match_id,cutoff,cutoff,cutoff)).fetchone()
        if fixture_row is None:
            raise EvidenceConflict("missing isolated visible fixture")
        f = json.loads(fixture_row[0])
        start = _instant(f["match_start_at"])
        now = conn.execute("SELECT clock_timestamp()").fetchone()[0]
        if (f["match_status"] != "SCHEDULED" or finalized is None or
                not cutoff <= finalized <= now <= start-timedelta(minutes=30)):
            raise EvidenceConflict("isolated runner or evidence timing invalid")
        # Bind all three actual persisted agent outputs to the same run/cutoff.
        agent_rows = conn.execute(
            """SELECT to_jsonb(p)::text,to_jsonb(c)::text FROM predictions p
               JOIN agent_config_versions c USING(agent_config_version_id)
               WHERE prediction_run_id=%s AND match_id=%s ORDER BY p.agent_code""",
            (run_id,match_id)).fetchall()
        agents = {}
        for payload, config in agent_rows:
            p = json.loads(payload)
            c = json.loads(config)
            code = p["agent_code"]
            if (code in agents or p["experiment_id"] != persistence.REHEARSAL_RUN
                    or _instant(p["as_of"]) != cutoff
                    or not cutoff <= _instant(p["prediction_locked_at"]) <= finalized
                    or p["model_version"] != c["model_version"]
                    or p["feature_version"] != c["feature_version"]):
                raise EvidenceConflict("agent lineage or lock mismatch")
            agents[code] = p
        if set(agents) != set(CODES):
            raise EvidenceConflict("actual three-agent matrix required")
        p1,p2 = f["player_1_id"],f["player_2_id"]
        request = PredictionRequest(p1,p2,f["surface"],f["tour_scope"],
            f["competition_tier"],f["best_of_sets"],f["qualifying_flag"],cutoff)
        history = load_agent1_history(conn,cutoff=cutoff,ingested_by=cutoff)
        ids = {p1,p2}
        for h in history:
            ids.update((h.player_1_id,h.player_2_id))
        resolved, reference = resolve_with_provenance(conn,player_ids=ids,as_of=cutoff)
        mapping = {pid: item.resolution.resolved_player_id for pid,item in resolved.items()}
        # Confirm equality with the actual corrected history loader, including
        # ordering, not just aggregate historical counts.
        corrected = load_agent1_history(conn,cutoff=cutoff,ingested_by=cutoff,
                                        bridge_model_identities=True)
        from dataclasses import replace
        expected = []
        for h in history:
            x,y = mapping[h.player_1_id],mapping[h.player_2_id]
            if x == y:
                raise EvidenceConflict("history identity collision")
            expected.append(replace(h,player_1_id=x,player_2_id=y))
        if expected != corrected:
            raise EvidenceConflict("corrected history loader parity mismatch")
        other = {code:(agents[code]["prediction_status"],agents[code]["selection_side"],
                       agents[code]["win_probability"]) for code in CODES[1:]}
        evidence = {
            "classification":"ISOLATED_DATABASE_RUNTIME_ONLY",
            "run_id":run_id,"match_id":match_id,
            "agent_prediction_ids":{code:p["prediction_id"] for code,p in agents.items()},
            "agent_payloads_sha256":_hash(agent_rows),
            "original_fixture_sha256":digest(fixture_row[0].encode()),
            "history_rows":len(history),
            "corrected_history_sha256":_hash([asdict(h) for h in corrected]),
            "identity_reference_sha256":reference.sha256,
            "actual_persisted_specialist_inputs":True,
            "corrected_history_loader_parity":True,
            "independent_receipt_verified":False,
            "can_promote":False,
        }
    return persistence.persist_corrected_rehearsal(
        conn,run_id=run_id,match_id=match_id,request=request,scheduled_start=start,
        history=history,identity_map=mapping,other_agents=other,runtime_evidence=evidence)
