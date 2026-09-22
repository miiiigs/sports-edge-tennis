from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from sports_edge_tennis.validation.sportradar_canonical_link_audit import (
    audit_sportradar_canonical_links,
)


DEFAULT_OUTPUT_DIR = Path("ops/validation/provider-cutover-rehearsal-20260922")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Read-only Sportradar canonical exact-link audit for provider cutover rehearsal"
    )
    parser.add_argument("--start-date", type=date.fromisoformat, default=date(2026, 9, 10))
    parser.add_argument("--end-date", type=date.fromisoformat, default=date(2026, 9, 21))
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args(argv)

    summary = audit_sportradar_canonical_links(
        output_dir=args.output_dir,
        start_date=args.start_date,
        end_date=args.end_date,
    )
    preview = summary["triple_exact_preview"]
    print(
        "SPORTRADAR_CANONICAL_LINK_AUDIT "
        + json.dumps(
            {
                "output_dir": str(args.output_dir),
                "transaction_read_only": summary["database"]["transaction_read_only"],
                "sportradar_daily_summary_calls": summary["sportradar"]["daily_summary_calls"],
                "oddspapi_fixture_calls": summary["oddspapi"]["fixture_calls"],
                "targets": summary["targets"],
                "link_status_counts": summary["link_status_counts"],
                "exact": summary["exact"],
                "triple_exact": summary["oddspapi"]["crosscheck_counts"].get("TRIPLE_EXACT", 0),
                "candidate_match_source_ids": summary["match_identity"]["candidate_match_source_ids"],
                "surface_gaps_resolvable": summary["surface"]["gaps_resolvable"],
                "result_gaps_resolvable": summary["result"]["gaps_resolvable"],
                "triple_exact_preview": {
                    "candidate_artifact_sha256": preview["candidate_artifact_sha256"],
                    "eligibility": preview["eligibility"],
                    "final_actions": preview["final_actions"],
                    "match_source_actions": preview["match_source_actions"],
                    "oddspapi_betradar": preview["oddspapi_betradar"],
                    "orientation": preview["orientation"],
                    "player_source_actions": preview["player_source_actions"],
                    "triple_exact_input": preview["triple_exact_input"],
                    "unique_sportradar_competitor_ids": preview["unique_sportradar_competitor_ids"],
                    "unique_sportradar_event_ids": preview["unique_sportradar_event_ids"],
                },
                "runtime_errors": len(summary["runtime_errors"]),
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
