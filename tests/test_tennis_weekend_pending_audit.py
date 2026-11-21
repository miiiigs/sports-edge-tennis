"""Weekend and old pending evidence excludes ITF/teams/doubles; never writes."""
from datetime import date
from sports_edge_tennis.validation.tennis_weekend_pending_audit import (
    _tier,_event_day,raw_source_counts,DATES,PROVIDER_WINDOWS,PENDING_IDS
)


def test_provider_scope_does_not_smuggle_itf_wta_or_doubles():
    assert _tier({"event_type_type":"Challenger Men Singles"})=="CHALLENGER"
    assert _tier({"event_type_type":"ATP Men Singles"})=="ATP_MAIN"
    assert _tier({"event_type_type":"ITF Men Singles"}) is None
    assert _tier({"event_type_type":"WTA Women Singles"}) is None
    assert _tier({"event_type_type":"Challenger Men Doubles"}) is None
    assert _tier({"event_type_type":"ATP Men Team"}) is None


def test_utc_to_manila_calendar_date_and_terminal_classification():
    raw=[{"event_key":121,"event_date":"2026-11-13","event_time":"18:00",
          "event_type_type":"Challenger Men Singles","event_status":"Finished"},
         {"event_key":122,"event_date":"2026-11-16","event_time":"08:00",
          "event_type_type":"Challenger Men Singles","event_status":"Scheduled"},
         {"event_key":123,"event_date":"2026-11-14","event_time":"04:00",
          "event_type_type":"ITF Men Singles","event_status":"Finished"}]
    days,bykey=raw_source_counts(raw)
    assert _event_day(raw[0])==date(2026,11,14)
    assert days["2026-11-14"]["CHALLENGER"]==1
    assert days["2026-11-14"]["CHALLENGER_terminal_status"]==1
    assert days["2026-11-16"]["CHALLENGER"]==1
    assert bykey["121"]["event_status"]=="Finished"
    assert len(DATES)==3 and len(PENDING_IDS)==5
    assert len(PROVIDER_WINDOWS)<=2


def test_sql_is_read_only_and_scoped_to_immutable_first_version():
    from sports_edge_tennis.validation import tennis_weekend_pending_audit as mod
    import inspect
    sqls=(mod._CANONICAL_SQL,mod._PREDICTION_RUN_SQL,
          mod._INGEST_SQL,mod._PENDING_SQL)
    for sql in sqls:
        low=sql.upper()
        for forbidden in ("UPDATE ","DELETE ","INSERT ","TRUNCATE ","DROP "):
            assert forbidden not in low
    assert "ORDER BY match_id, as_of ASC" in mod._CANONICAL_SQL
    assert "source_observed_at" in mod._CANONICAL_SQL
    assert "SET default_transaction_read_only=on" in inspect.getsource(mod.main)
