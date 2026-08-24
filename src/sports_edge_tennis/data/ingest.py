from __future__ import annotations

import argparse
import json
from pathlib import Path

import psycopg

from .manifest import build_source_document, write_manifest
from .pipeline import ingest_sackmann_document, ingest_tennis_data_document


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Historical tennis data ingest")
    parser.add_argument("--source", choices=("sackmann", "tennis_data"), required=True)
    parser.add_argument("--input-path", required=True, help="Historical source file path")
    parser.add_argument("--source-url", help="Canonical source URL for manifest tracking")
    parser.add_argument("--source-revision", help="Source revision, commit, or upstream version")
    parser.add_argument("--season", type=int, help="Season year for manifest metadata")
    parser.add_argument("--dry-run", action="store_true", help="Parse and normalize without DB writes")
    parser.add_argument("--db-dsn", help="Explicit PostgreSQL DSN")
    parser.add_argument(
        "--manifest-path",
        default="data/source_manifest.json",
        help="Manifest output path",
    )
    parser.add_argument(
        "--quality-report-path",
        default="data/reports/day5_quality_report.json",
        help="Quality report output path",
    )
    parser.add_argument(
        "--unmatched-report-path",
        default="data/reports/day5_unmatched_report.json",
        help="Unmatched report output path",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    input_path = Path(args.input_path).resolve()
    if not input_path.exists():
        parser.error(f"input path does not exist: {input_path}")

    if not args.dry_run and not args.db_dsn:
        parser.error("--db-dsn is required unless --dry-run is used")

    if args.source == "sackmann":
        source_system = "SACKMANN"
        source_name = "Jeff Sackmann tennis_atp historical matches"
        dataset_type = "historical_matches"
    else:
        source_system = "TENNIS_DATA"
        source_name = "Tennis-Data historical odds archive"
        dataset_type = "historical_odds"

    document = build_source_document(
        path=input_path,
        source_system=source_system,
        source_name=source_name,
        dataset_type=dataset_type,
        season=args.season,
        source_url=args.source_url,
        source_revision=args.source_revision,
    )
    manifest_path = Path(args.manifest_path)
    quality_path = Path(args.quality_report_path)
    unmatched_path = Path(args.unmatched_report_path)
    write_manifest(manifest_path, [document])

    if args.dry_run:
        if args.source == "sackmann":
            result = ingest_sackmann_document(
                None,
                document=document,
                dry_run=True,
                quality_path=quality_path,
                unmatched_path=unmatched_path,
            )
        else:
            result = ingest_tennis_data_document(
                None,
                document=document,
                dry_run=True,
                quality_path=quality_path,
                unmatched_path=unmatched_path,
            )
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0

    with psycopg.connect(args.db_dsn, autocommit=True) as conn:
        if args.source == "sackmann":
            result = ingest_sackmann_document(
                conn,
                document=document,
                dry_run=False,
                quality_path=quality_path,
                unmatched_path=unmatched_path,
            )
        else:
            result = ingest_tennis_data_document(
                conn,
                document=document,
                dry_run=False,
                quality_path=quality_path,
                unmatched_path=unmatched_path,
            )

    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
