"""Permanent provider attribution for normalized tennis market observations.

Why a read model instead of duplicate columns
---------------------------------------------
The existing schema already preserves every immutable fact needed to attribute
a normalized quote:

``odds_snapshots``
    the normalized snapshot id, canonical ``match_id``, selection, price,
    ``source_observed_at``, ``ingested_at``, ``raw_odds_observation_id`` and
    ``provider_bookmaker_mapping_id``;
``raw_odds_observations``
    the upstream API provider (``provider_code``), the provider fixture id
    (``provider_event_id``), the raw payload and the receive/provider clock;
``provider_bookmaker_mappings``
    the actual upstream bookmaker key (for example ``pinnacle+30``) and the
    canonical bookmaker code plus sharp/offered class;
``match_versions``
    the canonical participant identities visible at the observation time.

Copying those values into another mutable table would create a second source
of truth and drift the moment a mapping or match version changes. Instead this
module derives attribution from the immutable references and exposes it as a
single canonical read model. No migration is allocated: PR #394 owns migration
versions 0025 and 0026, so allocating 0027 here would either conflict or leave a
non-contiguous chain on this branch.

The two facts that are genuinely *not* stored per row are derived explicitly:

* historical reconstruction provenance, from the raw payload shape
  (``bookmakers`` historical vs ``bookmakerOdds`` live);
* benchmark qualification, from the prediction cutoff. Qualification is a
  property of a quote *relative to a prediction*, not an intrinsic quote
  attribute, so storing it per quote would be wrong when several prediction
  cutoffs exist.
"""
from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping, Sequence

import psycopg

ATTRIBUTION_VERSION = "MARKET_OBSERVATION_ATTRIBUTION_V1"
COVERAGE_REPORT_VERSION = "COMMERCIAL_PROVIDER_COVERAGE_V1"

DEFAULT_FRESHNESS = timedelta(hours=6)
DEFAULT_SIDE_SPAN = timedelta(minutes=5)
DEFAULT_BOOKSUM_RANGE = (0.95, 1.25)

BENCHMARK_QUALIFIED = "ORIGINAL_TIME_QUALIFIED"
BENCHMARK_LATE = "NOT_ORIGINAL_TIME_LATE"
BENCHMARK_BACKFILLED = "NOT_ORIGINAL_TIME_BACKFILLED"
BENCHMARK_UNPAIRED = "NOT_QUALIFIED_NO_TWO_SIDED_PAIR"
BENCHMARK_SUSPENDED = "NOT_QUALIFIED_SUSPENDED"
BENCHMARK_INVALID_PRICE = "NOT_QUALIFIED_INVALID_PRICE"
BENCHMARK_RETROSPECTIVE = "RETROSPECTIVE_RECONSTRUCTION_ONLY"
BENCHMARK_UNMAPPED = "NOT_QUALIFIED_UNMAPPED"


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("timestamps must be timezone-aware")
    return value.astimezone(timezone.utc)


@dataclass(frozen=True)
class MarketObservationAttribution:
    """One normalized quote with its complete provider lineage."""

    odds_snapshot_id: int
    match_id: int
    canonical_player_1_id: int
    canonical_player_2_id: int
    upstream_provider_code: str
    provider_fixture_id: str
    provider_bookmaker_key: str
    actual_bookmaker_code: str
    bookmaker_normalization_class: str
    normalized_bookmaker_code: str
    source_system: str
    selection_type: str
    decimal_price: float
    market_status: str
    raw_odds_observation_id: int
    provider_bookmaker_mapping_id: int
    observation_time_basis: str
    provider_observation_time: datetime
    received_at: datetime
    raw_persisted_at: datetime
    source_observed_at: datetime
    ingested_at: datetime
    scheduled_start_at: datetime | None
    historical_reconstruction_class: str
    observation_after_scheduled_start: bool

    @property
    def is_historical_reconstruction(self) -> bool:
        return self.historical_reconstruction_class == "LATEST_AT_CUTOFF_HISTORICAL"


def classify_historical_reconstruction(payload: Mapping[str, Any]) -> str:
    """Classify how the raw payload could have produced a price.

    ``bookmakers`` is the historical-odds shape (list-valued players) and is
    only ever a latest-at-cutoff reconstruction. ``bookmakerOdds`` is the live
    board shape and is a genuine observation of the offered price at the
    receive time. A retrospective reconstruction is never labelled as an
    originally available price.
    """
    if isinstance(payload.get("bookmakers"), Mapping):
        return "LATEST_AT_CUTOFF_HISTORICAL"
    if isinstance(payload.get("bookmakerOdds"), Mapping):
        return "LIVE_OBSERVED"
    return "UNKNOWN"


def price_age_components(
    *,
    observation_time: datetime,
    price_changed_at: datetime | None,
    as_of: datetime,
) -> dict[str, Any]:
    """Separate observation age from price-change age.

    A price that has not changed for ten hours is *not* stale if the bookmaker
    independently reconfirmed its availability a minute ago. Conversely, a
    recently changed price with no recent observation is not evidence that it
    was still available. Availability freshness therefore depends on the
    observation clock, while price-change age is reported separately.
    """
    observed = _utc(observation_time)
    reference = _utc(as_of)
    if reference < observed:
        raise ValueError("as_of cannot precede observation_time")
    price_changed = _utc(price_changed_at) if price_changed_at is not None else None
    observation_age = reference - observed
    return {
        "observation_age_seconds": observation_age.total_seconds(),
        "price_change_age_seconds": (
            (reference - price_changed).total_seconds()
            if price_changed is not None else None
        ),
        "price_unchanged_since_observation": (
            price_changed is None or price_changed <= observed
        ),
        "availability_confirmed_at": observed,
    }


def observation_is_fresh(
    *,
    observation_time: datetime,
    as_of: datetime,
    max_age: timedelta = DEFAULT_FRESHNESS,
) -> bool:
    """Whether availability was reconfirmed within ``max_age``.

    This deliberately ignores ``price_changed_at``. An unchanged price can be
    confirmed by a fresh observation.
    """
    age = _utc(as_of) - _utc(observation_time)
    return timedelta(0) <= age <= max_age


def benchmark_qualification(
    *,
    observation_time: datetime,
    ingested_at: datetime,
    prediction_locked_at: datetime,
    two_sided: bool,
    side_span: timedelta | None,
    statuses: Sequence[str],
    payload: Mapping[str, Any] | None = None,
    booksum: float | None = None,
    freshness: timedelta = DEFAULT_FRESHNESS,
    max_side_span: timedelta = DEFAULT_SIDE_SPAN,
    booksum_range: tuple[float, float] = DEFAULT_BOOKSUM_RANGE,
) -> dict[str, Any]:
    """Classify whether a quote qualifies as original-time benchmark evidence.

    Original-time means observed, persisted and ingested at or before the
    original prediction lock, inside the freshness window, with both sides
    open and synchronised. Anything else is reported with the exact reason and
    is never promoted to coverage.
    """
    observed = _utc(observation_time)
    ingested = _utc(ingested_at)
    lock = _utc(prediction_locked_at)
    reasons: list[str] = []

    if payload is not None and classify_historical_reconstruction(payload) == "LATEST_AT_CUTOFF_HISTORICAL":
        return {
            "status": BENCHMARK_RETROSPECTIVE,
            "reasons": ["HISTORICAL_RECONSTRUCTION_NOT_ORIGINAL_TIME"],
        }
    if observed > lock:
        return {"status": BENCHMARK_LATE, "reasons": ["OBSERVED_AFTER_LOCK"]}
    if observed < lock - freshness:
        return {"status": BENCHMARK_BACKFILLED, "reasons": ["OBSERVED_BEFORE_FRESHNESS_WINDOW"]}
    if ingested > lock:
        reasons.append("INGESTED_AFTER_LOCK")
    if not two_sided:
        reasons.append("NO_TWO_SIDED_PAIR")
    if side_span is None:
        reasons.append("SIDE_SPAN_UNKNOWN")
    elif side_span > max_side_span:
        reasons.append("SIDE_SPAN_TOO_WIDE")
    if any(status != "OPEN" for status in statuses):
        reasons.append("MARKET_NOT_OPEN")
    if booksum is None:
        reasons.append("BOOKSUM_UNKNOWN")
    elif not booksum_range[0] <= booksum <= booksum_range[1]:
        reasons.append("BOOKSUM_IMPLAUSIBLE")
    if set(statuses) and "OPEN" not in statuses:
        return {"status": BENCHMARK_SUSPENDED, "reasons": reasons or ["MARKET_NOT_OPEN"]}
    if reasons:
        return {"status": BENCHMARK_UNPAIRED, "reasons": reasons}
    return {"status": BENCHMARK_QUALIFIED, "reasons": []}


def load_attributions(
    conn: psycopg.Connection,
    *,
    provider_code: str | None = None,
    match_id: int | None = None,
    match_ids: Sequence[int] | None = None,
    limit: int | None = None,
) -> list[MarketObservationAttribution]:
    """Load the canonical attribution read model from immutable lineage."""
    clauses = ["o.raw_odds_observation_id IS NOT NULL"]
    params: list[Any] = []
    if provider_code is not None:
        clauses.append("r.provider_code = %s")
        params.append(provider_code)
    if match_id is not None:
        clauses.append("o.match_id = %s")
        params.append(match_id)
    if match_ids:
        clauses.append("o.match_id = ANY(%s)")
        params.append(list(match_ids))
    sql = f"""
        SELECT
            o.odds_snapshot_id,
            o.match_id,
            mv.player_1_id,
            mv.player_2_id,
            r.provider_code,
            r.provider_event_id,
            m.provider_bookmaker_key,
            m.bookmaker_code,
            m.normalization_class::text,
            o.bookmaker_code,
            o.source_system::text,
            o.selection_type::text,
            o.decimal_price::float8,
            o.market_status::text,
            r.raw_odds_observation_id,
            o.provider_bookmaker_mapping_id,
            r.observation_time_basis::text,
            r.observation_time,
            r.received_at,
            r.created_at,
            o.source_observed_at,
            o.ingested_at,
            mv.match_start_at,
            (r.payload_json ? 'bookmakers') AS historical_shape,
            (r.payload_json ? 'bookmakerOdds') AS live_shape,
            r.payload_json
        FROM odds_snapshots o
        JOIN raw_odds_observations r
          ON r.raw_odds_observation_id = o.raw_odds_observation_id
        JOIN provider_bookmaker_mappings m
          ON m.provider_bookmaker_mapping_id = o.provider_bookmaker_mapping_id
        JOIN LATERAL (
            SELECT player_1_id, player_2_id, match_start_at
            FROM match_versions
            WHERE match_id = o.match_id
              AND source_observed_at <= o.source_observed_at
              AND as_of <= o.source_observed_at
            ORDER BY as_of DESC, source_observed_at DESC, match_version_id DESC
            LIMIT 1
        ) mv ON TRUE
        WHERE {' AND '.join(clauses)}
        ORDER BY o.odds_snapshot_id
        {'LIMIT %s' if limit is not None else ''}
    """
    if limit is not None:
        params.append(int(limit))
    rows = conn.execute(sql, tuple(params)).fetchall()
    out: list[MarketObservationAttribution] = []
    for row in rows:
        payload = row[25] if isinstance(row[25], Mapping) else {}
        classification = classify_historical_reconstruction(payload)
        start = row[22]
        out.append(MarketObservationAttribution(
            odds_snapshot_id=int(row[0]),
            match_id=int(row[1]),
            canonical_player_1_id=int(row[2]),
            canonical_player_2_id=int(row[3]),
            upstream_provider_code=str(row[4]),
            provider_fixture_id=str(row[5]),
            provider_bookmaker_key=str(row[6]),
            actual_bookmaker_code=str(row[7]),
            bookmaker_normalization_class=str(row[8]),
            normalized_bookmaker_code=str(row[9]),
            source_system=str(row[10]),
            selection_type=str(row[11]),
            decimal_price=float(row[12]),
            market_status=str(row[13]),
            raw_odds_observation_id=int(row[14]),
            provider_bookmaker_mapping_id=int(row[15]),
            observation_time_basis=str(row[16]),
            provider_observation_time=_utc(row[17]),
            received_at=_utc(row[18]),
            raw_persisted_at=_utc(row[19]),
            source_observed_at=_utc(row[20]),
            ingested_at=_utc(row[21]),
            scheduled_start_at=_utc(start) if start is not None else None,
            historical_reconstruction_class=classification,
            observation_after_scheduled_start=(
                start is not None and _utc(row[20]) >= _utc(start)
            ),
        ))
    return out


def _provider_filter(provider_code: str | None, params: list[Any]) -> str:
    if provider_code is None:
        return ""
    params.append(provider_code)
    return " AND r.provider_code = %s"


def _raw_extraction_sql(provider_code: str | None, params: list[Any]) -> str:
    where = _provider_filter(provider_code, params)
    return f"""
        SELECT
            r.provider_code,
            count(*) AS raw_responses,
            count(DISTINCT r.provider_event_id) AS raw_fixtures,
            count(*) FILTER (WHERE r.payload_json ? 'bookmakerOdds') AS live_shape,
            count(*) FILTER (WHERE r.payload_json ? 'bookmakers') AS historical_shape,
            count(*) FILTER (
                WHERE r.provider_code = 'API_TENNIS'
                  AND r.payload_json ? 'Home/Away'
            ) AS provider_market_map_shape,
            min(r.received_at) AS first_received_at,
            max(r.received_at) AS last_received_at,
            count(*) - count(DISTINCT (r.provider_event_id, r.payload_hash))
                AS duplicate_payload_responses
        FROM raw_odds_observations r
        WHERE TRUE {where}
        GROUP BY r.provider_code
        ORDER BY r.provider_code
    """


def _extracted_sql(provider_code: str | None, params: list[Any]) -> str:
    where = _provider_filter(provider_code, params)
    return f"""
        WITH filtered AS (
            SELECT r.provider_code, r.payload_json
            FROM raw_odds_observations r
            WHERE TRUE {where}
        )
        SELECT provider_code, sum(extracted) AS extracted_winner_observations
        FROM (
            SELECT 'ODDSPAPI'::text AS provider_code, count(*) AS extracted
            FROM filtered r
            CROSS JOIN LATERAL jsonb_each(
                CASE WHEN jsonb_typeof(r.payload_json->'bookmakerOdds') = 'object'
                     THEN r.payload_json->'bookmakerOdds' ELSE '{{}}'::jsonb END
            ) b
            WHERE r.provider_code = 'ODDSPAPI'
              AND jsonb_typeof(b.value->'markets') = 'object'
              AND jsonb_typeof(b.value->'markets'->'121'->'outcomes') = 'object'
              AND (SELECT count(*) FROM jsonb_object_keys(
                    b.value->'markets'->'121'->'outcomes')) = 2
            UNION ALL
            SELECT 'API_TENNIS'::text, count(*)
            FROM filtered r
            WHERE r.provider_code = 'API_TENNIS'
              AND jsonb_typeof(r.payload_json->'Home/Away') = 'object'
              AND r.payload_json->'Home/Away' ? 'Home'
              AND r.payload_json->'Home/Away' ? 'Away'
        ) extracted_by_provider
        GROUP BY provider_code
        ORDER BY provider_code
    """


def _normalized_sql(provider_code: str | None, params: list[Any]) -> str:
    where = _provider_filter(provider_code, params)
    return f"""
        SELECT r.provider_code,
               count(*) AS normalized_quotes,
               count(DISTINCT o.match_id) AS normalized_fixtures,
               count(DISTINCT o.bookmaker_code) AS normalized_bookmakers,
               min(o.source_observed_at) AS first_observed_at,
               max(o.source_observed_at) AS last_observed_at,
               min(o.ingested_at) AS first_ingested_at,
               max(o.ingested_at) AS last_ingested_at
        FROM odds_snapshots o
        JOIN raw_odds_observations r
          ON r.raw_odds_observation_id = o.raw_odds_observation_id
        WHERE TRUE {where}
        GROUP BY r.provider_code
        ORDER BY r.provider_code
    """


def _rejections_sql(provider_code: str | None, params: list[Any]) -> str:
    where = _provider_filter(provider_code, params)
    return f"""
        WITH candidate AS (
            SELECT
                r.provider_code,
                r.provider_event_id,
                r.raw_odds_observation_id,
                r.observation_time,
                r.payload_json,
                EXISTS (
                    SELECT 1 FROM odds_snapshots o
                    WHERE o.raw_odds_observation_id = r.raw_odds_observation_id
                ) AS normalized,
                (SELECT count(*) FROM match_source_ids ms
                  WHERE ms.source_system = r.provider_code
                    AND ms.source_match_id = r.provider_event_id) AS links,
                EXISTS (
                    SELECT 1
                    FROM match_source_ids ms
                    JOIN match_versions mv ON mv.match_id = ms.match_id
                    WHERE ms.source_system = r.provider_code
                      AND ms.source_match_id = r.provider_event_id
                      AND mv.source_observed_at <= r.observation_time
                      AND mv.as_of <= r.observation_time
                ) AS has_pit_version
            FROM raw_odds_observations r
            WHERE TRUE {where}
        ),
        classified AS (
            SELECT provider_code,
                   CASE
                       WHEN normalized THEN 'NORMALIZED'
                       WHEN links = 0 THEN 'UNMATCHED_EVENT'
                       WHEN links > 1 THEN 'IDENTITY_CONFLICT'
                       WHEN provider_code = 'API_TENNIS'
                            AND coalesce(jsonb_typeof(payload_json->'Home/Away'), 'missing')
                                <> 'object'
                           THEN 'NO_HOME_AWAY_MARKET'
                       WHEN provider_code = 'API_TENNIS'
                            AND NOT EXISTS (
                                SELECT 1
                                FROM jsonb_each(payload_json->'Home/Away') side
                                WHERE side.key IN ('Home','Away')
                                  AND jsonb_typeof(side.value) = 'object'
                                  AND EXISTS (
                                      SELECT 1 FROM jsonb_each_text(side.value) price
                                      WHERE price.value ~ '^[0-9]+(\\.[0-9]+)?$'
                                        AND price.value::numeric > 1
                                  )
                            ) THEN 'NO_PRICED_HOME_AWAY_OUTCOMES'
                       WHEN provider_code = 'API_TENNIS'
                            AND NOT EXISTS (
                                SELECT 1
                                FROM jsonb_object_keys(
                                    payload_json->'Home/Away'->'Home') home_book
                                JOIN jsonb_object_keys(
                                    payload_json->'Home/Away'->'Away') away_book
                                  ON away_book = home_book
                            ) THEN 'NO_COMMON_BOOKMAKER_ACROSS_SIDES'
                       WHEN provider_code = 'API_TENNIS'
                            AND EXISTS (
                                SELECT 1
                                FROM match_source_ids ms
                                JOIN match_versions mv ON mv.match_id = ms.match_id
                                WHERE ms.source_system = 'API_TENNIS'
                                  AND ms.source_match_id = c.provider_event_id
                                  AND mv.match_start_at IS NOT NULL
                                  AND mv.match_start_at <= observation_time
                            ) THEN 'CANONICAL_START_PASSED'
                       WHEN provider_code = 'API_TENNIS'
                            AND NOT EXISTS (
                                SELECT 1
                                FROM jsonb_object_keys(
                                    payload_json->'Home/Away'->'Home') home_book
                                JOIN jsonb_object_keys(
                                    payload_json->'Home/Away'->'Away') away_book
                                  ON away_book = home_book
                                JOIN provider_bookmaker_mappings m
                                  ON m.provider_code = 'API_TENNIS'
                                 AND m.provider_bookmaker_key = home_book
                                 AND m.effective_from <= observation_time
                            ) THEN 'NO_LICENSED_BOOKMAKER_PAIR'
                       WHEN provider_code = 'API_TENNIS' THEN 'OTHER_REJECTED'
                       WHEN NOT has_pit_version THEN 'NO_PIT_MATCH_VERSION'
                       WHEN coalesce(payload_json->>'startTime', '') ~ '^\\d{{4}}-'
                            AND (payload_json->>'startTime')::timestamptz <= observation_time
                           THEN 'PROVIDER_START_PASSED'
                       WHEN coalesce(jsonb_typeof(payload_json->'bookmakerOdds'), 'missing')
                                <> 'object'
                           THEN 'NO_LIVE_BOOKMAKER_PAYLOAD'
                       WHEN NOT EXISTS (
                           SELECT 1 FROM jsonb_each(payload_json->'bookmakerOdds') b
                           WHERE jsonb_typeof(b.value->'markets') = 'object'
                             AND jsonb_typeof(b.value->'markets'->'121'->'outcomes') = 'object'
                             AND (SELECT count(*) FROM jsonb_object_keys(
                                    b.value->'markets'->'121'->'outcomes')) = 2
                       ) THEN 'NO_VALID_TWO_SIDED_MARKET'
                       ELSE 'OTHER_REJECTED'
                   END AS reason
            FROM candidate c
        )
        SELECT provider_code, reason, count(*) AS observations
        FROM classified
        GROUP BY provider_code, reason
        ORDER BY provider_code, observations DESC
    """


def _two_sided_sql(provider_code: str | None, params: list[Any]) -> str:
    where = _provider_filter(provider_code, params)
    return f"""
        SELECT pair.provider_code,
               count(*) AS valid_two_sided_markets,
               count(DISTINCT pair.match_id) AS fixtures_with_two_sided_market,
               count(DISTINCT pair.bookmaker_code) AS bookmakers_with_two_sided_market
        FROM (
            SELECT r.provider_code, o.match_id, o.bookmaker_code, o.source_observed_at
            FROM odds_snapshots o
            JOIN raw_odds_observations r
              ON r.raw_odds_observation_id = o.raw_odds_observation_id
            WHERE o.market_type = 'MATCH_WINNER'
              AND o.selection_type IN ('PLAYER_1', 'PLAYER_2')
              {where}
            GROUP BY r.provider_code, o.match_id, o.bookmaker_code, o.source_observed_at
            HAVING count(DISTINCT o.selection_type) = 2
               AND bool_and(o.market_status = 'OPEN')
        ) pair
        GROUP BY pair.provider_code
    """


def _original_time_sql(provider_code: str | None, params: list[Any]) -> str:
    where = _provider_filter(provider_code, params)
    return f"""
        WITH locks AS (
            SELECT DISTINCT ON (p.match_id)
                   p.match_id, p.prediction_locked_at
            FROM predictions p
            WHERE p.agent_code = 'AGENT_1_SURFACE_ELO'
              AND p.prediction_status = 'PREDICTED'
              AND p.selection_side IN (1, 2)
            ORDER BY p.match_id, p.as_of, p.prediction_id
        ),
        pairs AS (
            SELECT r.provider_code, o.match_id, o.bookmaker_code,
                   o.source_system::text AS source_system,
                   min(o.source_observed_at) AS observed_at,
                   max(o.source_observed_at) AS last_side_at,
                   max(o.ingested_at) AS ingested_at
            FROM odds_snapshots o
            JOIN raw_odds_observations r
              ON r.raw_odds_observation_id = o.raw_odds_observation_id
            JOIN locks l ON l.match_id = o.match_id
            WHERE o.market_type = 'MATCH_WINNER'
              AND o.selection_type IN ('PLAYER_1', 'PLAYER_2')
              AND o.market_status = 'OPEN'
              {where}
            GROUP BY r.provider_code, o.match_id, o.bookmaker_code, o.source_system
            HAVING count(DISTINCT o.selection_type) = 2
               AND max(o.source_observed_at) - min(o.source_observed_at)
                   <= interval '5 minutes'
               AND max(o.source_observed_at) <= max(l.prediction_locked_at)
               AND max(o.ingested_at) <= max(l.prediction_locked_at)
               AND max(o.source_observed_at)
                   >= max(l.prediction_locked_at) - interval '6 hours'
        )
        SELECT provider_code,
               count(*) AS original_time_qualified_comparisons,
               count(DISTINCT match_id) AS fixtures_with_original_time_pair,
               count(DISTINCT bookmaker_code) AS bookmakers
        FROM pairs
        GROUP BY provider_code
    """


def _mapped_fixtures_sql(provider_code: str | None, params: list[Any]) -> str:
    where = ""
    if provider_code is not None:
        where = " AND source_system = %s"
        params.append(provider_code)
    return f"""
        SELECT source_system,
               count(DISTINCT match_id) AS unique_mapped_fixtures,
               count(*) AS mapping_rows
        FROM match_source_ids
        WHERE TRUE {where}
        GROUP BY source_system
        ORDER BY source_system
    """


def provider_coverage_report(
    conn: psycopg.Connection,
    *,
    provider_code: str | None = None,
    as_of: datetime | None = None,
) -> dict[str, Any]:
    """Build the commercial provider coverage reconciliation.

    Counts are per upstream API provider and use only immutable lineage. The
    report is read-only and makes no provider calls.
    """

    def rows(sql: str, params: list[Any]) -> list[tuple]:
        return conn.execute(sql, tuple(params)).fetchall()

    report: dict[str, Any] = {
        "version": COVERAGE_REPORT_VERSION,
        "attribution_version": ATTRIBUTION_VERSION,
        "as_of": (_utc(as_of) if as_of is not None else None),
        "providers": {},
        "limitations": [
            "Raw responses are counted per provider poll, not per unique fixture.",
            "Duplicate observations are identical payload content under distinct response keys; "
            "normalized duplicate rows cannot exist because of the snapshot uniqueness guard.",
            "Rejection reasons are derived from stored lineage; the normalizer's transient "
            "per-group result is not persisted.",
            "Original-time qualification uses the first stored Agent 1 lock per match.",
        ],
        "provider_calls": 0,
    }
    providers: dict[str, dict[str, Any]] = {}

    def bucket(code: str) -> dict[str, Any]:
        return providers.setdefault(code, {
            "raw_responses": 0,
            "raw_fixtures": 0,
            "raw_live_shape": 0,
            "raw_historical_shape": 0,
            "duplicate_payload_responses": 0,
            "extracted_winner_observations": 0,
            "normalized_quotes": 0,
            "normalized_fixtures": 0,
            "normalized_bookmakers": 0,
            "rejection_reasons": {},
            "rejected_observations": 0,
            "unique_mapped_fixtures": 0,
            "valid_two_sided_markets": 0,
            "fixtures_with_two_sided_market": 0,
            "original_time_qualified_comparisons": 0,
        })

    def query(builder):
        params: list[Any] = []
        return rows(builder(provider_code, params), params)

    for code, raw, fixtures, live, historical, market_map, first_at, last_at, duplicates in query(
        _raw_extraction_sql
    ):
        item = bucket(str(code))
        item.update({
            "raw_responses": int(raw),
            "raw_fixtures": int(fixtures),
            "raw_live_shape": int(live),
            "raw_historical_shape": int(historical),
            "raw_provider_market_map_shape": int(market_map),
            "duplicate_payload_responses": int(duplicates),
            "first_received_at": first_at,
            "last_received_at": last_at,
        })

    for code, extracted in query(_extracted_sql):
        bucket(str(code))["extracted_winner_observations"] = int(extracted)

    for code, quotes, fixtures, books, first_obs, last_obs, first_ing, last_ing in query(
        _normalized_sql
    ):
        bucket(str(code)).update({
            "normalized_quotes": int(quotes),
            "normalized_fixtures": int(fixtures),
            "normalized_bookmakers": int(books),
            "first_normalized_observation_at": first_obs,
            "last_normalized_observation_at": last_obs,
            "first_normalized_ingested_at": first_ing,
            "last_normalized_ingested_at": last_ing,
        })

    for code, reason, count in query(_rejections_sql):
        item = bucket(str(code))
        item["rejection_reasons"][str(reason)] = int(count)
        if str(reason) != "NORMALIZED":
            item["rejected_observations"] += int(count)

    for code, mapped, mapping_rows in query(_mapped_fixtures_sql):
        item = bucket(str(code))
        item["unique_mapped_fixtures"] = int(mapped)
        item["mapping_rows"] = int(mapping_rows)

    for code, markets, fixtures, books in query(_two_sided_sql):
        bucket(str(code)).update({
            "valid_two_sided_markets": int(markets),
            "fixtures_with_two_sided_market": int(fixtures),
            "bookmakers_with_two_sided_market": int(books),
        })

    for code, qualified, fixtures, books in query(_original_time_sql):
        bucket(str(code)).update({
            "original_time_qualified_comparisons": int(qualified),
            "fixtures_with_original_time_pair": int(fixtures),
            "original_time_bookmakers": int(books),
        })

    by_bookmaker_rows = conn.execute(
        """
        SELECT r.provider_code, o.bookmaker_code, o.source_system::text,
               count(*) AS quotes, count(DISTINCT o.match_id) AS fixtures
        FROM odds_snapshots o
        JOIN raw_odds_observations r
          ON r.raw_odds_observation_id = o.raw_odds_observation_id
        WHERE (%s::text IS NULL OR r.provider_code = %s)
        GROUP BY 1, 2, 3
        ORDER BY 1, 4 DESC
        """,
        (provider_code, provider_code),
    ).fetchall()
    for code, bookmaker, source, quotes, fixtures in by_bookmaker_rows:
        item = bucket(str(code))
        item.setdefault("normalized_by_bookmaker", []).append({
            "bookmaker_code": str(bookmaker),
            "normalization_class": str(source),
            "quotes": int(quotes),
            "fixtures": int(fixtures),
        })

    report["providers"] = {code: providers[code] for code in sorted(providers)}
    return report


def attribution_completeness(
    rows: Iterable[MarketObservationAttribution],
) -> dict[str, Any]:
    """Summarize whether every row carries the required attribution fields."""
    total = 0
    complete = 0
    classifications: dict[str, int] = {}
    providers: dict[str, int] = {}
    for row in rows:
        total += 1
        required = (
            row.upstream_provider_code,
            row.provider_fixture_id,
            row.provider_bookmaker_key,
            row.actual_bookmaker_code,
            row.canonical_player_1_id,
            row.canonical_player_2_id,
            row.raw_odds_observation_id,
            row.odds_snapshot_id,
        )
        if all(required):
            complete += 1
        classifications[row.historical_reconstruction_class] = (
            classifications.get(row.historical_reconstruction_class, 0) + 1
        )
        providers[row.upstream_provider_code] = providers.get(row.upstream_provider_code, 0) + 1
    return {
        "rows": total,
        "complete_rows": complete,
        "completeness": complete / total if total else None,
        "historical_reconstruction_classes": dict(sorted(classifications.items())),
        "providers": dict(sorted(providers.items())),
    }


def main(argv: list[str] | None = None) -> int:
    """Read-only CLI for the commercial provider reconciliation report."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output")
    parser.add_argument("--provider")
    args = parser.parse_args(argv)
    dsn = os.environ.get("TENNIS_DATABASE_URL")
    if not dsn:
        parser.error("read-only TENNIS_DATABASE_URL required")
    with psycopg.connect(
        dsn,
        autocommit=True,
        connect_timeout=20,
        options="-c default_transaction_read_only=on",
    ) as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        report = provider_coverage_report(conn, provider_code=args.provider)
        attributions = load_attributions(conn, provider_code=args.provider)
        report["attribution_completeness"] = attribution_completeness(attributions)
    payload = json.dumps(report, sort_keys=True, default=str, indent=2)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(payload + "\n")
    print(payload)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
