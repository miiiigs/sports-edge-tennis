"""Read-only root-cause evidence for the 14-16 November 2026 deadline gap.

Reproduces the 53 stored main-draw fixtures with no pre-match-visible version and
no prediction target, then traces the ingestion runs, capture-health heartbeats,
prediction runs, first versions and results that explain the gap. It also
reports reschedule traceability and separates Manager consensus, tracking-only,
published Model Picks and settlement populations.

No writes, migrations, provider calls or deployment.
"""
from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from datetime import UTC, datetime, timedelta

import psycopg

from sports_edge_tennis.dashboard.aligned_model_picks import (
    aligned_production_model_picks_cte,
)
from sports_edge_tennis.validation.fixture_lifecycle import (
    MatchVersionObservation,
    build_fixture_timeline,
    detect_capture_gaps,
    reconcile_selection_populations,
)

VERSION = "TENNIS_FIXTURE_DISCOVERY_ROOT_CAUSE_20261125_V1"
WINDOW_START = datetime(2026, 11, 14, tzinfo=UTC)
WINDOW_END = datetime(2026, 11, 26, tzinfo=UTC)
GAP_END = datetime(2026, 11, 17, tzinfo=UTC)


def _dicts(conn, sql, params=None):
    cur = conn.execute(sql, params or {})
    names = [d.name for d in cur.description]
    return [dict(zip(names, row, strict=True)) for row in cur.fetchall()]


def _cohort(conn):
    return _dicts(
        conn,
        """
        WITH latest AS (
          SELECT DISTINCT ON (mv.match_id)
                 mv.match_id, mv.match_start_at, mv.tour_scope::text AS tour,
                 mv.qualifying_flag, mv.match_status::text AS status,
                 mv.as_of, mv.source_observed_at, mv.ingested_at
          FROM match_versions mv
          ORDER BY mv.match_id, mv.as_of DESC, mv.source_observed_at DESC,
                   mv.match_version_id DESC
        ), pit AS (
          SELECT l.match_id, bool_or(
                   mv.as_of <= l.match_start_at - interval '30 minutes'
                   AND mv.source_observed_at <= l.match_start_at - interval '30 minutes'
                   AND mv.ingested_at <= l.match_start_at - interval '30 minutes'
                 ) AS pit_visible
          FROM latest l JOIN match_versions mv ON mv.match_id = l.match_id
          GROUP BY l.match_id
        ), cohort AS (
          SELECT l.*, p.pit_visible
          FROM latest l JOIN pit p USING (match_id)
          WHERE l.match_start_at >= %(start)s AND l.match_start_at < %(end)s
            AND l.qualifying_flag = FALSE AND NOT p.pit_visible
        )
        SELECT c.match_id, c.match_start_at AS latest_start, c.tour,
               c.status AS latest_status, c.as_of AS latest_as_of,
               c.source_observed_at AS latest_observed, c.ingested_at AS latest_ingested,
               f.match_start_at AS first_version_start,
               f.match_status::text AS first_version_status,
               f.source_observed_at AS first_observed,
               f.ingested_at AS first_ingested,
               f.ingest_run_id AS first_ingest_run,
               v.versions, v.starts,
               EXISTS (
                 SELECT 1 FROM prediction_run_targets t WHERE t.match_id = c.match_id
               ) AS ever_targeted,
               r.outcome,
               r.source_observed_at AS result_observed
        FROM cohort c
        JOIN LATERAL (
          SELECT mv.match_start_at, mv.match_status, mv.source_observed_at,
                 mv.ingested_at, mv.ingest_run_id
          FROM match_versions mv
          WHERE mv.match_id = c.match_id
          ORDER BY mv.ingested_at, mv.as_of, mv.match_version_id
          LIMIT 1
        ) f ON TRUE
        JOIN LATERAL (
          SELECT count(*) AS versions, count(DISTINCT mv.match_start_at) AS starts
          FROM match_versions mv WHERE mv.match_id = c.match_id
        ) v ON TRUE
        LEFT JOIN LATERAL (
          SELECT outcome_status::text AS outcome, source_observed_at
          FROM match_result_observations mro
          WHERE mro.match_id = c.match_id
          ORDER BY mro.as_of DESC, mro.match_result_observation_id DESC
          LIMIT 1
        ) r ON TRUE
        ORDER BY c.match_start_at, c.match_id
        """,
        {"start": WINDOW_START, "end": GAP_END},
    )


def _first_runs(conn, run_ids):
    if not run_ids:
        return []
    return _dicts(
        conn,
        """
        SELECT ingest_run_id, source_system, source_dataset, started_at, finished_at,
               status::text, source_version, rows_seen, rows_inserted,
               rows_rejected, rows_unmatched,
               left(error_summary::text, 2000) AS error_summary
        FROM ingest_runs WHERE ingest_run_id = ANY(%s)
        ORDER BY ingest_run_id
        """,
        (list(run_ids),),
    )


def _run_calendar(conn):
    return _dicts(
        conn,
        """
        SELECT date_trunc('day', started_at AT TIME ZONE 'UTC')::date AS day_utc,
               source_dataset, status::text,
               count(*) AS runs, sum(rows_seen) AS seen,
               sum(rows_inserted) AS inserted, sum(rows_rejected) AS rejected
        FROM ingest_runs
        WHERE started_at >= '2026-11-10T00:00:00Z'
          AND started_at < '2026-11-19T00:00:00Z'
        GROUP BY 1, 2, 3 ORDER BY 1, 2, 3
        """,
    )


def _health(conn):
    rows = _dicts(
        conn,
        """
        SELECT data_health_check_id, checked_at, latest_observation_at,
               freshness_state::text, error_count, status::text, notes
        FROM data_health_checks
        WHERE source_system='SPORTS_EDGE_TENNIS'
          AND dataset_name='LIVE_BATCH_RUNTIME'
          AND checked_at >= '2026-11-10T00:00:00Z'
          AND checked_at < '2026-11-19T00:00:00Z'
        ORDER BY checked_at
        """,
    )
    return rows


def _prediction_calendar(conn):
    return _dicts(
        conn,
        """
        SELECT date_trunc('day', started_at AT TIME ZONE 'UTC')::date AS day_utc,
               count(*) AS runs,
               count(*) FILTER (WHERE prediction_finalized_at IS NOT NULL) AS finalized,
               count(prt.match_id) AS targets
        FROM prediction_runs pr
        LEFT JOIN prediction_run_targets prt
          ON prt.prediction_run_id = pr.prediction_run_id
        WHERE pr.started_at >= '2026-11-10T00:00:00Z'
          AND pr.started_at < '2026-11-19T00:00:00Z'
        GROUP BY 1 ORDER BY 1
        """,
    )


def _reschedules(conn):
    rows = _dicts(
        conn,
        """
        WITH versions AS (
          SELECT mv.match_id, mv.match_start_at, mv.as_of, mv.source_observed_at,
                 mv.match_version_id
          FROM match_versions mv
          WHERE mv.match_start_at >= %(start)s AND mv.match_start_at < %(end)s
        ), latest AS (
          SELECT DISTINCT ON (match_id) match_id, match_start_at
          FROM versions
          ORDER BY match_id, as_of DESC, source_observed_at DESC,
                   match_version_id DESC
        ), cohort AS (
          SELECT match_id, match_start_at FROM latest
        ), timeline AS (
          SELECT c.match_id,
                 count(mv.match_version_id) AS versions,
                 count(DISTINCT mv.match_start_at) AS starts,
                 min(mv.match_start_at) AS earliest_start,
                 max(mv.match_start_at) AS latest_start,
                 min(mv.source_observed_at) AS first_observed,
                 min(mv.ingested_at) AS first_ingested
          FROM cohort c
          JOIN match_versions mv ON mv.match_id = c.match_id
          GROUP BY c.match_id
        )
        SELECT count(*) AS matches,
               count(*) FILTER (WHERE starts > 1) AS rescheduled_matches,
               count(*) FILTER (WHERE versions > 1) AS multi_version_matches,
               count(*) FILTER (WHERE first_observed > earliest_start) AS first_seen_after_earliest_start,
               count(*) FILTER (WHERE first_observed > latest_start) AS first_seen_after_latest_start
        FROM timeline
        """,
        {"start": WINDOW_START, "end": WINDOW_END},
    )[0]
    distribution = _dicts(
        conn,
        """
        WITH versions AS (
          SELECT mv.match_id, mv.match_start_at, mv.as_of, mv.source_observed_at,
                 mv.match_version_id
          FROM match_versions mv
          WHERE mv.match_start_at >= %(start)s AND mv.match_start_at < %(end)s
        ), latest AS (
          SELECT DISTINCT ON (match_id) match_id, match_start_at
          FROM versions
          ORDER BY match_id, as_of DESC, source_observed_at DESC,
                   match_version_id DESC
        ), cohort AS (
          SELECT match_id FROM latest
        ), timeline AS (
          SELECT c.match_id, count(DISTINCT mv.match_start_at) AS starts
          FROM cohort c JOIN match_versions mv ON mv.match_id=c.match_id
          GROUP BY c.match_id
        )
        SELECT starts, count(*) AS matches FROM timeline GROUP BY 1 ORDER BY 1
        """,
        {"start": WINDOW_START, "end": WINDOW_END},
    )
    examples = _dicts(
        conn,
        """
        WITH versions AS (
          SELECT mv.match_id, mv.match_start_at, mv.as_of, mv.source_observed_at,
                 mv.match_version_id
          FROM match_versions mv
          WHERE mv.match_start_at >= %(start)s AND mv.match_start_at < %(end)s
        ), latest AS (
          SELECT DISTINCT ON (match_id) match_id, match_start_at
          FROM versions
          ORDER BY match_id, as_of DESC, source_observed_at DESC,
                   match_version_id DESC
        ), cohort AS (
          SELECT match_id FROM latest
        ), ranked AS (
          SELECT c.match_id, count(DISTINCT mv.match_start_at) AS starts
          FROM cohort c JOIN match_versions mv ON mv.match_id=c.match_id
          GROUP BY c.match_id ORDER BY starts DESC, c.match_id LIMIT 8
        )
        SELECT r.match_id, r.starts,
               json_agg(json_build_object(
                 'start', mv.match_start_at, 'status', mv.match_status::text,
                 'as_of', mv.as_of
               ) ORDER BY mv.as_of, mv.match_start_at) AS observations
        FROM ranked r
        JOIN match_versions mv ON mv.match_id=r.match_id
        GROUP BY r.match_id, r.starts ORDER BY r.starts DESC, r.match_id
        """,
        {"start": WINDOW_START, "end": WINDOW_END},
    )
    return {
        "aggregate": rows,
        "start_distribution": distribution,
        "examples": examples,
    }


def _populations(conn):
    consensus = _dicts(
        conn,
        """
        WITH first_run AS (
          SELECT DISTINCT ON (t.match_id)
                 t.match_id, pr.prediction_run_id
          FROM prediction_run_targets t
          JOIN prediction_runs pr ON pr.prediction_run_id=t.prediction_run_id
          WHERE pr.run_status='COMPLETED'
            AND pr.experiment_id IS NULL
            AND pr.prediction_contract_version='predictive_agent_result_v1'
            AND pr.prediction_finalized_at IS NOT NULL
          ORDER BY t.match_id, pr.prediction_finalized_at, pr.prediction_run_id
        )
        SELECT DISTINCT mcr.match_id, mcr.as_of
        FROM manager_consensus_results mcr
        JOIN first_run fr ON fr.match_id=mcr.match_id
          AND fr.prediction_run_id=mcr.prediction_run_id
        JOIN LATERAL (
          SELECT mv.match_start_at FROM match_versions mv
          WHERE mv.match_id=mcr.match_id
          ORDER BY mv.as_of DESC, mv.source_observed_at DESC,
                   mv.match_version_id DESC LIMIT 1
        ) mv ON TRUE
        WHERE mcr.manager_status='CONSENSUS'
          AND mv.match_start_at >= %(start)s AND mv.match_start_at < %(end)s
        """,
        {"start": WINDOW_START, "end": WINDOW_END},
    )
    aligned = _dicts(
        conn,
        f"""
        WITH {aligned_production_model_picks_cte()}
        SELECT p.match_id, p.aligned_pick_type, p.picked_at, mv.match_start_at
        FROM aligned_model_picks p
        JOIN LATERAL (
          SELECT mv0.match_start_at FROM match_versions mv0
          WHERE mv0.match_id=p.match_id
            AND mv0.as_of <= p.picked_at
            AND mv0.source_observed_at <= p.picked_at
          ORDER BY mv0.as_of DESC, mv0.source_observed_at DESC,
                   mv0.match_version_id DESC LIMIT 1
        ) mv ON TRUE
        WHERE mv.match_start_at >= %(start)s AND mv.match_start_at < %(end)s
          AND p.picked_at <= mv.match_start_at - interval '30 minutes'
        """,
        {"start": WINDOW_START, "end": WINDOW_END},
    )
    settled = _dicts(
        conn,
        """
        SELECT DISTINCT d.match_id
        FROM decision_settlements ds
        JOIN decisions d ON d.decision_id=ds.decision_id
        JOIN matches m ON m.match_id=d.match_id
        JOIN match_versions mv ON mv.match_id=m.match_id
        WHERE mv.match_start_at >= %(start)s AND mv.match_start_at < %(end)s
        """,
        {"start": WINDOW_START, "end": WINDOW_END},
    )
    result_settled = _dicts(
        conn,
        """
        SELECT DISTINCT mro.match_id
        FROM match_result_observations mro
        JOIN match_versions mv ON mv.match_id=mro.match_id
        WHERE mv.match_start_at >= %(start)s AND mv.match_start_at < %(end)s
          AND mro.outcome_status IN ('COMPLETED','RETIRED','WALKOVER','DEFAULTED')
        """,
        {"start": WINDOW_START, "end": WINDOW_END},
    )
    tracking = {int(r["match_id"]) for r in aligned if r["aligned_pick_type"] == "A1_A2_WIDE_SPREAD"}
    published = {int(r["match_id"]) for r in aligned}
    reconciliation = reconcile_selection_populations(
        manager_consensus={int(r["match_id"]) for r in consensus},
        tracking_only=tracking,
        published_model_picks=published,
        settled={int(r["match_id"]) for r in settled},
    )
    reconciliation["completed_result_matches"] = len({int(r["match_id"]) for r in result_settled})
    reconciliation["aligned_pick_type_counts"] = dict(
        Counter(r["aligned_pick_type"] for r in aligned)
    )
    reconciliation["aligned_picked_after_start"] = sum(
        1 for r in aligned if r["picked_at"] > r["match_start_at"]
    )
    reconciliation["manager_consensus_rows"] = len(consensus)
    return reconciliation


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-json", required=True)
    args = parser.parse_args(argv)
    dsn = os.getenv("TENNIS_DATABASE_URL")
    if not dsn:
        parser.error("TENNIS_DATABASE_URL required")

    with psycopg.connect(dsn, autocommit=True, connect_timeout=20) as conn:
        conn.execute("SET default_transaction_read_only=on")
        conn.execute("SET statement_timeout='280s'")
        cohort = _cohort(conn)
        run_ids = sorted({r["first_ingest_run"] for r in cohort if r["first_ingest_run"]})
        first_runs = _first_runs(conn, run_ids)
        run_calendar = _run_calendar(conn)
        health = _health(conn)
        prediction_calendar = _prediction_calendar(conn)
        reschedules = _reschedules(conn)
        populations = _populations(conn)
        version_rows = _dicts(
            conn,
            """
            SELECT match_id, match_version_id, match_start_at, match_status::text,
                   source_observed_at, as_of, ingested_at, round_code, best_of_sets,
                   qualifying_flag
            FROM match_versions
            WHERE match_id = ANY(%s)
            ORDER BY match_id, as_of, source_observed_at, match_version_id
            """,
            ([int(r["match_id"]) for r in cohort],),
        )

    timelines = {}
    for row in version_rows:
        timelines.setdefault(int(row["match_id"]), []).append(
            MatchVersionObservation(
                match_version_id=int(row["match_version_id"]),
                match_start_at=row["match_start_at"],
                match_status=row["match_status"],
                source_observed_at=row["source_observed_at"],
                as_of=row["as_of"],
                ingested_at=row["ingested_at"],
                round_code=row["round_code"],
                best_of_sets=row["best_of_sets"],
                qualifying_flag=row["qualifying_flag"],
            )
        )
    timeline_report = [
        build_fixture_timeline(mid, versions).__dict__ for mid, versions in timelines.items()
    ]

    lags = [
        (r["first_ingested"] - r["latest_start"]).total_seconds()
        for r in cohort
    ]
    report = {
        "version": VERSION,
        "report_as_of_utc": datetime.now(UTC).isoformat(),
        "read_only": True,
        "no_writes": True,
        "cohort": {
            "definition": (
                "latest stored version scheduled 2026-11-14..16 UTC, qualifying_flag=false, "
                "no version with as_of/source_observed_at/ingested_at <= start-30m"
            ),
            "match_count": len(cohort),
            "by_day": dict(Counter(r["latest_start"].date().isoformat() for r in cohort)),
            "by_tour": dict(Counter(r["tour"] for r in cohort)),
            "by_latest_status": dict(Counter(r["latest_status"] for r in cohort)),
            "ever_targeted": sum(1 for r in cohort if r["ever_targeted"]),
            "first_ingest_runs": run_ids,
            "first_observed_min": min(r["first_observed"] for r in cohort).isoformat(),
            "first_observed_max": max(r["first_observed"] for r in cohort).isoformat(),
            "first_ingested_min": min(r["first_ingested"] for r in cohort).isoformat(),
            "first_ingested_max": max(r["first_ingested"] for r in cohort).isoformat(),
            "latest_start_min": min(r["latest_start"] for r in cohort).isoformat(),
            "latest_start_max": max(r["latest_start"] for r in cohort).isoformat(),
            "ingest_lag_hours_min": min(lags) / 3600,
            "ingest_lag_hours_max": max(lags) / 3600,
            "version_count_distribution": dict(Counter(r["versions"] for r in cohort)),
            "distinct_start_distribution": dict(Counter(r["starts"] for r in cohort)),
            "rows": [
                {
                    key: (value.isoformat() if isinstance(value, datetime) else value)
                    for key, value in row.items()
                }
                for row in cohort
            ],
        },
        "first_ingest_runs": first_runs,
        "ingestion_run_calendar": run_calendar,
        "capture_health": {
            "rows": [
                {
                    **r,
                    "checked_at": r["checked_at"].isoformat(),
                    "latest_observation_at": (
                        None if r["latest_observation_at"] is None
                        else r["latest_observation_at"].isoformat()
                    ),
                }
                for r in health
            ],
            "gap": detect_capture_gaps(
                [r["checked_at"] for r in health],
                window_start=datetime(2026, 11, 10, tzinfo=UTC),
                window_end=datetime(2026, 11, 18, tzinfo=UTC),
                expected_interval=timedelta(hours=1),
            ).__dict__,
        },
        "prediction_run_calendar": prediction_calendar,
        "reschedule_traceability": reschedules,
        "selection_populations": populations,
        "timeline_classifications": dict(
            Counter(t["classification"] for t in timeline_report)
        ),
        "timeline_details": timeline_report,
        "cause": {
            "summary": (
                "The live batch scheduler stopped writing ingestion, health and prediction "
                "heartbeats after 2026-11-12 23:01 UTC and resumed on 2026-11-17. "
                "The 53 fixtures were first observed on 2026-11-17 08:16 UTC by a "
                "catch-up scheduled-fixtures run whose provider payload already carried "
                "completed/cancelled results. Their first version is therefore "
                "retrospective, not late: no pre-match version or target could exist."
            ),
            "not_a_provider_request_failure": True,
            "not_a_late_refresh": True,
            "not_missing_fixtures": True,
        },
    }
    payload = json.dumps(report, indent=2, sort_keys=True, default=str)
    with open(args.out_json, "w", encoding="utf-8") as handle:
        handle.write(payload + "\n")
    print(
        json.dumps(
            {
                "cohort": len(cohort),
                "ever_targeted": report["cohort"]["ever_targeted"],
                "ingest_lag_hours_min": report["cohort"]["ingest_lag_hours_min"],
                "ingest_lag_hours_max": report["cohort"]["ingest_lag_hours_max"],
                "first_ingest_runs": run_ids,
                "rescheduled_matches": reschedules["aggregate"]["rescheduled_matches"],
                "populations": populations,
            },
            indent=2,
            default=str,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

