from __future__ import annotations

from datetime import UTC, datetime

from sports_edge_tennis.live.oddspapi_bootstrap import (
    MAPPINGS,
    ODDSPAPI_V4_EFFECTIVE_FROM,
    ensure_oddspapi_bookmaker_mappings,
)


class _Result:
    def __init__(self, row):
        self.row = row

    def fetchone(self):
        return self.row


class _Conn:
    def __init__(self):
        self.rows = {}
        self.inserts = []
        self.next_id = 1

    def execute(self, sql, params=None):
        normalized = " ".join(sql.split()).lower()
        if "select provider_bookmaker_mapping_id" in normalized:
            provider_code, provider_key = params
            row = self.rows.get((provider_code, provider_key))
            return _Result(row)
        if "insert into provider_bookmaker_mappings" in normalized:
            (
                provider_code,
                provider_key,
                mapping_version,
                bookmaker_code,
                normalization_class,
                effective_from,
            ) = params
            mapping_id = self.next_id
            self.next_id += 1
            self.rows[(provider_code, provider_key)] = (
                mapping_id,
                bookmaker_code,
                normalization_class,
            )
            self.inserts.append(
                (
                    provider_key,
                    mapping_version,
                    bookmaker_code,
                    normalization_class,
                    effective_from,
                )
            )
            return _Result((mapping_id,))
        raise AssertionError(normalized)


def test_bootstrap_anchors_all_plan_book_mappings_to_2026_history():
    conn = _Conn()
    ids = ensure_oddspapi_bookmaker_mappings(conn)
    assert len(ids) == len(MAPPINGS)
    assert {row[0] for row in conn.inserts} == {
        "pinnacle+30",
        "1xbet",
        "unibet",
        "bet365",
    }
    assert all(row[4] == ODDSPAPI_V4_EFFECTIVE_FROM for row in conn.inserts)
    assert ODDSPAPI_V4_EFFECTIVE_FROM == datetime(2026, 1, 1, tzinfo=UTC)


def test_bootstrap_is_idempotent_for_matching_mappings():
    conn = _Conn()
    first = ensure_oddspapi_bookmaker_mappings(conn)
    second = ensure_oddspapi_bookmaker_mappings(conn)
    assert second == first
    assert len(conn.inserts) == len(MAPPINGS)
