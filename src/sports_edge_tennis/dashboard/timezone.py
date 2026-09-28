"""Central dashboard timezone configuration.

The dashboard stores every canonical timestamp in UTC. Time zones belong to the
*presentation* layer only: they decide which calendar day a match is filed
under and how a clock time reads to the client. Nothing here mutates stored
timestamps, provider ingestion, scheduling, or settlement.

Only three time zones are supported. The client never supplies an arbitrary
IANA name: it supplies one of the fixed keys below, which the server maps to a
trusted IANA name before any SQL is executed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

TIMEZONE_COOKIE_NAME = "tennis_dashboard_timezone"
DEFAULT_TIMEZONE_KEY = "utc"


@dataclass(frozen=True)
class DashboardTimezone:
    """One selectable dashboard time zone.

    ``short_label`` is fixed for zones whose abbreviation the platform reports
    differently from the label we want to show (for example ``Asia/Manila``
    reports ``PST``; we display ``PHT``). When it is ``None`` the abbreviation
    is resolved from the zone itself, so ``Europe/London`` automatically reports
    ``GMT`` or ``BST`` according to the actual date.
    """

    key: str
    iana: str
    display_label: str
    short_label: str | None = None

    @property
    def zone(self) -> ZoneInfo:
        return ZoneInfo(self.iana)

    def abbreviation(self, at: datetime) -> str:
        if self.short_label is not None:
            return self.short_label
        return _as_local(at, self).strftime("%Z")


SUPPORTED_TIMEZONES: tuple[DashboardTimezone, ...] = (
    DashboardTimezone("utc", "UTC", "UTC", "UTC"),
    DashboardTimezone("philippines", "Asia/Manila", "Philippines", "PHT"),
    DashboardTimezone("london", "Europe/London", "London / UK"),
)

TIMEZONE_BY_KEY: dict[str, DashboardTimezone] = {
    tz.key: tz for tz in SUPPORTED_TIMEZONES
}

DEFAULT_TIMEZONE: DashboardTimezone = TIMEZONE_BY_KEY[DEFAULT_TIMEZONE_KEY]


def is_supported_key(key: str | None) -> bool:
    """True only for one of the three fixed dashboard keys."""
    return bool(key) and key.strip().lower() in TIMEZONE_BY_KEY


def resolve_dashboard_timezone(key: str | None) -> DashboardTimezone:
    """Resolve a client-supplied key to a trusted zone, defaulting to UTC.

    Unknown or missing keys fall back to UTC rather than raising, so a stale
    cookie or a hand-edited query string can never break a page or inject an
    arbitrary zone into a query.
    """
    if not key:
        return DEFAULT_TIMEZONE
    return TIMEZONE_BY_KEY.get(key.strip().lower(), DEFAULT_TIMEZONE)


def is_valid_timezone_name(iana: str | None) -> bool:
    """Guard for values that travel into SQL.

    Only the IANA names mapped by the supported keys are ever accepted, so a
    resolved name handed to the database is always one of three known strings.
    """
    if not iana:
        return False
    return any(tz.iana == iana for tz in SUPPORTED_TIMEZONES)


# ---------------------------------------------------------------------------
# Presentation helpers
# ---------------------------------------------------------------------------


def _as_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def _as_local(value: datetime, tz: DashboardTimezone) -> datetime:
    return _as_aware(value).astimezone(tz.zone)


def dashboard_now(tz: DashboardTimezone) -> datetime:
    return datetime.now(tz.zone)


def dashboard_today(tz: DashboardTimezone) -> date:
    """The current calendar day in the selected dashboard time zone."""
    return dashboard_now(tz).date()


def local_date(value: datetime, tz: DashboardTimezone) -> date:
    """The calendar day a UTC instant falls on in the selected zone."""
    return _as_local(value, tz).date()


def utc_bounds_for_local_date(
    day: date, tz: DashboardTimezone
) -> tuple[datetime, datetime]:
    """Half-open UTC window ``[start, end)`` covering one local calendar day.

    Handles daylight-saving transitions correctly because the local midnight is
    built with the real zone rather than a fixed offset.
    """
    start = datetime.combine(day, time.min, tzinfo=tz.zone)
    end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=tz.zone)
    return start.astimezone(UTC), end.astimezone(UTC)


def format_dashboard_time(
    value: datetime | None, tz: DashboardTimezone, fmt: str = "%H:%M"
) -> str:
    """Render a UTC instant as ``HH:MM`` plus the zone's real abbreviation."""
    if value is None:
        return "—"
    local = _as_local(value, tz)
    return f"{local.strftime(fmt)} {tz.abbreviation(value)}"


def format_dashboard_datetime(
    value: datetime | None, tz: DashboardTimezone, fmt: str = "%d %b %H:%M"
) -> str:
    """Render a UTC instant as a date/time plus the zone abbreviation."""
    if value is None:
        return "—"
    local = _as_local(value, tz)
    return f"{local.strftime(fmt)} {tz.abbreviation(value)}"


def format_dashboard_date(
    value: datetime | None, tz: DashboardTimezone, fmt: str = "%a %d %b %Y"
) -> str:
    """Render the local calendar date of a UTC instant (no abbreviation)."""
    if value is None:
        return "—"
    return _as_local(value, tz).strftime(fmt)


__all__ = [
    "DEFAULT_TIMEZONE",
    "DEFAULT_TIMEZONE_KEY",
    "SUPPORTED_TIMEZONES",
    "TIMEZONE_BY_KEY",
    "TIMEZONE_COOKIE_NAME",
    "DashboardTimezone",
    "dashboard_now",
    "dashboard_today",
    "format_dashboard_date",
    "format_dashboard_datetime",
    "format_dashboard_time",
    "is_supported_key",
    "is_valid_timezone_name",
    "local_date",
    "resolve_dashboard_timezone",
    "utc_bounds_for_local_date",
]
