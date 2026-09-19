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


_LEGACY_VALUE_FLAGS = {
    "--start-date",
    "--chunk-days",
    "--min-chunk-days",
    "--max-attempts",
    "--backoff-seconds",
    "--checkpoint-key",
}
_LEGACY_BOOLEAN_FLAGS = {"--restart-window"}


def _target_argv(argv: list[str] | None) -> tuple[list[str], int]:
    """Remove legacy-only orchestration flags before invoking Sportradar.

    Shared flags such as --dsn and --end-date, plus target-native --days, are
    preserved. Unknown flags are preserved so the target parser still fails
    closed instead of silently accepting a typo.
    """
    args = list(argv or [])
    target: list[str] = []
    ignored = 0
    index = 0
    while index < len(args):
        value = args[index]
        if value in _LEGACY_BOOLEAN_FLAGS:
            ignored += 1
            index += 1
            continue

        matched_inline = next(
            (
                flag
                for flag in _LEGACY_VALUE_FLAGS
                if value.startswith(f"{flag}=")
            ),
            None,
        )
        if matched_inline is not None:
            ignored += 1
            index += 1
            continue

        if value in _LEGACY_VALUE_FLAGS:
            if index + 1 >= len(args):
                raise ValueError(f"missing value for {value}")
            ignored += 2
            index += 2
            continue

        target.append(value)
        index += 1

    return target, ignored


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

        # The target refresh has its own bounded --days contract. Translate
        # away legacy long-window/backfill flags so the same Railway schedule
        # can safely switch authority without carrying API-Tennis semantics.
        try:
            target_argv, ignored = _target_argv(argv)
        except ValueError as exc:
            print(
                "HISTORY_REFRESH_DISPATCH_BLOCKED "
                f"reason={str(exc).replace(' ', '_')}",
                flush=True,
            )
            return 2
        print(
            "HISTORY_REFRESH_DISPATCH "
            f"provider=SPORTRADAR_V3 legacy_args_ignored={ignored}",
            flush=True,
        )
        return sportradar_main(target_argv)

    print("HISTORY_REFRESH_DISPATCH_BLOCKED reason=UNSUPPORTED_PROVIDER", flush=True)
    return 2


if __name__ == "__main__":
    sys.exit(main())
