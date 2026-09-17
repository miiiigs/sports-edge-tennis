from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sports_edge_tennis.performance.pncl_concordance import (
    build_pncl_concordance_lines,
    load_pncl_pinnacle_concordance,
)


class _Cursor:
    def __init__(self, *, row=None, rows=None):
        self._row = row
        self._rows = rows or []

    def fetchone(self):
        return self._row

    def fetchall(self):
        return self._rows


class _Conn:
    def __init__(self, responses):
        self._responses = list(responses)
        self.sql = []

    def execute(self, sql, params=()):
        self.sql.append((str(sql), params))
        return self._responses.pop(0)


def test_pncl_concordance_pairs_same_match_side_and_reports_price_distance():
    as_of = datetime(2026, 9, 17, 10, 0, tzinfo=timezone.utc)
    pncl_at = as_of - timedelta(minutes=5)
    sharp_at = pncl_at + timedelta(seconds=12)
    conn = _Conn(
        [
            _Cursor(row=("PNCL", "OFFERED")),
            _Cursor(row=(4, 2)),
            _Cursor(
                rows=[
                    (101, "PLAYER_1", Decimal("1.910000"), Decimal("1.910000"), pncl_at, sharp_at, 12),
                    (101, "PLAYER_2", Decimal("1.990000"), Decimal("2.000000"), pncl_at, sharp_at, 12),
                ]
            ),
        ]
    )

    evidence = load_pncl_pinnacle_concordance(conn, as_of=as_of)

    assert evidence.mapping_found is True
    assert evidence.mapping_bookmaker_code == "PNCL"
    assert evidence.mapping_class == "OFFERED"
    assert evidence.pncl_quote_count == 4
    assert evidence.pncl_match_count == 2
    assert evidence.overlap_count == 2
    assert evidence.overlap_match_count == 1
    assert evidence.exact_price_count == 1
    assert evidence.mean_absolute_price_difference == Decimal("0.005000")
    assert evidence.median_absolute_price_difference == Decimal("0.005000")
    assert evidence.max_absolute_price_difference == Decimal("0.010000")
    assert evidence.max_gap_seconds == 12

    pair_sql = conn.sql[2][0]
    assert "s.match_id = p.match_id" in pair_sql
    assert "s.selection_type::text = p.selection_type" in pair_sql
    assert "s.source_system = 'SHARP'" in pair_sql
    assert "s.bookmaker_code = 'PINNACLE'" in pair_sql


def test_pncl_concordance_output_never_verifies_or_reclassifies_identity():
    as_of = datetime(2026, 9, 17, 10, 0, tzinfo=timezone.utc)
    conn = _Conn(
        [
            _Cursor(row=("PNCL", "OFFERED")),
            _Cursor(row=(0, 0)),
            _Cursor(rows=[]),
        ]
    )

    evidence = load_pncl_pinnacle_concordance(conn, as_of=as_of)
    lines = build_pncl_concordance_lines(evidence)

    assert len(lines) == 1
    assert "overlaps=0" in lines[0]
    assert "identity_verified=0" in lines[0]
    assert "classification_changed=0" in lines[0]
