"""Read-only market benchmark audit for the Agent 1 top/latest-800 populations.

Reproduce:

    python ops/validation/market_benchmark_audit_20261127.py \
      --output docs/research/evidence/market_benchmark_real_20261127.json

Only ORIGINAL_TIME quotes may support an edge claim. All stored quotes here are
later than first issue, so the retrospective block is descriptive only.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from sports_edge_tennis.validation.agent1_top800_bookmaker import (  # noqa: E402
    FRESHNESS,
    SPAN,
    TOP_N,
    load_predictions,
    load_quotes,
)
from sports_edge_tennis.validation.market_benchmark_methodology import (  # noqa: E402
    BenchmarkFixture,
    MarketQuotePair,
    benchmark_report,
)

AS_OF = datetime(2026, 11, 26, tzinfo=UTC)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output")
    args = parser.parse_args(argv)
    dsn = os.environ.get("TENNIS_DATABASE_URL")
    if not dsn:
        parser.error("read-only TENNIS_DATABASE_URL required")

    with psycopg.connect(
        dsn, autocommit=True, connect_timeout=20,
        options="-c default_transaction_read_only=on",
    ) as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        rows = load_predictions(conn, as_of=AS_OF)
        settled = [r for r in rows if r["settled"]]
        quotes = load_quotes(conn, sorted({r["match_id"] for r in settled}))

        start_by_match = {r["match_id"]: r["start_at"] for r in settled}
        quotes_by_match = {}
        for match_id, match_quotes in quotes.items():
            start_at = start_by_match.get(match_id, AS_OF)
            books = defaultdict(dict)
            for quote in match_quotes:
                if quote["status"] != "OPEN" or quote["observed"] >= start_at:
                    continue
                books[(quote["source"], quote["bookmaker"])][quote["side"]] = quote
            pairs = []
            for (_source, book), sides in books.items():
                if set(sides) != {"PLAYER_1", "PLAYER_2"}:
                    continue
                q1, q2 = sides["PLAYER_1"], sides["PLAYER_2"]
                if abs(q1["observed"] - q2["observed"]) > SPAN:
                    continue
                implied = 1 / q1["price"] + 1 / q2["price"]
                if not 0.95 <= implied <= 1.25:
                    continue
                observed = max(q1["observed"], q2["observed"])
                if (
                    max(q1["observed"], q1["as_of"], q1["ingested"])
                    <= q1["locked_at"]
                    and max(q2["observed"], q2["as_of"], q2["ingested"])
                    <= q2["locked_at"]
                    and observed >= q1["locked_at"] - FRESHNESS
                ):
                    provenance = "ORIGINAL_TIME"
                elif observed < q1["locked_at"] - FRESHNESS:
                    provenance = "BACKFILLED"
                else:
                    provenance = "LATE"
                pairs.append(MarketQuotePair(
                    match_id=match_id,
                    bookmaker_code=book,
                    source_system=_source,
                    player_1_price=q1["price"],
                    player_2_price=q2["price"],
                    observed_at=observed,
                    ingested_at=max(q1["ingested"], q2["ingested"]),
                    provenance=provenance,
                ))
            if pairs:
                quotes_by_match[match_id] = pairs

        def fixtures_for(population):
            return [
                BenchmarkFixture(
                    match_id=r["match_id"], winner=r["winner"],
                    model_p1=r["p1"], scheduled_start_at=r["start_at"],
                )
                for r in population
            ]

        def top_confidence(tour=None):
            pool = settled if tour is None else [
                r for r in settled if r["tour"] == tour]
            return sorted(pool, key=lambda r: (-r["confidence"], r["match_id"]))[:TOP_N]

        def latest(tour=None):
            pool = settled if tour is None else [
                r for r in settled if r["tour"] == tour]
            return sorted(
                pool, key=lambda r: (-r["start_at"].timestamp(), r["match_id"])
            )[:TOP_N]

        populations = {
            "top800_by_confidence_all": top_confidence(),
            "top800_by_confidence_atp": top_confidence("ATP_MAIN"),
            "top800_by_confidence_challenger": top_confidence("CHALLENGER"),
            "latest800_settled_all": latest(),
            "latest800_settled_atp": latest("ATP_MAIN"),
            "latest800_settled_challenger": latest("CHALLENGER"),
        }
        out = {
            "measured_at": datetime.now(UTC).isoformat(),
            "as_of": AS_OF.isoformat(),
            "populations": {},
            "provider_calls": 0,
        }
        for name, population in populations.items():
            report = benchmark_report(
                fixtures=fixtures_for(population),
                quotes_by_match=quotes_by_match,
            )
            out["populations"][name] = {
                "n": len(population),
                "original_time_fixtures": report["fixtures_with_original_time_quote"],
                "retrospective": report["retrospective"],
                "comparisons_original": {
                    key: {
                        "available": value.get("available"),
                        "coverage": value.get("coverage"),
                    }
                    for key, value in report["comparisons"].items()
                },
                "odds_distribution": report["odds_distribution"],
                "duplicate_pairs": report["retrospective"]["duplicate_pairs"],
                "clv": report["clv"],
            }

    payload = json.dumps(out, sort_keys=True, default=str, indent=2)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(payload + "\n")
    print(payload)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
