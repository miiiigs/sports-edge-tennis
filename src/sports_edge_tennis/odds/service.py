from __future__ import annotations

import psycopg

from .contracts import (
    LiveOddsIntegrityError,
    LiveOddsReconciliation,
    ProviderBookmakerMarket,
    ProviderObservation,
    RawObservationRecord,
)
from .normalizer import build_quote, source_system_for_mapping
from .persistence import (
    create_ingest_run,
    finish_ingest_run,
    insert_live_odds_snapshot,
    load_pit_match_version,
    persist_raw_observation,
    reconciliation_error_summary,
    resolve_provider_event,
    resolve_provider_player,
    select_bookmaker_mapping,
)


def ingest_provider_observation(conn: psycopg.Connection, observation: ProviderObservation) -> LiveOddsReconciliation:
    result = LiveOddsReconciliation(raw_observations_seen=1)

    with conn.transaction():
        ingest_run_id = create_ingest_run(
            conn,
            provider_code=observation.provider_code,
            started_at=observation.received_at,
        )
        raw = persist_raw_observation(conn, ingest_run_id=ingest_run_id, observation=observation)

    result.raw_inserted += 1 if raw.inserted else 0
    result.raw_existing += 0 if raw.inserted else 1

    try:
        _normalize_observation(conn, observation=observation, raw=raw, result=result)
    except Exception as exc:
        result.failed_groups += 1
        _record(result, "NORMALIZATION_ERROR", reason=str(exc))
        with conn.transaction():
            _finish(conn, ingest_run_id=ingest_run_id, result=result, status=result.terminal_status)
        raise

    with conn.transaction():
        _finish(conn, ingest_run_id=ingest_run_id, result=result, status=result.terminal_status)

    return result


def _finish(
    conn: psycopg.Connection,
    *,
    ingest_run_id: int,
    result: LiveOddsReconciliation,
    status: str,
) -> None:
    finish_ingest_run(
        conn,
        ingest_run_id=ingest_run_id,
        status=status,
        rows_seen=1,
        rows_inserted=result.raw_inserted + result.normalized_inserted,
        rows_rejected=result.failed_groups + result.invalid_market + result.invalid_price + result.invalid_status,
        rows_unmatched=result.unmatched_event + result.unknown_participant,
        error_summary=reconciliation_error_summary(result.details),
    )


def _normalize_observation(
    conn: psycopg.Connection,
    *,
    observation: ProviderObservation,
    raw: RawObservationRecord,
    result: LiveOddsReconciliation,
) -> None:
    event_status, match_id = resolve_provider_event(
        conn,
        provider_code=raw.provider_code,
        provider_event_id=raw.provider_event_id,
    )
    if event_status == "UNMATCHED":
        result.unmatched_event += len(observation.markets) or 1
        _record(result, "UNMATCHED_EVENT", provider_event_id=raw.provider_event_id)
        return
    if event_status == "IDENTITY_CONFLICT":
        result.identity_conflict += len(observation.markets) or 1
        _record(result, "IDENTITY_CONFLICT", provider_event_id=raw.provider_event_id)
        return
    assert match_id is not None

    match = load_pit_match_version(conn, match_id=match_id, observation_time=raw.observation_time)
    if match is None:
        result.unmatched_event += len(observation.markets) or 1
        _record(result, "UNMATCHED_EVENT", provider_event_id=raw.provider_event_id, reason="no_pit_match_version")
        return

    for market in observation.markets:
        group_result = LiveOddsReconciliation()
        with conn.transaction():
            _normalize_market(
                conn,
                observation=observation,
                raw=raw,
                match=match,
                market=market,
                result=group_result,
            )
        _merge_result(result, group_result)


def _normalize_market(
    conn: psycopg.Connection,
    *,
    observation: ProviderObservation,
    raw: RawObservationRecord,
    match,
    market: ProviderBookmakerMarket,
    result: LiveOddsReconciliation,
) -> None:
    if market.market_type != "MATCH_WINNER":
        result.invalid_market += 1
        result.raw_only += 1
        _record(result, "INVALID_MARKET", provider_bookmaker_key=market.provider_bookmaker_key)
        return
    if market.market_status not in ("OPEN", "SUSPENDED", "CLOSED", "UNKNOWN"):
        result.invalid_status += 1
        result.raw_only += 1
        _record(result, "INVALID_STATUS", provider_bookmaker_key=market.provider_bookmaker_key)
        return

    mapping = select_bookmaker_mapping(
        conn,
        provider_code=raw.provider_code,
        provider_bookmaker_key=market.provider_bookmaker_key,
        observation_time=raw.observation_time,
    )
    if mapping is None:
        result.unknown_bookmaker += 1
        result.raw_only += 1
        _record(result, "UNKNOWN_BOOKMAKER", provider_bookmaker_key=market.provider_bookmaker_key)
        return
    source_system = source_system_for_mapping(mapping)
    if source_system is None:
        result.raw_only_bookmaker += 1
        result.raw_only += 1
        _record(result, "RAW_ONLY_BOOKMAKER", provider_bookmaker_key=market.provider_bookmaker_key)
        return

    if mapping.provider_code != raw.provider_code:
        raise LiveOddsIntegrityError("raw provider and mapping provider differ")

    inserted_any = False
    for selection in market.selections:
        if selection.canonical_player_id is None:
            assert selection.provider_player_id is not None
            player_status, player_id = resolve_provider_player(
                conn,
                provider_code=raw.provider_code,
                provider_player_id=selection.provider_player_id,
            )
            if player_status != "RESOLVED" or player_id is None:
                result.unknown_participant += 1
                result.raw_only += 1
                _record(
                    result,
                    "UNKNOWN_PARTICIPANT",
                    provider_bookmaker_key=market.provider_bookmaker_key,
                    provider_player_id=selection.provider_player_id,
                )
                continue
        else:
            player_id = selection.canonical_player_id
        try:
            quote = build_quote(
                observation=observation,
                raw_odds_observation_id=raw.raw_odds_observation_id,
                market=market,
                selection=selection,
                selection_player_id=player_id,
                match=match,
                mapping=mapping,
            )
            if quote.source_system != source_system or quote.bookmaker_code != mapping.bookmaker_code:
                raise LiveOddsIntegrityError("mapping and normalized quote are incoherent")
            inserted = insert_live_odds_snapshot(conn, match_id=match.match_id, quote=quote)
            result.normalized_inserted += 1 if inserted else 0
            result.normalized_existing += 0 if inserted else 1
            inserted_any = inserted_any or inserted
        except ValueError as exc:
            result.failed_groups += 1
            result.raw_only += 1
            _record(
                result,
                "NORMALIZATION_ERROR",
                provider_bookmaker_key=market.provider_bookmaker_key,
                reason=str(exc),
            )
    if not inserted_any and not market.selections:
        result.raw_only += 1


def _record(result: LiveOddsReconciliation, status: str, **fields: object) -> None:
    payload = {"status": status}
    payload.update(fields)
    result.details.append(payload)


def _merge_result(result: LiveOddsReconciliation, group_result: LiveOddsReconciliation) -> None:
    result.raw_observations_seen += group_result.raw_observations_seen
    result.raw_inserted += group_result.raw_inserted
    result.raw_existing += group_result.raw_existing
    result.normalized_inserted += group_result.normalized_inserted
    result.normalized_existing += group_result.normalized_existing
    result.raw_only += group_result.raw_only
    result.unmatched_event += group_result.unmatched_event
    result.identity_conflict += group_result.identity_conflict
    result.unknown_bookmaker += group_result.unknown_bookmaker
    result.raw_only_bookmaker += group_result.raw_only_bookmaker
    result.unknown_participant += group_result.unknown_participant
    result.invalid_market += group_result.invalid_market
    result.invalid_price += group_result.invalid_price
    result.invalid_status += group_result.invalid_status
    result.failed_groups += group_result.failed_groups
    result.details.extend(group_result.details)
