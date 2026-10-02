"""Read-only operator report for agent abstention reasons and pick result coverage.

Usage:
    python -m ops.agent_observability_report            # or set --dsn
    TENNIS_DATABASE_URL=... python ops/agent_observability_report.py

This script only reads. It never mutates predictions, results, decisions, or any
model/Manager/Market/Veto configuration.
"""

from __future__ import annotations

import argparse
import os

import psycopg

from sports_edge_tennis.dashboard.result_coverage import (
    load_terminal_fixtures_without_result,
)
from sports_edge_tennis.performance.abstention_evidence import load_abstention_windows


def _dsn() -> str | None:
    return os.environ.get("TENNIS_DATABASE_URL") or os.environ.get("DATABASE_URL")


def _pct(value: float | None) -> str:
    return "NA" if value is None else f"{value * 100:.2f}%"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only agent observability report")
    parser.add_argument("--dsn", default=_dsn())
    parser.add_argument("--grace-hours", type=int, default=12)
    args = parser.parse_args(argv)

    if not args.dsn:
        print("AGENT_OBSERVABILITY_BLOCKED reason=NO_DATABASE")
        return 2

    with psycopg.connect(args.dsn, autocommit=True, read_only=True) as conn:
        has_reason_column = (
            conn.execute(
                "SELECT count(*) FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name='predictions' "
                "AND column_name='abstain_reason_code'"
            ).fetchone()[0]
            == 1
        )
        if not has_reason_column:
            print(
                "AGENT_ABSTENTION_SKIPPED reason=MIGRATION_0017_NOT_APPLIED "
                "hint=run_migration_0017_to_enable_abstention_reason_diagnostics"
            )
        else:
            print("AGENT_ABSTENTION_BEGIN")
            for window in load_abstention_windows(conn):
                print(f"AGENT_ABSTENTION_WINDOW window={window.window}")
                for agent in window.agents:
                    print(
                        "AGENT_ABSTENTION "
                        f"window={window.window} agent={agent.agent_code} "
                        f"evaluations={agent.evaluations} predicted={agent.predicted} "
                        f"abstained={agent.abstained} predicted_rate={_pct(agent.predicted_rate)} "
                        f"abstained_rate={_pct(agent.abstained_rate)} "
                        f"unknown_reason={agent.unknown_reason_count}"
                    )
                    for reason in agent.reasons:
                        print(
                            "AGENT_ABSTENTION_REASON "
                            f"window={window.window} agent={agent.agent_code} "
                            f"reason={reason.reason_code} count={reason.count} "
                            f"share={_pct(reason.share)}"
                        )

        report = load_terminal_fixtures_without_result(
            conn, grace_hours=args.grace_hours
        )
        print(
            "RESULT_COVERAGE_SUMMARY "
            f"grace_hours={report.grace_hours} stale_count={report.stale_count} "
            f"last_result_refresh_at="
            f"{report.last_result_refresh_at.isoformat() if report.last_result_refresh_at else 'NA'}"
        )
        for item in report.items:
            print(
                "RESULT_COVERAGE_GAP "
                f"match_id={item.match_id} status={item.match_status} "
                f"match_start_at={item.match_start_at.isoformat() if item.match_start_at else 'NA'} "
                f"age_hours={item.age_hours} "
                f"sources={','.join(item.source_systems) or 'NA'} "
                f"source_match_ids={','.join(item.source_match_ids) or 'NA'}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
