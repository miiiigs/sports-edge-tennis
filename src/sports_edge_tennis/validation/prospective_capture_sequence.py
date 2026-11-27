"""Pre-cutoff identity and price capture sequence (read-only evidence).

The failure mode this addresses: in the live cycle, predictions are persisted
before the TARGET odds branch resolves the OddsPapi identity and ingests prices.
A fixture can therefore have a first-issued prediction whose identity and quote
were recorded seconds later in the same cycle. Those later rows are real
evidence, but they were not available at the original prediction cutoff and
must not be presented as if they were.

Safe prospective sequence (each step must complete before the next):

1. ``IDENTITY_RECORDED`` - identity upsert history row exists at or before the
   prediction cutoff;
2. ``PRICE_OBSERVED`` - a two-sided-or-better market snapshot was observed and
   ingested at or before the cutoff;
3. ``PREDICTION_LOCKED`` - the prediction run is finalized.

This module only *measures* that order from immutable rows. It never reorders
the runtime, never backdates evidence and never upgrades a late capture to a
pre-cutoff one.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

SEQUENCE_POLICY_VERSION = "pre_cutoff_identity_price_sequence_v1"

SAFE_SEQUENCE = ("IDENTITY_RECORDED", "PRICE_OBSERVED", "PREDICTION_LOCKED")

STATUS_COMPLETE = "PRE_CUTOFF_COMPLETE"


@dataclass(frozen=True)
class CaptureTimestamps:
    match_id: int
    prediction_as_of: datetime
    prediction_locked_at: datetime
    identity_recorded_at: datetime | None
    first_price_observed_at: datetime | None
    first_price_ingested_at: datetime | None


@dataclass(frozen=True)
class CaptureSequenceAssessment:
    match_id: int
    identity_pre_cutoff: bool
    price_pre_cutoff: bool
    status: str
    policy_version: str = SEQUENCE_POLICY_VERSION

    @property
    def safe(self) -> bool:
        return self.status == STATUS_COMPLETE


def _le(value: datetime | None, cutoff: datetime) -> bool:
    return value is not None and value <= cutoff


def assess_capture(timestamps: CaptureTimestamps) -> CaptureSequenceAssessment:
    """Classify one fixture's identity/price capture against its cutoff."""
    cutoff = timestamps.prediction_as_of
    identity_pre = _le(timestamps.identity_recorded_at, cutoff)
    price_pre = (
        _le(timestamps.first_price_observed_at, cutoff)
        and _le(timestamps.first_price_ingested_at, cutoff)
    )

    identity_state = (
        "PRE_CUTOFF"
        if identity_pre
        else ("MISSING" if timestamps.identity_recorded_at is None else "LATE")
    )
    price_state = (
        "PRE_CUTOFF"
        if price_pre
        else ("MISSING" if timestamps.first_price_observed_at is None else "LATE")
    )
    status = (
        STATUS_COMPLETE
        if identity_pre and price_pre
        else f"IDENTITY_{identity_state}+PRICE_{price_state}"
    )

    return CaptureSequenceAssessment(
        match_id=timestamps.match_id,
        identity_pre_cutoff=identity_pre,
        price_pre_cutoff=price_pre,
        status=status,
    )


_LEDGER_SQL = """
WITH cutoffs AS (
    SELECT DISTINCT match_id,
           min(as_of) AS prediction_as_of,
           min(prediction_locked_at) AS prediction_locked_at
    FROM predictions
    WHERE prediction_run_id = %s
    GROUP BY match_id
)
SELECT c.match_id, c.prediction_as_of, c.prediction_locked_at,
       (SELECT min(h.recorded_at)
          FROM oddspapi_event_identity_history h
         WHERE h.canonical_match_id = c.match_id
           AND h.event_type = 'UPSERTED') AS identity_recorded_at,
       (SELECT min(o.source_observed_at)
          FROM odds_snapshots o
         WHERE o.match_id = c.match_id
           AND o.market_type = 'MATCH_WINNER') AS first_price_observed_at,
       (SELECT min(o.ingested_at)
          FROM odds_snapshots o
         WHERE o.match_id = c.match_id
           AND o.market_type = 'MATCH_WINNER') AS first_price_ingested_at
FROM cutoffs c
ORDER BY c.match_id
"""


def audit_run_capture(conn, *, prediction_run_id: int) -> dict[str, object]:
    """Read-only per-fixture capture sequence for one finalized prediction run."""
    rows = conn.execute(_LEDGER_SQL, (prediction_run_id,)).fetchall()
    assessments = [
        assess_capture(
            CaptureTimestamps(
                match_id=int(row[0]),
                prediction_as_of=row[1],
                prediction_locked_at=row[2],
                identity_recorded_at=row[3],
                first_price_observed_at=row[4],
                first_price_ingested_at=row[5],
            )
        )
        for row in rows
    ]
    counts: dict[str, int] = {}
    for assessment in assessments:
        counts[assessment.status] = counts.get(assessment.status, 0) + 1
    return {
        "policy_version": SEQUENCE_POLICY_VERSION,
        "safe_sequence": list(SAFE_SEQUENCE),
        "prediction_run_id": prediction_run_id,
        "fixtures": len(assessments),
        "safe_fixtures": sum(1 for a in assessments if a.safe),
        "status_counts": counts,
        "fixtures_detail": [
            {
                "match_id": a.match_id,
                "status": a.status,
                "identity_pre_cutoff": a.identity_pre_cutoff,
                "price_pre_cutoff": a.price_pre_cutoff,
            }
            for a in assessments
        ],
        "provider_calls": 0,
        "database_writes": False,
    }


__all__ = [
    "SAFE_SEQUENCE",
    "SEQUENCE_POLICY_VERSION",
    "STATUS_COMPLETE",
    "CaptureSequenceAssessment",
    "CaptureTimestamps",
    "assess_capture",
    "audit_run_capture",
]
