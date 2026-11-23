"""Ops Agent report must open a working read-only PostgreSQL connection."""
import ast
from pathlib import Path

import psycopg
import pytest


def test_agent_observability_report_uses_supported_libpq_readonly_option():
    text=Path("ops/agent_observability_report.py").read_text()
    assert 'options="-c default_transaction_read_only=on"' in text
    assert "read_only=True" not in text
    ast.parse(text, filename="ops/agent_observability_report.py")
    assert r"\n" not in text.split("with psycopg.connect(", 1)[1].split(") as conn:", 1)[0]


def test_operator_connection_is_read_only_and_supports_query(migrated_postgres):
    with psycopg.connect(
        migrated_postgres, autocommit=True,
        options="-c default_transaction_read_only=on",
    ) as conn:
        flag=conn.execute("SHOW default_transaction_read_only").fetchone()[0]
        value=conn.execute("SELECT 3+4").fetchone()[0]
    assert flag == "on"
    assert value == 7


def test_read_only_connection_cannot_change_any_rows(migrated_postgres):
    with psycopg.connect(
        migrated_postgres, autocommit=True,
        options="-c default_transaction_read_only=on",
    ) as conn:
        with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
            conn.execute("CREATE TABLE should_never_exist (id INT)")
