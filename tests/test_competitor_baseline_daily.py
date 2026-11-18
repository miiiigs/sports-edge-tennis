"""Daily all-date first-issued scorecard regression: no retrospective guessing."""
from datetime import date
import pytest
from sports_edge_tennis.validation.competitor_baseline_daily import daily_report


def fixture(mid,day="2026-11-17",tour="CHALLENGER",
            status="COMPLETED",winner="1",draw="MAIN_DRAW"):
    return {"match_id":str(mid),"player1_id":f"p{mid}1","player2_id":f"p{mid}2",
            "tour":tour,"draw":draw,"tournament_id":"12",
            "frozen_start_utc":day+"T13:30:00Z",
            "result_status":status,"winner_side":winner}


def forecast(mid,p="0.8",mode="MANAGER_CONSENSUS"):
    return {"match_id":str(mid),"source":"sports_edge_original",
            "player1_id":f"p{mid}1","player2_id":f"p{mid}2",
            "p_player1":p,"published_at_utc":"2026-11-17T10:00:00Z",
            "captured_at_utc":"2026-11-17T10:00:00Z",
            "model_version":"consensus:v2:"+mode}


def report(fxs,preds,**kwargs):
    return daily_report(fxs,preds,start_day=date(2026,11,17),
                        end_day=date(2026,11,19),
                        as_of_utc="2026-11-19T13:30:00Z",**kwargs)


def test_won_lost_void_and_pending_conservation_including_zero_day():
    fxs=[fixture(1),fixture(2,winner="2"),fixture(3,status="WALKOVER",winner=""),
         fixture(4,status="PENDING",winner=""),
         fixture(5,day="2026-11-19",tour="ATP_TOUR",winner="2")]
    predictions=[forecast(1),forecast(2,p="0.7"),forecast(3),forecast(4),
                 forecast(5,p="0.2",mode="A1_A2_WIDE_SPREAD")]
    r=report(fxs,predictions)
    assert r["total"]["picks"]==5
    assert r["total"]["won"]==2 and r["total"]["lost"]==1
    assert r["total"]["void"]==1 and r["total"]["pending"]==1
    assert r["total"]["settled_accuracy"]==pytest.approx(2/3,abs=1e-6)
    assert r["days"][1]["picks"]==0
    assert any(m["mode"]=="A1_A2_WIDE_SPREAD" for m in r["tour_and_mode"])


def test_duplicate_or_orientation_mismatch_rejected():
    with pytest.raises(ValueError,match="duplicate"):
        report([fixture(1),fixture(1)],[forecast(1)])
    wrong=forecast(1)
    wrong["player1_id"]="wrong"
    with pytest.raises(ValueError,match="identity"):
        report([fixture(1)],[wrong])


def test_retirement_does_not_become_win_or_loss():
    r=report([fixture(1,status="RETIRED")],[forecast(1)])
    assert r["total"]["void"]==1 and r["total"]["graded"]==0


def test_missing_prediction_is_not_hidden_as_zero_coverage():
    with pytest.raises(ValueError,match="mismatch"):
        report([fixture(1)],[])


def test_future_prediction_excluded_without_backdating():
    late=forecast(1)
    late["published_at_utc"]="2026-11-20T10:00:00Z"
    with pytest.raises(ValueError,match="future"):
        report([fixture(1)],[late])


def test_70pct_predeclared_shadow_conserves_winners_losses_and_volume():
    inputs=[fixture(1),fixture(2,winner="2"),fixture(3,status="WALKOVER",winner=""),
            fixture(4,status="PENDING",winner="")]
    preds=[forecast(1,p="0.80"),forecast(2,p="0.60"),
           forecast(3,p="0.73"),forecast(4,p="0.56")]
    result=report(inputs,preds)
    c=next(x for x in result["confidence70_shadow"]["by_tour"] if x["tour"]=="CHALLENGER")
    assert c["kept"]["picks"]==2 and c["skipped"]["picks"]==2
    assert c["kept"]["won"]==1 and c["kept"]["void"]==1
    assert c["skipped"]["lost"]==1 and c["skipped"]["pending"]==1
    assert c["issuance_coverage"]==0.5
    assert c["avoided_losses"]==1
    assert c["missed_winners"]==0
    assert c["kept"]["picks"]+c["skipped"]["picks"]==result["total"]["picks"]
