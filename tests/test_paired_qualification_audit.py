from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sports_edge_tennis.validation.paired_qualification_audit import (
    QUALIFIED,
    REJECTED_BACKFILLED,
    REJECTED_DIFFERENT_OBSERVATION,
    REJECTED_IDENTITY_AFTER_ACQUISITION,
    REJECTED_LATE_INGESTION,
    REJECTED_LATE_OBSERVATION,
    REJECTED_MIXED_BOOKMAKER,
    REJECTED_POST_START,
    REJECTED_RETROSPECTIVE,
    REJECTED_SINGLE_SIDED,
    REJECTED_SUSPENDED,
    QuoteLineage,
    audit_quotes,
    qualify_pair,
)

NOW = datetime(2026, 11, 27, 12, tzinfo=UTC)
LOCK = NOW
START = NOW + timedelta(hours=3)


def quote(
    selection: str,
    *,
    match_id: int = 1,
    raw: int = 10,
    snapshot: str = "snap-1",
    book: str = "PINNACLE",
    provider: str = "ODDSPAPI",
    source: str = "SHARP",
    price: float = 1.8,
    status: str = "OPEN",
    observed: datetime = NOW - timedelta(minutes=30),
    ingested: datetime | None = None,
    mapping: datetime | None = None,
    shape: str = "LIVE_OBSERVED",
    start: datetime = START,
) -> QuoteLineage:
    return QuoteLineage(
        match_id=match_id,
        raw_observation_id=raw,
        source_snapshot_id=snapshot,
        provider_code=provider,
        bookmaker_code=book,
        source_system=source,
        selection_type=selection,
        decimal_price=price,
        market_status=status,
        observed_at=observed,
        ingested_at=ingested or observed,
        mapping_created_at=mapping or (observed - timedelta(hours=1)),
        payload_shape=shape,
        scheduled_start_at=start,
    )


def pair(
    *,
    raw: int = 10,
    raw_2: int | None = None,
    snapshot: str = "snap-1",
    snapshot_2: str | None = None,
    book: str = "PINNACLE",
    book_2: str | None = None,
    price: float = 1.8,
    price_2: float = 2.1,
    status: str = "OPEN",
    status_2: str | None = None,
    observed: datetime = NOW - timedelta(minutes=30),
    observed_2: datetime | None = None,
    ingested: datetime | None = None,
    ingested_2: datetime | None = None,
    mapping: datetime | None = None,
    mapping_2: datetime | None = None,
    shape: str = "LIVE_OBSERVED",
    shape_2: str | None = None,
    start: datetime = START,
) -> dict:
    left = quote(
        "PLAYER_1", raw=raw, snapshot=snapshot, book=book, price=price,
        status=status, observed=observed, ingested=ingested, mapping=mapping,
        shape=shape, start=start,
    )
    right = quote(
        "PLAYER_2", raw=raw_2 if raw_2 is not None else raw,
        snapshot=snapshot_2 if snapshot_2 is not None else snapshot,
        book=book_2 if book_2 is not None else book,
        price=price_2,
        status=status_2 if status_2 is not None else "OPEN",
        observed=observed_2 if observed_2 is not None else observed,
        ingested=ingested_2 if ingested_2 is not None else (
            ingested if ingested is not None else observed
        ),
        mapping=mapping_2 if mapping_2 is not None else mapping,
        shape=shape_2 if shape_2 is not None else shape,
        start=start,
    )
    return qualify_pair(left, right, prediction_locked_at=LOCK, now=NOW)


def test_identical_raw_observation_and_snapshot_qualifies():
    result = pair()
    assert result["status"] == QUALIFIED, result


def test_different_observation_records_never_qualify():
    result = pair(raw_2=11)
    assert result["status"] == REJECTED_DIFFERENT_OBSERVATION


def test_mixed_bookmakers_and_suspended_markets_are_rejected():
    assert pair(book_2="BET365")["status"] == REJECTED_MIXED_BOOKMAKER
    assert pair(status_2="SUSPENDED")["status"] == REJECTED_SUSPENDED


def test_late_observation_backfill_ingestion_and_start_are_rejected():
    assert pair(observed=LOCK + timedelta(minutes=1))["status"] == REJECTED_LATE_OBSERVATION
    assert pair(observed=LOCK - timedelta(hours=7))["status"] == REJECTED_BACKFILLED
    assert pair(ingested=LOCK + timedelta(minutes=5))["status"] == REJECTED_LATE_INGESTION
    assert pair(start=NOW - timedelta(hours=1))["status"] == REJECTED_POST_START


def test_identity_recorded_after_acquisition_and_retrospective_payload():
    assert pair(mapping=NOW + timedelta(minutes=1))["status"] == (
        REJECTED_IDENTITY_AFTER_ACQUISITION
    )
    assert pair(shape="LATEST_AT_CUTOFF_HISTORICAL")["status"] == REJECTED_RETROSPECTIVE


def test_audit_reports_single_sided_and_different_observation_as_unqualified():
    quotes = [
        quote("PLAYER_1", raw=10),
        quote("PLAYER_2", raw=11),
    ]
    report = audit_quotes(quotes, prediction_locks={1: LOCK}, now=NOW)
    assert report["qualified_markets"] == 0
    assert report["status_counts"][REJECTED_SINGLE_SIDED] == 2
    assert report["single_sided_markets"]

    quotes = [
        quote("PLAYER_1", raw=10, snapshot="a"),
        quote("PLAYER_2", raw=10, snapshot="b"),
    ]
    report = audit_quotes(quotes, prediction_locks={1: LOCK}, now=NOW)
    assert report["qualified_markets"] == 0
    assert report["status_counts"][REJECTED_SINGLE_SIDED] == 2
