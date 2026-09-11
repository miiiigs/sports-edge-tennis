"""Command-line entry point for a single controlled Live MVP run."""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import psycopg

from sports_edge_tennis.live.credentials import (
    MissingCredentialError,
    load_secret,
)
from sports_edge_tennis.live.orchestrator import (
    LiveMvpError,
    LiveMvpResult,
    run_live_mvp,
)
from sports_edge_tennis.live.summary import render_terminal


def _db_dsn() -> str | None:
    import os

    return (
        os.environ.get("TENNIS_DATABASE_URL")
        or os.environ.get("DATABASE_URL")
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sports Edge Tennis Live MVP run")
    parser.add_argument(
        "--slate-date",
        type=date.fromisoformat,
        default=datetime.now(timezone.utc).date(),
        help="Slate date (YYYY-MM-DD); defaults to current UTC date",
    )
    parser.add_argument("--dsn", default=_db_dsn(), help="PostgreSQL DSN (default: TENNIS_DATABASE_URL/DATABASE_URL)")
    parser.add_argument("--artifacts-dir", default="artifacts/live_mvp")
    args = parser.parse_args(argv)

    if not args.dsn:
        print("BLOCKED_NO_LIVE_MVP_DATABASE: no DSN configured (TENNIS_DATABASE_URL / DATABASE_URL)")
        return 2

    try:
        api_tennis_key = load_secret("API_TENNIS_API_KEY")
    except MissingCredentialError as exc:
        print(f"BLOCKED_MISSING_API_TENNIS_KEY: {exc}")
        return 2
    if not api_tennis_key:
        print("BLOCKED_MISSING_API_TENNIS_KEY")
        return 2

    pinnapi_key = load_secret("PINNAPI_KEY")

    try:
        with psycopg.connect(args.dsn, autocommit=True) as conn:
            result = run_live_mvp(
                conn,
                slate_date=args.slate_date,
                api_tennis_key=api_tennis_key,
                pinnapi_key=pinnapi_key,
                artifacts_dir=Path(args.artifacts_dir),
            )
    except LiveMvpError as exc:
        print(f"{exc.result_code}: {exc}")
        return 2
    except MissingCredentialError as exc:
        print(f"BLOCKED_MISSING_PINNAPI_KEY: {exc}")
        return 2
    except Exception as exc:  # noqa: BLE001 - surface unexpected failures honestly
        print(f"IMPLEMENTATION_FAILED: {exc}")
        return 2

    print(render_terminal(_load_summary(result)))
    print(f"\n{result.status}")
    return 0


def _load_summary(result: LiveMvpResult) -> dict:
    if result.summary_path is None:
        return {}
    import json

    return json.loads(Path(result.summary_path).read_text(encoding="utf-8"))


if __name__ == "__main__":
    sys.exit(main())
