"""Export the sanitised Agent 1 selective-performance population.

Reads the frozen per-target ledger and enriches it with public fixture metadata
(round code), player inactivity and a pre-issue bookmaker benchmark.  Writes a
single deterministic CSV plus a target census.  Read-only: the database session
is forced to ``default_transaction_read_only=on`` and no provider is called.

The committed CSV is the input to
``ops/validation/a1_selective_performance_experiment_20261126.py``.  Running
this exporter again against the same database state produces the same rows
(apart from the recorded generation timestamp in the metadata file).
"""
from __future__ import annotations

import argparse
import bisect
import csv
import hashlib
import json
import statistics
import sys
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from sports_edge_tennis.data.agent1_history import load_agent1_history
from sports_edge_tennis.validation.a1_selective_performance import (
    confidence,
    selection_side,
)

E3_CUTOFF = datetime(2026, 11, 25, 7, 20, tzinfo=UTC)
DEFAULT_DSN_FILE = Path(r"C:\Users\Desktop 2\AppData\Local\Temp\opencode\tennis_dsn.txt")

COLUMNS = [
    "prediction_id",
    "match_id",
    "prediction_run_id",
    "match_version_id",
    "as_of",
    "prediction_created_at",
    "match_start_at",
    "date",
    "tour",
    "competition_tier",
    "round_code",
    "surface",
    "best_of_sets",
    "qualifying_flag",
    "season_year",
    "stored_side",
    "stored_probability",
    "p1_original",
    "p1_corrected",
    "confidence_original",
    "confidence_corrected",
    "side_original",
    "side_corrected",
    "winner_side",
    "y",
    "correct_original",
    "correct_corrected",
    "overall_matches_original_p1",
    "overall_matches_original_p2",
    "surface_matches_original_p1",
    "surface_matches_original_p2",
    "overall_rating_original_p1",
    "overall_rating_original_p2",
    "surface_rating_original_p1",
    "surface_rating_original_p2",
    "effective_rating_original_p1",
    "effective_rating_original_p2",
    "overall_matches_corrected_p1",
    "overall_matches_corrected_p2",
    "surface_matches_corrected_p1",
    "surface_matches_corrected_p2",
    "overall_rating_corrected_p1",
    "overall_rating_corrected_p2",
    "surface_rating_corrected_p1",
    "surface_rating_corrected_p2",
    "effective_rating_corrected_p1",
    "effective_rating_corrected_p2",
    "inactivity_days_original_p1",
    "inactivity_days_original_p2",
    "inactivity_days_corrected_p1",
    "inactivity_days_corrected_p2",
    "resolution_p1_live",
    "resolution_p1_model",
    "resolution_p1_method",
    "resolution_p1_evidence",
    "resolution_p1_name_source",
    "resolution_p1_candidate_count",
    "resolution_p1_bridged",
    "resolution_p2_live",
    "resolution_p2_model",
    "resolution_p2_method",
    "resolution_p2_evidence",
    "resolution_p2_name_source",
    "resolution_p2_candidate_count",
    "resolution_p2_bridged",
    "market_p1",
    "market_age_hours",
    "market_bookmaker_count",
]


def dsn_from_file(path: Path) -> str:
    if not path.exists():
        raise SystemExit(f"DSN file not found: {path}")
    return path.read_text(encoding="utf-8").strip()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def iso(value) -> str | None:
    return value.isoformat() if value is not None else None


def _history_index(history):
    """player -> (visible_at list, prefix max match_start list)."""
    grouped = defaultdict(list)
    for observation in history:
        available = max(observation.as_of, observation.source_observed_at)
        start = observation.match_start_at or observation.as_of
        for player in (observation.player_1_id, observation.player_2_id):
            grouped[player].append((available, start))
    index = {}
    for player, rows in grouped.items():
        rows.sort(key=lambda item: item[0])
        visible = []
        prefix = []
        latest = None
        for available, start in rows:
            visible.append(available)
            latest = start if latest is None or start > latest else latest
            prefix.append(latest)
        index[player] = (visible, prefix)
    return index


def inactivity_days(index, player_id, as_of: datetime) -> float | None:
    if player_id is None or int(player_id) not in index:
        return None
    visible, prefix = index[int(player_id)]
    position = bisect.bisect_right(visible, as_of)
    if position == 0:
        return None
    last_start = prefix[position - 1]
    if last_start is None:
        return None
    return (as_of - last_start).total_seconds() / 86400.0


def market_probabilities(conn, match_ids: list[int]) -> dict[int, dict[str, float | int | None]]:
    """Latest pre-issue devigged player-1 probability, median across books."""
    if not match_ids:
        return {}
    rows = conn.execute(
        """
        SELECT match_id, bookmaker_code, source_observed_at, selection_type::text,
               decimal_price
        FROM odds_snapshots
        WHERE match_id = ANY(%s)
          AND market_type='MATCH_WINNER'
          AND source_observed_at <= %s
          AND decimal_price > 1
        ORDER BY match_id, source_observed_at, bookmaker_code
        """,
        (match_ids, E3_CUTOFF),
    ).fetchall()
    quotes: dict[tuple[int, str, datetime], dict[str, float]] = defaultdict(dict)
    for match_id, bookmaker, observed, selection, price in rows:
        quotes[(int(match_id), str(bookmaker), observed)][str(selection)] = float(price)
    by_match: dict[int, list[tuple[datetime, float, int]]] = defaultdict(list)
    for (match_id, _bookmaker, observed), pair in quotes.items():
        p1 = pair.get("PLAYER_1")
        p2 = pair.get("PLAYER_2")
        if not p1 or not p2 or p1 <= 1 or p2 <= 1:
            continue
        q1, q2 = 1.0 / p1, 1.0 / p2
        by_match[match_id].append((observed, q1 / (q1 + q2), 1))
    result: dict[int, dict[str, float | int | None]] = {}
    for match_id, entries in by_match.items():
        # Aggregate each timestamp across books, then keep the latest timestamp.
        by_time: dict[datetime, list[float]] = defaultdict(list)
        for observed, probability, _ in entries:
            by_time[observed].append(probability)
        observed, values = max(by_time.items(), key=lambda item: item[0])
        result[match_id] = {
            "market_p1": statistics.median(values),
            "market_observed_at": observed,
            "market_bookmaker_count": len(values),
        }
    return result


def write_csv(path: Path, rows: list[dict], columns: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column, "") for column in columns})


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger", default=str(ROOT / "tmp" / "ledger_full.jsonl"))
    parser.add_argument("--dsn-file", default=str(DEFAULT_DSN_FILE))
    parser.add_argument(
        "--population-out",
        default=str(ROOT / "docs" / "research" / "evidence" / "a1_selective_population_20261126.csv"),
    )
    parser.add_argument(
        "--census-out",
        default=str(ROOT / "docs" / "research" / "evidence" / "a1_target_census_20261126.csv"),
    )
    parser.add_argument(
        "--metadata-out",
        default=str(ROOT / "docs" / "research" / "evidence" / "a1_selective_population_meta_20261126.json"),
    )
    args = parser.parse_args(argv)

    ledger_path = Path(args.ledger)
    ledger = [json.loads(line) for line in ledger_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    e3 = [row for row in ledger if row.get("experiment") == "E3"]
    settled = [row for row in e3 if row.get("outcome_status") == "COMPLETED" and row.get("winner_side") in (1, 2)]
    if len(e3) != 3776 or len(settled) != 3223:
        raise SystemExit(f"unexpected population: targets={len(e3)} settled={len(settled)}")

    dsn = dsn_from_file(Path(args.dsn_file))
    with psycopg.connect(
        dsn,
        autocommit=True,
        options="-c default_transaction_read_only=on -c statement_timeout=1800000",
    ) as conn:
        versions = sorted({int(row["match_version_id"]) for row in e3 if row.get("match_version_id")})
        round_by_version = dict(
            conn.execute(
                "SELECT match_version_id, round_code FROM match_versions WHERE match_version_id = ANY(%s)",
                (versions,),
            ).fetchall()
        )
        matches = sorted({int(row["match_id"]) for row in e3})
        tournament_by_match = {
            int(match_id): (int(tournament_id), season_year)
            for match_id, tournament_id, season_year in conn.execute(
                """
                SELECT m.match_id, t.tournament_id, te.season_year
                FROM matches m
                JOIN tournament_editions te USING (tournament_edition_id)
                JOIN tournaments t USING (tournament_id)
                WHERE m.match_id = ANY(%s)
                """,
                (matches,),
            ).fetchall()
        }
        market = market_probabilities(conn, matches)
        unbridged_history = load_agent1_history(conn, cutoff=E3_CUTOFF, ingested_by=E3_CUTOFF)
        bridged_history = load_agent1_history(
            conn, cutoff=E3_CUTOFF, ingested_by=E3_CUTOFF, bridge_model_identities=True
        )
    unbridged_index = _history_index(unbridged_history)
    bridged_index = _history_index(bridged_history)

    rows: list[dict] = []
    for source in sorted(settled, key=lambda row: int(row["prediction_id"])):
        as_of = datetime.fromisoformat(source["as_of"])
        original = source["A"]
        corrected = source["B"]
        p1_original = float(original["p1"])
        p1_corrected = float(corrected["p1"])
        winner_side = int(source["winner_side"])
        y = int(source["y"])
        prediction_id = int(source["prediction_id"])
        match_id = int(source["match_id"])
        round_code = round_by_version.get(int(source["match_version_id"]))
        tournament_id, season_year = tournament_by_match.get(match_id, (None, None))
        p1_live = int(source["p1_live"])
        p2_live = int(source["p2_live"])
        p1_model = (source.get("p1_resolution") or {}).get("resolved_player_id") or p1_live
        p2_model = (source.get("p2_resolution") or {}).get("resolved_player_id") or p2_live
        market_row = market.get(match_id, {})
        market_p1 = market_row.get("market_p1")
        market_age = None
        if market_p1 is not None:
            observed = market_row.get("market_observed_at")
            if observed is not None:
                market_age = (as_of - observed).total_seconds() / 3600.0
        row = {
            "prediction_id": prediction_id,
            "match_id": match_id,
            "prediction_run_id": int(source["prediction_run_id"]),
            "match_version_id": int(source["match_version_id"]),
            "as_of": source["as_of"],
            "prediction_created_at": source.get("prediction_created_at"),
            "match_start_at": source.get("match_start_at"),
            "date": (source.get("match_start_at") or source["as_of"])[:10],
            "tour": source["tour"],
            "competition_tier": source["competition_tier"],
            "round_code": round_code,
            "surface": source.get("raw_surface"),
            "best_of_sets": int(source["best_of_sets"]),
            "qualifying_flag": bool(source["qualifying_flag"]),
            "season_year": season_year,
            "stored_side": int(source["stored_side"]),
            "stored_probability": float(source["stored_p1"]),
            "p1_original": p1_original,
            "p1_corrected": p1_corrected,
            "confidence_original": confidence(p1_original),
            "confidence_corrected": confidence(p1_corrected),
            "side_original": selection_side(p1_original),
            "side_corrected": selection_side(p1_corrected),
            "winner_side": winner_side,
            "y": y,
            "correct_original": int(selection_side(p1_original) == winner_side),
            "correct_corrected": int(selection_side(p1_corrected) == winner_side),
            "overall_matches_original_p1": original.get("player_1_overall_matches"),
            "overall_matches_original_p2": original.get("player_2_overall_matches"),
            "surface_matches_original_p1": original.get("player_1_surface_matches"),
            "surface_matches_original_p2": original.get("player_2_surface_matches"),
            "overall_rating_original_p1": original.get("player_1_overall_rating"),
            "overall_rating_original_p2": original.get("player_2_overall_rating"),
            "surface_rating_original_p1": original.get("player_1_surface_rating"),
            "surface_rating_original_p2": original.get("player_2_surface_rating"),
            "effective_rating_original_p1": original.get("player_1_effective_rating"),
            "effective_rating_original_p2": original.get("player_2_effective_rating"),
            "overall_matches_corrected_p1": corrected.get("player_1_overall_matches"),
            "overall_matches_corrected_p2": corrected.get("player_2_overall_matches"),
            "surface_matches_corrected_p1": corrected.get("player_1_surface_matches"),
            "surface_matches_corrected_p2": corrected.get("player_2_surface_matches"),
            "overall_rating_corrected_p1": corrected.get("player_1_overall_rating"),
            "overall_rating_corrected_p2": corrected.get("player_2_overall_rating"),
            "surface_rating_corrected_p1": corrected.get("player_1_surface_rating"),
            "surface_rating_corrected_p2": corrected.get("player_2_surface_rating"),
            "effective_rating_corrected_p1": corrected.get("player_1_effective_rating"),
            "effective_rating_corrected_p2": corrected.get("player_2_effective_rating"),
            "inactivity_days_original_p1": inactivity_days(unbridged_index, p1_live, as_of),
            "inactivity_days_original_p2": inactivity_days(unbridged_index, p2_live, as_of),
            "inactivity_days_corrected_p1": inactivity_days(bridged_index, p1_model, as_of),
            "inactivity_days_corrected_p2": inactivity_days(bridged_index, p2_model, as_of),
            "resolution_p1_live": p1_live,
            "resolution_p1_model": p1_model,
            "resolution_p1_method": (source.get("p1_resolution") or {}).get("method"),
            "resolution_p1_evidence": (source.get("p1_resolution") or {}).get("evidence"),
            "resolution_p1_name_source": (source.get("p1_resolution") or {}).get("name_source"),
            "resolution_p1_candidate_count": (source.get("p1_resolution") or {}).get("candidate_count"),
            "resolution_p1_bridged": bool((source.get("p1_resolution") or {}).get("bridged")),
            "resolution_p2_live": p2_live,
            "resolution_p2_model": p2_model,
            "resolution_p2_method": (source.get("p2_resolution") or {}).get("method"),
            "resolution_p2_evidence": (source.get("p2_resolution") or {}).get("evidence"),
            "resolution_p2_name_source": (source.get("p2_resolution") or {}).get("name_source"),
            "resolution_p2_candidate_count": (source.get("p2_resolution") or {}).get("candidate_count"),
            "resolution_p2_bridged": bool((source.get("p2_resolution") or {}).get("bridged")),
            "market_p1": market_p1,
            "market_age_hours": market_age,
            "market_bookmaker_count": market_row.get("market_bookmaker_count"),
        }
        row["tournament_id"] = tournament_id
        rows.append(row)

    population_path = Path(args.population_out)
    write_csv(population_path, rows, COLUMNS)

    census_path = Path(args.census_out)
    census_columns = [
        "prediction_id",
        "match_id",
        "prediction_run_id",
        "match_version_id",
        "as_of",
        "match_start_at",
        "tour",
        "competition_tier",
        "qualifying_flag",
        "outcome_status",
        "winner_side",
        "settled",
        "exclusion_reason",
    ]
    census_rows = []
    settled_ids = {int(row["prediction_id"]) for row in settled}
    for source in sorted(e3, key=lambda row: int(row["prediction_id"])):
        outcome = source.get("outcome_status")
        winner = source.get("winner_side")
        if source["prediction_id"] in settled_ids:
            reason = None
        elif outcome == "COMPLETED" and winner not in (1, 2):
            reason = "COMPLETED_WITHOUT_VALID_WINNER"
        elif outcome is None:
            reason = "OUTCOME_NOT_OBSERVED_AT_FREEZE"
        else:
            reason = f"OUTCOME_{outcome}"
        census_rows.append(
            {
                "prediction_id": int(source["prediction_id"]),
                "match_id": int(source["match_id"]),
                "prediction_run_id": int(source["prediction_run_id"]),
                "match_version_id": int(source["match_version_id"]),
                "as_of": source["as_of"],
                "match_start_at": source.get("match_start_at"),
                "tour": source["tour"],
                "competition_tier": source["competition_tier"],
                "qualifying_flag": bool(source["qualifying_flag"]),
                "outcome_status": outcome or "UNOBSERVED",
                "winner_side": winner,
                "settled": int(source["prediction_id"] in settled_ids),
                "exclusion_reason": reason,
            }
        )
    write_csv(census_path, census_rows, census_columns)

    metadata = {
        "version": "A1_SELECTIVE_POPULATION_EXPORT_V1",
        "generated_at": datetime.now(UTC).isoformat(),
        "source_ledger": str(ledger_path.name),
        "source_ledger_sha256": sha256(ledger_path),
        "source_ledger_rows": len(ledger),
        "e3_cutoff": E3_CUTOFF.isoformat(),
        "e3_targets": len(e3),
        "settled_population": len(rows),
        "excluded_targets": len(census_rows) - len(rows),
        "exclusion_counts": {
            reason: sum(1 for row in census_rows if row["exclusion_reason"] == reason)
            for reason in sorted({row["exclusion_reason"] for row in census_rows if row["exclusion_reason"]})
        },
        "rows_with_round_code": sum(1 for row in rows if row["round_code"]),
        "rows_with_market_benchmark": sum(1 for row in rows if row["market_p1"] is not None),
        "market_benchmark_rule": (
            "latest pre-issue MATCH_WINNER quote at or before the original prediction as_of; "
            "devigged across PLAYER_1/PLAYER_2; median across bookmakers"
        ),
        "population_csv": population_path.name,
        "population_csv_sha256": sha256(population_path),
        "census_csv": census_path.name,
        "census_csv_sha256": sha256(census_path),
        "database_writes": False,
        "production_changed": False,
        "can_promote": False,
        "note": (
            "No names, aliases, contact details, account ids or provider payloads are exported. "
            "The bookmaker benchmark is retained only where a legitimate pre-issue quote existed."
        ),
    }
    Path(args.metadata_out).write_text(json.dumps(metadata, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(metadata, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
