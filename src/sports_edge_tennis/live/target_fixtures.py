from __future__ import annotations

import os
from datetime import date, datetime
from typing import Any

import psycopg

from sports_edge_tennis.live.sportradar import (
    SportradarTransportError,
    fetch_daily_summaries,
)
from sports_edge_tennis.live.sportradar_ingest import (
    SportradarIngestOutcome,
    ingest_sportradar_daily_summaries,
)
from sports_edge_tennis.live.transport import TransportError


def fetch_sportradar_fixture_rows(
    api_key: str,
    slate_date: date,
) -> tuple[datetime, list[dict[str, Any]]]:
    """Adapt Sportradar daily summaries to the stable live-runner fixture contract."""
    access_level = (
        os.environ.get("SPORTRADAR_TENNIS_ACCESS_LEVEL", "trial").strip()
        or "trial"
    )
    language = (
        os.environ.get("SPORTRADAR_TENNIS_LANGUAGE", "en").strip()
        or "en"
    )
    try:
        received_at, payload = fetch_daily_summaries(
            api_key,
            slate_date,
            access_level=access_level,
            language=language,
        )
    except SportradarTransportError as exc:
        raise TransportError(exc.kind, exc.message) from exc

    summaries = payload.get("summaries")
    if not isinstance(summaries, list):
        raise TransportError(
            "PARSE",
            "sportradar tennis daily summaries response missing summaries list",
        )
    return received_at, [
        dict(row) for row in summaries if isinstance(row, dict)
    ]


def ingest_sportradar_fixture_rows(
    conn: psycopg.Connection,
    fixtures: list[dict[str, Any]],
    received_at: datetime,
) -> SportradarIngestOutcome:
    """Persist a target-provider slate only after the global cutover gate has passed."""
    return ingest_sportradar_daily_summaries(
        conn,
        {"summaries": list(fixtures)},
        received_at=received_at,
        allow_new_events=True,
    )


__all__ = [
    "fetch_sportradar_fixture_rows",
    "ingest_sportradar_fixture_rows",
]
