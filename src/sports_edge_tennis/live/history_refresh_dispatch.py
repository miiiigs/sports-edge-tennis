"""Provider-aware Tennis history refresh entry point.

Legacy production remains API-Tennis until the paired provider cutover is explicitly
authorized. Target mode dispatches only to the bounded Sportradar recent-history
refresh. This module itself does not change provider authority.
"""

from __future__ import annotations

import os
import sys

from sports_edge_tennis.live.provider_authority import (
    LEGACY_DATA_MODE,
    TARGET_DATA_MODE,
    load_provider_authority,
)


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
