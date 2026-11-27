"""Strict paired-lineage qualification for original-time market evidence.

The earlier capture-sequence audit checked identity and price timestamps, but it
did not prove that the two sides of a market came from the same provider
observation. This module closes that gap. A pair qualifies only when it has
complete, consistent raw lineage:

* two selections, PLAYER_1 and PLAYER_2;
* the same canonical match, bookmaker and source system;
* the same ``raw_odds_observation_id`` and ``source_snapshot_id``;
* both markets OPEN;
* observed and ingested at or before the prediction lock;
* observed inside the freshness window;
* a provider identity that already existed when the price was observed;
* a live-observed payload, never a historical reconstruction;
* a pre-start observation, valid prices and a plausible de-vig total.

Anything else is rejected with the exact reason and cannot be promoted. This is
deliberately stricter than the legacy top-800 rule, which grouped sides by a
five-minute timestamp span and could accept two different observation records.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping, Sequence

import psycopg

QUALIFICATION_VERSION = "STRICT_PAIRED_ORIGINAL_TIME_V1"
DEFAULT_FRESHNESS = timedelta(hours=6)
DEFAULT_BOOKSUM_RANGE = (0.95, 1.25)

QUALIFIED = "ORIGINAL_TIME_QUALIFIED"

REJECTED_SINGLE_SIDED = "REJECTED_SINGLE_SIDED"
REJECTED_MIXED_BOOKMAKER = "REJECTED_MIXED_BOOKMAKER"
REJECTED_MIXED_PROVIDER = "REJECTED_MIXED_PROVIDER"
REJECTED_MIXED_SOURCE = "REJECTED_MIXED_SOURCE"
REJECTED_DIFFERENT_OBSERVATION = "REJECTED_DIFFERENT_OBSERVATION"
REJECTED_DIFFERENT_SNAPSHOT = "REJECTED_DIFFERENT_SNAPSHOT"
REJECTED_SUSPENDED = "REJECTED_SUSPENDED"
REJECTED_LATE_OBSERVATION = "REJECTED_LATE_OBSERVATION"
REJECTED_BACKFILLED = "REJECTED_BACKFILLED"
REJECTED_LATE_INGESTION = "REJECTED_LATE_INGESTION"
REJECTED_IDENTITY_AFTER_ACQUISITION = "REJECTED_IDENTITY_AFTER_ACQUISITION"
REJECTED_RETROSPECTIVE = "REJECTED_RETROSPECTIVE_RECONSTRUCTION"
REJECTED_INVALID_PRICE = "REJECTED_INVALID_PRICE"
REJECTED_POST_START = "REJECTED_POST_START"
REJECTED_BOOKSUM = "REJECTED_BOOKSUM"


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("timestamps must be timezone-aware")
    return value.astimezone(timezone.utc)


@dataclass(frozen=True)
class QuoteLineage:
    match_id: int
    raw_observation_id: int
    source_snapshot_id: str | None
    provider_code: str
    bookmaker_code: str
    source_system: str
    selection_type: str
    decimal_price: float
    market_status: str
    observed_at: datetime
    ingested_at: datetime
    mapping_created_at: datetime | None
    payload_shape: str
    scheduled_start_at: datetime | None

    def __post_init__(self) -> None:
        if self.selection_type not in ("PLAYER_1", "PLAYER_2"):
            raise ValueError(f"unsupported selection_type {self.selection_type!r}")
        if self.payload_shape not in (
            "LIVE_OBSERVED", "LATEST_AT_CUTOFF_HISTORICAL", "UNKNOWN",
        ):
            raise ValueError(f"unsupported payload_shape {self.payload_shape!r}")
        _utc(self.observed_at)
        _utc(self.ingested_at)


def qualify_pair(
    side_1: QuoteLineage,
    side_2: QuoteLineage,
    *,
    prediction_locked_at: datetime,
    now: datetime,
    freshness: timedelta = DEFAULT_FRESHNESS,
    booksum_range: tuple[float, float] = DEFAULT_BOOKSUM_RANGE,
) -> dict[str, Any]:
    """Return the single strict qualification decision for a paired market."""
    lock = _utc(prediction_locked_at)
    current = _utc(now)
    reasons: list[str] = []

    if side_1.selection_type == side_2.selection_type:
        return {"status": REJECTED_SINGLE_SIDED, "reasons": ["DUPLICATE_SELECTION"]}
    ordered = sorted((side_1, side_2), key=lambda item: item.selection_type)
    left, right = ordered
    if left.match_id != right.match_id:
        return {"status": REJECTED_DIFFERENT_OBSERVATION, "reasons": ["MATCH_MISMATCH"]}
    if left.bookmaker_code != right.bookmaker_code:
        return {"status": REJECTED_MIXED_BOOKMAKER, "reasons": ["BOOKMAKER_MISMATCH"]}
    if left.provider_code != right.provider_code:
        return {"status": REJECTED_MIXED_PROVIDER, "reasons": ["PROVIDER_MISMATCH"]}
    if left.source_system != right.source_system:
        return {"status": REJECTED_MIXED_SOURCE, "reasons": ["SOURCE_SYSTEM_MISMATCH"]}
    if left.raw_observation_id != right.raw_observation_id:
        return {
            "status": REJECTED_DIFFERENT_OBSERVATION,
            "reasons": ["RAW_OBSERVATION_MISMATCH"],
        }
    if (
        left.source_snapshot_id is None
        or right.source_snapshot_id is None
        or left.source_snapshot_id != right.source_snapshot_id
    ):
        return {
            "status": REJECTED_DIFFERENT_SNAPSHOT,
            "reasons": ["SOURCE_SNAPSHOT_MISMATCH"],
        }
    if left.market_status != "OPEN" or right.market_status != "OPEN":
        reasons.append("MARKET_NOT_OPEN")
    if left.payload_shape == "LATEST_AT_CUTOFF_HISTORICAL" or right.payload_shape == "LATEST_AT_CUTOFF_HISTORICAL":
        return {
            "status": REJECTED_RETROSPECTIVE,
            "reasons": ["HISTORICAL_RECONSTRUCTION_NOT_ORIGINAL_TIME"],
        }
    observed = max(_utc(left.observed_at), _utc(right.observed_at))
    ingested = max(_utc(left.ingested_at), _utc(right.ingested_at))
    if observed > lock:
        return {"status": REJECTED_LATE_OBSERVATION, "reasons": ["OBSERVED_AFTER_LOCK"]}
    if observed < lock - freshness:
        return {
            "status": REJECTED_BACKFILLED,
            "reasons": ["OBSERVED_BEFORE_FRESHNESS_WINDOW"],
        }
    if ingested > lock:
        reasons.append("INGESTED_AFTER_LOCK")
    if ingested > current:
        reasons.append("INGESTED_IN_FUTURE")
    for side in (left, right):
        if side.mapping_created_at is None:
            reasons.append("IDENTITY_MAPPING_MISSING")
        elif _utc(side.mapping_created_at) > _utc(side.observed_at):
            reasons.append("IDENTITY_RECORDED_AFTER_ACQUISITION")
    if left.decimal_price <= 1 or right.decimal_price <= 1:
        reasons.append("INVALID_PRICE")
    elif not booksum_range[0] <= (1 / left.decimal_price + 1 / right.decimal_price) <= booksum_range[1]:
        reasons.append("BOOKSUM_IMPLAUSIBLE")
    if left.scheduled_start_at is not None and observed >= _utc(left.scheduled_start_at):
        reasons.append("OBSERVED_AT_OR_AFTER_START")

    if "MARKET_NOT_OPEN" in reasons:
        return {"status": REJECTED_SUSPENDED, "reasons": reasons}
    if "IDENTITY_RECORDED_AFTER_ACQUISITION" in reasons:
        return {
            "status": REJECTED_IDENTITY_AFTER_ACQUISITION,
            "reasons": reasons,
        }
    if "IDENTITY_MAPPING_MISSING" in reasons:
        return {
            "status": REJECTED_IDENTITY_AFTER_ACQUISITION,
            "reasons": reasons,
        }
    if "INVALID_PRICE" in reasons:
        return {"status": REJECTED_INVALID_PRICE, "reasons": reasons}
    if "BOOKSUM_IMPLAUSIBLE" in reasons:
        return {"status": REJECTED_BOOKSUM, "reasons": reasons}
    if "OBSERVED_AT_OR_AFTER_START" in reasons:
        return {"status": REJECTED_POST_START, "reasons": reasons}
    if "INGESTED_AFTER_LOCK" in reasons or "INGESTED_IN_FUTURE" in reasons:
        return {"status": REJECTED_LATE_INGESTION, "reasons": reasons}
    return {"status": QUALIFIED, "reasons": []}


def audit_quotes(
    quotes: Iterable[QuoteLineage],
    *,
    prediction_locks: Mapping[int, datetime],
    now: datetime,
    freshness: timedelta = DEFAULT_FRESHNESS,
) -> dict[str, Any]:
    """Group quotes by complete lineage and audit every candidate pair.

    A group is keyed by match, bookmaker, provider, raw observation and snapshot,
    so sides from different observations can never be silently combined.
    """
    groups: dict[tuple[Any, ...], dict[str, QuoteLineage]] = {}
    single_sided: list[dict[str, Any]] = []
    for quote in quotes:
        key = (
            quote.match_id, quote.bookmaker_code, quote.provider_code,
            quote.source_system, quote.raw_observation_id,
            quote.source_snapshot_id,
        )
        groups.setdefault(key, {})[quote.selection_type] = quote

    statuses: dict[str, int] = {}
    qualified_matches: set[int] = set()
    examples: list[dict[str, Any]] = []
    for key, sides in sorted(groups.items(), key=lambda item: str(item[0])):
        match_id = int(key[0])
        lock = prediction_locks.get(match_id)
        if lock is None:
            status = REJECTED_LATE_OBSERVATION
            result = {"status": status, "reasons": ["NO_PREDICTION_LOCK"]}
        elif set(sides) != {"PLAYER_1", "PLAYER_2"}:
            status = REJECTED_SINGLE_SIDED
            result = {"status": status, "reasons": ["MISSING_OPPOSITE_SELECTION"]}
            single_sided.append({
                "match_id": match_id,
                "bookmaker_code": key[1],
                "raw_observation_id": key[4],
                "present": sorted(sides),
            })
        else:
            result = qualify_pair(
                sides["PLAYER_1"], sides["PLAYER_2"],
                prediction_locked_at=lock, now=now, freshness=freshness,
            )
            status = result["status"]
        statuses[status] = statuses.get(status, 0) + 1
        if status == QUALIFIED:
            qualified_matches.add(match_id)
        if status != QUALIFIED and len(examples) < 20:
            examples.append({
                "match_id": match_id,
                "bookmaker_code": key[1],
                "provider_code": key[2],
                "raw_observation_id": key[4],
                "status": status,
                "reasons": result["reasons"],
            })

    return {
        "version": QUALIFICATION_VERSION,
        "groups": sum(len(group) for group in groups.values()),
        "candidate_markets": len(groups),
        "qualified_markets": statuses.get(QUALIFIED, 0),
        "qualified_fixtures": len(qualified_matches),
        "status_counts": dict(sorted(statuses.items())),
        "single_sided_markets": single_sided[:20],
        "rejections": examples,
        "rule": (
            "Both sides must share one raw observation and source snapshot; "
            "different observation records never qualify."
        ),
    }


_LINEAGE_SQL = """
SELECT
    o.match_id,
    o.raw_odds_observation_id,
    o.source_snapshot_id,
    r.provider_code,
    o.bookmaker_code,
    o.source_system::text,
    o.selection_type::text,
    o.decimal_price::float8,
    o.market_status::text,
    o.source_observed_at,
    o.ingested_at,
    ms.mapping_created_at,
    CASE
        WHEN r.payload_json ? 'bookmakers' THEN 'LATEST_AT_CUTOFF_HISTORICAL'
        WHEN r.payload_json ? 'bookmakerOdds' THEN 'LIVE_OBSERVED'
        ELSE 'UNKNOWN'
    END AS payload_shape,
    mv.match_start_at
FROM odds_snapshots o
JOIN raw_odds_observations r
  ON r.raw_odds_observation_id = o.raw_odds_observation_id
LEFT JOIN LATERAL (
    SELECT min(created_at) AS mapping_created_at
    FROM match_source_ids msi
    WHERE msi.match_id = o.match_id
      AND msi.source_system = r.provider_code
      AND msi.source_match_id = r.provider_event_id
) ms ON TRUE
LEFT JOIN LATERAL (
    SELECT match_start_at
    FROM match_versions mv
    WHERE mv.match_id = o.match_id
      AND mv.as_of <= o.source_observed_at
      AND mv.source_observed_at <= o.source_observed_at
    ORDER BY mv.as_of DESC, mv.match_version_id DESC
    LIMIT 1
) mv ON TRUE
WHERE o.market_type = 'MATCH_WINNER'
  AND o.selection_type IN ('PLAYER_1', 'PLAYER_2')
  AND (%(match_ids)s::bigint[] IS NULL OR o.match_id = ANY(%(match_ids)s))
  AND (%(provider)s::text IS NULL OR r.provider_code = %(provider)s)
  AND (%(since)s::timestamptz IS NULL OR o.source_observed_at >= %(since)s)
ORDER BY o.match_id, o.bookmaker_code, o.raw_odds_observation_id,
         o.selection_type
"""


def load_quote_lineage(
    conn: psycopg.Connection,
    *,
    match_ids: Sequence[int] | None = None,
    provider_code: str | None = None,
    since: datetime | None = None,
) -> list[QuoteLineage]:
    rows = conn.execute(_LINEAGE_SQL, {
        "match_ids": list(match_ids) if match_ids else None,
        "provider": provider_code,
        "since": since,
    }).fetchall()
    return [
        QuoteLineage(
            match_id=int(row[0]),
            raw_observation_id=int(row[1]),
            source_snapshot_id=row[2],
            provider_code=str(row[3]),
            bookmaker_code=str(row[4]),
            source_system=str(row[5]),
            selection_type=str(row[6]),
            decimal_price=float(row[7]),
            market_status=str(row[8]),
            observed_at=row[9],
            ingested_at=row[10],
            mapping_created_at=row[11],
            payload_shape=str(row[12]),
            scheduled_start_at=row[13],
        )
        for row in rows
    ]


def prediction_locks(
    conn: psycopg.Connection,
    *,
    match_ids: Sequence[int] | None = None,
) -> dict[int, datetime]:
    rows = conn.execute(
        """
        SELECT DISTINCT ON (p.match_id) p.match_id, p.prediction_locked_at
        FROM predictions p
        WHERE p.agent_code = 'AGENT_1_SURFACE_ELO'
          AND p.prediction_status = 'PREDICTED'
          AND p.selection_side IN (1, 2)
          AND (%s::bigint[] IS NULL OR p.match_id = ANY(%s))
        ORDER BY p.match_id, p.as_of, p.prediction_id
        """,
        (list(match_ids) if match_ids else None,
         list(match_ids) if match_ids else None),
    ).fetchall()
    return {int(row[0]): row[1] for row in rows}


__all__ = [
    "QUALIFIED",
    "QUALIFICATION_VERSION",
    "QuoteLineage",
    "audit_quotes",
    "load_quote_lineage",
    "prediction_locks",
    "qualify_pair",
]
