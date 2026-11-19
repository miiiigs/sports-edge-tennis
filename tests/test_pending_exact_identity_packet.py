"""No inferred results in sports identity packet; PostgreSQL SQL validity."""
import psycopg
from sports_edge_tennis.validation.pending_exact_identity_packet import load,PENDING_IDS,_SQL


def test_exact_five_ids_and_no_dangerous_sql():
    assert PENDING_IDS==(130903,131712,133361,133347,133978)
    assert "player_source_ids" in _SQL
    assert "tournament_source_ids" in _SQL
    assert "match_source_ids" in _SQL
    upper=_SQL.upper()
    for x in ("INSERT ","UPDATE ","DELETE ","DROP ","TRUNCATE "):
        assert x not in upper


def test_empty_migrated_database_compiles(migrated_postgres):
    with psycopg.connect(migrated_postgres,autocommit=True) as conn:
        conn.execute("SET default_transaction_read_only=on")
        assert load(conn)==[]
