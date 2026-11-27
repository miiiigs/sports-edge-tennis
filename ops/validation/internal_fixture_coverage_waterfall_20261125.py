"""Read-only internal fixture coverage waterfall and 57-target reconciliation.

This does not create an independent official denominator. It executes the
existing licensed-census daily-waterfall logic against stored internal provider
records with completeness explicitly undeclared, and reconciles the archived
forward-target IDs against the original database rows.
"""
from __future__ import annotations

import argparse
import json
import os
from collections import Counter, defaultdict
from datetime import UTC, datetime

import psycopg

from sports_edge_tennis.dashboard.aligned_model_picks import (
    aligned_production_model_picks_cte,
)
from sports_edge_tennis.validation.official_census_waterfall import daily_waterfall

VERSION = "INTERNAL_FIXTURE_COVERAGE_WATERFALL_20261125_V1"
WINDOW_START = datetime.fromisoformat("2026-11-14T00:00:00+00:00")
WINDOW_END = datetime.fromisoformat("2026-11-26T00:00:00+00:00")
AGENTS = (
    "AGENT_1_SURFACE_ELO",
    "AGENT_2_HIERARCHICAL_MARKOV",
    "AGENT_3_INTRANSITIVITY",
)


class InternalManifest:
    """Internal stored-record manifest, explicitly not an independent licence."""

    source_system = "API_TENNIS"
    export_version = "INTERNAL_STORED_RECORDS"
    licence_reference = "AUTHORISED_INTERNAL_READ_ONLY_DB_NOT_AN_EXTERNAL_LICENCE"
    owner_approved_commercial_validation = False
    provider_declares_complete_window = False
    requested_window_start = WINDOW_START
    requested_window_end = WINDOW_END
    report_as_of = max(datetime.now(UTC), WINDOW_END)
    file_sha256 = "0" * 64

    def validate(self) -> None:
        if not self.requested_window_start < self.requested_window_end <= self.report_as_of:
            raise ValueError("invalid internal window")


class InternalReference:
    def __init__(self, row):
        self.source_match_id = str(row["source_match_id"])
        self.tour_scope = row["tour_scope"]
        self.draw_stage = "MAIN" if not row["qualifying_flag"] else "QUALIFYING"
        self.match_type = "SINGLES"
        self.scheduled_start = row["scheduled_start"]
        self.first_published = row["first_published"]
        self._row = row

    def validate(self) -> None:
        if not self.source_match_id.strip():
            raise ValueError("empty source id")
        if self.tour_scope not in ("ATP_MAIN", "CHALLENGER"):
            raise ValueError("out of scope")
        if self.draw_stage not in ("MAIN", "QUALIFYING"):
            raise ValueError("bad draw stage")
        if self.match_type != "SINGLES":
            raise ValueError("bad match type")


def _parse_archive(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as handle:
        payload = json.load(handle)
    candidates = []
    for entry in payload.get("logs", []):
        message = entry.get("message", "")
        prefix = "TENNIS_FROZEN_PROSPECTIVE_CANDIDATE "
        if message.startswith(prefix):
            record = json.loads(message[len(prefix):])
            record["_log_timestamp"] = entry.get("timestamp")
            record["_source_file"] = os.path.basename(path)
            candidates.append(record)
    return candidates


def _daily_stages(conn: psycopg.Connection, *, start: datetime, end: datetime) -> list[dict]:
    rows = conn.execute(
        """
        WITH versions AS (
            SELECT DISTINCT ON (mv.match_id)
                   mv.match_id, mv.match_start_at, mv.tour_scope::text AS tour,
                   mv.qualifying_flag, mv.match_status::text AS match_status,
                   mv.as_of, mv.source_observed_at, mv.ingested_at
            FROM match_versions mv
            WHERE mv.match_start_at >= %(start)s AND mv.match_start_at < %(end)s
            ORDER BY mv.match_id, mv.as_of DESC, mv.source_observed_at DESC,
                     mv.match_version_id DESC
        ), pit AS (
            SELECT v.match_id, bool_or(
                mv.as_of <= v.match_start_at - interval '30 minutes'
                AND mv.source_observed_at <= v.match_start_at - interval '30 minutes'
                AND mv.ingested_at <= v.match_start_at - interval '30 minutes'
            ) AS pit_visible
            FROM versions v
            JOIN match_versions mv ON mv.match_id = v.match_id
            GROUP BY v.match_id
        ), first_run AS (
            SELECT DISTINCT ON (t.match_id)
                   t.match_id, pr.prediction_run_id, pr.prediction_finalized_at AS issued_at
            FROM prediction_run_targets t
            JOIN prediction_runs pr ON pr.prediction_run_id = t.prediction_run_id
            WHERE pr.run_status = 'COMPLETED'
              AND pr.prediction_finalized_at IS NOT NULL
              AND pr.experiment_id IS NULL
              AND pr.prediction_contract_version = 'predictive_agent_result_v1'
            ORDER BY t.match_id, pr.prediction_finalized_at, pr.prediction_run_id
        ), agent AS (
            SELECT fr.match_id,
                   count(*) FILTER (WHERE p.agent_code='AGENT_1_SURFACE_ELO' AND p.prediction_status='PREDICTED') AS a1_pred,
                   count(*) FILTER (WHERE p.agent_code='AGENT_2_HIERARCHICAL_MARKOV' AND p.prediction_status='PREDICTED') AS a2_pred,
                   count(*) FILTER (WHERE p.agent_code='AGENT_3_INTRANSITIVITY' AND p.prediction_status='PREDICTED') AS a3_pred,
                   count(*) FILTER (WHERE p.prediction_status='ABSTAINED') AS abstained
            FROM first_run fr
            LEFT JOIN predictions p ON p.prediction_run_id=fr.prediction_run_id
              AND p.match_id=fr.match_id AND p.experiment_id IS NULL
            GROUP BY fr.match_id
        ), manager AS (
            SELECT DISTINCT ON (mcr.match_id) mcr.match_id,
                   mcr.manager_status::text AS manager_status, mcr.reason_code::text AS reason
            FROM manager_consensus_results mcr
            JOIN first_run fr ON fr.prediction_run_id=mcr.prediction_run_id
              AND fr.match_id=mcr.match_id
            ORDER BY mcr.match_id, mcr.as_of DESC, mcr.manager_consensus_result_id DESC
        ), result AS (
            SELECT DISTINCT ON (match_id) match_id, outcome_status::text AS outcome_status
            FROM match_result_observations
            ORDER BY match_id, as_of DESC, source_observed_at DESC,
                     match_result_observation_id DESC
        ), source AS (
            SELECT DISTINCT ON (match_id) match_id,
                   string_agg(DISTINCT source_system, ',' ORDER BY source_system) AS sources
            FROM match_source_ids
            GROUP BY match_id
        )
        SELECT date_trunc('day', v.match_start_at AT TIME ZONE 'UTC')::date AS day_utc,
               v.tour, v.qualifying_flag,
               count(*) AS stored_matches,
               count(*) FILTER (WHERE pit.pit_visible) AS pit_visible,
               count(*) FILTER (WHERE fr.match_id IS NOT NULL) AS targeted,
               count(*) FILTER (WHERE fr.issued_at <= v.match_start_at - interval '30 minutes') AS targeted_on_time,
               count(*) FILTER (WHERE agent.a1_pred > 0) AS a1_predicted,
               count(*) FILTER (WHERE agent.a2_pred > 0) AS a2_predicted,
               count(*) FILTER (WHERE agent.a3_pred > 0) AS a3_predicted,
               count(*) FILTER (WHERE manager.manager_status='CONSENSUS') AS manager_consensus,
               count(*) FILTER (WHERE result.outcome_status='COMPLETED') AS settled_completed,
               count(*) FILTER (WHERE result.outcome_status='CANCELLED' OR v.match_status='CANCELLED') AS cancelled,
               count(*) FILTER (WHERE result.match_id IS NULL) AS no_result,
               count(*) FILTER (WHERE source.match_id IS NULL) AS no_provider_source_id
        FROM versions v
        LEFT JOIN pit ON pit.match_id=v.match_id
        LEFT JOIN first_run fr ON fr.match_id=v.match_id
        LEFT JOIN agent ON agent.match_id=v.match_id
        LEFT JOIN manager ON manager.match_id=v.match_id
        LEFT JOIN result ON result.match_id=v.match_id
        LEFT JOIN source ON source.match_id=v.match_id
        GROUP BY 1,2,3
        ORDER BY 1,2,3
        """,
        {"start": start, "end": end},
    ).fetchall()
    keys = (
        "day_utc", "tour", "qualifying", "stored_matches", "pit_visible", "targeted",
        "targeted_on_time", "a1_predicted", "a2_predicted", "a3_predicted",
        "manager_consensus", "settled_completed", "cancelled", "no_result",
        "no_provider_source_id",
    )
    out = []
    for row in rows:
        record = dict(zip(keys, row, strict=True))
        record["day_utc"] = record["day_utc"].isoformat()
        out.append(record)
    return out


def _archived_targets(paths: list[str]) -> list[dict]:
    merged = {}
    for path in paths:
        for record in _parse_archive(path):
            mid = int(record["canonical_match_id"])
            prior = merged.get(mid)
            if prior is None:
                merged[mid] = record
            else:
                # Same target can appear in both overlapping exports. Keep the
                # earlier log receipt and verify the payload agrees.
                if prior.get("original_issued_at_utc") != record.get("original_issued_at_utc"):
                    raise ValueError(f"conflicting archived issue time for {mid}")
                if prior.get("_log_timestamp", "") > record.get("_log_timestamp", ""):
                    merged[mid] = record
    return [merged[mid] for mid in sorted(merged)]


def _reconcile_targets(conn: psycopg.Connection, records: list[dict]) -> dict:
    ids = sorted(int(r["canonical_match_id"]) for r in records)
    version_rows = conn.execute(
        """
        SELECT DISTINCT ON (match_id)
               match_id, match_version_id, match_start_at, tour_scope::text,
               qualifying_flag, match_status::text, as_of, source_observed_at, ingested_at
        FROM match_versions
        WHERE match_id = ANY(%s)
        ORDER BY match_id, as_of DESC, source_observed_at DESC, match_version_id DESC
        """,
        (ids,),
    ).fetchall()
    versions = {int(r[0]): r for r in version_rows}
    schedule_counts = dict(
        conn.execute(
            """
            SELECT match_id, count(DISTINCT match_start_at)
            FROM match_versions WHERE match_id = ANY(%s) GROUP BY match_id
            """,
            (ids,),
        ).fetchall()
    )
    result_rows = conn.execute(
        """
        SELECT DISTINCT ON (match_id) match_id, outcome_status::text, winner_side
        FROM match_result_observations
        WHERE match_id = ANY(%s)
        ORDER BY match_id, as_of DESC, source_observed_at DESC,
                 match_result_observation_id DESC
        """,
        (ids,),
    ).fetchall()
    results = {int(r[0]): r for r in result_rows}
    source_rows = conn.execute(
        """
        SELECT match_id, string_agg(DISTINCT source_system, ',' ORDER BY source_system)
        FROM match_source_ids WHERE match_id = ANY(%s) GROUP BY match_id
        """,
        (ids,),
    ).fetchall()
    sources = {int(r[0]): r[1] for r in source_rows}
    first_run_rows = conn.execute(
        """
        SELECT DISTINCT ON (t.match_id)
               t.match_id, pr.prediction_run_id, pr.prediction_finalized_at
        FROM prediction_run_targets t
        JOIN prediction_runs pr ON pr.prediction_run_id=t.prediction_run_id
        WHERE t.match_id = ANY(%s) AND pr.run_status='COMPLETED'
          AND pr.prediction_finalized_at IS NOT NULL AND pr.experiment_id IS NULL
          AND pr.prediction_contract_version='predictive_agent_result_v1'
        ORDER BY t.match_id, pr.prediction_finalized_at, pr.prediction_run_id
        """,
        (ids,),
    ).fetchall()
    first_runs = {int(r[0]): (int(r[1]), r[2]) for r in first_run_rows}
    prediction_rows = conn.execute(
        """
        SELECT prediction_run_id, match_id, agent_code::text,
               prediction_status::text, selection_side, win_probability
        FROM predictions
        WHERE match_id = ANY(%s) AND experiment_id IS NULL
        """,
        (ids,),
    ).fetchall()
    predictions = defaultdict(dict)
    for run_id, mid, agent, status, side, probability in prediction_rows:
        predictions[(int(run_id), int(mid))][agent] = {
            "status": status,
            "side": side,
            "p": float(probability) if probability is not None else None,
        }
    manager_rows = conn.execute(
        """
        SELECT prediction_run_id, match_id, manager_status::text, reason_code::text
        FROM manager_consensus_results WHERE match_id = ANY(%s)
        """,
        (ids,),
    ).fetchall()
    managers = {
        (int(r[0]), int(r[1])): {"status": r[2], "reason": r[3]} for r in manager_rows
    }
    aligned_rows = conn.execute(
        f"""
        WITH {aligned_production_model_picks_cte()}
        SELECT match_id, picked_at, aligned_pick_type
        FROM aligned_model_picks WHERE match_id = ANY(%s)
        """,
        (ids,),
    ).fetchall()
    aligned = {int(r[0]): (r[1], r[2]) for r in aligned_rows}

    details = []
    counts = Counter()
    by_day_tour = defaultdict(Counter)
    for record in records:
        mid = int(record["canonical_match_id"])
        run_id = int(record["first_prediction_run_id"])
        archived_start = datetime.fromisoformat(record["scheduled_start_at_utc"])
        archived_tour = record["tour"]
        version = versions.get(mid)
        result = results.get(mid)
        first_run = first_runs.get(mid)
        current_pred = predictions.get((run_id, mid), {})
        current_manager = managers.get((run_id, mid))
        reasons = []
        status = "MATCHED_ARCHIVE_TO_DB"
        if version is None:
            status = "DISAPPEARED_NO_MATCH_VERSION"
            reasons.append("NO_MATCH_VERSION")
        else:
            current_start = version[2]
            if current_start is None:
                reasons.append("CURRENT_VERSION_NO_START")
            elif current_start != archived_start:
                status = "RESCHEDULED_SINCE_ARCHIVE"
                reasons.append("SCHEDULE_MISMATCH")
            if version[3] != archived_tour:
                status = "TOUR_MISMATCH"
                reasons.append("TOUR_MISMATCH")
        if first_run is None:
            reasons.append("NO_FIRST_RUN")
        elif first_run[0] != run_id:
            reasons.append("FIRST_RUN_ID_CHANGED")
        archived_statuses = record.get("original_agent_statuses", {})
        for agent in AGENTS:
            current = current_pred.get(agent, {})
            if current.get("status") != archived_statuses.get(agent):
                reasons.append(f"AGENT_STATUS_MISMATCH:{agent}")
        archived_manager = record.get("original_manager_status")
        if (current_manager or {}).get("status") != archived_manager:
            reasons.append("MANAGER_STATUS_MISMATCH")
        if result is None:
            reasons.append("NO_RESULT")
        elif result[1] == "CANCELLED":
            reasons.append("CANCELLED_RESULT")
        elif result[1] == "COMPLETED":
            reasons.append("SETTLED_COMPLETED")
        else:
            reasons.append(f"RESULT_{result[1]}")
        if aligned.get(mid):
            reasons.append("ALIGNED_PICK")
        else:
            reasons.append("NO_ALIGNED_PICK")
        if schedule_counts.get(mid, 1) > 1:
            reasons.append("MULTIPLE_SCHEDULE_VERSIONS")
        counts[status] += 1
        by_day_tour[(archived_start.date().isoformat(), archived_tour)][status] += 1
        details.append(
            {
                "match_id": mid,
                "first_prediction_run_id": run_id,
                "archive_source_file": record.get("_source_file"),
                "archive_log_timestamp": record.get("_log_timestamp"),
                "archive_issued_at": record.get("original_issued_at_utc"),
                "archive_scheduled_start": record.get("scheduled_start_at_utc"),
                "archive_tour": archived_tour,
                "archive_agent_statuses": archived_statuses,
                "archive_manager_status": archived_manager,
                "archive_manager_reason": record.get("original_manager_reason"),
                "db_match_version_id": version[1] if version else None,
                "db_scheduled_start": version[2].isoformat() if version and version[2] else None,
                "db_tour": version[3] if version else None,
                "db_qualifying": version[4] if version else None,
                "db_match_status": version[5] if version else None,
                "db_first_run_id": first_run[0] if first_run else None,
                "db_first_run_finalized_at": first_run[1].isoformat() if first_run else None,
                "db_agent_statuses": {
                    agent: current_pred.get(agent, {}).get("status") for agent in AGENTS
                },
                "db_manager_status": (current_manager or {}).get("status"),
                "db_manager_reason": (current_manager or {}).get("reason"),
                "db_result": result[1] if result else None,
                "db_sources": sources.get(mid),
                "db_distinct_schedule_starts": schedule_counts.get(mid),
                "db_aligned_pick_at": aligned[mid][0].isoformat() if aligned.get(mid) else None,
                "db_aligned_pick_type": aligned[mid][1] if aligned.get(mid) else None,
                "classification": status,
                "reasons": reasons,
            }
        )
    return {
        "archived_unique_targets": len(records),
        "classification_counts": dict(sorted(counts.items())),
        "by_day_tour": {f"{d}|{t}": dict(sorted(c.items())) for (d, t), c in sorted(by_day_tour.items())},
        "targets": details,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive-json", action="append", required=True)
    parser.add_argument("--out-json", required=True)
    parser.add_argument("--run-adapter", action="store_true")
    args = parser.parse_args(argv)
    dsn = os.getenv("TENNIS_DATABASE_URL")
    if not dsn:
        parser.error("TENNIS_DATABASE_URL required")

    records = _archived_targets(args.archive_json)
    with psycopg.connect(dsn, autocommit=True, connect_timeout=15) as conn:
        conn.execute("SET default_transaction_read_only=on")
        conn.execute("SET statement_timeout='300s'")
        stages = _daily_stages(conn, start=WINDOW_START, end=WINDOW_END)
        reconciliation = _reconcile_targets(conn, records)

        adapter = None
        if args.run_adapter:
            ref_rows = conn.execute(
                """
                SELECT DISTINCT ON (msi.source_match_id)
                       msi.source_match_id, mv.match_start_at AS scheduled_start,
                       mv.tour_scope::text AS tour_scope, mv.qualifying_flag,
                       (SELECT min(pfm.observed_at)
                        FROM provider_fixture_metadata pfm
                        WHERE pfm.source_system=msi.source_system
                          AND pfm.source_match_id=msi.source_match_id) AS first_published
                FROM match_source_ids msi
                JOIN match_versions mv ON mv.match_id=msi.match_id
                WHERE msi.source_system='API_TENNIS'
                  AND mv.match_start_at >= %(start)s AND mv.match_start_at < %(end)s
                ORDER BY msi.source_match_id, mv.as_of DESC, mv.match_version_id DESC
                """,
                {"start": WINDOW_START, "end": WINDOW_END},
            ).fetchall()
            refs = [
                InternalReference(
                    {
                        "source_match_id": row[0],
                        "scheduled_start": row[1],
                        "tour_scope": row[2],
                        "qualifying_flag": row[3],
                        "first_published": row[4],
                    }
                )
                for row in ref_rows
                if row[1] is not None
            ]
            manifest = InternalManifest()
            aligned_rows = conn.execute(
                f"""
                WITH {aligned_production_model_picks_cte()}
                SELECT match_id, picked_at, aligned_pick_type
                FROM aligned_model_picks WHERE picked_at <= %s
                """,
                (manifest.report_as_of,),
            ).fetchall()
            aligned = {int(r[0]): (r[1], r[2]) for r in aligned_rows}
            # Reuse the existing source-matching adapter for each internal ID.
            from sports_edge_tennis.validation import licensed_fixture_census as census

            internal = {
                ref.source_match_id: census.source_matching(
                    conn, manifest=manifest, ref=ref
                )
                for ref in refs
                if ref.draw_stage == "MAIN"
            }
            adapter = {
                "source_rows": len(refs),
                "source_system": manifest.source_system,
                "provider_declares_complete_window": False,
                "official_denominator_available": False,
                "daily_waterfall": daily_waterfall(manifest, refs, internal, aligned),
            }

    report = {
        "version": VERSION,
        "report_as_of_utc": datetime.now(UTC).isoformat(),
        "window_start_utc": WINDOW_START.isoformat(),
        "window_end_utc": WINDOW_END.isoformat(),
        "internal_coverage_waterfall": stages,
        "forward_target_reconciliation": reconciliation,
        "existing_adapter_internal_execution": adapter,
        "independent_official_denominator": None,
        "official_source_blocker": {
            "status": "BLOCKED_NO_AUTHORISED_INDEPENDENT_OFFICIAL_SOURCE",
            "reason": "No licensed complete ATP/Challenger fixture export or permitted official feed "
                      "was supplied. Internal stored provider records are not an independent denominator.",
            "no_scraping": True,
            "no_provider_calls": True,
        },
        "read_only": True,
        "no_database_writes": True,
    }
    with open(args.out_json, "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, sort_keys=True, default=str)
    print("INTERNAL_COVERAGE_WROTE " + args.out_json)
    print(json.dumps({
        "archived_unique_targets": reconciliation["archived_unique_targets"],
        "classification_counts": reconciliation["classification_counts"],
        "stage_rows": len(stages),
        "adapter_source_rows": adapter["source_rows"] if adapter else None,
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
