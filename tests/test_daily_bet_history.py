from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

from sports_edge_tennis.dashboard.daily_bets import (
    load_daily_bet_summaries,
    load_daily_bets,
)


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _Conn:
    def __init__(self, *responses):
        self._responses = iter(responses)
        self.calls = []

    def execute(self, sql, params=()):
        self.calls.append((sql, params))
        return _Result(next(self._responses))


def test_daily_summary_uses_settled_count_for_roi_and_wl_for_hit_rate():
    conn = _Conn(
        [
            (
                date(2026, 9, 13),
                6,
                5,
                1,
                3,
                1,
                1,
                0,
                Decimal("1.40"),
            )
        ]
    )

    rows = load_daily_bet_summaries(conn)

    assert len(rows) == 1
    row = rows[0]
    assert row.bets == 6
    assert row.settled == 5
    assert row.pending == 1
    assert row.wins == 3
    assert row.losses == 1
    assert row.voids == 1
    assert row.one_unit_profit == Decimal("1.40")
    assert row.roi == Decimal("0.28")
    assert row.hit_rate == 0.75
    assert "TYPE_A_MODEL_EDGE" in conn.calls[0][0]
    assert "decision_status = 'BET'" in conn.calls[0][0]


def test_daily_bets_keep_pending_separate_and_compute_one_unit_profit():
    now = datetime(2026, 9, 13, 3, 0, tzinfo=timezone.utc)
    conn = _Conn(
        [
            (
                101,
                now,
                now,
                "Test Open",
                "Player A",
                "Player B",
                "Player A",
                Decimal("2.10"),
                "BOOK",
                Decimal("0.61"),
                "WON",
                now,
            ),
            (
                102,
                now,
                now,
                "Test Open",
                "Player C",
                "Player D",
                "Player D",
                Decimal("1.85"),
                "BOOK",
                Decimal("0.58"),
                "LOST",
                now,
            ),
            (
                103,
                now,
                now,
                "Test Open",
                "Player E",
                "Player F",
                "Player E",
                Decimal("1.90"),
                "BOOK",
                Decimal("0.56"),
                "PENDING",
                None,
            ),
        ]
    )

    rows = load_daily_bets(conn, decision_date=date(2026, 9, 13))

    assert rows[0].settlement_status == "WON"
    assert rows[0].profit_units == Decimal("1.10")
    assert rows[1].settlement_status == "LOST"
    assert rows[1].profit_units == Decimal("-1")
    assert rows[2].settlement_status == "PENDING"
    assert rows[2].profit_units is None
    assert rows[0].selection == "Player A"
    assert conn.calls[0][1] == (date(2026, 9, 13),)
