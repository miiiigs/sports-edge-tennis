"""Independent pre-match comparator is strictly research-only and fail-closed."""
from sports_edge_tennis.validation.competitor_forward_audit import compare, utc


def fx(mid, tour="ATP_TOUR",status="COMPLETED",winner="1",month="11",draw="MAIN_DRAW"):
    return {"match_id":str(mid), "tour":tour, "draw":draw,
            "player1_id":f"p{mid}a", "player2_id":f"p{mid}b",
            "tournament_id":f"tour{mid}",
            "frozen_start_utc":f"2026-{month}-18T18:00:00Z",
            "result_status":status,"winner_side":winner}


def fc(mid,source="sports_edge_original",p=".7",captured="2026-11-18T16:00:00Z",
       rights="internal",published="2026-11-18T15:45:00Z",version="v1"):
    return {"source":source, "match_id":str(mid),"player1_id":f"p{mid}a",
            "player2_id":f"p{mid}b", "p_player1":str(p),
            "captured_at_utc":captured,"published_at_utc":published,
            "rights":rights,"model_version":version}


def test_separate_atp_and_ch_and_only_settled_scores():
    fixtures=[fx(1),fx(2,"CHALLENGER"),fx(3,"CHALLENGER","PENDING","")]
    report=compare(fixtures,[fc(1),fc(2),fc(3)])
    assert report["universe_unique_matches"]==3
    assert report["settled_unique_matches"]==2
    assert report["pending_or_void_matches"]==1
    ch=next(x for x in report["per_tour"] if x["tour"]=="CHALLENGER")
    assert ch["settled_evaluated"]==1
    assert ch["universe_matches"]==2
    assert ch["forward_captured"]==2


def test_reject_late_capture_or_postpublication_not_observable():
    rows=[fc(1,captured="2026-11-18T17:40:00Z"),
          fc(2,published="2026-11-18T17:40:00Z")]
    r=compare([fx(1),fx(2)],rows)
    assert r["rejected_forecast_rows"]["LATE_OR_UNVERIFIABLE_TIMESTAMP"]==2
    assert r["settled_unique_matches"]==2
    assert r["per_tour"][0]["settled_evaluated"]==0


def test_reject_wrong_participant_orientation_and_unauthorized_data():
    wrong=fc(1,source="vendor",rights="licensed")
    wrong["player1_id"],wrong["player2_id"]=wrong["player2_id"],wrong["player1_id"]
    r=compare([fx(1)], [wrong,fc(1,source="unlicensed",rights="unknown")])
    assert r["rejected_forecast_rows"]["PARTICIPANT_ORIENTATION_MISMATCH"]==1
    assert r["rejected_forecast_rows"]["RIGHTS_NOT_APPROVED"]==1


def test_duplicate_eligible_forecasts_fail_closed_no_cherrypick():
    report=compare([fx(1)],[fc(1,p=".7"),fc(1,p=".9")])
    assert report["rejected_forecast_rows"]["DUPLICATE_FORECAST"]==2
    assert report["per_tour"][0]["forward_captured"]==0


def test_pairing_only_shared_settled_matches():
    rows=[fx(1),fx(2),fx(3,"CHALLENGER")]
    forecasts=[fc(1),fc(2),fc(3),fc(1,"vendor",p=".6",rights="licensed"),
               fc(3,"vendor",p=".8",rights="licensed")]
    report=compare(rows,forecasts)
    paired={x["tour"]:x for x in report["paired"] if x["challenger"]=="vendor"}
    assert paired["ATP_TOUR"]["paired_settled_matches"]==1
    assert paired["CHALLENGER"]["paired_settled_matches"]==1
    assert paired["ATP_TOUR"]["paired_log_loss_delta"] is not None
    assert paired["ATP_TOUR"]["paired_log_loss_95pct_cluster_ci"] is None


def test_excludes_void_and_walkover_from_proper_scores():
    report=compare([fx(1,status="WALKOVER",winner=""),fx(2,status="VOID",winner="")],
                   [fc(1),fc(2)])
    assert report["settled_unique_matches"]==0
    assert report["pending_or_void_matches"]==2


def test_reject_out_of_scope_and_invalid_utc():
    import pytest
    with pytest.raises(ValueError):
        compare([fx(1,"ITF")],[])
    with pytest.raises(ValueError):
        utc("2026-11-18T18:00:00+08:00")
    with pytest.raises(ValueError):
        compare([fx(1),fx(1)],[])


def test_comparison_has_no_external_calls_or_live_change_path():
    import inspect
    from sports_edge_tennis.validation import competitor_forward_audit as mod
    text=inspect.getsource(mod)
    assert "requests." not in text
    assert "psycopg" not in text
    assert "urllib" not in text
    assert "stake" not in text.lower().split("def main")[0].split("return {")[0]


def test_retired_match_void_is_not_scored_as_a_model_win():
    report=compare([fx(91,status="RETIRED",winner="1")],[fc(91)])
    assert report["settled_unique_matches"]==0
    assert report["pending_or_void_matches"]==1

def test_defaulted_is_terminal_but_ungraded_in_competitor_metrics():
    report=compare([fx(1,status="DEFAULTED",winner="1")],[fc(1)])
    assert report["settled_unique_matches"]==0
    assert report["pending_or_void_matches"]==1
    assert report["per_tour"][0]["settled_evaluated"]==0



def test_overall_comparison_uses_only_shared_settled_fixtures_not_individual_rates():
    fixtures = [fx(1), fx(2), fx(3, "CHALLENGER"), fx(4, status="DEFAULTED", winner="1")]
    forecasts = [
        fc(1, p=".9"), fc(2, p=".55"), fc(3, p=".6"), fc(4, p=".9"),
        fc(1, source="vendor", p=".6", rights="licensed"),
        fc(3, source="vendor", p=".75", rights="licensed"),
        fc(4, source="vendor", p=".9", rights="licensed"),
    ]
    r = compare(fixtures, forecasts)
    assert r["settled_unique_matches"] == 3
    by_source = {x["source"]: x for x in r["overall"]}
    assert by_source["sports_edge_original"]["settled_evaluated"] == 3
    assert by_source["vendor"]["settled_evaluated"] == 2
    assert by_source["vendor"]["forward_captured"] == 3

    paired = next(x for x in r["paired_overall"] if x["challenger"] == "vendor")
    assert paired["paired_settled_matches"] == 2
    assert paired["calendar_span_days"] == 1
    assert paired["baseline_on_shared_fixtures"]["settled_evaluated"] == 2
    assert paired["challenger_on_shared_fixtures"]["settled_evaluated"] == 2
    assert paired["paired_brier_delta"] is not None
    assert paired["paired_log_loss_95pct_cluster_ci"] is None
    assert paired["size_and_duration_gate_met"] is False
    assert paired["not_proof_of_prospective_or_licensed_access"] is True


def test_unmatched_vendor_cannot_claim_advantage_or_satisfy_forward_gate():
    r = compare([fx(1), fx(2)], [
        fc(1), fc(2),
        fc(1, source="vendor", rights="licensed", captured="2026-11-18T17:59:00Z"),
    ])
    paired = next(x for x in r["paired_overall"] if x["challenger"] == "vendor")
    assert paired["paired_settled_matches"] == 0
    assert paired["paired_log_loss_delta"] is None
    assert paired["paired_brier_delta"] is None
    assert paired["baseline_on_shared_fixtures"]["settled_evaluated"] == 0
    assert paired["size_and_duration_gate_met"] is False
    assert r["rejected_forecast_rows"]["LATE_OR_UNVERIFIABLE_TIMESTAMP"] == 1


def test_void_default_and_retirement_never_enter_paired_score():
    r = compare(
        [fx(1), fx(2, status="DEFAULTED"), fx(3, status="RETIRED")],
        [fc(1), fc(2), fc(3),
         fc(1, "vendor", p=".55", rights="licensed"),
         fc(2, "vendor", p=".9", rights="licensed"),
         fc(3, "vendor", p=".9", rights="licensed")],
    )
    paired = r["paired_overall"][0]
    assert paired["paired_settled_matches"] == 1
    assert paired["challenger_on_shared_fixtures"]["settled_evaluated"] == 1

