"""Commercial interpretation of the Agent 1 top-800 populations.

Read-only. Produces the exact population definitions and the measured bookmaker
coverage for each, without extrapolating a small priced subset to the whole
population.

Populations
-----------
* ``top800_by_confidence``: the 800 highest-confidence first-issue Agent 1
  predictions in the frozen research window, overall and by tour.
* ``latest800_settled``: the most recent 800 settled first-issue Agent 1
  predictions by scheduled start, overall and by tour.

Bookmaker rules
---------------
Only a two-sided MATCH_WINNER quote observed, timestamped and ingested at or
before the original prediction lock, within a six-hour freshness window, with a
five-minute side span, the same data source and a sane de-vig total is counted
as original-time coverage. Everything else is reported as late, backfilled or
unmatched; it is never promoted.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg

VERSION = "AGENT1_TOP800_BOOKMAKER_V1"
WINDOW_START = datetime(2026, 9, 10, tzinfo=UTC)
WINDOW_END = datetime(2026, 11, 21, tzinfo=UTC)
FRESHNESS = timedelta(hours=6)
SPAN = timedelta(minutes=5)
TOP_N = 800


def _utc(value: datetime, name: str) -> datetime:
    if value is None or value.tzinfo is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(UTC)


def load_predictions(conn: psycopg.Connection, *, as_of: datetime) -> list[dict]:
    rows = conn.execute(
        """
        WITH first_run AS (
            SELECT DISTINCT ON (t.match_id)
                   t.match_id, pr.prediction_run_id,
                   pr.prediction_finalized_at
            FROM prediction_run_targets t
            JOIN prediction_runs pr USING (prediction_run_id)
            WHERE pr.run_status = 'COMPLETED'
              AND pr.prediction_finalized_at IS NOT NULL
              AND pr.experiment_id IS NULL
              AND pr.prediction_contract_version = 'predictive_agent_result_v1'
            ORDER BY t.match_id, pr.prediction_finalized_at, pr.prediction_run_id
        )
        SELECT p.match_id, p.prediction_run_id, p.selection_side,
               p.win_probability, p.as_of, p.prediction_locked_at,
               v.match_start_at, v.tour_scope::text, v.surface::text,
               v.qualifying_flag,
               r.outcome_status::text, r.winner_side, r.known_at
        FROM first_run f
        JOIN predictions p
          ON p.prediction_run_id = f.prediction_run_id
         AND p.match_id = f.match_id
         AND p.agent_code = 'AGENT_1_SURFACE_ELO'
         AND p.prediction_status = 'PREDICTED'
         AND p.selection_side IN (1, 2)
         AND p.win_probability IS NOT NULL
        JOIN LATERAL (
            SELECT match_start_at, tour_scope, surface, qualifying_flag
            FROM match_versions mv
            WHERE mv.match_id = p.match_id
              AND mv.as_of <= p.as_of
              AND mv.source_observed_at <= p.as_of
              AND mv.ingested_at <= p.as_of
            ORDER BY mv.as_of DESC, mv.source_observed_at DESC,
                     mv.match_version_id DESC LIMIT 1
        ) v ON TRUE
        LEFT JOIN LATERAL (
            SELECT outcome_status, winner_side,
                   GREATEST(as_of, source_observed_at, ingested_at) AS known_at
            FROM match_result_observations ro
            WHERE ro.match_id = p.match_id
              AND ro.as_of <= %(as_of)s
              AND ro.source_observed_at <= %(as_of)s
              AND ro.ingested_at <= %(as_of)s
            ORDER BY ro.as_of DESC, ro.source_observed_at DESC,
                     ro.match_result_observation_id DESC LIMIT 1
        ) r ON TRUE
        WHERE p.as_of <= %(as_of)s
          AND v.match_start_at >= %(start)s
          AND v.match_start_at < %(as_of)s
          AND v.tour_scope IN ('ATP_MAIN','CHALLENGER')
        ORDER BY v.match_start_at DESC, p.match_id
        """,
        {"as_of": _utc(as_of, "as_of"), "start": WINDOW_START},
    ).fetchall()
    out = []
    for row in rows:
        side = int(row[2])
        confidence = float(row[3])
        if not (.5 <= confidence < 1):
            continue
        p1 = confidence if side == 1 else 1 - confidence
        winner = int(row[11]) if row[11] in (1, 2) else None
        settled = (
            row[10] == "COMPLETED" and winner in (1, 2)
            and row[12] is not None and _utc(row[12], "known_at") <= _utc(as_of, "as_of"))
        out.append({
            "match_id": int(row[0]), "run_id": int(row[1]),
            "selected_side": side, "confidence": confidence, "p1": p1,
            "as_of": _utc(row[4], "as_of"),
            "locked_at": _utc(row[5], "locked_at") if row[5] else None,
            "start_at": _utc(row[6], "start_at"),
            "tour": row[7], "surface": row[8] or "UNKNOWN",
            "qualifying": bool(row[9]),
            "winner": winner, "settled": settled,
            "known_at": _utc(row[12], "known_at") if row[12] else None,
        })
    return out


def load_quotes(conn: psycopg.Connection, match_ids: list[int]) -> dict[int, list[dict]]:
    if not match_ids:
        return {}
    rows = conn.execute(
        """
        SELECT o.match_id, o.source_system::text, o.bookmaker_code,
               o.selection_type::text, o.decimal_price::float8,
               o.market_status::text, o.source_observed_at, o.as_of,
               o.ingested_at, lock.locked_at
        FROM odds_snapshots o
        JOIN LATERAL (
            SELECT p.prediction_locked_at AS locked_at
            FROM predictions p
            WHERE p.match_id = o.match_id
              AND p.agent_code = 'AGENT_1_SURFACE_ELO'
              AND p.prediction_status = 'PREDICTED'
            ORDER BY p.as_of LIMIT 1
        ) lock ON TRUE
        WHERE o.match_id = ANY(%s)
          AND o.market_type = 'MATCH_WINNER'
          AND o.selection_type IN ('PLAYER_1','PLAYER_2')
        ORDER BY o.match_id, o.bookmaker_code, o.selection_type,
                 o.source_observed_at DESC, o.odds_snapshot_id DESC
        """,
        (match_ids,),
    ).fetchall()
    by_match: dict[int, list[dict]] = defaultdict(list)
    for row in rows:
        by_match[int(row[0])].append({
            "source": row[1], "bookmaker": row[2], "side": row[3],
            "price": row[4], "status": row[5], "observed": row[6],
            "as_of": row[7], "ingested": row[8], "locked_at": row[9],
        })
    return by_match


def _quote_state(quote: dict) -> str:
    lock = quote["locked_at"]
    if lock is None:
        return "UNMATCHED"
    observed, as_of, ingested = quote["observed"], quote["as_of"], quote["ingested"]
    if max(observed, as_of, ingested) <= lock and observed >= lock - FRESHNESS:
        return "PREISSUE"
    if observed < lock - FRESHNESS and ingested > lock:
        return "BACKFILLED"
    return "LATE"


def _preissue_pair(quotes: list[dict]) -> dict | None:
    """Latest strict two-sided quote per book, same source, five-minute span."""
    books: dict[tuple, dict] = defaultdict(dict)
    for quote in quotes:
        if _quote_state(quote) != "PREISSUE" or quote["status"] != "OPEN":
            continue
        books[(quote["source"], quote["bookmaker"])][quote["side"]] = quote
    eligible = []
    for (source, book), sides in books.items():
        if set(sides) != {"PLAYER_1", "PLAYER_2"}:
            continue
        q1, q2 = sides["PLAYER_1"], sides["PLAYER_2"]
        if abs(q1["observed"] - q2["observed"]) > SPAN:
            continue
        if not (1 < q1["price"] < 100 and 1 < q2["price"] < 100):
            continue
        implied = [1 / q1["price"], 1 / q2["price"]]
        total = sum(implied)
        if not .95 <= total <= 1.25:
            continue
        eligible.append({
            "source": source, "bookmaker": book,
            "p1": implied[0] / total, "prices": (q1["price"], q2["price"]),
            "observed": min(q1["observed"], q2["observed"]),
        })
    if not eligible:
        return None
    return min(eligible, key=lambda item: item["observed"])


def coverage(row: dict, quotes: list[dict]) -> dict:
    states = Counter(_quote_state(q) for q in quotes)
    pair = _preissue_pair(quotes)
    return {
        "any_quote": bool(quotes),
        "preissue_two_sided": pair is not None,
        "pair": pair,
        "late_only": bool(quotes) and states.get("PREISSUE", 0) == 0
        and states.get("LATE", 0) > 0,
        "backfilled_only": bool(quotes) and states.get("PREISSUE", 0) == 0
        and states.get("BACKFILLED", 0) > 0,
        "unmatched": not quotes,
        "quotes_observed_before_issue": sum(
            1 for q in quotes if q["locked_at"] and q["observed"] <= q["locked_at"]),
        "quotes_received_before_issue": sum(
            1 for q in quotes if q["locked_at"] and q["ingested"] <= q["locked_at"]),
    }


def _brier(pairs):
    return sum((p - y) ** 2 for p, y in pairs) / len(pairs) if pairs else None


def _logloss(pairs):
    if not pairs:
        return None
    return -sum(y * math.log(max(p, 1e-12))
                + (1 - y) * math.log(max(1 - p, 1e-12))
                for p, y in pairs) / len(pairs)


def summarize(name: str, definition: str, rows: list[dict],
              quotes: dict[int, list[dict]]) -> dict[str, Any]:
    settled = [r for r in rows if r["settled"]]
    model = [(r["p1"], int(r["winner"] == 1)) for r in settled]
    wins = sum(r["selected_side"] == r["winner"] for r in settled)
    model_expected = sum(r["confidence"] for r in settled)
    confidences = [r["confidence"] for r in settled]
    variance = sum(c * (1 - c) for c in confidences)
    coverage_rows = [(r, coverage(r, quotes.get(r["match_id"], [])))
                     for r in settled]
    priced = [(r, c) for r, c in coverage_rows if c["pair"] is not None]
    market_pairs = [(c["pair"]["p1"], int(r["winner"] == 1)) for r, c in priced]
    model_priced = [(r["p1"], int(r["winner"] == 1)) for r, _ in priced]
    market_expected = sum(
        c["pair"]["p1"] if r["selected_side"] == 1 else 1 - c["pair"]["p1"]
        for r, c in priced)
    prices = [p for _, c in priced for p in c["pair"]["prices"]]
    favourite_agreement = sum(
        (r["selected_side"] == (1 if c["pair"]["p1"] >= .5 else 2))
        for r, c in priced)
    return {
        "population": name,
        "definition": definition,
        "n": len(rows),
        "settled": len(settled),
        "ids_sha256": hashlib.sha256(json.dumps(
            sorted(r["match_id"] for r in rows),
            separators=(",", ":")).encode()).hexdigest(),
        "actual_wins": wins,
        "actual_rate": wins / len(settled) if settled else None,
        "mean_predicted_confidence": (
            sum(r["confidence"] for r in settled) / len(settled)
            if settled else None),
        "model_expected_wins": model_expected,
        "model_residual": wins - model_expected,
        "model_expected_variance_independent": variance,
        "model_residual_z_independent": (
            (wins - model_expected) / math.sqrt(variance) if variance else None),
        "model_brier": _brier(model),
        "model_log_loss": _logloss(model),
        "bookmaker": {
            "priced": len(priced),
            "coverage": len(priced) / len(settled) if settled else None,
            "actual_wins_on_priced": sum(
                r["selected_side"] == r["winner"] for r, _ in priced),
            "model_expected_wins_on_priced": sum(
                r["confidence"] for r, _ in priced),
            "market_expected_wins_on_priced": market_expected,
            "mean_market_probability_of_selected_side": (
                market_expected / len(priced) if priced else None),
            "de_vig": "proportional inverse-odds normalization per two-sided book",
            "model_brier_on_priced": _brier(model_priced),
            "market_brier_on_priced": _brier(market_pairs),
            "favourite_agreement": favourite_agreement,
            "favourite_agreement_rate": (
                favourite_agreement / len(priced) if priced else None),
            "by_bookmaker": dict(sorted(Counter(
                c["pair"]["bookmaker"] for _, c in priced).items())),
            "odds_distribution": {
                "count": len(prices),
                "min": min(prices) if prices else None,
                "median": (
                    sorted(prices)[len(prices) // 2] if prices else None),
                "max": max(prices) if prices else None,
                "favourite_price_median": (
                    sorted(max(c["pair"]["prices"]) for _, c in priced)[
                        len(priced) // 2] if priced else None),
                "underdog_price_median": (
                    sorted(min(c["pair"]["prices"]) for _, c in priced)[
                        len(priced) // 2] if priced else None),
            },
            "no_extrapolation": (
                "Bookmaker metrics use only the priced subset. They are not "
                "projected to the unpriced remainder."),
        },
        "coverage": {
            "any_quote": sum(c["any_quote"] for _, c in coverage_rows),
            "preissue_two_sided": len(priced),
            "late_only": sum(c["late_only"] for _, c in coverage_rows),
            "backfilled_only": sum(c["backfilled_only"] for _, c in coverage_rows),
            "unmatched": sum(c["unmatched"] for _, c in coverage_rows),
            "quotes_observed_before_issue": sum(
                c["quotes_observed_before_issue"] for _, c in coverage_rows),
            "quotes_received_before_issue": sum(
                c["quotes_received_before_issue"] for _, c in coverage_rows),
        },
    }


def run(conn: psycopg.Connection, *, as_of: datetime) -> dict[str, Any]:
    rows = load_predictions(conn, as_of=as_of)
    quotes = load_quotes(conn, [r["match_id"] for r in rows])
    settled = [r for r in rows if r["settled"]]

    def top_by_confidence(tour: str | None) -> list[dict]:
        pool = settled if tour is None else [r for r in settled if r["tour"] == tour]
        return sorted(pool, key=lambda r: (-r["confidence"], r["match_id"]))[:TOP_N]

    def top_by_latest(tour: str | None) -> list[dict]:
        pool = settled if tour is None else [r for r in settled if r["tour"] == tour]
        return sorted(pool, key=lambda r: (-r["start_at"].timestamp(), r["match_id"]))[:TOP_N]
    report: dict[str, Any] = {
        "version": VERSION,
        "report_as_of_utc": _utc(as_of, "as_of").isoformat(),
        "window": [WINDOW_START.isoformat(), WINDOW_END.isoformat()],
        "top_n": TOP_N,
        "available_settled_first_issue_a1": len(settled),
        "populations": {},
        "limitations": [
            "Original-time coverage is strict; late and backfilled quotes are separated.",
            "No market metric is extrapolated to unpriced selections.",
            "Agent 1 probabilities are the stored first-issue values, not a recalibration.",
            "The research window was previously inspected; this is descriptive.",
        ],
        "provider_calls": 0,
        "database_writes": False,
    }
    definitions = {
        "top800_by_confidence_all": (top_by_confidence(None), "highest stored A1 confidence, all tours"),
        "top800_by_confidence_atp": (
            top_by_confidence("ATP_MAIN"), "highest stored A1 confidence, ATP main"),
        "top800_by_confidence_challenger": (
            top_by_confidence("CHALLENGER"), "highest stored A1 confidence, Challenger"),
        "latest800_settled_all": (top_by_latest(None), "most recent scheduled start, all tours"),
        "latest800_settled_atp": (
            top_by_latest("ATP_MAIN"), "most recent scheduled start, ATP main"),
        "latest800_settled_challenger": (
            top_by_latest("CHALLENGER"), "most recent scheduled start, Challenger"),
    }
    for name, (population, definition) in definitions.items():
        report["populations"][name] = summarize(name, definition, population, quotes)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--as-of-utc")
    parser.add_argument("--output")
    args = parser.parse_args()
    as_of = (datetime.fromisoformat(args.as_of_utc) if args.as_of_utc
             else datetime.now(UTC))
    dsn = os.environ.get("TENNIS_DATABASE_URL")
    if not dsn:
        parser.error("authorized TENNIS_DATABASE_URL required")
    with psycopg.connect(dsn, autocommit=True, connect_timeout=15,
                         options="-c default_transaction_read_only=on") as conn:
        report = run(conn, as_of=as_of)
    payload = json.dumps(report, sort_keys=True, default=str, indent=2)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(payload + "\n")
    print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
