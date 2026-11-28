"""Read-only Manager C cohort reconciliation and Agent 2 specialist audit.

Reproduce:

    python ops/validation/manager_c_cohort_probe_20261127.py \
      --output docs/research/evidence/manager_c_cohort_reconciliation_real_20261127.json

No retrain, refreeze, threshold change or production write.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from sports_edge_tennis.validation.agent2_specialist_audit import (  # noqa: E402
    specialist_report,
)
from sports_edge_tennis.validation.manager_c_cohort_reconciliation import (  # noqa: E402
    PUBLISHED_COUNTS,
    arm_metrics,
    paired_bootstrap,
    paired_side_changes,
    reconcile_cohorts,
)
from sports_edge_tennis.validation.manager_c_identity_comparison import (  # noqa: E402
    POLICY,
    build_rows,
)


def p1_of(candidate):
    return None if candidate is None else float(candidate[1])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output")
    args = parser.parse_args(argv)
    dsn = os.environ.get("TENNIS_DATABASE_URL")
    if not dsn:
        parser.error("read-only TENNIS_DATABASE_URL required")

    with psycopg.connect(
        dsn, autocommit=True, connect_timeout=30,
        options="-c default_transaction_read_only=on",
    ) as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        built = build_rows(conn)
        rows = built["rows"]
        target_by_id = {t["match_id"]: t for t in built["targets"]}

        recon_rows = []
        for row in rows:
            if row.get("winner") not in (1, 2):
                continue
            agents = row.get("agents_p1") or {}
            recon_rows.append({
                "match_id": int(row["match_id"]),
                "winner": int(row["winner"]),
                "a1_p1": row.get("a1_bridged_replay_p1"),
                "a2_p1": agents.get("AGENT_2_HIERARCHICAL_MARKOV"),
                "a3_p1": agents.get("AGENT_3_INTRANSITIVITY"),
                "a1_original_p1": row.get("a1_stored_p1"),
                "manager_corrected_p1": p1_of(
                    (row.get("candidates") or {}).get("B")),
                "manager_original_p1": p1_of(
                    (row.get("candidates") or {}).get(POLICY)),
                "tour": row.get("tour") or row.get("tour_scope"),
                "main_draw_verified": bool(row.get("main_draw_verified")),
            })

        reconciled = reconcile_cohorts(recon_rows)
        reconciled.pop("ids_sha256_inputs", None)

        cohorts = {}
        subsets = {
            "all": recon_rows,
            "atp_main": [r for r in recon_rows if r["tour"] == "ATP_MAIN"],
            "challenger": [r for r in recon_rows if r["tour"] == "CHALLENGER"],
            "main_draw": [r for r in recon_rows if r["main_draw_verified"]],
            "qualifying": [r for r in recon_rows if not r["main_draw_verified"]],
        }
        for name, subset in subsets.items():
            cohorts[name] = {
                "settled": len(subset),
                "a1": arm_metrics(subset, "a1_p1"),
                "a2": arm_metrics(subset, "a2_p1"),
                "manager_corrected": arm_metrics(subset, "manager_corrected_p1"),
                "manager_original": arm_metrics(subset, "manager_original_p1"),
                "paired_a1_vs_manager": paired_side_changes(
                    subset, left="a1_p1", right="manager_corrected_p1"),
                "paired_manager_corrected_vs_original": paired_side_changes(
                    subset, left="manager_corrected_p1",
                    right="manager_original_p1"),
                "bootstrap_a1_vs_manager": paired_bootstrap(
                    subset, left="a1_p1", right="manager_corrected_p1"),
                "bootstrap_manager_corrected_vs_original": paired_bootstrap(
                    subset, left="manager_corrected_p1",
                    right="manager_original_p1"),
            }

        # Support counts for the pre-registered Agent 2 specialist categories.
        values: list[str] = []
        params: list = []
        for row in recon_rows:
            target = target_by_id.get(row["match_id"])
            if target is None:
                continue
            for player_key in ("player_1_id", "player_2_id"):
                values.append("(%s::bigint, %s::bigint, %s::timestamptz)")
                params.extend([row["match_id"], target[player_key],
                               target["a1_as_of"]])
        stats = {}
        if values:
            query = f"""
                WITH targets(match_id, player_id, cutoff) AS (
                    VALUES {','.join(values)}
                )
                SELECT t.match_id, t.player_id,
                       count(*) AS matches,
                       coalesce(sum(s.service_points_played), 0) AS service_points,
                       coalesce(sum(s.return_points_won), 0) AS return_points_won
                FROM targets t
                JOIN match_stat_observations s
                  ON s.player_id = t.player_id
                 AND s.observation_kind = 'RAW_SOURCE'
                 AND s.source_observed_at <= t.cutoff
                 AND s.as_of <= t.cutoff
                 AND s.ingested_at <= t.cutoff
                 AND s.service_points_played > 0
                GROUP BY t.match_id, t.player_id
            """
            for match_id, player_id, matches, serve, returns in conn.execute(
                query, params
            ):
                stats[(int(match_id), int(player_id))] = {
                    "matches": int(matches),
                    "service": int(serve),
                    "returns": int(returns),
                }

        agent_rows = []
        for row in recon_rows:
            target = target_by_id.get(row["match_id"])
            if target is None:
                continue
            s1 = stats.get((row["match_id"], target["player_1_id"]), {})
            s2 = stats.get((row["match_id"], target["player_2_id"]), {})
            agent_rows.append({
                **row,
                "p1_matches": s1.get("matches", 0),
                "p2_matches": s2.get("matches", 0),
                "p1_service_points": s1.get("service", 0),
                "p2_service_points": s2.get("service", 0),
                "p1_return_points": s1.get("returns", 0),
                "p2_return_points": s2.get("returns", 0),
            })

        out = {
            "measured_at": datetime.now(UTC).isoformat(),
            "manager_reconciliation": reconciled,
            "cohorts": cohorts,
            "published_counts": PUBLISHED_COUNTS,
            "agent2_specialist": specialist_report(agent_rows),
            "agent2_support_rows": len(agent_rows),
            "production_rule_changed": False,
            "provider_calls": 0,
        }

    payload = json.dumps(out, sort_keys=True, default=str, indent=2)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(payload + "\n")
    print(payload)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
