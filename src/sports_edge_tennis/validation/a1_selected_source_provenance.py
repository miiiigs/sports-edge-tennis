"""Read-only source census for an explicitly supplied A1 history attribution ledger.

This traces the selected result and match-version rows using the A1 loader's
ordering and visibility rules, with the additional ingestion-time guard.
It does not resolve identity rights, run a model, or grant commercial clearance.
"""
from collections import Counter
from datetime import datetime, timezone


SOURCE_CENSUS_SQL = """
WITH requested AS (
    SELECT * FROM unnest(%(ids)s::bigint[], %(expected_as_of)s::timestamptz[])
        AS u(match_id, expected_as_of)
)
SELECT q.match_id, q.expected_as_of,
       r.match_result_observation_id, r.as_of,
       ri.source_system, ri.source_dataset, ri.source_version, ri.source_file_hash,
       v.match_version_id, vi.source_system, vi.source_dataset, vi.source_version,
       vi.source_file_hash,
       COALESCE(vs.source_rows, '[]'::jsonb)
FROM requested q
LEFT JOIN LATERAL (
    SELECT mro.*
    FROM match_result_observations mro
    WHERE mro.match_id=q.match_id
      AND mro.source_observed_at <= %(cutoff)s
      AND mro.as_of <= %(cutoff)s
      AND mro.ingested_at <= %(cutoff)s
    ORDER BY mro.as_of DESC, mro.source_observed_at DESC,
             mro.match_result_observation_id DESC
    LIMIT 1
) r ON TRUE
LEFT JOIN ingest_runs ri ON ri.ingest_run_id=r.ingest_run_id
LEFT JOIN LATERAL (
    SELECT mv.*
    FROM match_versions mv
    WHERE mv.match_id=q.match_id
      AND mv.source_observed_at <= %(cutoff)s
      AND mv.as_of <= %(cutoff)s
      AND mv.source_observed_at <= r.as_of
      AND mv.as_of <= r.as_of
      AND mv.ingested_at <= %(cutoff)s
    ORDER BY mv.as_of DESC, mv.source_observed_at DESC, mv.match_version_id DESC
    LIMIT 1
) v ON TRUE
LEFT JOIN ingest_runs vi ON vi.ingest_run_id=v.ingest_run_id
LEFT JOIN LATERAL (
    SELECT jsonb_agg(jsonb_build_object(
        'source_system', s.source_system,
        'source_version_hash', s.source_version_hash,
        'source_record_hash', s.source_record_hash,
        'ingest_run_id', s.ingest_run_id
    ) ORDER BY s.match_version_source_id) AS source_rows
    FROM match_version_sources s
    WHERE s.match_version_id=v.match_version_id
      AND s.source_observed_at <= %(cutoff)s
      AND s.as_of <= %(cutoff)s
      AND s.ingested_at <= %(cutoff)s
) vs ON TRUE
ORDER BY q.match_id
"""


def _aware(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError("history timestamps must be timezone-aware")
    return value.astimezone(timezone.utc)


def audit_selected_history_sources(conn, *, ledger_rows, cutoff):
    """Use one caller-owned REPEATABLE READ, READ ONLY transaction.

    The ledger's expected history_as_of is a consistency check, not a proof
    of exact original row identity: the original ledger lacks result/version IDs.
    No missing or conflicting record inherits another provider's attribution.
    """
    cutoff = _aware(cutoff)
    targets = {}
    for row in ledger_rows:
        match_id = int(row["match_id"])
        if match_id <= 0 or match_id in targets:
            raise ValueError("invalid or duplicate ledger match ID")
        targets[match_id] = _aware(row["history_as_of"])
    if not targets:
        raise ValueError("nonempty attribution ledger required")
    if conn.execute("SHOW transaction_read_only").fetchone()[0] != "on":
        raise ValueError("read-only transaction required")
    if conn.execute("SHOW transaction_isolation").fetchone()[0] not in (
        "repeatable read", "serializable"
    ):
        raise ValueError("repeatable-read transaction required")

    ids = sorted(targets)
    rows = conn.execute(SOURCE_CENSUS_SQL, {
        "ids": ids, "expected_as_of": [targets[i] for i in ids], "cutoff": cutoff,
    }).fetchall()
    if [int(row[0]) for row in rows] != ids:
        raise ValueError("source census failed to reconcile ledger denominator")
    counts = Counter()
    source_groups = Counter()
    records = []
    for row in rows:
        (mid, expected, result_id, result_as_of, result_source, result_dataset,
         result_version, result_hash, version_id, version_source, version_dataset,
         version_version, version_hash, version_source_rows) = row
        if _aware(expected) != targets[int(mid)]:
            raise ValueError("ledger timestamp altered by census")
        if result_id is None:
            status = "RESULT_UNAVAILABLE"
        elif result_as_of != expected:
            status = "EXPECTED_HISTORY_AS_OF_MISMATCH"
        elif version_id is None:
            status = "VERSION_UNAVAILABLE"
        elif not result_source or not version_source:
            status = "SOURCE_UNKNOWN"
        else:
            status = "SELECTED_ROWS_SOURCE_ATTRIBUTED"
        counts[status] += 1
        if status == "SELECTED_ROWS_SOURCE_ATTRIBUTED":
            source_groups[(result_source, version_source)] += 1
        records.append({
            "match_id": int(mid),
            "expected_history_as_of": expected.isoformat(),
            "selected_result_as_of": result_as_of.isoformat() if result_as_of else None,
            "result_observation_id": result_id,
            "match_version_id": version_id,
            "result_ingest_source": result_source,
            "result_ingest_dataset": result_dataset,
            "result_ingest_version": result_version,
            "result_ingest_file_hash": result_hash,
            "version_ingest_source": version_source,
            "version_ingest_dataset": version_dataset,
            "version_ingest_version": version_version,
            "version_ingest_file_hash": version_hash,
            "match_version_source_rows": version_source_rows,
            "identity_source": "UNKNOWN_NOT_TRACED",
            "status": status,
        })
    return {
        "cutoff": cutoff.isoformat(),
        "ledger_matches": len(ids),
        "status_counts": dict(sorted(counts.items())),
        "attributed_groups": [
            {"result_source": r, "version_source": v, "matches": n}
            for (r, v), n in sorted(source_groups.items())
        ],
        "records": records,
        "exact_original_row_identity_proven": False,
        "identity_source_complete": False,
        "commercial_rights_confirmed": False,
        "can_promote": False,
    }
