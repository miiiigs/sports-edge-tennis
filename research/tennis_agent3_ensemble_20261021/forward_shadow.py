"""Append-only forward shadow collection.

Run after ``extract.py`` on a schedule. This process appends new shadow rows to
a local JSONL ledger and never writes to the tennis database. Rows are deduped
on ``(match_id, prediction_run_id)`` so repeated extraction is idempotent.

The ledger is the forward evidence source. Retrospective rows already present
from the impact audit must not be counted as forward evidence; start the
forward ledger from an empty file and record the first collection timestamp.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from research.tennis_agent3_ensemble_20261021.analysis import load_jsonl
from research.tennis_agent3_ensemble_20261021.ensemble_shadow import evaluate_rows


def _key(row: dict[str, Any]) -> tuple[int, int]:
    return int(row["match_id"]), int(row["prediction_run_id"])


def append_new(
    shadow_rows: list[dict[str, Any]],
    existing_rows: Iterable[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    """Return new rows and the count of duplicates skipped."""
    existing = {_key(row) for row in existing_rows}
    new_rows = []
    duplicates = 0
    for row in shadow_rows:
        if _key(row) in existing:
            duplicates += 1
            continue
        existing.add(_key(row))
        new_rows.append(row)
    return new_rows, duplicates


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def collect(
    *,
    extract_dir: Path,
    shadow_path: Path,
    state_path: Path,
) -> dict[str, Any]:
    rows = load_jsonl(extract_dir / "ensemble_rows.jsonl")
    evaluated = evaluate_rows(rows)
    existing = load_jsonl(shadow_path) if shadow_path.exists() else []
    new_rows, duplicates = append_new(evaluated, existing)
    shadow_path.parent.mkdir(parents=True, exist_ok=True)
    with shadow_path.open("a", encoding="utf-8", newline="\n") as handle:
        for row in new_rows:
            handle.write(json.dumps(row, sort_keys=True, ensure_ascii=True) + "\n")
    now = datetime.now(timezone.utc).isoformat()
    state = {
        "updated_utc": now,
        "extract_dir": str(extract_dir),
        "shadow_path": str(shadow_path),
        "cumulative_rows": len(existing) + len(new_rows),
        "new_rows": len(new_rows),
        "duplicates_skipped": duplicates,
        "shadow_sha256": _sha256(shadow_path) if shadow_path.exists() else None,
    }
    state_path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return state


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--extract-dir", type=Path, required=True)
    parser.add_argument("--shadow-path", type=Path, required=True)
    parser.add_argument("--state-path", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(collect(**vars(args)), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
