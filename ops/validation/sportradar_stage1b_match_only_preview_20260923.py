from __future__ import annotations

import json
import os

from sports_edge_tennis.validation.sportradar_stage1b_match_only import (
    preview_stage1b_match_only,
)


def main() -> int:
    dsn = os.environ.get("TENNIS_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not dsn:
        raise RuntimeError("database URL unavailable")
    result = preview_stage1b_match_only(dsn=dsn)
    print(
        "SPORTRADAR_STAGE1B_MATCH_ONLY_PREVIEW "
        + json.dumps(result, sort_keys=True),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
