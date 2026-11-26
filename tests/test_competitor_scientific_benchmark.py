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
