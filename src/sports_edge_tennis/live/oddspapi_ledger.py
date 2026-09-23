from __future__ import annotations

"""Durable OddsPapi request-usage ledger and monthly budget enforcement.

OddsPapi exposes no provider usage endpoint, so the pipeline keeps its own
authoritative month-to-date ledger in the production database. Every billable
HTTP attempt (including retries) reserves a row *before* the request is sent and
records its outcome afterwards. Reservation is atomic: it takes a transaction
advisory lock, counts the current billing month, and refuses the request when it
would exceed the usable monthly allowance.

The ledger never stores credentials, authorization headers, or secret query
contents.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

LEDGER_LOCK_KEY = "oddspapi_request_ledger_v1"
LEDGER_TABLE = "oddspapi_request_ledger"

REQUEST_PURPOSES = ("QUALIFICATION", "DISCOVERY", "DIAGNOSTIC")
CLASSIFICATIONS = ("PRODUCTION", "SHADOW", "DIAGNOSTIC")


class OddspapiBudgetExceeded(RuntimeError):
    """Raised when a provider request would exceed the usable monthly allowance."""


@dataclass(frozen=True)
class OddspapiLedgerConfig:
    monthly_limit: int = 5000
    reserve: int = 500

    @property
    def usable_allowance(self) -> int:
        return max(0, self.monthly_limit - self.reserve)


def billing_month(now: datetime) -> str:
    if now.tzinfo is None:
        raise ValueError("billing_month requires a timezone-aware timestamp")
    return now.astimezone(UTC).strftime("%Y-%m")


def month_window(now: datetime) -> tuple[datetime, datetime]:
    if now.tzinfo is None:
        raise ValueError("month_window requires a timezone-aware timestamp")
    start = now.astimezone(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    end = (start + timedelta(days=32)).replace(day=1)
    return start, end


def remaining_allowance(*, used: int, config: OddspapiLedgerConfig) -> int:
    return max(0, config.usable_allowance - int(used))


def would_exceed_monthly(*, used: int, config: OddspapiLedgerConfig) -> bool:
    return int(used) + 1 > config.usable_allowance


def month_to_date_count(conn, *, month: str) -> int:
    row = conn.execute(
        f"""
        SELECT count(*) FROM {LEDGER_TABLE}
        WHERE billing_month = %s
          AND classification <> 'DIAGNOSTIC'
        """,
        (month,),
    ).fetchone()
    if row is None or row[0] is None:
        return 0
    return int(row[0])


def reserve_attempt(
    conn,
    *,
    config: OddspapiLedgerConfig,
    endpoint: str,
    request_purpose: str,
    classification: str,
    now: datetime,
    bookmaker: str | None = None,
    tournament_chunk_size: int | None = None,
    correlation_id: str | None = None,
    retry_sequence: int = 0,
) -> int:
    """Atomically reserve one billable request slot; return the ledger row id."""
    if request_purpose not in REQUEST_PURPOSES:
        raise ValueError(f"unsupported request_purpose {request_purpose!r}")
    if classification not in CLASSIFICATIONS:
        raise ValueError(f"unsupported classification {classification!r}")
    if retry_sequence < 0:
        raise ValueError("retry_sequence must be >= 0")
    month = billing_month(now)
    with conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (LEDGER_LOCK_KEY,))
        used = month_to_date_count(conn, month=month)
        if would_exceed_monthly(used=used, config=config):
            raise OddspapiBudgetExceeded(
                "oddspapi_monthly_budget_exceeded "
                f"used={used} allowance={config.usable_allowance} month={month}"
            )
        row = conn.execute(
            f"""
            INSERT INTO {LEDGER_TABLE} (
                requested_at,
                billing_month,
                endpoint,
                bookmaker,
                tournament_chunk_size,
                request_purpose,
                classification,
                attempt_status,
                retry_sequence,
                correlation_id
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, 'RESERVED', %s, %s)
            RETURNING oddspapi_request_ledger_id
            """,
            (
                now.astimezone(UTC),
                month,
                endpoint,
                bookmaker,
                tournament_chunk_size,
                request_purpose,
                classification,
                retry_sequence,
                correlation_id,
            ),
        ).fetchone()
        return int(row[0])


def record_outcome(
    conn,
    *,
    ledger_id: int,
    success: bool,
    http_status: int | None = None,
    error_kind: str | None = None,
    retry_sequence: int | None = None,
) -> None:
    conn.execute(
        f"""
        UPDATE {LEDGER_TABLE}
        SET attempt_status = %s,
            http_status = %s,
            error_kind = %s,
            retry_sequence = COALESCE(%s, retry_sequence)
        WHERE oddspapi_request_ledger_id = %s
        """,
        (
            "SUCCESS" if success else "FAILURE",
            http_status,
            error_kind,
            retry_sequence,
            ledger_id,
        ),
    )


def projected_monthly(*, requests_per_cycle: int, cycles_per_month: int) -> int:
    return int(requests_per_cycle) * int(cycles_per_month)


class OddspapiLedgerBinding:
    """Binds a database connection and classification for transport accounting."""

    def __init__(
        self,
        *,
        conn,
        config: OddspapiLedgerConfig,
        request_purpose: str,
        classification: str,
        correlation_id: str,
    ) -> None:
        if request_purpose not in REQUEST_PURPOSES:
            raise ValueError(f"unsupported request_purpose {request_purpose!r}")
        if classification not in CLASSIFICATIONS:
            raise ValueError(f"unsupported classification {classification!r}")
        self.conn = conn
        self.config = config
        self.request_purpose = request_purpose
        self.classification = classification
        self.correlation_id = correlation_id

    def reserve(
        self,
        *,
        endpoint: str,
        bookmaker: str | None = None,
        chunk_size: int | None = None,
        retry_sequence: int = 0,
        now: datetime | None = None,
    ) -> int:
        return reserve_attempt(
            self.conn,
            config=self.config,
            endpoint=endpoint,
            request_purpose=self.request_purpose,
            classification=self.classification,
            now=now or datetime.now(UTC),
            bookmaker=bookmaker,
            tournament_chunk_size=chunk_size,
            correlation_id=self.correlation_id,
            retry_sequence=retry_sequence,
        )

    def record(
        self,
        ledger_id: int,
        *,
        success: bool,
        http_status: int | None = None,
        error_kind: str | None = None,
        retry_sequence: int | None = None,
    ) -> None:
        record_outcome(
            self.conn,
            ledger_id=ledger_id,
            success=success,
            http_status=http_status,
            error_kind=error_kind,
            retry_sequence=retry_sequence,
        )


__all__ = [
    "CLASSIFICATIONS",
    "LEDGER_LOCK_KEY",
    "LEDGER_TABLE",
    "REQUEST_PURPOSES",
    "OddspapiBudgetExceeded",
    "OddspapiLedgerBinding",
    "OddspapiLedgerConfig",
    "billing_month",
    "month_to_date_count",
    "month_window",
    "projected_monthly",
    "record_outcome",
    "remaining_allowance",
    "reserve_attempt",
    "would_exceed_monthly",
]
