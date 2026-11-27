"""Measured acquisition-delay decomposition for tennis market observations.

The earlier audit reported a ~10-11 hour median lag between the first-issued
Agent 1 prediction and the first stored OddsPapi price. A single median cannot
say *where* the delay comes from. This module measures, per fixture, the clocks
that actually exist in the immutable schema and attributes the lag to explicit
stages instead of assuming one universal cause.

Stages preserved independently
------------------------------
1. first canonical fixture discovery            ``match_versions.ingested_at``
2. first player identity resolution             ``player_source_ids.created_at``
3. first bookmaker fixture discovery            identity-cache ``resolved_at``
4. first durable cross-provider mapping         ``match_source_ids.created_at``
5. first valid individual price                 ``odds_snapshots.source_observed_at``
6. first valid two-sided market                 both sides at one observation
7. original AI issuance                         first Agent 1 ``as_of``
8. scheduled start                              PIT ``match_start_at``
9. actual competitive start                     not stored (reported, not invented)
10. provider fixture update time                payload ``updatedAt``
11. actual database acquisition                  raw ``received_at``
12. actual database persistence                  raw/snapshot ``created_at``,
                                                 ``odds_snapshots.ingested_at``

Attribution of a positive issue-to-price lag
--------------------------------------------
``identity_discovery_wait``  issue -> bookmaker fixture discovery
``scheduling_wait``          previous stage -> the batch run that acquired it
``provider_wait``            batch run start -> first observed price
``persistence_wait``         observed price -> ingested/persisted price
``residual``                 measured lag minus the above

A negative lag means the price was observed before that prediction was issued;
it is reported as such and is never promoted to pre-issue coverage.
"""
from __future__ import annotations

import argparse
import json
import math
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Sequence

import psycopg

DELAY_DECOMPOSITION_VERSION = "ACQUISITION_DELAY_DECOMPOSITION_V1"
MISSING = "NOT_STORED"

STAGE_FIELDS = (
    "first_fixture_discovered_at",
    "first_player_identity_resolved_at",
    "first_bookmaker_fixture_discovered_at",
    "first_cross_provider_mapping_at",
    "first_individual_price_observed_at",
    "first_two_sided_market_at",
    "original_ai_issued_at",
    "scheduled_start_at",
    "actual_competitive_start_at",
    "provider_fixture_updated_at",
    "first_price_received_at",
    "first_price_persisted_at",
)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("timestamps must be timezone-aware")
    return value.astimezone(timezone.utc)


def _seconds(start: datetime | None, end: datetime | None) -> float | None:
    if start is None or end is None:
        return None
    return (_utc(end) - _utc(start)).total_seconds()


@dataclass(frozen=True)
class FixtureAcquisitionClocks:
    match_id: int
    provider_code: str
    first_fixture_discovered_at: datetime | None
    first_player_identity_resolved_at: datetime | None
    first_bookmaker_fixture_discovered_at: datetime | None
    first_cross_provider_mapping_at: datetime | None
    first_individual_price_observed_at: datetime | None
    first_two_sided_market_at: datetime | None
    original_ai_issued_at: datetime | None
    prediction_locked_at: datetime | None
    scheduled_start_at: datetime | None
    actual_competitive_start_at: datetime | None
    provider_fixture_updated_at: datetime | None
    provider_price_changed_at: datetime | None
    first_price_received_at: datetime | None
    first_price_persisted_at: datetime | None
    first_price_batch_started_at: datetime | None
    first_price_classification: str
    provider_start_time: datetime | None

    @property
    def clocks(self) -> dict[str, datetime | None]:
        return {field: getattr(self, field) for field in STAGE_FIELDS}


def decompose(clocks: FixtureAcquisitionClocks) -> dict[str, Any]:
    """Attribute the issue-to-price lag to measured, additive stages.

    The batch clock is the most recent prediction-run start at or before the
    first stored price. The identity clock only counts as a wait when it falls
    inside the acquiring batch; a cache row written after the price is evidence
    of late identity recording, not a reason the price was late.
    """
    issue = clocks.original_ai_issued_at
    discovery = clocks.first_bookmaker_fixture_discovered_at
    batch_start = clocks.first_price_batch_started_at
    observed = clocks.first_individual_price_observed_at
    received = clocks.first_price_received_at
    persisted = clocks.first_price_persisted_at

    issue_to_price = _seconds(issue, observed)
    issue_to_persisted = _seconds(issue, persisted)

    def _non_negative(value: float | None) -> float | None:
        return None if value is None else max(0.0, value)

    batch_wait = _non_negative(_seconds(issue, batch_start))
    discovery_after_price = (
        discovery is not None and observed is not None and _utc(discovery) > _utc(observed)
    )
    if batch_start is not None and discovery is not None and not discovery_after_price:
        identity_wait = _non_negative(_seconds(batch_start, discovery))
        provider_start = max(_utc(batch_start), _utc(discovery))
    else:
        identity_wait = 0.0 if batch_start is not None else None
        provider_start = _utc(batch_start) if batch_start is not None else None
    provider_wait = (
        None if provider_start is None or observed is None
        else _non_negative(_seconds(provider_start, observed))
    )
    persistence_wait = _non_negative(_seconds(observed, persisted))

    components = {
        "batch_schedule_wait_seconds": batch_wait,
        "within_batch_identity_wait_seconds": identity_wait,
        "post_identity_provider_wait_seconds": provider_wait,
        "persistence_wait_seconds": persistence_wait,
    }
    if issue_to_persisted is not None and issue_to_persisted > 0:
        known = sum(value for value in components.values() if value is not None)
        residual = max(0.0, issue_to_persisted - known)
    else:
        residual = None

    return {
        "match_id": clocks.match_id,
        "provider_code": clocks.provider_code,
        "issue_to_first_price_seconds": issue_to_price,
        "issue_to_persisted_seconds": issue_to_persisted,
        "price_observed_to_received_seconds": _seconds(observed, received),
        "price_received_to_persisted_seconds": _seconds(received, persisted),
        "price_observed_to_persisted_seconds": _seconds(observed, persisted),
        # Diagnostic deltas (not additive when stages overlap).
        "identity_discovery_wait_seconds": _seconds(issue, discovery),
        "scheduling_wait_seconds": _seconds(issue, batch_start),
        "provider_wait_seconds": _seconds(batch_start, observed),
        # Additive path attribution.
        **components,
        "residual_wait_seconds": residual,
        "bookmaker_discovery_after_price": discovery_after_price,
        "issue_before_price": (
            None if issue_to_price is None else issue_to_price >= 0
        ),
        "price_change_age_seconds": _seconds(
            clocks.provider_price_changed_at, observed
        ),
        "observation_age_at_last_update_seconds": _seconds(
            clocks.provider_fixture_updated_at, observed
        ),
        "provider_start_to_scheduled_start_seconds": _seconds(
            clocks.scheduled_start_at, clocks.provider_start_time
        ),
        "observed_after_scheduled_start": (
            None if observed is None or clocks.scheduled_start_at is None
            else _utc(observed) >= _utc(clocks.scheduled_start_at)
        ),
        "unavailable_clocks": [
            field for field in STAGE_FIELDS if getattr(clocks, field) is None
        ],
    }


def _percentile(values: Sequence[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def _distribution(values: Iterable[float | None]) -> dict[str, Any]:
    clean = [float(value) for value in values if value is not None]
    if not clean:
        return {"n": 0, "p50": None, "p90": None, "min": None, "max": None, "mean": None}
    return {
        "n": len(clean),
        "p50": _percentile(clean, 0.5),
        "p90": _percentile(clean, 0.9),
        "min": min(clean),
        "max": max(clean),
        "mean": sum(clean) / len(clean),
    }


def summarize_decomposition(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Summarize measured stage delays across fixtures."""
    positive = [row for row in rows if (row.get("issue_to_first_price_seconds") or 0) > 0]
    return {
        "version": DELAY_DECOMPOSITION_VERSION,
        "fixtures": len(rows),
        "fixtures_with_positive_issue_to_price": len(positive),
        "fixtures_with_price_before_issue": sum(
            1 for row in rows if row.get("issue_before_price") is False
        ),
        "issue_to_first_price": _distribution(
            row.get("issue_to_first_price_seconds") for row in positive
        ),
        "issue_to_persisted": _distribution(
            row.get("issue_to_persisted_seconds") for row in positive
        ),
        "batch_schedule_wait": _distribution(
            row.get("batch_schedule_wait_seconds") for row in positive
        ),
        "within_batch_identity_wait": _distribution(
            row.get("within_batch_identity_wait_seconds") for row in positive
        ),
        "post_identity_provider_wait": _distribution(
            row.get("post_identity_provider_wait_seconds") for row in positive
        ),
        "persistence_wait": _distribution(
            row.get("persistence_wait_seconds") for row in positive
        ),
        "residual_wait": _distribution(
            row.get("residual_wait_seconds") for row in positive
        ),
        "diagnostic_identity_discovery_delta": _distribution(
            row.get("identity_discovery_wait_seconds") for row in positive
        ),
        "diagnostic_scheduling_delta": _distribution(
            row.get("scheduling_wait_seconds") for row in positive
        ),
        "bookmaker_discovery_after_price": sum(
            1 for row in rows if row.get("bookmaker_discovery_after_price")
        ),
        "provider_start_minus_scheduled_start": _distribution(
            row.get("provider_start_to_scheduled_start_seconds") for row in rows
        ),
        "observed_after_scheduled_start": sum(
            1 for row in rows if row.get("observed_after_scheduled_start")
        ),
        "missing_actual_competitive_start": sum(
            1 for row in rows
            if "actual_competitive_start_at" in row.get("unavailable_clocks", [])
        ),
        "stage_component_medians_share_of_positive_lag": _component_shares(positive),
        "cause_attribution_note": (
            "Components are measured, non-negative stage waits. They can overlap "
            "when identity discovery happens inside the same run as the price. "
            "Residual is the unexplained remainder, not a provider excuse."
        ),
    }


def _component_shares(rows: Sequence[dict[str, Any]]) -> dict[str, float | None]:
    medians = {
        key: _percentile(
            [row[key] for row in rows if row.get(key) is not None], 0.5
        )
        for key in (
            "batch_schedule_wait_seconds",
            "within_batch_identity_wait_seconds",
            "post_identity_provider_wait_seconds",
            "persistence_wait_seconds",
            "residual_wait_seconds",
        )
    }
    total = sum(value for value in medians.values() if value is not None)
    if not total:
        return {key: None for key in medians}
    return {
        key: (value / total if value is not None else None)
        for key, value in medians.items()
    }


def _extract_provider_times(payload: Mapping[str, Any] | None) -> tuple[datetime | None, datetime | None]:
    """Return (fixture updatedAt, earliest recent price changedAt)."""
    if not isinstance(payload, Mapping):
        return None, None
    updated_at = _parse_timestamp(payload.get("updatedAt"))
    changed: list[datetime] = []
    books = payload.get("bookmakerOdds")
    if isinstance(books, Mapping):
        for raw_book in books.values():
            if not isinstance(raw_book, Mapping):
                continue
            markets = raw_book.get("markets")
            if not isinstance(markets, Mapping):
                continue
            market = markets.get("121")
            if not isinstance(market, Mapping):
                continue
            outcomes = market.get("outcomes")
            if not isinstance(outcomes, Mapping):
                continue
            for raw_outcome in outcomes.values():
                players = raw_outcome.get("players") if isinstance(raw_outcome, Mapping) else None
                player = players.get("0") if isinstance(players, Mapping) else None
                if not isinstance(player, Mapping):
                    continue
                for key in ("changedAt", "bookmakerChangedAt"):
                    parsed = _parse_timestamp(player.get(key))
                    if parsed is not None:
                        changed.append(parsed)
    return updated_at, (max(changed) if changed else None)


def _parse_timestamp(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc) if value.tzinfo else None
    try:
        parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo else None


_CLOCK_SQL = """
WITH first_prediction AS (
    SELECT DISTINCT ON (p.match_id)
           p.match_id, p.as_of, p.prediction_locked_at
    FROM predictions p
    WHERE p.agent_code = 'AGENT_1_SURFACE_ELO'
      AND p.prediction_status = 'PREDICTED'
      AND p.selection_side IN (1, 2)
      AND (%(match_ids)s::bigint[] IS NULL OR p.match_id = ANY(%(match_ids)s))
    ORDER BY p.match_id, p.as_of, p.prediction_id
),
first_version AS (
    SELECT match_id,
           min(ingested_at) AS discovered_at,
           min(created_at) AS version_created_at
    FROM match_versions
    GROUP BY match_id
),
first_players AS (
    SELECT m.match_id,
           CASE
               WHEN (SELECT min(psi.created_at) FROM player_source_ids psi
                      WHERE psi.player_id = latest.player_1_id) IS NULL
                 OR (SELECT min(psi.created_at) FROM player_source_ids psi
                      WHERE psi.player_id = latest.player_2_id) IS NULL
               THEN NULL
               ELSE GREATEST(
                   (SELECT min(psi.created_at) FROM player_source_ids psi
                     WHERE psi.player_id = latest.player_1_id),
                   (SELECT min(psi.created_at) FROM player_source_ids psi
                     WHERE psi.player_id = latest.player_2_id)
               )
           END AS resolved_at
    FROM matches m
    JOIN LATERAL (
        SELECT player_1_id, player_2_id
        FROM match_versions mv
        WHERE mv.match_id = m.match_id
        ORDER BY mv.as_of DESC, mv.match_version_id DESC
        LIMIT 1
    ) latest ON TRUE
),
mapping AS (
    SELECT ms.match_id, min(ms.created_at) AS mapped_at
    FROM match_source_ids ms
    WHERE ms.source_system = %(provider_code)s
    GROUP BY ms.match_id
),
cache AS (
    SELECT canonical_match_id AS match_id,
           min(resolved_at) AS discovered_at
    FROM oddspapi_event_identity_cache
    GROUP BY canonical_match_id
),
raw AS (
    SELECT ms.match_id, r.raw_odds_observation_id, r.received_at, r.created_at,
           r.observation_time, r.ingest_run_id, r.payload_json
    FROM raw_odds_observations r
    JOIN match_source_ids ms
      ON ms.source_system = r.provider_code
     AND ms.source_match_id = r.provider_event_id
    WHERE r.provider_code = %(provider_code)s
),
first_price AS (
    SELECT o.match_id,
           min(o.source_observed_at) AS observed_at,
           min(o.ingested_at) AS ingested_at,
           min(o.raw_odds_observation_id) AS raw_odds_observation_id
    FROM odds_snapshots o
    JOIN raw r ON r.raw_odds_observation_id = o.raw_odds_observation_id
    GROUP BY o.match_id
),
first_two_sided AS (
    SELECT pair.match_id, min(pair.observed_at) AS observed_at
    FROM (
        SELECT o.match_id, o.source_observed_at AS observed_at
        FROM odds_snapshots o
        JOIN raw r ON r.raw_odds_observation_id = o.raw_odds_observation_id
        WHERE o.market_type = 'MATCH_WINNER'
          AND o.selection_type IN ('PLAYER_1', 'PLAYER_2')
          AND o.market_status = 'OPEN'
        GROUP BY o.match_id, o.bookmaker_code, o.source_observed_at
        HAVING count(DISTINCT o.selection_type) = 2
    ) pair
    GROUP BY pair.match_id
),
first_raw AS (
    SELECT DISTINCT ON (r.match_id)
           r.match_id, r.received_at, r.created_at, r.ingest_run_id,
           r.payload_json
    FROM raw r
    JOIN first_price fp
      ON fp.match_id = r.match_id
     AND fp.raw_odds_observation_id = r.raw_odds_observation_id
    ORDER BY r.match_id, r.raw_odds_observation_id
),
run AS (
    SELECT fr.match_id, ir.started_at
    FROM first_raw fr
    JOIN ingest_runs ir ON ir.ingest_run_id = fr.ingest_run_id
)
SELECT
    fp.match_id,
    fv.discovered_at,
    fpl.resolved_at,
    cache.discovered_at AS bookmaker_discovered_at,
    mapping.mapped_at,
    fp.observed_at AS price_observed_at,
    fts.observed_at AS two_sided_at,
    pred.as_of AS issued_at,
    pred.prediction_locked_at,
    mv.match_start_at,
    fr.received_at,
    fr.created_at AS raw_persisted_at,
    fp.ingested_at AS price_ingested_at,
    COALESCE(
        (SELECT max(pr.started_at)
         FROM prediction_runs pr
         WHERE pr.started_at <= fp.observed_at),
        run.started_at
    ) AS batch_started_at,
    fr.payload_json,
    (fr.payload_json->>'startTime') AS provider_start_text
FROM first_prediction pred
JOIN first_price fp ON fp.match_id = pred.match_id
LEFT JOIN first_version fv ON fv.match_id = pred.match_id
LEFT JOIN first_players fpl ON fpl.match_id = pred.match_id
LEFT JOIN cache ON cache.match_id = pred.match_id
LEFT JOIN mapping ON mapping.match_id = pred.match_id
LEFT JOIN first_two_sided fts ON fts.match_id = pred.match_id
LEFT JOIN LATERAL (
    SELECT mv.match_start_at
    FROM match_versions mv
    WHERE mv.match_id = pred.match_id
      AND mv.source_observed_at <= pred.as_of
      AND mv.as_of <= pred.as_of
    ORDER BY mv.as_of DESC, mv.match_version_id DESC
    LIMIT 1
) mv ON TRUE
LEFT JOIN first_raw fr ON fr.match_id = pred.match_id
LEFT JOIN run ON run.match_id = pred.match_id
ORDER BY pred.match_id
"""


def load_fixture_clocks(
    conn: psycopg.Connection,
    *,
    provider_code: str = "ODDSPAPI",
    match_ids: Sequence[int] | None = None,
) -> list[FixtureAcquisitionClocks]:
    """Read the immutable clocks used by the decomposition. Read-only."""
    rows = conn.execute(
        _CLOCK_SQL,
        {
            "provider_code": provider_code,
            "match_ids": list(match_ids) if match_ids else None,
        },
    ).fetchall()
    out: list[FixtureAcquisitionClocks] = []
    for row in rows:
        payload = row[14] if isinstance(row[14], Mapping) else None
        provider_updated, price_changed = _extract_provider_times(payload)
        provider_start = _parse_timestamp(row[15])
        observed = row[5]
        classification = "LIVE_OBSERVED"
        if isinstance(payload, Mapping) and isinstance(payload.get("bookmakers"), Mapping):
            classification = "LATEST_AT_CUTOFF_HISTORICAL"
        out.append(FixtureAcquisitionClocks(
            match_id=int(row[0]),
            provider_code=provider_code,
            first_fixture_discovered_at=row[1],
            first_player_identity_resolved_at=row[2],
            first_bookmaker_fixture_discovered_at=row[3],
            first_cross_provider_mapping_at=row[4],
            first_individual_price_observed_at=observed,
            first_two_sided_market_at=row[6],
            original_ai_issued_at=row[7],
            prediction_locked_at=row[8],
            scheduled_start_at=row[9],
            actual_competitive_start_at=None,
            provider_fixture_updated_at=provider_updated,
            provider_price_changed_at=price_changed,
            first_price_received_at=row[10],
            first_price_persisted_at=row[12] or row[11],
            first_price_batch_started_at=row[13],
            first_price_classification=classification,
            provider_start_time=provider_start,
        ))
    return out


def build_report(
    clocks: Sequence[FixtureAcquisitionClocks],
) -> dict[str, Any]:
    """Full report: per-fixture rows plus aggregate distributions."""
    rows = []
    for item in clocks:
        detail = decompose(item)
        detail["first_price_classification"] = item.first_price_classification
        detail["prediction_locked_at"] = item.prediction_locked_at
        detail["scheduled_start_at"] = item.scheduled_start_at
        rows.append(detail)
    summary = summarize_decomposition(rows)
    summary["limitations"] = [
        "Actual competitive start is not persisted in the current schema; it is "
        "reported as NOT_STORED rather than inferred from a result timestamp.",
        "Provider fixture updatedAt is the provider's last fixture change, not "
        "the price observation time.",
        "Stage waits can overlap when discovery and price capture occur in one run.",
        "Budget pressure is measured from the request ledger separately; it is not "
        "attributed per fixture.",
    ]
    summary["fixtures_detail"] = rows
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output")
    parser.add_argument("--provider", default="ODDSPAPI")
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
        report = build_report(load_fixture_clocks(conn, provider_code=args.provider))
    payload = json.dumps(report, sort_keys=True, default=str, indent=2)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(payload + "\n")
    print(payload)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
