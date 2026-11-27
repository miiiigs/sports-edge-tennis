from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sports_edge_tennis.validation.market_benchmark_methodology import (
    BenchmarkFixture,
    MarketQuotePair,
    benchmark_report,
    duplicate_price_pair_accounting,
    market_probability,
    mcnemar_exact,
    odds_distribution,
)

T = datetime(2026, 11, 27, 0, 0, tzinfo=timezone.utc)


def pair(match_id: int, p1: float, p2: float, *, book: str = "PINNACLE",
         provenance: str = "ORIGINAL_TIME", observed: datetime | None = None) -> MarketQuotePair:
    observed_at = observed or (T - timedelta(minutes=30))
    return MarketQuotePair(
        match_id=match_id,
        bookmaker_code=book,
        source_system="SHARP",
        player_1_price=p1,
        player_2_price=p2,
        observed_at=observed_at,
        ingested_at=observed_at,
        provenance=provenance,
    )


def test_deduplicated_consensus_does_not_count_the_same_pair_twice():
    quotes = [
        pair(1, 1.5, 2.6, book="A"),
        pair(1, 1.5, 2.6, book="B"),
        pair(1, 1.4, 2.9, book="C"),
    ]
    raw = market_probability(quotes, method="consensus")
    deduped = market_probability(quotes, method="consensus_deduplicated")
    assert raw != deduped
    m1 = (1 / 1.5) / ((1 / 1.5) + (1 / 2.6))
    m2 = (1 / 1.4) / ((1 / 1.4) + (1 / 2.9))
    assert deduped == (m1 + m2) / 2
    accounting = duplicate_price_pair_accounting(quotes)
    assert accounting["bookmakers"] == 3
    assert accounting["price_pairs"] == 2
    assert accounting["exact_duplicate_pairs"] == 1
    assert accounting["independent_information_units"] == 2


def test_reference_bookmaker_uses_latest_original_time_quote():
    quotes = [
        pair(1, 1.5, 2.6, book="PINNACLE", observed=T - timedelta(hours=2)),
        pair(1, 1.8, 2.1, book="PINNACLE", observed=T - timedelta(minutes=10)),
        pair(1, 1.4, 2.9, book="1XBET", observed=T - timedelta(minutes=5)),
    ]
    reference = market_probability(quotes, method="reference")
    assert reference == (1 / 1.8) / (1 / 1.8 + 1 / 2.1)


def test_benchmark_report_scores_identical_and_common_fixtures_without_edge_claim():
    fixtures = [
        BenchmarkFixture(match_id=i, winner=1, model_p1=p)
        for i, p in enumerate((0.4, 0.6, 0.4, 0.8), start=1)
    ]
    quotes = {i: [pair(i, 1.4, 2.9)] for i in range(1, 5)}
    report = benchmark_report(fixtures=fixtures, quotes_by_match=quotes)
    comparison_a = report["comparisons"]["A_identical_selected"]
    assert comparison_a["fixtures"] == 2
    assert comparison_a["model"]["accuracy"] == 0.0
    assert comparison_a["market"]["accuracy"] == 1.0
    comparison_b = report["comparisons"]["B_all_common"]
    assert comparison_b["model"]["accuracy"] == 0.5
    assert comparison_b["market"]["accuracy"] == 1.0
    assert comparison_b["model"]["brier"] > comparison_b["market"]["brier"]
    assert comparison_b["paired"]["n"] == 4
    assert report["comparisons"]["C_independent_ranking"]["k"] == 4
    assert report["comparisons"]["D_market_assisted"]["available"] is False
    assert report["original_time_gate"]["met"] is False
    assert report["retrospective"]["never_used_for_market_edge"] is True


def test_late_quotes_never_produce_a_market_comparison():
    fixtures = [BenchmarkFixture(match_id=1, winner=1, model_p1=0.9)]
    quotes = {1: [pair(1, 1.4, 2.9, provenance="LATE")]}
    report = benchmark_report(fixtures=fixtures, quotes_by_match=quotes)
    assert report["fixtures_with_original_time_quote"] == 0
    assert report["comparisons"]["A_identical_selected"]["available"] is False
    assert report["comparisons"]["B_all_common"]["available"] is False
    assert report["comparisons"]["D_market_assisted"]["available"] is False
    assert report["retrospective"]["fixtures"] == 1


def test_odds_distribution_and_mcnemar_are_explicit():
    distribution = odds_distribution([pair(1, 1.4, 2.9), pair(2, 2.2, 1.7)])
    assert distribution["n"] == 2
    assert abs(distribution["favourite_price"]["p50"] - 1.55) < 1e-12

    result = mcnemar_exact(5, 3)
    assert result["b"] == 5 and result["c"] == 3
    assert abs(result["p_value"] - 0.7265625) < 1e-12
    assert mcnemar_exact(0, 0)["p_value"] == 1.0
