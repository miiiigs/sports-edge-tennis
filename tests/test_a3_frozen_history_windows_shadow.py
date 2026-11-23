"""Registered A3 1-year, 3-year and current 4-year pure shadow profiles."""
from datetime import UTC,datetime,timedelta
from decimal import Decimal

import pytest

from sports_edge_tennis.agents.agent3_intransitivity import (
    Agent3HistorySnapshot,OutcomeObservation,PairHistorySnapshot,
    PlayerHistorySnapshot,PredictionRequest,ResidualObservation,
)
from sports_edge_tennis.validation.a3_frozen_history_windows_shadow import (
    VERSION,WINDOW_DAYS,compare_locked_windows,filter_snapshot,
)

ASOF=datetime(2026,11,20,12,tzinfo=UTC)
GENERATED=ASOF+timedelta(minutes=1)
ISSUE=ASOF+timedelta(minutes=2)
START=ASOF+timedelta(hours=3)


def sample(*,extra_future=False):
    entries=[
        ASOF-timedelta(days=70),
        ASOF-timedelta(days=550),
        ASOF-timedelta(days=1000),
        ASOF-timedelta(days=1200),
    ]
    if extra_future:
        entries.append(ASOF+timedelta(days=1))
    p1=PlayerHistorySnapshot(
        player_id=1,
        all_observations=tuple(OutcomeObservation(observed_at=x,value=1)
                               for x in entries),
        surface_observations={"HARD":tuple(
            OutcomeObservation(observed_at=x,value=1) for x in entries
        )},
    )
    p2=PlayerHistorySnapshot(
        player_id=2,
        all_observations=tuple(OutcomeObservation(observed_at=x,value=-1)
                               for x in entries),
        surface_observations={"HARD":tuple(
            OutcomeObservation(observed_at=x,value=-1) for x in entries
        )},
    )
    pair=PairHistorySnapshot(
        player_low_id=1,player_high_id=2,
        all_observations=tuple(
            ResidualObservation(observed_at=x,residual=Decimal("1"))
            for x in entries
        ),
        surface_observations={"HARD":tuple(
            ResidualObservation(observed_at=x,residual=Decimal("1"))
            for x in entries
        )},
    )
    return Agent3HistorySnapshot(
        players={1:p1,2:p2},pairs={(1,2):pair},
        adjacency={1:(2,),2:(1,)},
    )


def request(snapshot=None):
    return PredictionRequest(player_1_id=1,player_2_id=2,surface="HARD",
                             history_snapshot=snapshot or sample(),as_of=ASOF)


def test_history_filter_is_nonmutating_and_windows_increase_support():
    source=sample()
    counts=[]
    for days in WINDOW_DAYS:
        filtered=filter_snapshot(source,cutoff=ASOF,days=days)
        counts.append(len(filtered.pairs[(1,2)].all_observations))
        assert filtered is not source
        assert filtered.players[1] is not source.players[1]
    assert counts==[1,3,4]
    assert len(source.pairs[(1,2)].all_observations)==4


def test_all_windows_use_same_agent3_config_and_pit_issue():
    result=compare_locked_windows(
        request(),issue_at=ISSUE,match_start_at=START,generated_at=GENERATED
    )
    assert result["version"]==VERSION
    assert result["production_lookback_days"]==1460
    assert result["four_year_parity_with_baseline"] is True
    assert set(result["experimental_1y_3y_4y"])=={"365","1095","1460"}
    assert result["no_live_prediction_written"] is True
    assert result["not_outcome_validated"] is True
    assert len(result["data_signature_sha256"])==64


def test_same_inputs_original_orientation_is_deterministic():
    first=compare_locked_windows(request(),issue_at=ISSUE,
        match_start_at=START,generated_at=GENERATED)
    next_=compare_locked_windows(request(),issue_at=ISSUE,
        match_start_at=START,generated_at=GENERATED)
    assert first==next_
    assert first["experimental_1y_3y_4y"]["1460"]==first["original_baseline"]


def test_future_history_rejected_and_production_snapshot_not_modified():
    with pytest.raises(ValueError,match="future or cutoff-time"):
        compare_locked_windows(request(sample(extra_future=True)),
             issue_at=ISSUE,match_start_at=START,generated_at=GENERATED)


def test_outcome_known_after_match_cannot_be_reconstructed_as_prior_issue():
    with pytest.raises(ValueError,match="actual computation"):
        compare_locked_windows(request(),issue_at=ISSUE,
             match_start_at=START,generated_at=START+timedelta(hours=1))
    with pytest.raises(ValueError,match="original fixture"):
        compare_locked_windows(request(),issue_at=ISSUE,
             match_start_at=ISSUE+timedelta(minutes=10),generated_at=GENERATED)


def test_cutoff_after_original_issue_rejected():
    with pytest.raises(ValueError,match="issue must follow"):
        compare_locked_windows(request(),issue_at=ASOF-timedelta(minutes=1),
             match_start_at=START,generated_at=ASOF-timedelta(minutes=2))


def test_invalid_experimental_window_not_silently_used():
    with pytest.raises(ValueError,match="registered"):
        filter_snapshot(sample(),cutoff=ASOF,days=800)


def test_missing_or_empty_snapshot_fails_closed_to_abstention():
    req=request(Agent3HistorySnapshot())
    out=compare_locked_windows(req,issue_at=ISSUE,
        match_start_at=START,generated_at=GENERATED)
    assert all(v["status"]=="ABSTAINED"
               for v in out["experimental_1y_3y_4y"].values())
    assert out["four_year_parity_with_baseline"]


def test_past_historical_edge_outside_one_year_is_not_carried_forward():
    snap=sample()
    short=filter_snapshot(snap,cutoff=ASOF,days=365)
    assert len(short.pairs[(1,2)].all_observations)==1
    assert short.adjacency=={1:(2,),2:(1,)}
    empty=filter_snapshot(snap,cutoff=ASOF+timedelta(days=1461),days=365)
    assert empty.pairs=={}
    assert empty.adjacency=={}


def test_surface_missing_still_abstains_not_fabricates():
    req=PredictionRequest(player_1_id=1,player_2_id=2,
        surface=None,history_snapshot=sample(),as_of=ASOF)
    out=compare_locked_windows(req,issue_at=ISSUE,
        match_start_at=START,generated_at=GENERATED)
    assert all(v["status"]=="ABSTAINED"
               for v in out["experimental_1y_3y_4y"].values())
