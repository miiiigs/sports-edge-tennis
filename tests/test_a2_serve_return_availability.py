"""Only true two-sided observed point statistics qualify for A2 expansion."""
from __future__ import annotations

from datetime import UTC, datetime

import psycopg
import pytest

from sports_edge_tennis.validation.a2_serve_return_availability import (
    report_coverage,run,source_query,source_inventory_sql,
)


AS_OF=datetime(2026,11,20,tzinfo=UTC)


def test_two_sided_stat_coverage_is_not_coerced_to_zero():
    report=report_coverage(
        [("ATP_MAIN","HARD",100,70,40,9),
         ("CHALLENGER","CLAY",80,16,6,1),
         ("CHALLENGER","HARD",40,8,3,0)],
        [("api_tennis","NORMALIZED_SOURCE",250,90),
         ("other","RAW_SOURCE",500,120)],
        as_of=AS_OF,
    )
    assert report["status"]=="HISTORICAL_STAT_SOURCE_AVAILABILITY_ONLY"
    assert report["totals"]=={
        "recorded_matches":220,"two_sided":94,
        "essential_complete":49,"extended_complete":10,
    }
    assert report["by_tour"]["ATP_MAIN"]["essential_coverage_pct"]==40
    assert report["by_tour"]["CHALLENGER"]["essential_complete"]==9
    assert report["missing_values_imputed"] is False
    assert report["source_licenses_verified"] is False
    assert report["PIT_for_each_original_prediction_verified"] is False
    assert report["db_writes"] is False


def test_internal_completeness_arithmetic_fails_closed():
    with pytest.raises(ValueError,match="incoherent"):
        report_coverage(
            [("ATP_MAIN","HARD",20,5,6,0)],[],as_of=AS_OF
        )


def test_sql_requires_asof_and_previously_ingested_normalized_statistics():
    sql=source_query()
    assert "observation_kind='NORMALIZED_SOURCE'" in sql
    assert sql.count("ingested_at<=%(as_of)s")>=2
    assert "source_observed_at<=%(as_of)s" in sql
    assert "first_serve_points_won IS NOT NULL" in sql
    assert "second_serve_points_won IS NOT NULL" in sql
    assert "service_points_played>0" in sql
    assert "return_points_won IS NOT NULL" in sql
    assert "player_id=mv.player_1_id" in sql
    assert "player_id=mv.player_2_id" in sql
    assert "COALESCE(first_serve_points_won,0)" not in sql
    assert "source_system" in source_inventory_sql()


def test_migrated_postgres_empty_schema_is_readonly_and_truthfully_empty(migrated_postgres):
    with psycopg.connect(migrated_postgres,autocommit=True) as conn:
        report=run(conn,as_of=AS_OF)
    assert report["totals"]["recorded_matches"]==0
    assert report["totals"]["essential_complete"]==0
    assert report["tour_surface"]==[]
    assert report["source_inventory"]==[]
    assert report["production_model_changed"] is False
