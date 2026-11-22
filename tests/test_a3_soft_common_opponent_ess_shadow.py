"""A3 soft evidence prototypes are separate from locked original A3."""
from datetime import UTC,datetime,timedelta
from decimal import Decimal

import pytest

from sports_edge_tennis.agents.agent3_intransitivity import (
    Agent3HistorySnapshot,PairHistorySnapshot,ResidualObservation,
    PredictionRequest,
)
from sports_edge_tennis.validation.a3_soft_common_opponent_ess_shadow import (
    PROFILES,_soft_variant,compare_snapshot,
)

NOW=datetime(2026,11,22,14,tzinfo=UTC)
MANIFEST="a"*64


def snapshot(*,age_days=300,gaps=4,future=False):
    pairs={}
    adj={1:set(),2:set()}
    obs_at=NOW+timedelta(days=1) if future else NOW-timedelta(days=age_days)
    for i in range(gaps):
        opp=i+10
        first=PairHistorySnapshot(
            player_low_id=1,player_high_id=opp,
            all_observations=(
                ResidualObservation(observed_at=obs_at,residual=Decimal("1")),
            ),
        )
        second=PairHistorySnapshot(
            player_low_id=2,player_high_id=opp,
            all_observations=(
                ResidualObservation(observed_at=obs_at,residual=Decimal("-1")),
            ),
        )
        pairs[(1,opp)]=first
        pairs[(2,opp)]=second
        adj[1].add(opp);adj[2].add(opp)
    return Agent3HistorySnapshot(
        players={},
        pairs=pairs,
        adjacency={p:tuple(sorted(v)) for p,v in adj.items()},
    )


def request(s=None,*,surface="HARD",reverse=False):
    return PredictionRequest(
        player_1_id=2 if reverse else 1,
        player_2_id=1 if reverse else 2,
        surface=surface,history_snapshot=s or snapshot(),as_of=NOW,
    )


def run(r=None,*,verified=True):
    return compare_snapshot(
        r or request(),
        capture_at=NOW+timedelta(minutes=2),
        match_start_at=NOW+timedelta(hours=4),
        verified_original_source_ingestion=verified,
        source_manifest_sha256=MANIFEST,
    )


def test_relaxed_edge_retains_older_pair_but_original_edge_diagnostic_drops():
    r=run()
    variants=r["research_variants"]
    original=variants["original_edge_threshold_diagnostic"]
    relaxed=variants["relaxed_edge_050_unweighted"]
    assert original["status"]=="ABSTAINED"
    assert relaxed["eligible_shared_opponents"]==4
    assert relaxed["kish_ess"]>=3
    assert relaxed["status"]=="EXPERIMENTAL_PREDICTION"
    assert relaxed["selected_side"]==1
    assert r["original_agent3_production_control"]["status"]=="ABSTAINED"
    assert r["not_calibrated"] is True
    assert r["no_production_inference_or_manager_change"] is True


def test_insufficient_effective_support_still_abstains():
    r=run(request(snapshot(gaps=1)))
    assert all(v["status"]=="ABSTAINED" for v in r["research_variants"].values())


def test_unknown_surface_does_not_get_guessed():
    r=run(request(surface=None))
    assert all(v["status"]=="ABSTAINED" for v in r["research_variants"].values())


def test_soft_surface_weight_changes_evidence_without_pretending_new_surface():
    r=run()
    a=r["research_variants"]["relaxed_edge_050_unweighted"]
    b=r["research_variants"]["relaxed_edge_050_surface_soft065"]
    assert a["effective_evidence_sum"]>b["effective_evidence_sum"]
    assert a["kish_ess"]==b["kish_ess"]


def test_reversed_player_order_produces_reciprocal_probability():
    a=run(request())["research_variants"]["relaxed_edge_050_unweighted"]
    b=run(request(reverse=True))["research_variants"]["relaxed_edge_050_unweighted"]
    assert a["p1"]==pytest.approx(1-b["p1"],abs=2e-8)
    assert a["selected_side"]==1
    assert b["selected_side"]==2


def test_future_observation_and_delayed_capture_hard_rejected():
    with pytest.raises(ValueError,match="future or cutoff-time"):
        run(request(snapshot(future=True)))
    with pytest.raises(ValueError,match="before fixture"):
        compare_snapshot(request(),capture_at=NOW+timedelta(hours=4),
            match_start_at=NOW+timedelta(hours=4,minutes=20),
            verified_original_source_ingestion=True,source_manifest_sha256=MANIFEST)


def test_requires_independent_source_ingestion_manifest():
    with pytest.raises(ValueError,match="source-ingestion"):
        run(verified=False)
    with pytest.raises(ValueError,match="source-ingestion"):
        compare_snapshot(request(),capture_at=NOW+timedelta(minutes=2),
            match_start_at=NOW+timedelta(hours=4),
            verified_original_source_ingestion=True,source_manifest_sha256="notsha")


def test_unchanged_original_four_year_config_and_immutable_inputs():
    s=snapshot()
    first=run(request(s))
    second=run(request(s))
    assert first==second
    assert len(s.pairs)==8
    assert set(PROFILES)==set(first["research_variants"])
    assert first["not_outcome_tested"]
