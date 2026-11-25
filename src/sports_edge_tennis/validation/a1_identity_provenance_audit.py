"""Read-only audit of identity reference-data PIT safety and replay fidelity.

Two questions, both answered from stored production rows only:

1. **Reference-data leakage.** The bridge reads mutable reference data
   (``players.canonical_name``, ``player_aliases``) and caller-dated bridge events.
   Comparing ``reference_policy="permissive"`` (the pre-2026-11-25 behaviour) with
   ``reference_policy="strict"`` (the fail-closed policy) shows exactly which target
   mappings were only reachable through reference state that did not exist, or had a
   different value, at the original cutoff. If reverting those mappings does not move
   Brier or accuracy, the identity correction is not driven by future information.

2. **Residual replay mismatch.** The previously reported ~92.9% exact reproduction of
   stored Agent 1 probabilities is a re-issue reproduction, not first-issue fidelity.
   This audit decomposes the residual by mechanism: target identity resolution, surface
   resolution, history ingest visibility, and unexplained remainder.

No writes. No provider calls. ``can_promote=false``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import random
import sys
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg

from sports_edge_tennis.agents.agent1_surface_elo import (
    DEFAULT_CONFIG,
    PredictionRequest,
    advance_replay_history,
    predict_from_replay,
    replay_history,
)
from sports_edge_tennis.data.agent1_history import load_agent1_history
from sports_edge_tennis.data.model_identity import (
    REFERENCE_POLICY_PERMISSIVE,
    REFERENCE_POLICY_STRICT,
    resolve_model_player_ids_with_provenance,
)
from sports_edge_tennis.runner.targets import _resolve_model_surface

VERSION = "A1_IDENTITY_PROVENANCE_AUDIT_V1"
SINCE = datetime(2026, 9, 10, tzinfo=UTC)
BOOTSTRAP_REPS = 1000
BOOTSTRAP_SEED = 20261125

TARGET_SQL = """
WITH a1 AS (
  SELECT DISTINCT ON (p.match_id)
         p.prediction_id, p.match_id, p.prediction_run_id, p.as_of,
         p.selection_side, p.win_probability, pr.prediction_finalized_at
  FROM predictions p JOIN prediction_runs pr USING (prediction_run_id)
  WHERE p.agent_code='AGENT_1_SURFACE_ELO'
    AND p.prediction_status='PREDICTED'
    AND p.experiment_id IS NULL AND pr.experiment_id IS NULL
    AND pr.run_status='COMPLETED'
    AND p.as_of >= %(since)s
  ORDER BY p.match_id, p.created_at DESC
)
SELECT a1.prediction_id, a1.match_id, a1.prediction_run_id, a1.as_of,
       a1.prediction_finalized_at, a1.selection_side, a1.win_probability,
       mv.match_version_id, mv.player_1_id, mv.player_2_id,
       mv.surface::text AS live_surface, te.surface::text AS edition_surface,
       t.tournament_id, t.canonical_name, mv.tour_scope::text,
       mv.competition_tier::text, te.season_year, mv.best_of_sets,
       mv.qualifying_flag, mv.match_start_at,
       a2.prediction_status::text AS a2_status, a2.selection_side AS a2_side,
       a2.win_probability AS a2_probability,
       a3.prediction_status::text AS a3_status,
       r.outcome_status::text AS outcome_status, r.winner_side
FROM a1
JOIN prediction_run_targets prt
  ON prt.prediction_run_id=a1.prediction_run_id AND prt.match_id=a1.match_id
JOIN LATERAL (
  SELECT mv0.* FROM match_versions mv0
  WHERE mv0.match_id=a1.match_id
    AND mv0.source_observed_at <= a1.as_of
    AND mv0.as_of <= a1.as_of
    AND mv0.ingested_at <= a1.as_of
  ORDER BY mv0.as_of DESC, mv0.source_observed_at DESC,
           mv0.ingested_at DESC, mv0.match_version_id DESC
  LIMIT 1
) mv ON TRUE
JOIN matches m ON m.match_id=a1.match_id
JOIN tournament_editions te USING (tournament_edition_id)
JOIN tournaments t USING (tournament_id)
LEFT JOIN predictions a2
  ON a2.prediction_run_id=a1.prediction_run_id
 AND a2.match_id=a1.match_id
 AND a2.agent_code='AGENT_2_HIERARCHICAL_MARKOV'
LEFT JOIN predictions a3
  ON a3.prediction_run_id=a1.prediction_run_id
 AND a3.match_id=a1.match_id
 AND a3.agent_code='AGENT_3_INTRANSITIVITY'
LEFT JOIN LATERAL (
  SELECT mro.outcome_status, mro.winner_side
  FROM match_result_observations mro
  WHERE mro.match_id=a1.match_id
  ORDER BY mro.as_of DESC, mro.source_observed_at DESC,
           mro.ingested_at DESC, mro.match_result_observation_id DESC
  LIMIT 1
) r ON TRUE
ORDER BY a1.as_of, a1.match_id
"""


def dsn_from_env() -> str:
    direct = os.environ.get("TENNIS_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if direct:
        return direct
    if os.environ.get("PGHOST") and os.environ.get("PGPASSWORD"):
        return psycopg.conninfo.make_conninfo(
            host=os.environ["PGHOST"],
            port=os.environ.get("PGPORT", "5432"),
            user=os.environ["PGUSER"],
            password=os.environ["PGPASSWORD"],
            dbname=os.environ["PGDATABASE"],
            sslmode="require",
            connect_timeout=30,
            keepalives=1,
            keepalives_idle=30,
            keepalives_interval=10,
            keepalives_count=5,
        )
    raise SystemExit("TENNIS_DATABASE_URL, DATABASE_URL or PG* variables are required")


def metrics(pairs: list[tuple[float, int]]) -> dict[str, Any]:
    n = len(pairs)
    if not n:
        return {"n": 0}
    wins = sum(1 for p, y in pairs if (p >= 0.5) == bool(y))
    brier = sum((p - y) ** 2 for p, y in pairs) / n
    ll = -sum(
        y * math.log(min(max(p, 1e-12), 1 - 1e-12))
        + (1 - y) * math.log(min(max(1 - p, 1e-12), 1 - 1e-12))
        for p, y in pairs
    ) / n
    return {"n": n, "accuracy": wins / n, "brier": brier, "log_loss": ll}


def paired_day_bootstrap(
    base: list[dict[str, Any]],
    cand: list[dict[str, Any]],
    *,
    reps: int = BOOTSTRAP_REPS,
    seed: int = BOOTSTRAP_SEED,
) -> dict[str, Any]:
    by_day: dict[str, list[tuple[dict[str, Any], dict[str, Any]]]] = defaultdict(list)
    for b, c in zip(base, cand, strict=True):
        by_day[b["date"]].append((b, c))
    days = sorted(by_day)
    rng = random.Random(seed)
    db: list[float] = []
    dl: list[float] = []
    da: list[float] = []
    for _ in range(reps):
        sample = [x for _ in days for x in by_day[rng.choice(days)]]
        mb = metrics([(x[0]["p1"], x[0]["y"]) for x in sample])
        mc = metrics([(x[1]["p1"], x[1]["y"]) for x in sample])
        db.append(mc["brier"] - mb["brier"])
        dl.append(mc["log_loss"] - mb["log_loss"])
        da.append(mc["accuracy"] - mb["accuracy"])
    for arr in (db, dl, da):
        arr.sort()
    lo, hi = int(0.025 * (reps - 1)), int(0.975 * (reps - 1))
    return {
        "brier_delta": [db[lo], db[hi]],
        "log_loss_delta": [dl[lo], dl[hi]],
        "accuracy_delta": [da[lo], da[hi]],
        "reps": reps,
        "seed": seed,
    }


def _load_targets(conn: psycopg.Connection) -> list[Any]:
    return conn.execute(TARGET_SQL, {"since": SINCE}).fetchall()


def _resolve_all(
    conn: psycopg.Connection,
    targets: list[Any],
    *,
    reference_policy: str,
    alias_refresh_gate: bool = False,
) -> tuple[dict[datetime, dict[int, int]], dict[datetime, dict[str, Any]]]:
    by_asof: dict[datetime, list[Any]] = defaultdict(list)
    for target in targets:
        by_asof[target[3]].append(target)
    resolved: dict[datetime, dict[int, int]] = {}
    provenance: dict[datetime, dict[str, Any]] = {}
    for as_of, group in by_asof.items():
        ids = {int(t[8]) for t in group} | {int(t[9]) for t in group}
        result = resolve_model_player_ids_with_provenance(
            conn,
            player_ids=ids,
            as_of=as_of,
            reference_policy=reference_policy,
            alias_refresh_gate=alias_refresh_gate,
        )
        resolved[as_of] = {pid: r.resolved_player_id for pid, r in result.items()}
        provenance[as_of] = {
            "methods": dict(Counter(r.method for r in result.values())),
            "name_sources": dict(Counter(r.name_source for r in result.values())),
            "evidence": dict(Counter(r.evidence for r in result.values())),
            "mapped_count": sum(1 for r in result.values() if r.bridged),
            "resolutions": result,
        }
    return resolved, provenance


def _replay(
    history: list[Any],
    targets: list[Any],
    resolved_by_asof: dict[datetime, dict[int, int]],
    surface_by_prediction: dict[int, str | None],
) -> dict[int, float]:
    hist = sorted(history, key=lambda o: (max(o.as_of, o.source_observed_at), o.match_id))
    out: dict[int, float] = {}
    replay = None
    index = 0
    previous = None
    for target in sorted(targets, key=lambda x: x[3]):
        as_of = target[3]
        if replay is None:
            visible = [
                o for o in hist if o.as_of <= as_of and o.source_observed_at <= as_of
            ]
            replay = replay_history(visible, as_of)
            index = len(visible)
        else:
            new = []
            while index < len(hist) and max(
                hist[index].as_of, hist[index].source_observed_at
            ) <= as_of:
                new.append(hist[index])
                index += 1
            if new:
                try:
                    replay = advance_replay_history(
                        replay, new, previous_prediction_as_of=previous, prediction_as_of=as_of
                    )
                except Exception:  # noqa: BLE001 - fall back to a full rebuild
                    replay = replay_history(hist[:index], as_of)
        previous = as_of
        ids = resolved_by_asof[as_of]
        p1 = ids.get(target[8], target[8])
        p2 = ids.get(target[9], target[9])
        if p1 == p2:
            p1, p2 = target[8], target[9]
        try:
            prediction = predict_from_replay(
                PredictionRequest(
                    player_1_id=p1,
                    player_2_id=p2,
                    surface=surface_by_prediction[target[0]],
                    tour_scope=target[14],
                    competition_tier=target[15],
                    best_of_sets=int(target[17]),
                    qualifying_flag=bool(target[18]),
                    as_of=as_of,
                ),
                replay,
                DEFAULT_CONFIG,
            )
            out[target[0]] = float(prediction.player_1_probability)
        except Exception:  # noqa: BLE001, S112 - one fixture must not abort the audit
            # The missing probability is visible as a smaller n in the result, so the
            # audit never silently substitutes a value.
            continue
    return out


def _stored_p1(target: Any) -> float:
    probability = float(target[6])
    return probability if target[5] == 1 else 1.0 - probability


def _rows(
    targets: list[Any],
    probabilities: dict[int, float],
) -> list[dict[str, Any]]:
    rows = []
    for target in targets:
        if target[0] not in probabilities:
            continue
        rows.append(
            {
                "prediction_id": target[0],
                "match_id": target[1],
                "p1": probabilities[target[0]],
                "stored_p1": _stored_p1(target),
                "y": 1 if target[25] == 1 else 0,
                "tour": target[14],
                "date": (target[19] or target[3]).date().isoformat(),
            }
        )
    return rows


def _fidelity(rows: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(rows)
    if not n:
        return {"n": 0}
    exact = sum(1 for r in rows if abs(r["p1"] - r["stored_p1"]) <= 1e-6) / n
    within = sum(1 for r in rows if abs(r["p1"] - r["stored_p1"]) <= 0.005) / n
    return {"n": n, "exact": exact, "within_005": within}


def _replay_exact(
    history: list[Any],
    targets: list[Any],
    resolved_by_asof: dict[datetime, dict[int, int]],
    surface_by_prediction: dict[int, str | None],
) -> dict[int, float]:
    """Rebuild the replay per ``as_of`` instead of advancing it.

    Used for small sub-populations where an incremental advance would change the
    prediction clock and therefore the probability. Exact and side-effect free.
    """
    out: dict[int, float] = {}
    by_asof: dict[datetime, list[Any]] = defaultdict(list)
    for target in targets:
        by_asof[target[3]].append(target)
    for as_of, group in sorted(by_asof.items()):
        visible = [
            o for o in history if o.as_of <= as_of and o.source_observed_at <= as_of
        ]
        replay = replay_history(visible, as_of)
        ids = resolved_by_asof[as_of]
        for target in group:
            p1 = ids.get(target[8], target[8])
            p2 = ids.get(target[9], target[9])
            if p1 == p2:
                p1, p2 = target[8], target[9]
            try:
                prediction = predict_from_replay(
                    PredictionRequest(
                        player_1_id=p1,
                        player_2_id=p2,
                        surface=surface_by_prediction[target[0]],
                        tour_scope=target[14],
                        competition_tier=target[15],
                        best_of_sets=int(target[17]),
                        qualifying_flag=bool(target[18]),
                        as_of=as_of,
                    ),
                    replay,
                    DEFAULT_CONFIG,
                )
                out[target[0]] = float(prediction.player_1_probability)
            except Exception:  # noqa: BLE001, S112 - one fixture must not abort the audit
                continue
    return out


def _resolve_surfaces(
    conn: psycopg.Connection, targets: list[Any]
) -> tuple[dict[int, str | None], dict[int, str]]:
    surface_by_prediction: dict[int, str | None] = {}
    surface_status: dict[int, str] = {}
    for target in targets:
        surface, status = _resolve_model_surface(
            conn,
            tournament_id=int(target[12]),
            canonical_name=str(target[13]),
            tour_scope=str(target[14]),
            season_year=int(target[16]),
            live_surface=target[10],
            edition_surface=target[11],
            as_of=target[3],
        )
        surface_by_prediction[target[0]] = surface
        surface_status[target[0]] = status
    return surface_by_prediction, surface_status


def reference_policy_report(
    conn: psycopg.Connection,
    targets: list[Any],
    surface_by_prediction: dict[int, str | None] | None = None,
) -> dict[str, Any]:
    # The reference-policy comparison varies *target* resolution only. History bridging
    # at ``cutoff=now`` is identical under every policy, because no reference row can
    # have been mutated after "now", so one bridged load is reused.
    bridged_history = load_agent1_history(
        conn, cutoff=datetime.now(UTC), bridge_model_identities=True
    )
    policies: dict[str, dict[str, Any]] = {}
    for label, policy, refresh in (
        ("permissive", REFERENCE_POLICY_PERMISSIVE, False),
        ("strict", REFERENCE_POLICY_STRICT, False),
        ("strict_alias_refresh_gate", REFERENCE_POLICY_STRICT, True),
    ):
        resolved, provenance = _resolve_all(
            conn, targets, reference_policy=policy, alias_refresh_gate=refresh
        )
        method_totals: Counter[str] = Counter()
        for group in provenance.values():
            method_totals.update(group["methods"])
        policies[label] = {
            "mapped_players": sum(p["mapped_count"] for p in provenance.values()),
            "methods": dict(method_totals),
            "_resolved": resolved,
        }

    permissive = policies["permissive"]

    def _differences(candidate: dict[str, Any]) -> list[dict[str, Any]]:
        differences: list[dict[str, Any]] = []
        for as_of, baseline in permissive["_resolved"].items():
            mapping = candidate["_resolved"].get(as_of, {})
            for player_id, resolved_id in baseline.items():
                if mapping.get(player_id, player_id) != resolved_id:
                    differences.append(
                        {
                            "as_of": as_of.isoformat(),
                            "player_id": player_id,
                            "candidate_resolved": mapping.get(player_id, player_id),
                            "permissive_resolved": resolved_id,
                        }
                    )
        return differences

    strict_differences = _differences(policies["strict"])
    refresh_differences = _differences(policies["strict_alias_refresh_gate"])

    # Replay only the fixtures whose resolution actually changed. This keeps the audit
    # proportionate: when the strict gates change nothing, no replay is needed at all.
    changed_players = {
        int(entry["player_id"]) for entry in (*strict_differences, *refresh_differences)
    }
    affected_targets = [
        target
        for target in targets
        if int(target[8]) in changed_players or int(target[9]) in changed_players
    ]
    affected_rows: dict[str, list[dict[str, Any]]] = {}
    if affected_targets and surface_by_prediction is None:
        surface_by_prediction, _ = _resolve_surfaces(conn, affected_targets)
    surfaces = surface_by_prediction or {}
    for label in ("permissive", "strict", "strict_alias_refresh_gate"):
        policy_rows = policies[label]
        if affected_targets:
            probabilities = _replay_exact(
                bridged_history,
                affected_targets,
                policy_rows["_resolved"],
                surfaces,
            )
        else:
            probabilities = {}
        affected_rows[label] = _rows(affected_targets, probabilities)

    comparable = [
        (b, c)
        for b, c in zip(
            affected_rows["permissive"], affected_rows["strict"], strict=True
        )
        if b["prediction_id"] == c["prediction_id"]
    ]
    return {
        "targets": len(targets),
        "affected_targets": len(affected_targets),
        "policies": {
            label: {
                "mapped_players": value["mapped_players"],
                "methods": value["methods"],
                "affected_metrics": metrics(
                    [(row["p1"], row["y"]) for row in affected_rows[label]]
                ),
            }
            for label, value in policies.items()
        },
        "strict_vs_permissive": {
            "changed_player_mappings": len(strict_differences),
            "examples": strict_differences[:20],
            "affected_targets": len(affected_targets),
            "paired": (
                paired_day_bootstrap([b for b, _ in comparable], [c for _, c in comparable])
                if comparable
                else None
            ),
        },
        "strict_alias_refresh_gate_vs_permissive": {
            "changed_player_mappings": len(refresh_differences),
            "examples": refresh_differences[:20],
        },
    }


def mismatch_report(
    conn: psycopg.Connection,
    targets: list[Any],
    surface_by_prediction: dict[int, str | None],
    surface_status: dict[int, str],
) -> dict[str, Any]:
    """Decompose the residual original-replay mismatch by mechanism.

    Buckets are mutually exclusive and assigned in the order that most strongly
    explains a difference from the stored production probability:

    1. ``unknown_surface`` — the target has no usable surface at all;
    2. ``surface_fallback_resolution`` — the surface came from an edition or
       historical tournament fallback rather than the live version;
    3. ``history_late_ingest`` — an eligible history row for the target match was
       ingested after the original cutoff, so production could not have used it;
    4. ``target_identity_bridged`` — the PIT target identity differs from the live id;
    5. ``unexplained`` — no structural cause found; this is the irreducible remainder
       of never having persisted the exact production request player ids.
    """
    original_history = load_agent1_history(conn, cutoff=datetime.now(UTC))
    resolved, _ = _resolve_all(conn, targets, reference_policy=REFERENCE_POLICY_STRICT)
    probabilities = _replay(original_history, targets, resolved, surface_by_prediction)

    rows = _rows(targets, probabilities)
    buckets: Counter[str] = Counter()
    bucket_totals: Counter[str] = Counter()
    for row in rows:
        target = next(t for t in targets if t[0] == row["prediction_id"])
        mismatch = abs(row["p1"] - row["stored_p1"]) > 1e-6
        status = surface_status.get(target[0], "UNRESOLVED")
        if status in ("UNRESOLVED", "AMBIGUOUS_HISTORY"):
            bucket = "unknown_surface"
        elif status in (
            "CURRENT_EDITION",
            "HISTORICAL_TOURNAMENT",
            "HISTORICAL_TOURNAMENT_NORMALIZED",
        ):
            bucket = "surface_fallback_resolution"
        elif target[8] != resolved[target[3]].get(target[8], target[8]) or target[9] != (
            resolved[target[3]].get(target[9], target[9])
        ):
            bucket = "target_identity_bridged"
        else:
            bucket = "unexplained"
        bucket_totals[bucket] += 1
        if mismatch:
            buckets[bucket] += 1

    return {
        "population": len(rows),
        "fidelity": _fidelity(rows),
        "history_ingest_visibility": _history_ingest_visibility(conn, targets),
        "mismatch_by_mechanism": {
            bucket: {
                "targets": bucket_totals[bucket],
                "mismatches": buckets[bucket],
                "mismatch_rate": (
                    buckets[bucket] / bucket_totals[bucket]
                    if bucket_totals[bucket]
                    else None
                ),
            }
            for bucket in sorted(bucket_totals)
        },
        "total_mismatches": sum(buckets.values()),
    }


def _history_ingest_visibility(
    conn: psycopg.Connection, targets: list[Any]
) -> dict[str, Any]:
    """Share of replay-visible history that production could not have seen at the cutoff.

    The retrospective replay loads history at ``cutoff=now``. For an original cutoff
    ``T`` that means it can include results whose ``as_of`` predates ``T`` but whose
    ``ingested_at`` is later than ``T``. This block quantifies that structural gap at the
    earliest, median and latest original cutoff. It is a property of the replay, not of
    any individual fixture, so it is reported once rather than bucketed per target.
    """
    cutoffs = sorted({target[3] for target in targets})
    if not cutoffs:
        return {"cutoffs": []}
    sample = [cutoffs[0], cutoffs[len(cutoffs) // 2], cutoffs[-1]]
    rows = []
    for cutoff in sample:
        late, visible = conn.execute(
            """
            SELECT count(*) FILTER (WHERE mro.ingested_at > %(cutoff)s) AS late,
                   count(*) AS visible
            FROM match_result_observations mro
            WHERE mro.as_of <= %(cutoff)s
              AND mro.source_observed_at <= %(cutoff)s
            """,
            {"cutoff": cutoff},
        ).fetchone()
        rows.append(
            {
                "as_of": cutoff.isoformat(),
                "visible_result_observations": int(visible),
                "ingested_after_cutoff": int(late),
                "share_ingested_after_cutoff": (
                    int(late) / int(visible) if int(visible) else None
                ),
            }
        )
    return {"cutoffs": rows}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        default="docs/research/evidence/a1_identity_provenance_audit_20261125.json",
        help="output JSON path",
    )
    parser.add_argument(
        "--mode", choices=("all", "reference", "mismatch"), default="all"
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="optional target cap for a fast smoke run (0 = all)",
    )
    args = parser.parse_args(argv)

    conninfo = dsn_from_env()

    def _execute() -> dict[str, Any]:
        with psycopg.connect(
            conninfo,
            options="-c default_transaction_read_only=on -c statement_timeout=3600000",
        ) as conn:
            targets = _load_targets(conn)
            if args.limit:
                targets = targets[: args.limit]

            report: dict[str, Any] = {
                "version": VERSION,
                "generated_at": datetime.now(UTC).isoformat(),
                "since": SINCE.isoformat(),
                "targets": len(targets),
                "settled": sum(
                    1
                    for t in targets
                    if t[24] == "COMPLETED" and t[25] in (1, 2)
                ),
                "can_promote": False,
                "production_changed": False,
            }
            surface_by_prediction: dict[int, str | None] = {}
            surface_status: dict[int, str] = {}
            if args.mode in ("all", "mismatch"):
                surface_by_prediction, surface_status = _resolve_surfaces(conn, targets)
                report["surface_status"] = dict(Counter(surface_status.values()))
            if args.mode in ("all", "reference"):
                report["reference_policy"] = reference_policy_report(
                    conn, targets, surface_by_prediction or None
                )
            if args.mode in ("all", "mismatch"):
                report["mismatch"] = mismatch_report(
                    conn, targets, surface_by_prediction, surface_status
                )
            return report

    # The public database proxy drops a backend occasionally under long analytical
    # reads. Retry from a clean connection rather than emitting partial evidence.
    report: dict[str, Any] = {}
    last_error: Exception | None = None
    for attempt in range(1, 4):
        try:
            report = _execute()
            report["connection_attempts"] = attempt
            last_error = None
            break
        except psycopg.OperationalError as exc:  # pragma: no cover - network dependent
            last_error = exc
            print(f"retryable operational error on attempt {attempt}: {exc}")
    if last_error is not None:
        raise last_error

    payload = json.dumps(report, indent=2, default=str)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(payload, encoding="utf-8")
    report["evidence_sha256"] = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    print(json.dumps({k: v for k, v in report.items() if k != "reference_policy"}, indent=2)[:4000])
    print("wrote", out)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main(sys.argv[1:]))
