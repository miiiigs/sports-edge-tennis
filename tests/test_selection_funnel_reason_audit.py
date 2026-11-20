"""Same-run selection funnel must not treat price as match-winner gate."""
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import psycopg
import pytest

from sports_edge_tennis.validation.selection_funnel_reason_audit import (
    AGENTS, _reason, discover, read_run, summarise,
)


def agent(status,side=1,p=.7):
    if status=="ABSTAINED":
        return ("ABSTAINED",None,None)
    return ("PREDICTED",side,p)


def test_same_run_manager_vs_market_not_same_population_and_reasons_exhaustive():
    agents={
        1:{AGENTS[0]:agent("PREDICTED"),AGENTS[1]:agent("PREDICTED"),
           AGENTS[2]:agent("PREDICTED")},
        2:{AGENTS[0]:agent("PREDICTED"),AGENTS[1]:agent("PREDICTED"),
           AGENTS[2]:agent("ABSTAINED")},
        3:{AGENTS[0]:agent("PREDICTED"),AGENTS[1]:agent("PREDICTED",side=2),
           AGENTS[2]:agent("PREDICTED")},
        4:{AGENTS[0]:agent("PREDICTED"),AGENTS[1]:agent("PREDICTED"),
           AGENTS[2]:agent("PREDICTED")},
        5:{AGENTS[0]:agent("PREDICTED"),AGENTS[1]:agent("ABSTAINED")},
    }
    managers={
        1:("CONSENSUS","NONE",1,Decimal(".73")),
        2:("NO_BET","AGENT3_ABSTENTION_SPREAD_EXCEEDED",None,None),
        3:("NO_BET","DIRECTIONAL_DISAGREEMENT",None,None),
        4:("CONSENSUS","NONE",1,Decimal(".55")),
    }
    picks={
        1:("MANAGER_CONSENSUS",Decimal(".73")),
        2:("A1_A2_WIDE_SPREAD",Decimal(".71")),
    }
    market={
        1:[("BET","NONE","TYPE_A_MODEL_EDGE",True)],
        2:[("NO_BET","NOT_MANAGER_ELIGIBLE","TYPE_A_MODEL_EDGE",False)],
    }
    kw=dict(run_id=2311,targets=[1,2,3,4,5],agent_rows=agents,
            managers=managers,aligned=picks,market=market,
            finalized_at=datetime(2026,11,20,tzinfo=UTC))
    r=summarise(**kw)
    assert r["target_count"]==5
    assert r["manager_consensus_count"]==2
    assert r["dashboard_aligned_picks"]==2
    assert r["dashboard_manager_picks"]==1
    assert r["dashboard_a1a2_tracking_only"]==1
    assert r["market_bet_matches"]==1
    assert r["all_three_agents_predicted"]==3
    assert r["agent_counts"][AGENTS[2]]=={
        "predicted":3,"abstained":1,"missing":1,
    }
    assert r["per_target_exclusion_reasons"]=={
        "DASHBOARD_MANAGER_CONSENSUS_PICK":1,
        "DASHBOARD_A1_A2_TRACKING_ONLY":1,
        "MANAGER_REJECTED:DIRECTIONAL_DISAGREEMENT":1,
        "MANAGER_CONSENSUS_BELOW_60_PCT_DASHBOARD_FLOOR":1,
        "MISSING_AGENT_RECORD:AGENT_3_INTRANSITIVITY":1,
    }
    assert r["aligned_confidence70_research_only_count"]==2
    assert sum(r["per_target_exclusion_reasons"].values())==r["target_count"]
    assert r["db_writes"] is False
    assert r["market_is_separate_from_model_picks"] is True
    assert r["provider_eligible_fixtures_before_targeting"]=="UNAVAILABLE_IN_RUN_LEDGER"
    assert r["detail_sha256"]==summarise(**kw)["detail_sha256"]


def test_funnel_rejects_duplicate_target_and_outside_manager_or_pick():
    base=dict(run_id=1,targets=[1],agent_rows={},managers={},
              aligned={},market={},finalized_at=datetime(2026,11,20,tzinfo=UTC))
    with pytest.raises(ValueError,match="duplicate"):
        summarise(**{**base,"targets":[1,1]})
    with pytest.raises(ValueError,match="Manager exists outside"):
        summarise(**{**base,"managers":{9:("CONSENSUS","NONE",1,Decimal(".8"))}})
    with pytest.raises(ValueError,match="aligned picks exist outside"):
        summarise(**{**base,"aligned":{9:("MANAGER_CONSENSUS",Decimal(".8"))}})


def test_research_has_distinct_model_and_market_reason_classification():
    a={AGENTS[0]:agent("PREDICTED"),AGENTS[1]:agent("PREDICTED"),
       AGENTS[2]:agent("PREDICTED")}
    assert _reason(a,("NO_BET","PROBABILITY_SPREAD",None,None),None)==(
        "MANAGER_REJECTED:PROBABILITY_SPREAD"
    )
    assert _reason(a,("CONSENSUS","NONE",1,Decimal(".8")),None)==(
        "CONSENSUS_NOT_IN_ALIGNED_LEDGER_NEEDS_INVESTIGATION"
    )


def test_empty_migrated_postgres_is_not_fake_completed_26_by_7(migrated_postgres):
    with psycopg.connect(migrated_postgres,autocommit=True) as c:
        c.execute("SET default_transaction_read_only=on")
        rows=discover(c,cutoff=datetime(2026,11,20,tzinfo=UTC))
        assert rows==[]
        actual=read_run(c,run_id=123456)
    assert actual["status"]=="NOT_COMPLETED_NONEXPERIMENT_RUN"
    assert actual["db_writes"] is False


def test_first_issue_in_an_earlier_hourly_run_explains_new_consensus():
    agents={AGENTS[0]:agent("PREDICTED"),AGENTS[1]:agent("PREDICTED"),
            AGENTS[2]:agent("ABSTAINED")}
    a=summarise(
        run_id=2369,targets=[101,102],
        agent_rows={101:agents,102:agents},
        managers={
            101:("CONSENSUS","NONE",1,Decimal(".72")),
            102:("CONSENSUS","NONE",1,Decimal(".55")),
        },
        aligned={},
        market={
            101:[("BET","NONE","TYPE_A_MODEL_EDGE",True)],
        },
        first_issued={
            101:(2317,"MANAGER_CONSENSUS",Decimal(".75"))
        },
        finalized_at=datetime(2026,11,20,tzinfo=UTC),
    )
    assert a["manager_consensus_count"]==2
    assert a["dashboard_aligned_picks"]==0
    assert a["previously_issued_aligned_on_other_runs"]==1
    assert a["ever_first_issued_aligned_among_targets"]==1
    assert a["market_bet_matches"]==1
    assert a["per_target_exclusion_reasons"]=={
        "CONSENSUS_FOR_PREVIOUSLY_FIRST_ISSUED_MATCH":1,
        "MANAGER_CONSENSUS_BELOW_60_PCT_DASHBOARD_FLOOR":1,
    }
    first=next(row for row in a["per_match_evidence"] if row["match_id"]==101)
    assert first["first_issued_aligned_run"]==2317
    assert first["previously_issued_aligned"] is True
    assert first["market_bet"] is True


def test_nonexistent_first_issued_match_does_not_silently_claim_it_was_sent():
    result=summarise(
        run_id=9,targets=[1],
        agent_rows={1:{name:agent("PREDICTED") for name in AGENTS}},
        managers={1:("CONSENSUS","NONE",1,Decimal(".81"))},
        aligned={},first_issued={},market={},
        finalized_at=datetime(2026,11,20,tzinfo=UTC),
    )
    assert result["per_target_exclusion_reasons"]=={
        "CONSENSUS_NOT_IN_ALIGNED_LEDGER_NEEDS_INVESTIGATION":1
    }
