from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Iterable

import psycopg

from sports_edge_tennis.market.contracts import SharpReferenceSpec

CLV_ACTIVATION_ENV_VAR = "TENNIS_CLV_ACTIVATION_AT"
CLV_CAPTURE_VERSION = "tennis_decision_close_v1"
CLV_CALCULATION_VERSION = "tennis_clv_v1"
DEFAULT_FINALIZATION_DELAY = timedelta(hours=2)
_CLV_QUANTIZER = Decimal("0.00000001")

_CLOSE_LEDGER_DDL = """
CREATE TABLE IF NOT EXISTS decision_close_evidence (
    decision_id BIGINT PRIMARY KEY REFERENCES decisions(decision_id),
    capture_version TEXT NOT NULL CHECK (btrim(capture_version) <> ''),
    activation_at TIMESTAMPTZ NOT NULL,
    match_start_at TIMESTAMPTZ NOT NULL,
    match_status_at_capture TEXT NOT NULL CHECK (btrim(match_status_at_capture) <> ''),
    captured_at TIMESTAMPTZ NOT NULL,
    close_status TEXT NOT NULL CHECK (close_status IN ('COMPLETE', 'PARTIAL', 'MISSING')),

    sharp_player_1_snapshot_id BIGINT REFERENCES odds_snapshots(odds_snapshot_id),
    sharp_player_2_snapshot_id BIGINT REFERENCES odds_snapshots(odds_snapshot_id),
    sharp_player_1_price NUMERIC(12, 6) CHECK (sharp_player_1_price IS NULL OR sharp_player_1_price > 1),
    sharp_player_2_price NUMERIC(12, 6) CHECK (sharp_player_2_price IS NULL OR sharp_player_2_price > 1),
    sharp_pair_observed_at TIMESTAMPTZ,
    sharp_booksum NUMERIC(12, 8) CHECK (sharp_booksum IS NULL OR sharp_booksum > 0),

    same_book_source_system TEXT,
    same_book_bookmaker_code TEXT,
    same_book_player_1_snapshot_id BIGINT REFERENCES odds_snapshots(odds_snapshot_id),
    same_book_player_2_snapshot_id BIGINT REFERENCES odds_snapshots(odds_snapshot_id),
    same_book_player_1_price NUMERIC(12, 6) CHECK (same_book_player_1_price IS NULL OR same_book_player_1_price > 1),
    same_book_player_2_price NUMERIC(12, 6) CHECK (same_book_player_2_price IS NULL OR same_book_player_2_price > 1),
    same_book_pair_observed_at TIMESTAMPTZ,
    same_book_booksum NUMERIC(12, 8) CHECK (same_book_booksum IS NULL OR same_book_booksum > 0),

    clv_available BOOLEAN NOT NULL,
    clv_unavailable_reason TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CHECK (captured_at >= activation_at),
    CHECK (captured_at >= match_start_at),
    CHECK (
        (close_status = 'COMPLETE'
            AND sharp_player_1_snapshot_id IS NOT NULL
            AND sharp_player_2_snapshot_id IS NOT NULL
            AND sharp_pair_observed_at IS NOT NULL)
        OR close_status IN ('PARTIAL', 'MISSING')
    ),
    CHECK (
        clv_available
        OR clv_unavailable_reason IS NOT NULL
    )
);

CREATE INDEX IF NOT EXISTS idx_decision_close_evidence_captured_at
    ON decision_close_evidence (captured_at DESC);
CREATE INDEX IF NOT EXISTS idx_decision_close_evidence_status
    ON decision_close_evidence (close_status, captured_at DESC);

DO $$
BEGIN
    IF to_regprocedure('raise_append_only_mutation()') IS NOT NULL
       AND NOT EXISTS (
           SELECT 1 FROM pg_trigger WHERE tgname = 'trg_decision_close_evidence_append_only'
       ) THEN
        CREATE TRIGGER trg_decision_close_evidence_append_only
        BEFORE UPDATE OR DELETE ON decision_close_evidence
        FOR EACH ROW EXECUTE FUNCTION raise_append_only_mutation();
    END IF;

    IF to_regprocedure('raise_append_only_mutation()') IS NOT NULL
       AND NOT EXISTS (
           SELECT 1 FROM pg_trigger WHERE tgname = 'trg_decision_clv_append_only_v1'
       ) THEN
        CREATE TRIGGER trg_decision_clv_append_only_v1
        BEFORE UPDATE OR DELETE ON decision_clv
        FOR EACH ROW EXECUTE FUNCTION raise_append_only_mutation();
    END IF;
END;
$$;
"""


class ClvConfigurationError(ValueError):
    """Raised when prospective CLV activation is malformed or unsafe."""


class ClvIntegrityError(RuntimeError):
    """Raised when persisted closing/CLV truth is internally inconsistent."""


@dataclass(frozen=True)
class SnapshotPoint:
    odds_snapshot_id: int
    selection_type: str
    decimal_price: Decimal
    source_snapshot_id: str
    source_observed_at: datetime
    as_of: datetime


@dataclass(frozen=True)
class PairEvidence:
    player_1: SnapshotPoint | None
    player_2: SnapshotPoint | None
    pair_observed_at: datetime | None

    @property
    def complete(self) -> bool:
        return self.player_1 is not None and self.player_2 is not None and self.pair_observed_at is not None

    @property
    def booksum(self) -> Decimal | None:
        if not self.complete:
            return None
        assert self.player_1 is not None and self.player_2 is not None
        return (Decimal("1") / self.player_1.decimal_price) + (
            Decimal("1") / self.player_2.decimal_price
        )


@dataclass(frozen=True)
class DecisionToClose:
    decision_id: int
    match_id: int
    decision_status: str
    selected_side: int | None
    market_decimal_price: Decimal | None
    price_source_system: str | None
    price_bookmaker_code: str | None
    decision_as_of: datetime
    decision_created_at: datetime
    match_start_at: datetime
    match_status: str


@dataclass(frozen=True)
class CloseFinalizeOutcome:
    activation_at: datetime
    captured_at: datetime
    eligible_count: int
    finalized_count: int
    complete_count: int
    partial_count: int
    missing_count: int
    clv_created_count: int
    clv_unavailable_count: int


@dataclass(frozen=True)
class ClvSummary:
    initialized: bool
    close_records: int = 0
    complete_closes: int = 0
    partial_closes: int = 0
    missing_closes: int = 0
    no_bet_close_records: int = 0
    no_bet_complete_closes: int = 0
    priced_decisions: int = 0
    clv_records: int = 0
    mean_clv: Decimal | None = None
    median_clv: Decimal | None = None
    activation_at: datetime | None = None

    @property
    def close_coverage(self) -> Decimal | None:
        if self.close_records == 0:
            return None
        return Decimal(self.complete_closes) / Decimal(self.close_records)

    @property
    def priced_clv_coverage(self) -> Decimal | None:
        if self.priced_decisions == 0:
            return None
        return Decimal(self.clv_records) / Decimal(self.priced_decisions)


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ClvConfigurationError("CLV timestamps must be timezone-aware")
    return value.astimezone(timezone.utc)


def _parse_activation(raw: str) -> datetime:
    value = raw.strip()
    if not value:
        raise ClvConfigurationError(f"{CLV_ACTIVATION_ENV_VAR} must not be empty")
    if value.endswith("Z"):
        value = f"{value[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ClvConfigurationError(
            f"{CLV_ACTIVATION_ENV_VAR} must be an ISO-8601 timestamp with timezone"
        ) from exc
    return _aware_utc(parsed)


def activation_from_env() -> datetime | None:
    raw = os.getenv(CLV_ACTIVATION_ENV_VAR)
    if raw is None:
        return None
    return _parse_activation(raw)


def ensure_close_ledger(conn: psycopg.Connection) -> None:
    conn.execute(_CLOSE_LEDGER_DDL)


def _row_to_point(row) -> SnapshotPoint:
    return SnapshotPoint(
        odds_snapshot_id=int(row[0]),
        selection_type=str(row[1]),
        decimal_price=Decimal(row[2]),
        source_snapshot_id=str(row[3]),
        source_observed_at=row[4],
        as_of=row[5],
    )


def _load_pair_evidence(
    conn: psycopg.Connection,
    *,
    match_id: int,
    source_system: str,
    bookmaker_code: str,
    close_cutoff: datetime,
    available_as_of: datetime,
) -> PairEvidence:
    rows = conn.execute(
        """
        SELECT
            odds_snapshot_id,
            selection_type::text,
            decimal_price,
            source_snapshot_id,
            source_observed_at,
            as_of
        FROM odds_snapshots
        WHERE match_id = %s
          AND source_system = %s
          AND bookmaker_code = %s
          AND market_type = 'MATCH_WINNER'
          AND selection_type IN ('PLAYER_1', 'PLAYER_2')
          AND market_status = 'OPEN'
          AND odds_origin = 'LIVE_PROVIDER'
          AND source_snapshot_id IS NOT NULL
          AND source_observed_at <= %s
          AND as_of <= %s
          AND ingested_at <= %s
        ORDER BY source_observed_at DESC, as_of DESC, odds_snapshot_id DESC
        """,
        (
            match_id,
            source_system,
            bookmaker_code,
            close_cutoff,
            available_as_of,
            available_as_of,
        ),
    ).fetchall()

    groups: dict[tuple[str, datetime], dict[str, SnapshotPoint]] = {}
    latest_by_side: dict[str, SnapshotPoint] = {}
    ordered_group_keys: list[tuple[str, datetime]] = []
    for row in rows:
        point = _row_to_point(row)
        key = (point.source_snapshot_id, point.source_observed_at)
        if key not in groups:
            groups[key] = {}
            ordered_group_keys.append(key)
        groups[key].setdefault(point.selection_type, point)
        latest_by_side.setdefault(point.selection_type, point)

    for key in ordered_group_keys:
        sides = groups[key]
        if "PLAYER_1" in sides and "PLAYER_2" in sides:
            return PairEvidence(
                player_1=sides["PLAYER_1"],
                player_2=sides["PLAYER_2"],
                pair_observed_at=key[1],
            )

    return PairEvidence(
        player_1=latest_by_side.get("PLAYER_1"),
        player_2=latest_by_side.get("PLAYER_2"),
        pair_observed_at=None,
    )


def _load_decisions_to_close(
    conn: psycopg.Connection,
    *,
    activation_at: datetime,
    captured_at: datetime,
    finalization_delay: timedelta,
) -> list[DecisionToClose]:
    if finalization_delay < timedelta(0):
        raise ClvConfigurationError("finalization_delay must be non-negative")
    close_before = captured_at - finalization_delay
    rows = conn.execute(
        """
        SELECT
            d.decision_id,
            d.match_id,
            d.decision_status::text,
            d.selection_side,
            d.market_decimal_price,
            d.price_source_system,
            d.price_bookmaker_code,
            d.as_of,
            d.created_at,
            mv.match_start_at,
            mv.match_status::text
        FROM decisions d
        JOIN LATERAL (
            SELECT match_start_at, match_status
            FROM match_versions
            WHERE match_id = d.match_id
              AND source_observed_at <= %s
              AND as_of <= %s
            ORDER BY as_of DESC, source_observed_at DESC, match_version_id DESC
            LIMIT 1
        ) mv ON TRUE
        WHERE d.signal_type = 'TYPE_A_MODEL_EDGE'
          AND d.manager_consensus_result_id IS NOT NULL
          AND d.created_at >= %s
          AND d.created_at <= %s
          AND mv.match_start_at <= %s
          AND mv.match_status <> 'POSTPONED'
          AND NOT EXISTS (
              SELECT 1
              FROM decision_close_evidence dce
              WHERE dce.decision_id = d.decision_id
          )
        ORDER BY mv.match_start_at, d.decision_id
        """,
        (captured_at, captured_at, activation_at, captured_at, close_before),
    ).fetchall()
    return [
        DecisionToClose(
            decision_id=int(row[0]),
            match_id=int(row[1]),
            decision_status=str(row[2]),
            selected_side=int(row[3]) if row[3] is not None else None,
            market_decimal_price=Decimal(row[4]) if row[4] is not None else None,
            price_source_system=str(row[5]) if row[5] is not None else None,
            price_bookmaker_code=str(row[6]) if row[6] is not None else None,
            decision_as_of=row[7],
            decision_created_at=row[8],
            match_start_at=row[9],
            match_status=str(row[10]),
        )
        for row in rows
    ]


def _price_taken_lineage(
    conn: psycopg.Connection,
    decision: DecisionToClose,
) -> tuple[datetime, Decimal] | None:
    row = conn.execute(
        """
        SELECT os.source_observed_at, os.decimal_price,
               os.source_system, os.bookmaker_code, os.selection_type::text
        FROM decision_odds_snapshots dos
        JOIN odds_snapshots os ON os.odds_snapshot_id = dos.odds_snapshot_id
        WHERE dos.decision_id = %s
          AND dos.snapshot_role = 'OFFERED_SELECTION'
        LIMIT 1
        """,
        (decision.decision_id,),
    ).fetchone()
    if row is None:
        return None
    if decision.selected_side not in (1, 2):
        return None
    expected_selection = "PLAYER_1" if decision.selected_side == 1 else "PLAYER_2"
    if (
        decision.market_decimal_price is None
        or decision.price_source_system is None
        or decision.price_bookmaker_code is None
        or str(row[2]) != decision.price_source_system
        or str(row[3]) != decision.price_bookmaker_code
        or str(row[4]) != expected_selection
        or Decimal(row[1]) != decision.market_decimal_price
    ):
        return None
    return row[0], Decimal(row[1])


def _clv_values(
    conn: psycopg.Connection,
    *,
    decision: DecisionToClose,
    same_book: PairEvidence | None,
    sharp: PairEvidence,
) -> tuple[bool, str | None, tuple | None]:
    if decision.decision_status not in {"BET", "VETOED"}:
        return False, "NOT_PRICED_DECISION", None
    if decision.selected_side not in (1, 2):
        return False, "MISSING_SELECTED_SIDE", None
    if decision.market_decimal_price is None or not decision.price_bookmaker_code or not decision.price_source_system:
        return False, "MISSING_PRICE_TAKEN", None
    if decision.match_status in {"CANCELLED", "POSTPONED", "WALKOVER"}:
        return False, "MATCH_NOT_PLAYED_AS_SCHEDULED", None
    price_lineage = _price_taken_lineage(conn, decision)
    if price_lineage is None:
        return False, "PRICE_TAKEN_LINEAGE_MISSING", None
    price_taken_at, price_taken = price_lineage
    if price_taken_at > decision.match_start_at:
        return False, "PRICE_TAKEN_AFTER_START", None
    if same_book is None or not same_book.complete:
        return False, "SAME_BOOK_CLOSE_MISSING", None
    assert same_book.player_1 is not None and same_book.player_2 is not None
    assert same_book.pair_observed_at is not None
    if same_book.pair_observed_at < price_taken_at:
        return False, "SAME_BOOK_CLOSE_PRECEDES_PRICE_TAKEN", None
    booksum = same_book.booksum
    assert booksum is not None
    margin = (booksum - Decimal("1")).quantize(
        _CLV_QUANTIZER,
        rounding=ROUND_HALF_UP,
    )
    if margin <= 0:
        return False, "SAME_BOOK_MARGIN_NON_POSITIVE", None

    close_same = (
        same_book.player_1.decimal_price
        if decision.selected_side == 1
        else same_book.player_2.decimal_price
    )
    closing_sharp = None
    if sharp.complete:
        assert sharp.player_1 is not None and sharp.player_2 is not None
        closing_sharp = (
            sharp.player_1.decimal_price
            if decision.selected_side == 1
            else sharp.player_2.decimal_price
        )

    clv = (
        (price_taken / close_same / (Decimal("1") + margin)) - Decimal("1")
    ).quantize(_CLV_QUANTIZER, rounding=ROUND_HALF_UP)
    return (
        True,
        None,
        (
            price_taken,
            price_taken_at,
            close_same,
            closing_sharp,
            margin,
            same_book.pair_observed_at,
            clv,
        ),
    )


def _close_status(sharp: PairEvidence) -> str:
    if sharp.complete:
        return "COMPLETE"
    if sharp.player_1 is not None or sharp.player_2 is not None:
        return "PARTIAL"
    return "MISSING"


def _point_id(point: SnapshotPoint | None) -> int | None:
    return point.odds_snapshot_id if point is not None else None


def _point_price(point: SnapshotPoint | None) -> Decimal | None:
    return point.decimal_price if point is not None else None


def _insert_close_evidence(
    conn: psycopg.Connection,
    *,
    decision: DecisionToClose,
    activation_at: datetime,
    captured_at: datetime,
    sharp: PairEvidence,
    same_book: PairEvidence | None,
    clv_available: bool,
    clv_unavailable_reason: str | None,
) -> bool:
    inserted = conn.execute(
        """
        INSERT INTO decision_close_evidence (
            decision_id, capture_version, activation_at, match_start_at,
            match_status_at_capture, captured_at, close_status,
            sharp_player_1_snapshot_id, sharp_player_2_snapshot_id,
            sharp_player_1_price, sharp_player_2_price,
            sharp_pair_observed_at, sharp_booksum,
            same_book_source_system, same_book_bookmaker_code,
            same_book_player_1_snapshot_id, same_book_player_2_snapshot_id,
            same_book_player_1_price, same_book_player_2_price,
            same_book_pair_observed_at, same_book_booksum,
            clv_available, clv_unavailable_reason
        )
        VALUES (
            %s, %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s, %s, %s, %s,
            %s, %s
        )
        ON CONFLICT (decision_id) DO NOTHING
        RETURNING decision_id
        """,
        (
            decision.decision_id,
            CLV_CAPTURE_VERSION,
            activation_at,
            decision.match_start_at,
            decision.match_status,
            captured_at,
            _close_status(sharp),
            _point_id(sharp.player_1),
            _point_id(sharp.player_2),
            _point_price(sharp.player_1),
            _point_price(sharp.player_2),
            sharp.pair_observed_at,
            sharp.booksum,
            decision.price_source_system if same_book is not None else None,
            decision.price_bookmaker_code if same_book is not None else None,
            _point_id(same_book.player_1) if same_book is not None else None,
            _point_id(same_book.player_2) if same_book is not None else None,
            _point_price(same_book.player_1) if same_book is not None else None,
            _point_price(same_book.player_2) if same_book is not None else None,
            same_book.pair_observed_at if same_book is not None else None,
            same_book.booksum if same_book is not None else None,
            clv_available,
            clv_unavailable_reason,
        ),
    ).fetchone()
    return inserted is not None


def _insert_clv(conn: psycopg.Connection, decision_id: int, values: tuple) -> bool:
    (
        price_taken,
        price_taken_at,
        close_same,
        closing_sharp,
        closing_margin,
        closing_observed_at,
        clv,
    ) = values
    inserted = conn.execute(
        """
        INSERT INTO decision_clv (
            decision_id, price_taken, price_taken_at,
            closing_same_book_price, closing_sharp_price,
            closing_margin, closing_observed_at,
            calculation_version, clv_pct
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (decision_id) DO NOTHING
        RETURNING decision_id
        """,
        (
            decision_id,
            price_taken,
            price_taken_at,
            close_same,
            closing_sharp,
            closing_margin,
            closing_observed_at,
            CLV_CALCULATION_VERSION,
            clv,
        ),
    ).fetchone()
    if inserted is not None:
        return True

    existing = conn.execute(
        """
        SELECT price_taken, price_taken_at, closing_same_book_price,
               closing_sharp_price, closing_margin, closing_observed_at,
               calculation_version, clv_pct
        FROM decision_clv
        WHERE decision_id = %s
        """,
        (decision_id,),
    ).fetchone()
    expected = (
        price_taken,
        price_taken_at,
        close_same,
        closing_sharp,
        closing_margin,
        closing_observed_at,
        CLV_CALCULATION_VERSION,
        clv,
    )
    if existing is None or tuple(existing) != expected:
        raise ClvIntegrityError("persisted CLV differs from canonical prospective calculation")
    return False


def finalize_closing_evidence(
    conn: psycopg.Connection,
    *,
    activation_at: datetime,
    captured_at: datetime,
    sharp_reference: SharpReferenceSpec,
    finalization_delay: timedelta = DEFAULT_FINALIZATION_DELAY,
) -> CloseFinalizeOutcome:
    activation = _aware_utc(activation_at)
    captured = _aware_utc(captured_at)
    if captured < activation:
        raise ClvConfigurationError("captured_at must be on or after activation_at")

    ensure_close_ledger(conn)
    decisions = _load_decisions_to_close(
        conn,
        activation_at=activation,
        captured_at=captured,
        finalization_delay=finalization_delay,
    )

    finalized = 0
    complete = 0
    partial = 0
    missing = 0
    clv_created = 0
    clv_unavailable = 0

    with conn.transaction():
        for decision in decisions:
            sharp = _load_pair_evidence(
                conn,
                match_id=decision.match_id,
                source_system=sharp_reference.source_system,
                bookmaker_code=sharp_reference.bookmaker_code,
                close_cutoff=decision.match_start_at,
                available_as_of=captured,
            )
            same_book = None
            if decision.price_source_system and decision.price_bookmaker_code:
                same_book = _load_pair_evidence(
                    conn,
                    match_id=decision.match_id,
                    source_system=decision.price_source_system,
                    bookmaker_code=decision.price_bookmaker_code,
                    close_cutoff=decision.match_start_at,
                    available_as_of=captured,
                )

            clv_available, unavailable_reason, clv_values = _clv_values(
                conn,
                decision=decision,
                same_book=same_book,
                sharp=sharp,
            )
            inserted = _insert_close_evidence(
                conn,
                decision=decision,
                activation_at=activation,
                captured_at=captured,
                sharp=sharp,
                same_book=same_book,
                clv_available=clv_available,
                clv_unavailable_reason=unavailable_reason,
            )
            if not inserted:
                continue

            finalized += 1
            status = _close_status(sharp)
            complete += int(status == "COMPLETE")
            partial += int(status == "PARTIAL")
            missing += int(status == "MISSING")

            if clv_available:
                assert clv_values is not None
                clv_created += int(_insert_clv(conn, decision.decision_id, clv_values))
            else:
                clv_unavailable += 1

    return CloseFinalizeOutcome(
        activation_at=activation,
        captured_at=captured,
        eligible_count=len(decisions),
        finalized_count=finalized,
        complete_count=complete,
        partial_count=partial,
        missing_count=missing,
        clv_created_count=clv_created,
        clv_unavailable_count=clv_unavailable,
    )


def _decimal_or_none(value) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value))


def load_clv_summary(conn: psycopg.Connection) -> ClvSummary:
    initialized = conn.execute(
        "SELECT to_regclass('public.decision_close_evidence')"
    ).fetchone()[0]
    if initialized is None:
        return ClvSummary(initialized=False)

    row = conn.execute(
        """
        SELECT
            count(*) AS close_records,
            count(*) FILTER (WHERE dce.close_status = 'COMPLETE') AS complete_closes,
            count(*) FILTER (WHERE dce.close_status = 'PARTIAL') AS partial_closes,
            count(*) FILTER (WHERE dce.close_status = 'MISSING') AS missing_closes,
            count(*) FILTER (WHERE d.decision_status = 'NO_BET') AS no_bet_close_records,
            count(*) FILTER (
                WHERE d.decision_status = 'NO_BET' AND dce.close_status = 'COMPLETE'
            ) AS no_bet_complete_closes,
            count(*) FILTER (
                WHERE d.decision_status IN ('BET', 'VETOED')
                  AND d.market_decimal_price IS NOT NULL
                  AND d.price_bookmaker_code IS NOT NULL
            ) AS priced_decisions,
            count(dc.decision_id) AS clv_records,
            avg(dc.clv_pct) AS mean_clv,
            percentile_cont(0.5) WITHIN GROUP (ORDER BY dc.clv_pct) AS median_clv,
            min(dce.activation_at) AS activation_at
        FROM decision_close_evidence dce
        JOIN decisions d ON d.decision_id = dce.decision_id
        LEFT JOIN decision_clv dc ON dc.decision_id = dce.decision_id
        """
    ).fetchone()
    assert row is not None
    return ClvSummary(
        initialized=True,
        close_records=int(row[0]),
        complete_closes=int(row[1]),
        partial_closes=int(row[2]),
        missing_closes=int(row[3]),
        no_bet_close_records=int(row[4]),
        no_bet_complete_closes=int(row[5]),
        priced_decisions=int(row[6]),
        clv_records=int(row[7]),
        mean_clv=_decimal_or_none(row[8]),
        median_clv=_decimal_or_none(row[9]),
        activation_at=row[10],
    )
