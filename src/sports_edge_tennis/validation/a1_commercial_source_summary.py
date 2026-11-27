"""Aggregate the central A1 source census without turning attribution into rights."""
from collections import Counter
from datetime import datetime, timezone
import hashlib

LEDGER_SHA256 = "cbbb0056f82939ccdfde12e8a41de95ac9f2b8e17ab1da23feaf7591f2cdcfa9"
LEDGER_COUNT = 16104
E3_CUTOFF = datetime(2026, 11, 25, 7, 20, tzinfo=timezone.utc)

def verify_frozen_ledger(raw):
    if hashlib.sha256(raw).hexdigest() != LEDGER_SHA256:
        raise ValueError("attribution ledger digest differs from frozen bytes")

def summarize(report):
    rows = report["records"]
    if len(rows) != report["ledger_matches"] or len({r["match_id"] for r in rows}) != len(rows):
        raise ValueError("source census denominator mismatch")
    result = {"ledger_matches": len(rows), "status_counts": dict(Counter(r["status"] for r in rows)),
              "exact_original_row_identity_proven": False, "commercial_rights_confirmed": False,
              "can_promote": False}
    for label, prefix, identifier in (("results", "result", "result_observation_id"),
                                      ("match_versions", "version", "match_version_id")):
        counts = Counter()
        for r in rows:
            if r["status"] == "EXPECTED_HISTORY_AS_OF_MISMATCH":
                bucket = ("CUTOFF_MISMATCH", None, None)
            elif r[identifier] is None:
                bucket = ("ROW_UNAVAILABLE", None, None)
            else:
                bucket = tuple(r.get(prefix + "_ingest_" + k) for k in ("source", "dataset", "version"))
            counts[bucket] += 1
        result[label] = [{"source": s, "dataset": d, "version": v, "matches": n}
                         for (s,d,v),n in sorted(counts.items(), key=lambda p: repr(p[0]))]
    result["identity_untraced_matches"] = sum(r.get("identity_source") == "UNKNOWN_NOT_TRACED" for r in rows)
    return result

# Only references to selected rows, never broad extraction of names or source IDs.
# Identity inventory is a candidate-source inventory, NOT a reconstruction of
# exact historical resolver inputs or a claim of original licensing.
REFERENCE_SQL = """
WITH selected AS (
 SELECT * FROM unnest(%(result_ids)s::bigint[], %(version_ids)s::bigint[]) AS x(rid,vid)
), people AS (
 SELECT DISTINCT player_1_id AS pid FROM match_versions JOIN selected ON match_version_id=vid
 UNION
 SELECT DISTINCT player_2_id FROM match_versions JOIN selected ON match_version_id=vid
), refs AS (
 SELECT 'source_id' AS kind, s.source_system, s.player_id AS pid, s.ingest_run_id
 FROM player_source_ids s JOIN people p ON p.pid=s.player_id
 WHERE s.created_at <= %(cutoff)s
 UNION ALL
 SELECT 'alias', a.source_system, a.player_id, a.ingest_run_id
 FROM player_aliases a JOIN people p ON p.pid=a.player_id
 WHERE a.created_at <= %(cutoff)s AND a.updated_at <= %(cutoff)s
 AND a.first_observed_at <= %(cutoff)s AND a.last_observed_at <= %(cutoff)s
 UNION ALL
 SELECT 'resolved_event', e.source_system, e.resolved_player_id, e.ingest_run_id
 FROM identity_resolution_events e JOIN people p ON p.pid=e.resolved_player_id
 WHERE e.as_of <= %(cutoff)s AND e.source_observed_at <= %(cutoff)s
 AND e.ingested_at <= %(cutoff)s
)
SELECT kind, refs.source_system, i.source_dataset, i.source_version,
 count(*) AS reference_rows, count(DISTINCT pid) AS players,
 count(*) FILTER (WHERE i.ingest_run_id IS NULL) AS missing_ingest_refs
FROM refs LEFT JOIN ingest_runs i USING (ingest_run_id)
GROUP BY kind, refs.source_system, i.source_dataset, i.source_version
ORDER BY kind, refs.source_system, i.source_dataset, i.source_version
"""
MISSING_SQL = """
WITH requested AS (
 SELECT * FROM unnest(%(result_ids)s::bigint[], %(version_ids)s::bigint[]) AS x(rid,vid)
)
SELECT count(*) FILTER (WHERE r.match_result_observation_id IS NOT NULL AND ri.ingest_run_id IS NULL),
 count(*) FILTER (WHERE v.match_version_id IS NOT NULL AND vi.ingest_run_id IS NULL)
FROM requested q
LEFT JOIN match_result_observations r ON r.match_result_observation_id=q.rid
LEFT JOIN ingest_runs ri ON ri.ingest_run_id=r.ingest_run_id
LEFT JOIN match_versions v ON v.match_version_id=q.vid
LEFT JOIN ingest_runs vi ON vi.ingest_run_id=v.ingest_run_id
"""

def reference_inventory(conn, report, cutoff):
    if conn.execute("SHOW transaction_read_only").fetchone()[0] != "on" or conn.execute("SHOW transaction_isolation").fetchone()[0] not in ("repeatable read", "serializable"):
        raise ValueError("read-only repeatable snapshot required")
    rows = [r for r in report["records"] if r["status"] != "EXPECTED_HISTORY_AS_OF_MISMATCH"]
    params = {"result_ids": [r["result_observation_id"] for r in rows],
              "version_ids": [r["match_version_id"] for r in rows], "cutoff": cutoff}
    missing = conn.execute(MISSING_SQL, params).fetchone()
    refs = conn.execute(REFERENCE_SQL, params).fetchall()
    return {"result_missing_ingest_refs": missing[0], "version_missing_ingest_refs": missing[1],
            "identity_reference_groups": [dict(zip(("kind","source_system","dataset","version","reference_rows","players","missing_ingest_refs"), row)) for row in refs],
            "identity_scope": "selected version original player IDs only; PIT-conservative candidate references, not exact consumed resolver inputs",
            "exact_identity_lineage_proven": False, "rating_state_lineage_proven": False,
            "can_promote": False}
