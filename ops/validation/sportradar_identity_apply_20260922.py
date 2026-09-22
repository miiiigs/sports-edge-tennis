from __future__ import annotations

import os
import sys

from sports_edge_tennis.validation.sportradar_identity_apply import (
    apply_triple_exact_backfill,
)


def main() -> int:
    dsn = os.environ.get("TENNIS_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not dsn:
        print("SPORTRADAR_IDENTITY_APPLY status=BLOCKED reason=NO_DATABASE", flush=True)
        return 2

    try:
        result = apply_triple_exact_backfill(dsn=dsn)
    except Exception as exc:
        print(
            "SPORTRADAR_IDENTITY_APPLY "
            f"status=FAILED type={type(exc).__name__} message={str(exc)}",
            flush=True,
        )
        return 1

    print(
        "SPORTRADAR_IDENTITY_APPLY "
        "status=OK "
        f"eligible={result['eligible_rows']} "
        f"blocked={result['blocked_rows']} "
        f"match_inserted={result['match_inserted']} "
        f"match_existing={result['match_existing']} "
        f"player_inserted={result['player_inserted']} "
        f"player_existing={result['player_existing']} "
        f"artifact_sha256={result['artifact_sha256']}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
