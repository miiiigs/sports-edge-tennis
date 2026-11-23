"""Historical original match-version known-at and archived ingestion tests."""
from datetime import UTC, datetime, timedelta

import psycopg
import pytest

from sports_edge_tennis.validation.historical_version_visibility_reconciliation import (
    run, source_sql, summarize,
)

RESULT=datetime(2020,5,10,16,tzinfo=UTC)
START=RESULT-timedelta(hours=2)
CUTOFF=datetime(2026,11,21,3,30,tzinfo=UTC)


def observation(mid,*,year=2020,versions=1,version_asof=None,
                version_observed=None,version_ingested=None,
                pit=0):
    asof=version_asof if version_asof is not None else START-timedelta(days=2)
    source=version_observed if version_observed is not None else asof
    ingested=version_ingested if version_ingested is not None else asof
    return (
        year,mid,RESULT,RESULT,RESULT+timedelta(minutes=3),
        versions,asof if versions else None,
        source if versions else None,ingested if versions else None,
        max(asof,source,ingested) if versions else None,
        pit if versions else None,START if versions else None,
    )


def test_distinguishes_absent_versions_and_later_ingestion():
    a=observation(1,versions=None)
    b=observation(
        2,version_asof=RESULT+timedelta(days=10),
        version_observed=RESULT+timedelta(days=10),
        version_ingested=RESULT+timedelta(days=10)
    )
    c=observation(
        3,version_asof=RESULT-timedelta(days=2),
        version_observed=RESULT-timedelta(days=2),
        version_ingested=RESULT+timedelta(days=10),
    )
    d=observation(4,pit=1)
    report=summarize([a,b,c,d])
    x=report["totals"]
    assert x["stored_historical_result_matches"]==4
    assert x["NO_VERSION_STORED_AS_OF_REPORT"]==1
    assert x["MATCH_VERSION_STORED_AS_OF_REPORT"]==3
    assert x["FIRST_VERSION_VISIBILITY_AFTER_RESULT_AS_OF"]==2
    assert x["EVERY_VERSION_AS_OF_AFTER_RESULT_AS_OF"]==1
    assert x["EVERY_VERSION_INGESTED_AFTER_RESULT_AS_OF"]==2
    assert x["HAS_STRICT_PREMATCH_VERSION"]==1
    assert x["NO_STRICT_PREMATCH_VERSION"]==2
    assert not report["historical_75pct_proven"]
    assert report["db_writes"] is False


def test_later_stored_match_version_is_not_false_prematch():
    a=observation(1,version_asof=RESULT,version_observed=RESULT,
                  version_ingested=RESULT,pit=0)
    r=summarize([a])
    assert r["totals"]["FIRST_VERSION_NOT_VISIBLE_UNTIL_MATCH_STARTED"]==1
    assert r["totals"]["NO_STRICT_PREMATCH_VERSION"]==1


def test_duplicate_match_ids_raise_instead_of_inflating():
    with pytest.raises(ValueError,match="duplicate canonical"):
        summarize([observation(1),observation(1)])


def test_sql_restricts_result_time_and_version_observation():
    text=source_sql()
    assert "r.as_of <= %(as_of)s" in text
    assert "r.ingested_at <= %(as_of)s" in text
    assert "v.ingested_at<=%(as_of)s" in text
    assert "v.ingested_at <= v.match_start_at - INTERVAL '30 minutes'" in text
    assert "min(GREATEST(v.as_of,v.source_observed_at,v.ingested_at))" in text
    assert "INSERT" not in text and "UPDATE" not in text


def test_empty_database_reconciliation_is_empty_and_readonly(migrated_postgres):
    with psycopg.connect(migrated_postgres,autocommit=True) as conn:
        r=run(conn,as_of=CUTOFF)
    assert r["totals"]=={}
    assert r["samples_first_12_for_replay"]==[]
    assert not r["historical_75pct_proven"]
