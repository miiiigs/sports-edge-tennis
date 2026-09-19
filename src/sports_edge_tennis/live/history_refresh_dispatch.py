"""Provider-aware Tennis history refresh entry point.

Legacy production remains API-Tennis until the paired provider cutover is explicitly
authorized. Target mode dispatches only to the bounded Sportradar recent-history
refresh. This module itself does not change provider authority.
"""

from __future__ import annotations

import os
import sys

import psycopg

from sports_edge_tennis.live.provider_authority import (
    LEGACY_DATA_MODE,
    TARGET_DATA_MODE,
    assert_cutover_ready,
    load_provider_authority,
)


def _dsn_from_argv(argv: list[str] | None) -> str | None:
    args = list(argv or [])
    for index, value in enumerate(args):
        if value == "--dsn" and index + 1 < len(args):
            return args[index + 1]
        if value.startswith("--dsn="):
            return value.split("=", 1)[1]
    return os.environ.get("TENNIS_DATABASE_URL") or os.environ.get("DATABASE_URL")


def _assert_target_refresh_ready(argv: list[str] | None) -> bool:
    dsn = _dsn_from_argv(argv)
    if not dsn:
        print(
            "HISTORY_REFRESH_DISPATCH_BLOCKED reason=NO_DATABASE_FOR_TARGET_PREFLIGHT",
            flush=True,
        )
        return False
    try:
        with psycopg.connect(dsn, autocommit=True) as conn:
            assert_cutover_ready(conn, env=os.environ)
    except Exception as exc:
        print(
            "HISTORY_REFRESH_DISPATCH_BLOCKED "
            f"reason=TARGET_PREFLIGHT_FAILED kind={type(exc).__name__}",
            flush=True,
        )
        return False
    return True


def main(argv: list[str] | None = None) -> int:
    try:
        authority = load_provider_authority(os.environ)
    except ValueError as exc:
        print(
            f"HISTORY_REFRESH_DISPATCH_BLOCKED reason={str(exc).replace(' ', '_')}",
            flush=True,
        )
        return 2

    if authority.data_mode == LEGACY_DATA_MODE:
        from sports_edge_tennis.live.history_refresh_resilient import (
            main as legacy_main,
        )

        print("HISTORY_REFRESH_DISPATCH provider=API_TENNIS", flush=True)
        return legacy_main(argv)

    if authority.data_mode == TARGET_DATA_MODE:
        if not _assert_target_refresh_ready(argv):
            return 2

        from sports_edge_tennis.live.sportradar_history_refresh import (
            main as sportradar_main,
        )

        # The target refresh has its own bounded --days contract. Never pass
        # legacy long-window/backfill flags into it.
        print("HISTORY_REFRESH_DISPATCH provider=SPORTRADAR_V3", flush=True)
        return sportradar_main(argv)

    print("HISTORY_REFRESH_DISPATCH_BLOCKED reason=UNSUPPORTED_PROVIDER", flush=True)
    return 2


if __name__ == "__main__":
    sys.exit(main())
