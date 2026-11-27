"""Synthetic inputs only; no competitor observations or sporting results."""
import json
import sqlite3
from datetime import datetime, timezone

import pytest

from sports_edge_tennis.validation import competitor_scientific_benchmark as m


@pytest.fixture
def inputs(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "_now", lambda: datetime(2026, 12, 1, 10, tzinfo=timezone.utc))
    fixture = dict(match_id="1", tour="CHALLENGER", sex="M", discipline="SINGLES",
                   draw="MAIN_DRAW", eligible=True, player1_id="a", player2_id="b",
                   tournament_id="t", census_ref="synthetic",
                   census_observed_at_utc="2026-12-01T09:00:00Z",
                   frozen_start_utc="2026-12-01T12:00:00Z")
    grant = dict(source="pronocast", commercial_research=True, prospective_capture=True,
                 reference="synthetic-not-a-real-license", allowed_hosts=["example.com"],
                 valid_from_utc="2026-11-01T00:00:00Z", valid_until_utc="2027-01-01T00:00:00Z")
    record = dict(source="pronocast", match_id="1", source_url="https://example.com/f",
                  market="MATCH_WINNER", market_class="market_assisted", player1_id="b",
                  player2_id="a", predicted_winner_id="a", p_player1=.3,
                  probability_kind="win_probability", published_at_utc=None, model_version=None,
                  evidence_sha256="a"*64, evidence_ref="synthetic", outcomes_available=False)
    return m.ObservationStore(tmp_path/"receipts.db"), fixture, grant, record


def raw(r):
    return json.dumps(r, sort_keys=True).encode()


def test_first_revision_and_retry(inputs):
    store, f, g, r = inputs
    first = store.receive(raw(r), f, g)
    assert store.receive(raw(r), f, g)["seq"] == first["seq"]
    assert store.receive(raw(dict(r, p_player1=.2)), f, g)["revision"]
    assert store.first()[0]["forecast"]["p_player1"] == .3
    assert store.first()[0]["forecast"]["published_at_utc"] is None
    with sqlite3.connect(store.path) as db:
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("DELETE FROM observations")


@pytest.mark.parametrize("change", [
    {"market":"TOTALS"}, {"player1_id":"c"}, {"p_player1":.9},
    {"probability_kind":"trust_score"}, {"source_url":"https://wrong.example/f"},
    {"published_at_utc":"2026-12-01T11:00:00Z"}, {"outcomes_available":True},
])
def test_invalid(inputs, change):
    store, f, g, r = inputs
    with pytest.raises(ValueError):
        store.receive(raw(dict(r, **change)), f, g)
    assert not store.first()


def test_permission_and_scope(inputs):
    store, f, g, r = inputs
    with pytest.raises(ValueError):
        store.receive(raw(r), f, dict(g, commercial_research=False))
    with pytest.raises(ValueError):
        store.receive(raw(r), dict(f, sex="F"), g)
    with pytest.raises(ValueError):
        store.receive(raw(r), dict(f, frozen_start_utc="2026-12-01T10:15:00Z"), g)


def test_winner_only_and_actual_start(inputs):
    store, f, g, r = inputs
    store.receive(raw(dict(r, p_player1=None, probability_kind="unavailable")), f, g)
    result = dict(status="COMPLETED", winner_id="a", official_evidence_ref="synthetic",
                  actual_start_utc="2026-12-01T12:00:00Z", result_observed_at_utc="2026-12-01T14:00:00Z")
    def report(s):
        reports = m.score([f], store.first(), {"1":s}, as_of="2026-12-02T00:00:00Z")["reports"]
        return next(x for x in reports if x["tour"]=="ALL" and x["source"]=="pronocast"
                    and x["market_class"]=="market_assisted")
    assert report(result)["own"]["accuracy"] == 1
    assert report(result)["own"]["brier"] is None
    assert report(dict(result, actual_start_utc="2026-12-01T10:10:00Z"))["own"]["settled"] == 0
    assert report(dict(result, status="RETIRED"))["own"]["settled"] == 0


def test_reverse_probability(inputs):
    store, f, g, r = inputs
    store.receive(raw(r), f, g)
    result = dict(status="COMPLETED", winner_id="a", official_evidence_ref="synthetic",
                  actual_start_utc="2026-12-01T12:00:00Z", result_observed_at_utc="2026-12-01T14:00:00Z")
    report = m.score([f], store.first(), {"1":result}, as_of="2026-12-02T00:00:00Z")["reports"]
    own = next(x["own"] for x in report if x["tour"]=="ALL" and x["source"]=="pronocast"
               and x["market_class"]=="market_assisted")
    assert own["brier"] == pytest.approx(.09)


def test_aggregate_rounding():
    out = m.reconcile_bands([(2242,54.4),(1844,64.3),(1378,73.9),(1199,89.7)],2)
    assert out["total"] == 6663 and out["selected"] == 2577
    assert out["weighted_rate_pct"] == pytest.approx(81.25126115638339)
    assert out["possible_rate_max_pct"] < 81.6


def test_equal_coverage_and_probability_subset(inputs):
    store, f, g, r = inputs
    store.receive(raw(r), f, g)
    baseline = dict(r, source="sports_edge_original", market_class="market_independent",
                    p_player1=.4)
    store.receive(raw(baseline), f, dict(g, source="sports_edge_original"))
    result = dict(status="COMPLETED", winner_id="a", official_evidence_ref="synthetic",
                  actual_start_utc="2026-12-01T12:00:00Z", result_observed_at_utc="2026-12-01T14:00:00Z")
    out = m.score([f],store.first(),{"1":result},as_of="2026-12-02T00:00:00Z")
    own = next(x for x in out["reports"] if x["tour"]=="ALL" and x["source"]=="pronocast"
               and x["market_class"]=="market_assisted")
    assert own["common_probability_source"]["probability_n"] == 1
    assert own["paired_baseline_classes"] == {"market_independent":1}
    equal = next(x for x in out["equal_coverage"] if x["tour"]=="ALL" and x["source"]=="pronocast"
                 and x["market_class"]=="market_assisted" and x["baseline_class"]=="market_independent"
                 and x["target_fraction"]==1)
    assert equal["feasible"] and equal["source_selected"] == equal["baseline_selected"] == 1


def test_configured_collector_rejects_unapproved_and_historical(inputs):
    import hashlib

    store, f, g, r = inputs
    evidence = b"synthetic source bytes"
    g = dict(g, digest_retention=True, normalized_forecast_retention=True,
             internal_comparison=True)
    r = dict(r, evidence_sha256=hashlib.sha256(evidence).hexdigest(),
             origin="observed_pre_match")
    collector = m.ConfiguredCompetitorCollector(store, {"pronocast": g}, {"1": f})

    def receive(record, source_bytes=evidence):
        payload = raw(record)
        return collector.receive(payload, hashlib.sha256(payload).hexdigest(), source_bytes)

    with pytest.raises(ValueError, match="historical"):
        receive(dict(r, origin="historical_result_page"))
    with pytest.raises(ValueError, match="source evidence digest"):
        receive(r, b"wrong evidence")
    with pytest.raises(ValueError, match="publication timestamp semantics"):
        receive(dict(r, published_at_utc="2026-12-01T09:30:00Z"))
    with pytest.raises(ValueError, match="transport digest"):
        collector.receive(raw(r), "0"*64, evidence)
    assert not store.first()
    receipt = receive(r)
    assert receipt["source_evidence_digest_verified"]
    assert receipt["independent_clock_verified"] is False
    assert receipt["eligible_for_activation_evidence"] is False
    assert receive(r)["duplicate"]


def test_draw_strata_and_paired_uncertainty(inputs):
    store, f, g, r = inputs
    store.receive(raw(r), f, g)
    b = dict(r, source="sports_edge_original", market_class="market_independent",
             p_player1=.4)
    store.receive(raw(b), f, dict(g, source="sports_edge_original"))
    s = dict(status="COMPLETED", winner_id="a", official_evidence_ref="synthetic",
             actual_start_utc="2026-12-01T12:00:00Z",
             result_observed_at_utc="2026-12-01T14:00:00Z")
    rows = m.score([f], store.first(), {"1": s}, as_of="2026-12-02T00:00:00Z")["reports"]
    by_draw = {x["draw"]: x for x in rows if x["tour"] == "CHALLENGER"
               and x["source"] == "pronocast" and x["market_class"] == "market_assisted"}
    assert by_draw["MAIN_DRAW"]["eligible"] == 1
    assert by_draw["QUALIFYING"]["eligible"] == 0
    metrics = by_draw["MAIN_DRAW"]["paired_uncertainty_by_baseline_class"]["market_independent"]
    assert metrics["accuracy"]["delta"] == 0
    assert metrics["brier"]["delta"] == pytest.approx(-.07)
    assert metrics["log_loss"]["delta"] < 0
    assert metrics["accuracy"]["ci95"] is None


def test_replace_cannot_overwrite_append_only(inputs):
    store, f, g, r = inputs
    store.receive(raw(r), f, g)
    with store._connect() as db:
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("INSERT OR REPLACE INTO observations SELECT * FROM observations")
