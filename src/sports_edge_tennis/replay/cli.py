"""Command-line entry point for the current-system replay harness.

Example (official rerun after the historical snapshot is frozen):

    python -m sports_edge_tennis.replay.cli \
        --database-url "$DATABASE_URL" \
        --start-date 2026-09-10 \
        --end-date 2026-09-24 \
        --match-ids-file tmp/replay_match_ids.txt \
        --artifacts-root artifacts/replay \
        --label official

The CLI only reads the database. It never writes production decision tables.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from datetime import date, datetime, time, timezone
from pathlib import Path

from sports_edge_tennis.agents.agent1_surface_elo import DEFAULT_CONFIG as A1_CONFIG
from sports_edge_tennis.agents.agent2_hierarchical_markov import DEFAULT_CONFIG as A2_CONFIG
from sports_edge_tennis.agents.agent3_intransitivity import DEFAULT_CONFIG as A3_CONFIG
from sports_edge_tennis.replay.artifacts import write_replay_artifact
from sports_edge_tennis.replay.contracts import (
    PROVISIONAL_HISTORICAL_RECOVERY_NOT_FROZEN,
    REPLAY_ID,
    REPLAY_SCHEMA_VERSION,
)
from sports_edge_tennis.replay.engine import run_replay
from sports_edge_tennis.replay.first_qualified import (
    first_current_rule_model_picks,
    first_qualified_value_bets,
    model_pick_metrics,
    value_bet_metrics,
)
from sports_edge_tennis.replay.loader import (
    DEFAULT_MARKET_DELAY_SECONDS,
    ReplayDecisionTimePolicy,
    ReplayLoadRequest,
    derive_run_specs_from_match_days,
    derive_run_specs_pit_safe,
    load_replay_inputs,
)
from sports_edge_tennis.replay.manifest import (
    ManifestMetadata,
    ReplayVersionSet,
    build_manifest,
)
from sports_edge_tennis.veto.service import VETO_POLICY_VERSION
from sports_edge_tennis.market.staking import STAKING_RULE_VERSION


#: Explicit override so a packaged/deployed replay can record the exact code
#: revision it is executing without relying on a git checkout or process cwd.
GIT_SHA_ENV = "TENNIS_REPLAY_GIT_SHA"


def _source_repo_root() -> Path | None:
    """Return the repository root that contains this source file, if any.

    ``cli.py`` lives at ``<root>/src/sports_edge_tennis/replay/cli.py``. The git
    SHA must be resolved from *that* root (the code actually executing), never
    from whatever directory the process happens to run in.
    """
    candidate = Path(__file__).resolve().parents[3]
    return candidate if (candidate / ".git").exists() else None


def _git_sha() -> str:
    override = (os.environ.get(GIT_SHA_ENV) or "").strip()
    if override:
        return override
    repo_root = _source_repo_root()
    if repo_root is None:
        return "UNKNOWN"
    try:
        resolved = subprocess.check_output(
            ["git", "-C", str(repo_root), "rev-parse", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
        return resolved or "UNKNOWN"
    except Exception:
        return "UNKNOWN"


def _load_match_ids(path: Path) -> tuple[int, ...]:
    ids: list[int] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        token = line.strip()
        if token and not token.startswith("#"):
            ids.append(int(token))
    return tuple(ids)


def _end_of_day(value: date) -> datetime:
    return datetime.combine(value, time(23, 59, 59), tzinfo=timezone.utc)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sports-edge-tennis-replay")
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
    parser.add_argument("--start-date", required=True, type=date.fromisoformat)
    parser.add_argument("--end-date", required=True, type=date.fromisoformat)
    parser.add_argument("--match-ids-file", type=Path)
    parser.add_argument("--match-ids", type=str, default="")
    parser.add_argument("--manager-policy-version", default="v1")
    parser.add_argument("--market-policy-version", default="v1")
    parser.add_argument("--artifacts-root", type=Path, default=Path("artifacts/replay"))
    parser.add_argument("--replay-id", default=REPLAY_ID)
    parser.add_argument(
        "--market-delay-seconds",
        type=int,
        default=DEFAULT_MARKET_DELAY_SECONDS,
        help="Production sequencing delay between prediction and market "
        "evaluation (market_cutoff_pit_v1).",
    )
    parser.add_argument(
        "--run-planner",
        choices=("pit_safe", "daily_earliest"),
        default="pit_safe",
        help="pit_safe = per-match hourly stabbing cutoffs (default); "
        "daily_earliest = legacy one-cutoff-per-day planner",
    )
    parser.add_argument(
        "--label",
        choices=("provisional", "official"),
        default="provisional",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.database_url:
        print("replay: DATABASE_URL (or --database-url) is required", file=sys.stderr)
        return 2

    match_ids: list[int] = []
    if args.match_ids_file:
        match_ids.extend(_load_match_ids(args.match_ids_file))
    if args.match_ids:
        match_ids.extend(int(token) for token in args.match_ids.split(",") if token.strip())
    if not match_ids:
        print("replay: no match ids supplied", file=sys.stderr)
        return 2

    import psycopg

    provisional = args.label == "provisional"
    with psycopg.connect(args.database_url, autocommit=True) as conn:
        if args.run_planner == "pit_safe":
            specs = derive_run_specs_pit_safe(
                conn,
                match_ids=tuple(match_ids),
                policy=ReplayDecisionTimePolicy(),
                market_delay_seconds=args.market_delay_seconds,
            )
        else:
            specs = derive_run_specs_from_match_days(
                conn,
                match_ids=tuple(match_ids),
                policy=ReplayDecisionTimePolicy(),
            )
        request = ReplayLoadRequest(
            run_specs=specs,
            result_as_of=_end_of_day(args.end_date),
            manager_policy_version=args.manager_policy_version,
            market_policy_version=args.market_policy_version,
        )
        loaded = load_replay_inputs(conn, request)
        # Keep the connection open through run_replay: the per-match veto-context
        # factory reads PIT evidence lazily for otherwise-qualified BETs.
        result = run_replay(
            loaded.runs,
            replay_id=args.replay_id,
            provisional=provisional,
            provisional_label=(
                PROVISIONAL_HISTORICAL_RECOVERY_NOT_FROZEN if provisional else None
            ),
        )

    versions = ReplayVersionSet(
        a1_config_version=A1_CONFIG.config_version,
        a2_config_version=A2_CONFIG.config_version,
        a3_config_version=A3_CONFIG.config_version,
        manager_policy_version=args.manager_policy_version,
        market_policy_version=args.market_policy_version,
        veto_policy_version=VETO_POLICY_VERSION,
        staking_rule_version=STAKING_RULE_VERSION,
    )
    manifest = build_manifest(
        result,
        metadata=ManifestMetadata(
            git_sha=_git_sha(),
            generated_at=datetime.now(timezone.utc),
            historical_range_start=args.start_date,
            historical_range_end=args.end_date,
            input_snapshot_ids=loaded.input_snapshot_ids,
        ),
        versions=versions,
        replay_schema_version=REPLAY_SCHEMA_VERSION,
    )
    paths = write_replay_artifact(args.artifacts_root, result=result, manifest=manifest)

    picks = first_current_rule_model_picks(result.model_picks)
    bets = first_qualified_value_bets(result.value_bets)
    pick_metrics = model_pick_metrics(picks)
    bet_metrics = value_bet_metrics(bets)

    print(f"replay_id={result.replay_id} label={args.label}")
    if result.provisional_label:
        print(f"status={result.provisional_label}")
    print(f"artifact={paths.root}")
    print(f"ledger_sha256={manifest.ledger_sha256}")
    print(f"reproducibility_sha256={paths.reproducibility_sha256}")
    print(
        "model_picks="
        f"{pick_metrics.count} wins={pick_metrics.wins} losses={pick_metrics.losses} "
        f"pending={pick_metrics.pending} hit_rate={pick_metrics.hit_rate}"
    )
    print(
        "value_bets="
        f"{bet_metrics.bets} settled={bet_metrics.settled} W={bet_metrics.wins} "
        f"L={bet_metrics.losses} profit_units={bet_metrics.profit_units} roi={bet_metrics.roi}"
    )
    print(
        "data_not_available_pit="
        f"{manifest.data_not_available_pit_count} "
        f"identity_blocked={manifest.identity_failures} "
        f"surface_blocked={manifest.surface_failures} "
        f"missing_market={manifest.missing_historical_odds}"
    )
    print("Replay output must never be blended with ORIGINAL LIVE production results.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
