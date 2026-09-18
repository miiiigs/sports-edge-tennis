from __future__ import annotations

import argparse
import os

import psycopg

from sports_edge_tennis.publication_control import (
    HALT_SOURCES,
    enable_publication,
    halt_publication,
    load_publication_control_state,
)


def _dsn() -> str:
    value = os.environ.get("TENNIS_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not value:
        raise RuntimeError("TENNIS_DATABASE_URL or DATABASE_URL is required")
    return value


def _print_state(state) -> None:
    print(
        "PUBLICATION_CONTROL "
        f"enabled={int(state.enabled)} "
        f"action={state.action or '-'} "
        f"source={state.source or '-'} "
        f"actor={state.actor or '-'} "
        f"created_at={state.created_at.isoformat() if state.created_at else '-'} "
        f"reason={state.reason or '-'}",
        flush=True,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Inspect or explicitly change the Tennis publication control state"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status")

    halt = sub.add_parser("halt")
    halt.add_argument("--source", choices=sorted(HALT_SOURCES), required=True)
    halt.add_argument("--reason", required=True)
    halt.add_argument("--actor")

    enable = sub.add_parser("enable")
    enable.add_argument("--reason", required=True)
    enable.add_argument("--actor", required=True)

    args = parser.parse_args(argv)
    with psycopg.connect(_dsn(), autocommit=True) as conn:
        if args.command == "status":
            state = load_publication_control_state(conn)
        elif args.command == "halt":
            state = halt_publication(
                conn,
                source=args.source,
                reason=args.reason,
                actor=args.actor,
            )
        else:
            state = enable_publication(
                conn,
                actor=args.actor,
                reason=args.reason,
            )
    _print_state(state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
