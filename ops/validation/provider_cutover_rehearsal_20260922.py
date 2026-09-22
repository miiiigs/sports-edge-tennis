from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from sports_edge_tennis.validation.provider_cutover_rehearsal import run_rehearsal


DEFAULT_OUTPUT_DIR = Path("ops/validation/provider-cutover-rehearsal-20260922")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Shadow-only Sports Edge Tennis provider cutover rehearsal artifacts"
    )
    parser.add_argument("--start-date", type=date.fromisoformat, default=date(2026, 9, 10))
    parser.add_argument("--end-date", type=date.fromisoformat, default=date(2026, 9, 21))
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument(
        "--skip-provider-fetch",
        action="store_true",
        help="Generate blocked/local artifacts without calling provider APIs.",
    )
    args = parser.parse_args(argv)

    result = run_rehearsal(
        output_dir=args.output_dir,
        start_date=args.start_date,
        end_date=args.end_date,
        fetch_providers=not args.skip_provider_fetch,
    )
    print(
        "PROVIDER_CUTOVER_REHEARSAL "
        + json.dumps(
            {
                "output_dir": result["output_dir"],
                "overall": result["gates"]["overall"],
                "db_available": result["summary"]["database"].get("available"),
                "transaction_read_only": result["summary"]["database"].get("transaction_read_only"),
                "canonical_matches": result["summary"].get("sporting_matches_found"),
                "canonical_atp": result["summary"].get("atp_count"),
                "canonical_challenger": result["summary"].get("challenger_count"),
                "oddspapi_fixtures": result["summary"].get("oddspapi_fixture_rows"),
                "oddspapi_betradar_id_coverage": result["summary"].get("oddspapi_betradar_id_coverage"),
                "oddspapi_pinnacle_id_coverage": result["summary"].get("oddspapi_pinnacle_id_coverage"),
                "total_billable_requests": result["economics"]["total_billable_requests"],
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
