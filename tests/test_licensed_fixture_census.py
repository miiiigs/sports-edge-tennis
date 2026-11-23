"""Authorized independent fixture census: source completeness, exact mapping, timing."""
import csv
import hashlib
import json
from dataclasses import replace
from datetime import UTC,datetime,timedelta

import psycopg
import pytest

from sports_edge_tennis.validation.licensed_fixture_census import (
    Manifest,Reference,audit,load_authorised_csv,source_matching,run_db,
)

START=datetime(2026,11,15,tzinfo=UTC)
END=datetime(2026,11,22,tzinfo=UTC)
ASOF=datetime(2026,11,22,2,tzinfo=UTC)
MATCH=datetime(2026,11,18,12,tzinfo=UTC)


def manifest(complete=True,approved=True):
    return Manifest(
        source_system="LICENSED_TEST",export_version="one",
        licence_reference="licensed-record-123",
        owner_approved_commercial_validation=approved,
        provider_declares_complete_window=complete,
        requested_window_start=START,requested_window_end=END,
        report_as_of=ASOF,file_sha256="a"*64,
    )


def fixture(fid="123",*,published=True,late=False,stage="MAIN",tour="ATP_MAIN"):
    return Reference(
        source_match_id=fid,tour_scope=tour,draw_stage=stage,
        match_type="SINGLES",scheduled_start=MATCH,
        first_published=(MATCH-timedelta(hours=5) if published and not late
                         else MATCH-timedelta(minutes=10) if late else None),
    )


def original(mid=99,*,discovered=True,manager="CONSENSUS",agent3="ABSTAINED"):
    return {
        "identity":"EXACT_CANONICAL","canonical_match_id":mid,
        "internal_discovered_prematch":discovered,
        "original_run_on_time":True,
        "original_agent_statuses":{
            "AGENT_1_SURFACE_ELO":"PREDICTED",
            "AGENT_2_HIERARCHICAL_MARKOV":"PREDICTED",
            "AGENT_3_INTRANSITIVITY":agent3,
        },
        "original_manager_status":manager,
    }


def test_complete_licensed_reference_traces_each_stage():
    refs=[fixture("1"),fixture("2"),fixture("3")]
    result=audit(manifest(),refs,{
        "1":original(99),
        "2":original(98,discovered=False),
        "3":{"identity":"NO_EXACT_PROVIDER_MAPPING"},
    },aligned_picks={99:(MATCH-timedelta(hours=1),"MANAGER_CONSENSUS")})
    c=result["counts"]
    assert c["officially_knowable_by_30m_cutoff"]==3
    assert c["exact_provider_id_mapped"]==2
    assert c["discovered_prematch_exact_verified"]==1
    assert c["exact_mapped_but_not_discovered_by_30m"]==1
    assert c["mapping_NO_EXACT_PROVIDER_MAPPING"]==1
    assert c["original_manager_consensus"]==1
    assert c["aligned_dashboard_model_pick_published_before_30m"]==1
    assert c["AGENT_3_INTRANSITIVITY_PREDICTED"]==0 if "AGENT_3_INTRANSITIVITY_PREDICTED" in c else True
    assert result["true_official_match_discovery_rate_verified"] is False
    assert result["exact_provider_match_coverage_lower_bound_conditional_on_export"]==pytest.approx(1/3)


def test_without_source_completeness_no_official_total_claim():
    r=audit(manifest(complete=False),[fixture()],{"123":original()})
    assert r["official_eligible_singles_total_if_source_complete"] is None
    assert r["exact_provider_match_coverage_lower_bound_conditional_on_export"] is None


def test_unknown_published_time_or_late_schedule_not_silent_eligible():
    refs=[fixture("1",published=False),fixture("2",late=True)]
    r=audit(manifest(),refs,{})
    assert r["counts"]["officially_knowable_by_30m_cutoff"]==0 if "officially_knowable_by_30m_cutoff" in r["counts"] else True
    assert r["counts"]["unknown_original_source_publication_time"]==1
    assert r["counts"]["officially_first_announced_after_30m_cutoff"]==1


def test_reject_unlicensed_or_invalid_official_source_manifest():
    with pytest.raises(ValueError,match="commercial data rights"):
        audit(manifest(approved=False),[fixture()],{})
    with pytest.raises(ValueError,match="duplicate reference"):
        audit(manifest(),[fixture("123"),fixture("123")],{})


def test_qualifying_matches_stay_explicitly_out_of_main_draw_scope():
    r=audit(manifest(),[fixture("a",stage="QUALIFYING"),fixture("b")],{})
    assert r["counts"]["out_of_scope_qualifiers_or_non_singles"]==1
    assert r["counts"]["main_draw_singles_in_export"]==1


def test_authorized_csv_sha256_manifest_and_duplicate_validation(tmp_path):
    csv_path=tmp_path/"authorized.csv"
    fields=["source_match_id","tour_scope","draw_stage","match_type",
            "scheduled_start_utc","first_published_utc"]
    with csv_path.open("w",newline="") as fh:
        writer=csv.DictWriter(fh,fieldnames=fields)
        writer.writeheader()
        writer.writerow({
            "source_match_id":"A100","tour_scope":"ATP_MAIN",
            "draw_stage":"MAIN","match_type":"SINGLES",
            "scheduled_start_utc":"2026-11-18T12:00:00Z",
            "first_published_utc":"2026-11-18T08:00:00Z",
        })
    meta={
        "source_system":"LICENSED_TEST",
        "export_version":"v1",
        "licence_reference":"licensed vendor authorization",
        "owner_approved_commercial_validation":True,
        "provider_declares_complete_window":True,
        "requested_window_start_utc":"2026-11-15T00:00:00Z",
        "requested_window_end_utc":"2026-11-22T00:00:00Z",
        "report_as_of_utc":"2026-11-22T02:00:00Z",
        "file_sha256":hashlib.sha256(csv_path.read_bytes()).hexdigest(),
    }
    manifest_path=tmp_path/"source.json"
    manifest_path.write_text(json.dumps(meta))
    m,rows=load_authorised_csv(csv_path,manifest_path)
    assert len(rows)==1
    assert rows[0].source_match_id=="A100"
    meta["file_sha256"]="0"*64
    manifest_path.write_text(json.dumps(meta))
    with pytest.raises(ValueError,match="SHA256 mismatch"):
        load_authorised_csv(csv_path,manifest_path)


def test_missing_source_mapping_is_unknown_not_proven_discovery_failure(migrated_postgres):
    with psycopg.connect(migrated_postgres,autocommit=True) as conn:
        ref=fixture("ABC")
        m=manifest()
        r=run_db(conn,m,[ref])
    assert r["counts"]["mapping_NO_EXACT_PROVIDER_MAPPING"]==1
    assert r["counts"]["officially_knowable_by_30m_cutoff"]==1
    assert r["true_official_match_discovery_rate_verified"] is False
    assert r["no_database_writes"] is True


def test_source_identifiers_must_not_use_name_only_fuzzy_guesses():
    x=Reference(" ", "ATP_MAIN","MAIN","SINGLES",MATCH,MATCH-timedelta(hours=1))
    with pytest.raises(ValueError,match="empty official fixture ID"):
        x.validate()
