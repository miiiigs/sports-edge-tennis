"""Live MVP orchestration: wire frozen components into one controlled flow.

The orchestrator performs a single API-Tennis fixture fetch, persists the
canonical slate, runs the frozen full-slate runner + Manager, and — only when at
least one Manager consensus match exists — performs exactly one pinnapi board
fetch followed by raw-first ingestion, consensus-only reconciliation, canonical
orientation, and SHARP/PINNACLE normalization.

Every external effect is injectable so the control flow can be tested with
deterministic mocks without live network access.
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable

import psycopg

from sports_edge_tennis.agents.agent1_surface_elo import DEFAULT_CONFIG as A1_CONFIG
from sports_edge_tennis.agents.agent2_hierarchical_markov import DEFAULT_CONFIG as A2_CONFIG
from sports_edge_tennis.agents.agent3_intransitivity import DEFAULT_CONFIG as A3_CONFIG
from sports_edge_tennis.data.sources.api_tennis_live import (
    ApiTennisIngestOutcome,
    ingest_api_tennis_source_a_fixtures,
)
from sports_edge_tennis.live.summary import (
    build_live_summary,
    render_terminal,
    write_summary,
)
from sports_edge_tennis.live.transport import (
    PINNAPI_FAILURE_CATEGORIES,
    TransportError,
    fetch_api_tennis_fixtures,
    fetch_pinnapi_board,
)
from sports_edge_tennis.manager.persistence import select_manager_policy_version
from sports_edge_tennis.odds.contracts import ProviderObservation
from sports_edge_tennis.odds.persistence import select_bookmaker_mapping
from sports_edge_tennis.odds.providers.pinnapi import (
    PINNAPI_ADAPTER_VERSION,
    PINNAPI_BOOKMAKER_KEY,
    PINNAPI_PROVIDER_CODE,
    PinnapiBoardEvent,
    PinnapiBoardParseResult,
    build_pinnapi_market,
    new_pinnapi_board_observation_id,
    parse_pinnapi_board,
    pinnapi_event_observation_key,
    to_pinnapi_event_evidence,
    to_pinnapi_raw_observation,
)
from sports_edge_tennis.odds.reconciliation import (
    reconcile_pinnapi_event,
)
from sports_edge_tennis.odds.service import ingest_provider_observation
from sports_edge_tennis.runner.contracts import (
    AGENT_1,
    AGENT_2,
    AGENT_3,
    AgentConfigRefV1,
    FullSlateRunOutcomeV1,
    FullSlateRunRequestV1,
)
from sports_edge_tennis.runner.full_slate import run_full_slate


class LiveMvpError(RuntimeError):
    result_code = "IMPLEMENTATION_FAILED"


class BlockedNoLiveMvpDatabase(LiveMvpError):
    result_code = "BLOCKED_NO_LIVE_MVP_DATABASE"


class BlockedMissingPinnapiKey(LiveMvpError):
    result_code = "BLOCKED_MISSING_PINNAPI_KEY"


class BlockedPinnapiBookmakerMapping(LiveMvpError):
    result_code = "BLOCKED_PINNAPI_BOOKMAKER_MAPPING"


class LivePilotApiTennisFailed(LiveMvpError):
    result_code = "LIVE_PILOT_API_TENNIS_FAILED"

    def __init__(self, message: str, *, failure_kind: str | None = None):
        super().__init__(message)
        self.failure_kind = failure_kind


class LivePilotSportradarFailed(LiveMvpError):
    result_code = "LIVE_PILOT_SPORTRADAR_FAILED"

    def __init__(self, message: str, *, failure_kind: str | None = None):
        super().__init__(message)
        self.failure_kind = failure_kind


class LivePilotPinnapiFailed(LiveMvpError):
    result_code = "LIVE_PILOT_PINNAPI_FAILED"


# Injectable step signatures.
FetchFixtures = Callable[[str, date], tuple[datetime, list[dict[str, Any]]]]
FetchBoard = Callable[[str], tuple[datetime, dict[str, Any]]]
IngestFixtures = Callable[[psycopg.Connection, list[dict[str, Any]], datetime], ApiTennisIngestOutcome]
RunSlate = Callable[[psycopg.Connection, tuple[int, ...], datetime], FullSlateRunOutcomeV1]
LoadConsensus = Callable[[psycopg.Connection, int], list[int]]
ParseBoard = Callable[[dict[str, Any], datetime, str], PinnapiBoardParseResult]
ProcessEvent = Callable[
    [psycopg.Connection, PinnapiBoardEvent, str, datetime, tuple[int, ...], datetime], dict[str, Any]
]

_REQUIRED_TABLES = (
    "matches",
    "players",
    "match_versions",
    "predictions",
    "manager_consensus_results",
    "raw_odds_observations",
    "odds_snapshots",
    "provider_bookmaker_mappings",
)


@dataclass(frozen=True)
class LiveMvpResult:
    status: str
    run_id: str
    slate_date: str
    prediction_as_of: datetime | None
    prediction_run_id: int | None
    fixture_count: int
    fixture_rows_seen: int
    fixture_rows_ingested: int
    fixture_rows_rejected: int
    fixture_rejection_reasons: tuple[tuple[str, int], ...]
    target_count: int
    manager_consensus_count: int
    pinnapi_requests: int
    matches: tuple[dict[str, Any], ...]
    summary_path: str | None
    notes: tuple[str, ...] = ()


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def ensure_agent_config_versions_v1(conn: psycopg.Connection) -> None:
    configs = {
        AGENT_1: A1_CONFIG,
        AGENT_2: A2_CONFIG,
        AGENT_3: A3_CONFIG,
    }
    for agent_code, cfg in configs.items():
        conn.execute(
            """
            INSERT INTO agent_config_versions (
                agent_code, config_version, model_version, feature_version,
                calibration_version, parameter_schema_version, config_payload
            )
            VALUES (%s, %s, %s, %s, NULL, %s, %s::jsonb)
            ON CONFLICT (agent_code, config_version) DO NOTHING
            """,
            (
                agent_code,
                cfg.config_version,
                cfg.model_version,
                cfg.feature_version,
                cfg.parameter_schema_version,
                json.dumps(cfg.to_payload(), sort_keys=True),
            ),
        )


def _preflight_database(conn: psycopg.Connection) -> None:
    for table in _REQUIRED_TABLES:
        present = conn.execute(
            "SELECT to_regclass(%s)", (f"public.{table}",)
        ).fetchone()[0]
        if present is None:
            raise BlockedNoLiveMvpDatabase(f"missing table public.{table}")
    match_count = conn.execute("SELECT count(*) FROM matches").fetchone()[0]
    result_count = conn.execute("SELECT count(*) FROM match_result_observations").fetchone()[0]
    stat_count = conn.execute("SELECT count(*) FROM match_stat_observations").fetchone()[0]
    if match_count == 0 or result_count == 0 or stat_count == 0:
        raise BlockedNoLiveMvpDatabase(
            "existing Tennis database lacks historical feature corpus "
            "(matches/match_result_observations/match_stat_observations)"
        )


def _ingest_fixtures(
    conn: psycopg.Connection,
    fixtures: list[dict[str, Any]],
    received_at: datetime,
) -> ApiTennisIngestOutcome:
    return ingest_api_tennis_source_a_fixtures(conn, fixtures, received_at=received_at)


def fixture_reconciliation_summary(outcome: ApiTennisIngestOutcome) -> dict[str, Any]:
    """Summarize Source A fixture reconciliation without raw provider payloads."""
    if outcome.rows_seen != outcome.rows_ingested + outcome.rows_rejected:
        raise LiveMvpError(
            "fixture ingest reconciliation invariant failed: "
            "rows_seen must equal rows_ingested + rows_rejected"
        )

    reason_counts: dict[str, int] = {}
    for row in outcome.rejected_rows:
        reason = row.get("reason")
        key = str(reason) if reason is not None else "UNKNOWN"
        reason_counts[key] = reason_counts.get(key, 0) + 1

    return {
        "fixture_rows_seen": outcome.rows_seen,
        "fixture_rows_ingested": outcome.rows_ingested,
        "fixture_rows_rejected": outcome.rows_rejected,
        "fixture_rejection_reasons": dict(sorted(reason_counts.items())),
    }


def _parse_board(
    payload: dict[str, Any],
    received_at: datetime,
    board_observation_id: str,
) -> PinnapiBoardParseResult:
    return parse_pinnapi_board(
        payload,
        received_at=received_at,
        board_observation_id=board_observation_id,
    )


def _run_slate(
    conn: psycopg.Connection,
    match_ids: tuple[int, ...],
    as_of: datetime,
) -> FullSlateRunOutcomeV1:
    ensure_agent_config_versions_v1(conn)
    policy = select_manager_policy_version(
        conn,
        policy_version=os.environ.get("TENNIS_MANAGER_POLICY_VERSION", "v1"),
    )
    request = FullSlateRunRequestV1(
        match_ids=tuple(match_ids),
        as_of=as_of,
        agent_config_refs=(
            AgentConfigRefV1(AGENT_1, A1_CONFIG.config_version),
            AgentConfigRefV1(AGENT_2, A2_CONFIG.config_version),
            AgentConfigRefV1(AGENT_3, A3_CONFIG.config_version),
        ),
        manager_policy_version_id=policy.manager_policy_version_id,
    )
    return run_full_slate(conn, request)


def _load_consensus(conn: psycopg.Connection, prediction_run_id: int) -> list[int]:
    rows = conn.execute(
        """
        SELECT match_id
        FROM manager_consensus_results
        WHERE prediction_run_id = %s AND manager_status = 'CONSENSUS'
        ORDER BY match_id
        """,
        (prediction_run_id,),
    ).fetchall()
    return [row[0] for row in rows]


def _normalize_observation(
    event: PinnapiBoardEvent,
    market,
    board_identity: str,
    received_at: datetime,
) -> ProviderObservation:
    return ProviderObservation(
        provider_code=PINNAPI_PROVIDER_CODE,
        provider_event_id=event.provider_event_id,
        provider_observation_key=pinnapi_event_observation_key(
            board_identity=board_identity,
            provider_event_id=event.provider_event_id,
        ),
        received_at=received_at,
        payload=event.raw_payload,
        adapter_version=PINNAPI_ADAPTER_VERSION,
        markets=(market,),
        provider_observed_at=None,
        provider_response_id=board_identity,
    )


def _process_event(
    conn: psycopg.Connection,
    event: PinnapiBoardEvent,
    board_identity: str,
    received_at: datetime,
    consensus_match_ids: tuple[int, ...],
    as_of: datetime,
) -> dict[str, Any]:
    """Raw-first ingest, consensus-only reconciliation, canonical normalization."""
    # 1. raw evidence first.
    raw_obs = to_pinnapi_raw_observation(event, board_identity=board_identity, received_at=received_at)
    ingest_provider_observation(conn, raw_obs)

    # 2. reconcile only against consensus candidates.
    evidence = to_pinnapi_event_evidence(event)
    result = reconcile_pinnapi_event(
        conn,
        evidence=evidence,
        candidate_match_ids=list(consensus_match_ids),
        as_of=as_of,
    )

    if result.status != "RESOLVED" or result.match is None:
        return {
            "event_id": event.provider_event_id,
            "reconciliation_status": result.status,
            "reconciliation_reason": result.reason,
            "candidate_match_ids": result.candidate_match_ids,
            "match_id": None,
            "normalized": False,
        }

    orientation = result.match

    # 3. normalize via post-reconciliation canonical market.
    market = build_pinnapi_market(event, orientation)
    normalize_obs = _normalize_observation(event, market, board_identity, received_at)
    ingest_provider_observation(conn, normalize_obs)

    return {
        "event_id": event.provider_event_id,
        "reconciliation_status": "RESOLVED",
        "reconciliation_reason": result.reason,
        "candidate_match_ids": result.candidate_match_ids,
        "match_id": orientation.match_id,
        "normalized": True,
    }


def _ensure_bookmaker_mapping(conn: psycopg.Connection, observation_time: datetime) -> None:
    mapping = select_bookmaker_mapping(
        conn,
        provider_code=PINNAPI_PROVIDER_CODE,
        provider_bookmaker_key=PINNAPI_BOOKMAKER_KEY,
        observation_time=observation_time,
    )
    if mapping is None:
        raise BlockedPinnapiBookmakerMapping(
            "missing provider_bookmaker_mappings row for PINNAPI/pinnacle"
        )
    if mapping.normalization_class != "SHARP" or mapping.bookmaker_code != "PINNACLE":
        raise BlockedPinnapiBookmakerMapping(
            "PINNAPI/pinnacle mapping must normalize to SHARP/PINNACLE"
        )


def _final_status(
    target_match_ids: tuple[int, ...],
    consensus_match_ids: tuple[int, ...],
) -> str:
    if not target_match_ids:
        return "LIVE_MVP_SUCCESS_EMPTY_SLATE"
    if consensus_match_ids:
        return "LIVE_MVP_SUCCESS"
    return "LIVE_MVP_SUCCESS_NO_CONSENSUS"


def run_live_mvp(
    conn: psycopg.Connection,
    *,
    slate_date: date,
    api_tennis_key: str,
    pinnapi_key: str | None = None,
    preflight: Callable[[psycopg.Connection], None] = _preflight_database,
    fetch_fixtures: FetchFixtures = fetch_api_tennis_fixtures,
    fetch_board: FetchBoard = fetch_pinnapi_board,
    ingest_fixtures: IngestFixtures = _ingest_fixtures,
    run_slate: RunSlate = _run_slate,
    load_consensus: LoadConsensus = _load_consensus,
    parse_board: ParseBoard = _parse_board,
    process_event: ProcessEvent = _process_event,
    ensure_bookmaker_mapping: Callable[[psycopg.Connection, datetime], None] = _ensure_bookmaker_mapping,
    build_match_entries: Callable | None = None,
    write_summary: Callable = write_summary,
    board_observation_id_factory: Callable[[], str] = new_pinnapi_board_observation_id,
    clock: Callable[[], datetime] = _utc_now,
    artifacts_dir: Path = Path("artifacts/live_mvp"),
    fixture_provider_code: str = "API_TENNIS",
    provider_lineage: str | None = None,
) -> LiveMvpResult:
    preflight(conn)

    run_id = f"{slate_date.isoformat()}-{uuid.uuid4().hex[:12]}"

    # 1. fetch + persist the canonical fixture slate.
    try:
        fixtures_received_at, fixtures = fetch_fixtures(api_tennis_key, slate_date)
    except TransportError as exc:
        if fixture_provider_code == "SPORTRADAR_V3":
            raise LivePilotSportradarFailed(
                exc.message, failure_kind=exc.kind
            ) from exc
        raise LivePilotApiTennisFailed(
            exc.message, failure_kind=exc.kind
        ) from exc

    ingest_outcome = ingest_fixtures(conn, fixtures, fixtures_received_at)
    fixture_reconciliation = fixture_reconciliation_summary(ingest_outcome)

    target_matches = [m for m in ingest_outcome.matches if m.match_status == "SCHEDULED"]
    target_match_ids = tuple(m.match_id for m in target_matches)

    prediction_as_of = clock()

    # 2. run the frozen full-slate runner + Manager.
    if target_match_ids:
        outcome = run_slate(conn, target_match_ids, prediction_as_of)
        prediction_run_id = outcome.prediction_run_id
    else:
        outcome = None
        prediction_run_id = None

    consensus_match_ids = (
        tuple(load_consensus(conn, prediction_run_id)) if prediction_run_id is not None else ()
    )

    # 3. pinnapi sharp retrieval only when consensus exists.
    pinnapi_requests = 0
    sharp_by_match: dict[int, dict[str, Any]] = {}
    notes: list[str] = [provider_lineage] if provider_lineage else []

    if consensus_match_ids:
        if pinnapi_key is None:
            notes.append("consensus exists but PINNAPI_KEY is unavailable")
        else:
            ensure_bookmaker_mapping(conn, prediction_as_of)
            try:
                board_received_at, board_payload = fetch_board(pinnapi_key)
            except TransportError as exc:
                # Surface the stable transport kind for operator diagnosis,
                # never the response body, credential or credential-bearing URL.
                safe_kind = (
                    exc.kind
                    if exc.kind in {"NETWORK", "TIMEOUT", "PARSE", "PROVIDER_ERROR"}
                    or (
                        exc.kind.startswith("HTTP_")
                        and len(exc.kind) == 8
                        and exc.kind[5:].isascii()
                        and exc.kind[5:].isdigit()
                    )
                    else "UNCLASSIFIED"
                )
                safe_category = (
                    exc.category
                    if getattr(exc, "category", None) in PINNAPI_FAILURE_CATEGORIES
                    else None
                )
                raise LivePilotPinnapiFailed(
                    f"pinnapi transport failed kind={safe_kind}"
                    + (f" category={safe_category}" if safe_category else "")
                ) from exc
            pinnapi_requests = 1
            board_observation_id = board_observation_id_factory()
            parse_result = parse_board(board_payload, board_received_at, board_observation_id)
            board_identity = parse_result.board_identity
            for event in parse_result.events:
                event_result = process_event(
                    conn,
                    event,
                    board_identity,
                    board_received_at,
                    consensus_match_ids,
                    prediction_as_of,
                )
                if event_result["match_id"] is not None:
                    sharp_by_match[event_result["match_id"]] = {
                        "requested": True,
                        "reconciliation_status": event_result["reconciliation_status"],
                        "pinnacle_event_id": event_result["event_id"],
                        "player_1_price": str(event.home_decimal_price),
                        "player_2_price": str(event.away_decimal_price),
                    }

    # 4. assemble summary truth.
    if build_match_entries is None:
        build_match_entries = _build_match_entries
    matches = build_match_entries(
        conn,
        target_matches,
        prediction_run_id,
        consensus_match_ids,
        sharp_by_match,
    )

    summary = build_live_summary(
        run_id=run_id,
        slate_date=slate_date.isoformat(),
        prediction_as_of=prediction_as_of,
        fixture_count=len(fixtures),
        fixture_reconciliation=fixture_reconciliation,
        target_count=len(target_match_ids),
        manager_consensus_count=len(consensus_match_ids),
        pinnapi_requests=pinnapi_requests,
        provider_lineage=provider_lineage,
        matches=matches,
    )
    summary_path = write_summary(summary, artifacts_dir=artifacts_dir, run_id=run_id)

    status = _final_status(target_match_ids, consensus_match_ids)
    return LiveMvpResult(
        status=status,
        run_id=run_id,
        slate_date=slate_date.isoformat(),
        prediction_as_of=prediction_as_of,
        prediction_run_id=prediction_run_id,
        fixture_count=len(fixtures),
        fixture_rows_seen=fixture_reconciliation["fixture_rows_seen"],
        fixture_rows_ingested=fixture_reconciliation["fixture_rows_ingested"],
        fixture_rows_rejected=fixture_reconciliation["fixture_rows_rejected"],
        fixture_rejection_reasons=tuple(
            fixture_reconciliation["fixture_rejection_reasons"].items()
        ),
        target_count=len(target_match_ids),
        manager_consensus_count=len(consensus_match_ids),
        pinnapi_requests=pinnapi_requests,
        matches=tuple(matches),
        summary_path=str(summary_path),
        notes=tuple(notes),
    )


def _build_match_entries(
    conn: psycopg.Connection,
    target_matches,
    prediction_run_id: int | None,
    consensus_match_ids: tuple[int, ...],
    sharp_by_match: dict[int, dict[str, Any]],
) -> list[dict[str, Any]]:
    if not target_matches:
        return []

    match_ids = [m.match_id for m in target_matches]
    base = _load_match_base(conn, match_ids)
    predictions = _load_predictions(conn, prediction_run_id)
    manager = _load_manager(conn, prediction_run_id)

    consensus_set = set(consensus_match_ids)
    entries: list[dict[str, Any]] = []
    for tm in target_matches:
        mid = tm.match_id
        info = base.get(mid, {})
        preds = predictions.get(mid, {})
        mgr = manager.get(mid, {})

        agent = {}
        for agent_code, display in (
            ("AGENT_1_SURFACE_ELO", "agent_1"),
            ("AGENT_2_HIERARCHICAL_MARKOV", "agent_2"),
            ("AGENT_3_INTRANSITIVITY", "agent_3"),
        ):
            p = preds.get(agent_code)
            if p is None:
                agent[display] = {"probability": None, "selection": None, "abstained": True}
            else:
                agent[display] = {
                    "probability": str(p["win_probability"]) if p["win_probability"] is not None else None,
                    "selection": p["selection_side"],
                    "abstained": p["prediction_status"] == "ABSTAINED",
                }

        sharp = sharp_by_match.get(mid, {"requested": mid in consensus_set})
        client_state = _client_state(mgr, sharp, mid in consensus_set)

        entries.append(
            {
                "match_id": mid,
                "event_key": info.get("event_key"),
                "tournament": info.get("tournament"),
                "competition_tier": info.get("competition_tier"),
                "scheduled_start": info.get("scheduled_start"),
                "player_1": info.get("player_1"),
                "player_2": info.get("player_2"),
                **agent,
                "manager": mgr,
                "sharp": sharp,
                "client_state": client_state,
            }
        )
    return entries


def _client_state(manager: dict[str, Any], sharp: dict[str, Any], is_consensus: bool) -> str:
    if manager.get("result") != "CONSENSUS":
        return "NO_BET_MODEL"
    if sharp.get("reconciliation_status") == "IDENTITY_CONFLICT":
        return "RECONCILIATION_FAILED"
    if sharp.get("reconciliation_status") == "RESOLVED":
        return "AWAITING_OFFERED_PRICE"
    return "SHARP_UNAVAILABLE"


def _load_match_base(conn: psycopg.Connection, match_ids: list[int]) -> dict[int, dict[str, Any]]:
    if not match_ids:
        return {}
    rows = conn.execute(
        """
        SELECT
            m.match_id,
            msi.source_match_id,
            t.canonical_name,
            mv.competition_tier,
            mv.match_start_at,
            p1.canonical_name,
            p2.canonical_name
        FROM matches m
        JOIN tournament_editions te ON te.tournament_edition_id = m.tournament_edition_id
        JOIN tournaments t ON t.tournament_id = te.tournament_id
        JOIN LATERAL (
            SELECT player_1_id, player_2_id, competition_tier, match_start_at
            FROM match_versions
            WHERE match_id = m.match_id
            ORDER BY as_of DESC, source_observed_at DESC, match_version_id DESC
            LIMIT 1
        ) mv ON TRUE
        JOIN players p1 ON p1.player_id = mv.player_1_id
        JOIN players p2 ON p2.player_id = mv.player_2_id
        LEFT JOIN LATERAL (
            SELECT source_match_id FROM match_source_ids
            WHERE match_id = m.match_id AND source_system = 'API_TENNIS'
            ORDER BY match_source_id LIMIT 1
        ) msi ON TRUE
        WHERE m.match_id = ANY(%s)
        """,
        (match_ids,),
    ).fetchall()
    out: dict[int, dict[str, Any]] = {}
    for row in rows:
        out[row[0]] = {
            "event_key": row[1],
            "tournament": row[2],
            "competition_tier": row[3],
            "scheduled_start": row[4].isoformat() if row[4] is not None else None,
            "player_1": row[5],
            "player_2": row[6],
        }
    return out


def _load_predictions(conn: psycopg.Connection, prediction_run_id: int | None) -> dict[int, dict[str, dict]]:
    if prediction_run_id is None:
        return {}
    rows = conn.execute(
        """
        SELECT match_id, agent_code::text, prediction_status::text, selection_side, win_probability
        FROM predictions
        WHERE prediction_run_id = %s
        """,
        (prediction_run_id,),
    ).fetchall()
    out: dict[int, dict[str, dict]] = {}
    for row in rows:
        match_id, agent_code, status, side, prob = row
        out.setdefault(match_id, {})[agent_code] = {
            "prediction_status": status,
            "selection_side": side,
            "win_probability": prob,
        }
    return out


def _load_manager(conn: psycopg.Connection, prediction_run_id: int | None) -> dict[int, dict[str, Any]]:
    if prediction_run_id is None:
        return {}
    rows = conn.execute(
        """
        SELECT match_id, manager_status::text, selected_side, consensus_probability, reason_code::text
        FROM manager_consensus_results
        WHERE prediction_run_id = %s
        """,
        (prediction_run_id,),
    ).fetchall()
    out: dict[int, dict[str, Any]] = {}
    for row in rows:
        match_id, status, side, prob, reason = row
        out[match_id] = {
            "result": status,
            "selected_side": side,
            "consensus_probability": str(prob) if prob is not None else None,
            "reason": reason,
        }
    return out
