from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

from sports_edge_tennis.dashboard.daily_bets import (
    DailyBetSummary,
    aggregate_bet_summaries,
    build_performance_windows,
    load_daily_bet_summaries,
    load_daily_bets,
)


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


class _Conn:
    """A connection stub that answers the loaders' queries in order.

    The Veto audit-table probe is answered out of band and does not consume a
    scripted response, so a test scripts only the rows it actually cares
    about. Reporting the table as absent is the right answer for a stub with
    no database behind it: Veto is then fingerprinted as MISSING, exactly as
    it is on a database where the Veto service has never written.
    """

    def __init__(self, *responses):
        self._responses = iter(responses)
        self.calls = []

    def execute(self, sql, params=()):
        if "to_regclass" in sql:
            return _Result([(None,)])
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
    assert "published_at IS NOT NULL" in conn.calls[0][0]
    assert "match_start_at" in conn.calls[0][0]


def test_period_summary_rolls_up_daily_profit_and_settled_stake():
    rows = (
        DailyBetSummary(
            decision_date=date(2026, 9, 13),
            bets=3,
            settled=2,
            pending=1,
            wins=1,
            losses=1,
            voids=0,
            cancelled=0,
            one_unit_profit=Decimal("0.50"),
            roi=Decimal("0.25"),
            hit_rate=0.5,
        ),
        DailyBetSummary(
            decision_date=date(2026, 9, 12),
            bets=4,
            settled=4,
            pending=0,
            wins=3,
            losses=1,
            voids=0,
            cancelled=0,
            one_unit_profit=Decimal("1.50"),
            roi=Decimal("0.375"),
            hit_rate=0.75,
        ),
    )

    summary = aggregate_bet_summaries(rows)

    assert summary.bets == 7
    assert summary.settled == 6
    assert summary.pending == 1
    assert summary.wins == 4
    assert summary.losses == 2
    assert summary.one_unit_profit == Decimal("2.00")
    assert summary.roi == Decimal("2.00") / Decimal("6")
    assert summary.hit_rate == 4 / 6


def test_performance_windows_use_current_utc_date_boundaries():
    rows = (
        DailyBetSummary(date(2026, 9, 13), 2, 2, 0, 2, 0, 0, 0, Decimal("1.00"), Decimal("0.50"), 1.0),
        DailyBetSummary(date(2026, 9, 7), 2, 2, 0, 1, 1, 0, 0, Decimal("0.20"), Decimal("0.10"), 0.5),
        DailyBetSummary(date(2026, 9, 6), 2, 2, 0, 1, 1, 0, 0, Decimal("-0.20"), Decimal("-0.10"), 0.5),
        DailyBetSummary(date(2026, 8, 15), 2, 2, 0, 1, 1, 0, 0, Decimal("0.40"), Decimal("0.20"), 0.5),
        DailyBetSummary(date(2026, 8, 14), 2, 2, 0, 1, 1, 0, 0, Decimal("0.60"), Decimal("0.30"), 0.5),
    )

    windows = build_performance_windows(rows, as_of_date=date(2026, 9, 13))

    assert windows["today"].bets == 2
    assert windows["last_7_days"].bets == 4
    assert windows["last_30_days"].bets == 8
    assert windows["all_time"].bets == 10
    assert windows["last_7_days"].one_unit_profit == Decimal("1.20")
    assert windows["last_7_days"].roi == Decimal("1.20") / Decimal("4")


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
                "v1",
                "OFFERED",
                "REGIME_1111111111111111",
                "v1",
                "tennis_veto_v1",
                "a1-base",
                "a2-base",
                "a3-base",
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
                "v1",
                "OFFERED",
                "REGIME_1111111111111111",
                "v1",
                "tennis_veto_v1",
                "a1-base",
                "a2-base",
                "a3-base",
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
                None,
                None,
                "LEGACY_2222222222222222",
                None,
                None,
                None,
                None,
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
    assert "published_at IS NOT NULL" in conn.calls[0][0]
    assert "match_start_at AT TIME ZONE 'UTC'" in conn.calls[0][0]
