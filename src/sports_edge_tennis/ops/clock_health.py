"""Verified-clock evidence for the independent monitoring service.

A monitor with a wrong clock either alerts early or, worse, accepts a late
receipt as timely. This module compares the local UTC clock with an independent
HTTP ``Date`` response and refuses a monitoring cycle whose skew exceeds the
approved threshold.

No external request is made by the tests; the transport is injectable.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Callable, Mapping

CLOCK_OK = "CLOCK_OK"
CLOCK_SKEW_EXCEEDED = "CLOCK_SKEW_EXCEEDED"
CLOCK_UNVERIFIED = "CLOCK_UNVERIFIED"

MAX_SKEW_SECONDS = 60.0


@dataclass(frozen=True)
class ClockReading:
    source: str
    remote_utc: datetime
    local_utc: datetime
    skew_seconds: float

    def to_payload(self) -> dict[str, object]:
        return {
            "source": self.source,
            "remote_utc": self.remote_utc.isoformat(),
            "local_utc": self.local_utc.isoformat(),
            "skew_seconds": self.skew_seconds,
        }


@dataclass(frozen=True)
class ClockAssessment:
    status: str
    max_abs_skew_seconds: float | None
    readings: tuple[ClockReading, ...]
    detail: str

    def to_payload(self) -> dict[str, object]:
        return {
            "status": self.status,
            "max_abs_skew_seconds": self.max_abs_skew_seconds,
            "readings": [reading.to_payload() for reading in self.readings],
            "detail": self.detail,
        }


def read_http_date(
    *,
    source: str,
    headers: Mapping[str, str],
    local_now: datetime,
) -> ClockReading:
    """Read an independent HTTP ``Date`` header and compute the local skew."""

    if local_now.tzinfo is None or local_now.utcoffset() is None:
        raise ValueError("local_now must be timezone-aware")
    raw = headers.get("Date") or headers.get("date")
    if not raw:
        raise ValueError("response has no Date header")
    remote = parsedate_to_datetime(raw)
    if remote.tzinfo is None:
        remote = remote.replace(tzinfo=UTC)
    remote = remote.astimezone(UTC)
    skew = (local_now.astimezone(UTC) - remote).total_seconds()
    return ClockReading(
        source=source,
        remote_utc=remote,
        local_utc=local_now.astimezone(UTC),
        skew_seconds=skew,
    )


def assess_clock(
    readings: list[ClockReading],
    *,
    max_skew_seconds: float = MAX_SKEW_SECONDS,
) -> ClockAssessment:
    if max_skew_seconds <= 0:
        raise ValueError("max_skew_seconds must be positive")
    if not readings:
        return ClockAssessment(
            CLOCK_UNVERIFIED, None, (), "NO_INDEPENDENT_CLOCK_SOURCE"
        )
    worst = max(abs(reading.skew_seconds) for reading in readings)
    status = CLOCK_OK if worst <= max_skew_seconds else CLOCK_SKEW_EXCEEDED
    return ClockAssessment(
        status,
        worst,
        tuple(readings),
        "WITHIN_THRESHOLD" if status == CLOCK_OK else "SKEW_THRESHOLD_EXCEEDED",
    )


def verify_clock(
    *,
    sources: Mapping[str, Callable[[], Mapping[str, str]]],
    local_now: datetime,
    max_skew_seconds: float = MAX_SKEW_SECONDS,
) -> ClockAssessment:
    readings: list[ClockReading] = []
    for source, fetch in sources.items():
        readings.append(
            read_http_date(
                source=source,
                headers=fetch(),
                local_now=local_now,
            )
        )
    return assess_clock(readings, max_skew_seconds=max_skew_seconds)
