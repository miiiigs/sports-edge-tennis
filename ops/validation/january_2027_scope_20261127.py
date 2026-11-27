"""Emit the January 2027 scope and readiness evidence (no provider call)."""
from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime
from pathlib import Path

from sports_edge_tennis.validation.january_scope import (
    eligible_competitions,
    first_eligible_start,
    load_scope,
    qualifying_window,
    readiness_matrix,
    validate_scope,
)

VERSION = "JANUARY_2027_SCOPE_EVIDENCE_20261127_V1"
DEFAULT_SPEC = Path("specs/january_2027_scope_20261127.json")


def build_evidence(spec_path: Path) -> dict[str, object]:
    spec = load_scope(spec_path)
    validate_scope(spec)
    return {
        "version": VERSION,
        "report_as_of_utc": datetime.now(UTC).isoformat(),
        "spec_version": spec["version"],
        "spec_verified_as_of_utc": spec["verified_as_of_utc"],
        "provider_calls": 0,
        "database_writes": 0,
        "scraping": 0,
        "entries_published": False,
        "draws_published": False,
        "fixtures_created": 0,
        "first_eligible_start": first_eligible_start(spec).isoformat(),
        "qualifying_window": [day.isoformat() for day in qualifying_window(spec)],
        "eligible_competitions": [
            {
                "key": competition["key"],
                "name": competition["name"],
                "tour_scope": competition["tour_scope"],
                "competition_tier": competition["competition_tier"],
                "surface": competition["surface"],
                "scheduled_start": competition["scheduled_start"],
                "scheduled_end": competition["scheduled_end"],
                "best_of_sets": competition["best_of_sets"],
                "draw_size": competition.get("draw_size"),
                "entry_list_status": competition.get("entry_list_status"),
                "draw_status": competition.get("draw_status"),
                "source_ids": competition["source_ids"],
            }
            for competition in eligible_competitions(spec)
        ],
        "readiness_matrix": readiness_matrix(spec),
        "unsupported_competitions": spec["unsupported_competitions"],
        "sources": spec["sources"],
        "operational_requirements": spec["operational_requirements"],
        "readiness_gate": spec["readiness_gate"],
        "explicit_non_claims": spec["explicit_non_claims"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", default=str(DEFAULT_SPEC))
    parser.add_argument(
        "--out-json",
        default="docs/research/evidence/january_2027_scope_20261127.json",
    )
    args = parser.parse_args(argv)
    evidence = build_evidence(Path(args.spec))
    text = json.dumps(evidence, indent=2, sort_keys=True)
    print(text)
    if args.out_json:
        directory = os.path.dirname(os.path.abspath(args.out_json))
        os.makedirs(directory, exist_ok=True)
        with open(args.out_json, "w", encoding="utf-8") as handle:
            handle.write(text + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
