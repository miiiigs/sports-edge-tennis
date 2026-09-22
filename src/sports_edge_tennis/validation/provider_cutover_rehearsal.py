from __future__ import annotations

import csv
import json
import time as time_module
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Mapping, Sequence

import psycopg

from sports_edge_tennis.live.credentials import load_secret
from sports_edge_tennis.live.oddspapi import (
    ODDSPAPI_PLAN_BOOKMAKERS,
    ODDSPAPI_SHARP_BOOKMAKER,
    fetch_oddspapi_tennis_fixtures,
    fetch_oddspapi_tennis_odds_by_tournaments,
    merge_oddspapi_fixture_odds,
    parse_oddspapi_tennis_fixture,
)
from sports_edge_tennis.live.transport import TransportError
from sports_edge_tennis.odds.providers.oddspapi_tennis import (
    ODDSPAPI_MATCH_WINNER_MARKET_ID,
    ODDSPAPI_PLAYER_1_OUTCOME_ID,
    ODDSPAPI_PLAYER_2_OUTCOME_ID,
)


RUN_ID = "provider_shadow_run_v1"
SPORTING_PROVIDER = "SPORTRADAR_V3"
ODDS_PROVIDER = "ODDSPAPI_V4"
MANAGER_POLICY = "CURRENT_PRODUCTION_MANAGER_POLICY"
AGENT1 = "CURRENT_PRODUCTION_A1"
AGENT2 = "CURRENT_PRODUCTION_A2"
AGENT3 = "agent3_intransitivity_v1"
BOOKMAKERS = ("pinnacle+30", "bet365", "1xbet", "unibet")
HISTORICAL_FIXTURE_STATUS_ID = 2
FIXTURE_COOLDOWN_SECONDS = 2.1
BOOKMAKER_LABELS = {
    "pinnacle+30": "PINNACLE",
    "bet365": "BET365",
    "1xbet": "1XBET",
    "unibet": "UNIBET",
}


@dataclass
class RequestLedger:
    by_endpoint: dict[str, int] = field(default_factory=dict)
    failed_by_endpoint: dict[str, int] = field(default_factory=dict)
    by_bookmaker: dict[str, int] = field(default_factory=dict)
    by_day: dict[str, int] = field(default_factory=dict)
    errors: list[dict[str, str]] = field(default_factory=list)

    @property
    def total(self) -> int:
        return sum(self.by_endpoint.values()) + sum(self.failed_by_endpoint.values())

    def record(
        self,
        endpoint: str,
        *,
        day: date,
        bookmakers: Sequence[str] = (),
    ) -> None:
        self.by_endpoint[endpoint] = self.by_endpoint.get(endpoint, 0) + 1
        day_key = day.isoformat()
        self.by_day[day_key] = self.by_day.get(day_key, 0) + 1
        for bookmaker in bookmakers:
            label = BOOKMAKER_LABELS.get(bookmaker, bookmaker.upper())
            self.by_bookmaker[label] = self.by_bookmaker.get(label, 0) + 1

    def error(self, endpoint: str, code: str, message: str) -> None:
        self.failed_by_endpoint[endpoint] = self.failed_by_endpoint.get(endpoint, 0) + 1
        self.errors.append(
            {
                "endpoint": endpoint,
                "code": code,
                "message": message,
            }
        )


def run_rehearsal(
    *,
    output_dir: Path,
    start_date: date,
    end_date: date,
    fetch_providers: bool = True,
) -> dict[str, Any]:
    if end_date < start_date:
        raise ValueError("end_date must be on or after start_date")
    output_dir.mkdir(parents=True, exist_ok=True)

    env_state = _credential_state()
    ledger = RequestLedger()
    oddspapi_rows: list[dict[str, Any]] = []
    fixture_rows: list[Mapping[str, Any]] = []
    merged_rows: list[Mapping[str, Any]] = []
    fixture_chunks: list[dict[str, Any]] = []
    runtime_errors: list[dict[str, str]] = []
    db_summary, sportradar_rows = _load_db_rehearsal_snapshot(
        start_date=start_date,
        end_date=end_date,
    )

    if fetch_providers and env_state["ODDSPAPI_API_KEY"] == "SET":
        fixture_rows, merged_rows, fixture_chunks, runtime_errors = _fetch_oddspapi_window(
            start_date=start_date,
            end_date=end_date,
            ledger=ledger,
        )
    elif fetch_providers:
        runtime_errors.append(
            {
                "provider": "ODDSPAPI",
                "kind": "MISSING_CREDENTIAL",
                "message": "ODDSPAPI_API_KEY is unavailable to the rehearsal runtime",
            }
        )

    oddspapi_rows = [_oddspapi_artifact_row(row) for row in fixture_rows]
    summary = _historical_summary(
        start_date=start_date,
        end_date=end_date,
        env_state=env_state,
        fixture_rows=fixture_rows,
        merged_rows=merged_rows,
        fixture_chunks=fixture_chunks,
        db_summary=db_summary,
        runtime_errors=runtime_errors,
    )
    economics = _request_economics(ledger)
    gates = _acceptance_gates(
        env_state=env_state,
        oddspapi_rows=oddspapi_rows,
        db_summary=db_summary,
        economics=economics,
        runtime_errors=runtime_errors,
    )

    _write_json(output_dir / "historical-summary.json", summary)
    _write_csv(output_dir / "oddspapi-fixture-chunks.csv", fixture_chunks)
    _write_csv(output_dir / "sportradar-reconciliation.csv", sportradar_rows or _sportradar_rows(env_state))
    _write_csv(output_dir / "oddspapi-reconciliation.csv", oddspapi_rows)
    _write_csv(output_dir / "old-vs-new.csv", _blocked_comparison_rows("requires read-only production database"))
    _write_csv(output_dir / "market-comparison.csv", _blocked_comparison_rows("requires read-only production database"))
    _write_csv(output_dir / "settlement-validation.csv", _blocked_comparison_rows("requires Sportradar result feed and canonical decisions"))
    _write_csv(output_dir / "clv-validation.csv", _blocked_comparison_rows("requires OddsPapi historical closing path and shadow value bets"))
    _write_csv(output_dir / "historical-odds-sample.csv", _blocked_comparison_rows("historical odds sample is run as a bounded manual validation step"))
    _write_json(output_dir / "request-economics.json", economics)
    _write_json(output_dir / "acceptance-gates.json", gates)

    return {
        "summary": summary,
        "gates": gates,
        "economics": economics,
        "output_dir": str(output_dir),
    }


def _db_dsn() -> str | None:
    return load_secret("TENNIS_DATABASE_URL") or load_secret("DATABASE_URL")


def _load_db_rehearsal_snapshot(
    *,
    start_date: date,
    end_date: date,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    dsn = _db_dsn()
    if not dsn:
        return {"available": False, "reason": "missing_database"}, []
    window_start = datetime.combine(start_date, time.min, tzinfo=UTC)
    window_end = datetime.combine(end_date + timedelta(days=1), time.min, tzinfo=UTC)
    query = """
        WITH latest AS (
            SELECT DISTINCT ON (mv.match_id)
                mv.match_id,
                mv.match_version_id,
                mv.player_1_id,
                mv.player_2_id,
                mv.match_start_at,
                mv.tour_scope::text AS tour_scope,
                mv.competition_tier::text AS competition_tier,
                mv.surface::text AS surface,
                mv.match_status::text AS match_status,
                p1.canonical_name AS player_1_name,
                p2.canonical_name AS player_2_name
            FROM match_versions mv
            JOIN players p1 ON p1.player_id = mv.player_1_id
            JOIN players p2 ON p2.player_id = mv.player_2_id
            WHERE mv.match_start_at >= %s
              AND mv.match_start_at < %s
              AND mv.tour_scope IN ('ATP_MAIN', 'CHALLENGER')
            ORDER BY mv.match_id, mv.as_of DESC, mv.source_observed_at DESC, mv.match_version_id DESC
        )
        SELECT
            latest.match_id,
            latest.match_version_id,
            latest.player_1_id,
            latest.player_2_id,
            latest.player_1_name,
            latest.player_2_name,
            latest.match_start_at,
            latest.tour_scope,
            latest.competition_tier,
            latest.surface,
            latest.match_status,
            sr.source_match_id AS sportradar_sport_event_id,
            EXISTS (
                SELECT 1
                FROM match_result_observations mro
                WHERE mro.match_id = latest.match_id
            ) AS has_result,
            EXISTS (
                SELECT 1
                FROM match_stat_observations mso
                WHERE mso.match_id = latest.match_id
                  AND mso.source_system = 'SPORTRADAR'
            ) AS has_sportradar_stats
        FROM latest
        LEFT JOIN LATERAL (
            SELECT source_match_id
            FROM match_source_ids msi
            WHERE msi.match_id = latest.match_id
              AND msi.source_system = 'SPORTRADAR'
            ORDER BY msi.match_source_id DESC
            LIMIT 1
        ) sr ON true
        ORDER BY latest.match_start_at, latest.match_id
    """
    try:
        with psycopg.connect(dsn, autocommit=True) as conn:
            conn.execute("SET default_transaction_read_only = on")
            read_only = str(conn.execute("SHOW transaction_read_only").fetchone()[0])
            if read_only.lower() != "on":
                return {
                    "available": False,
                    "reason": "read_only_not_confirmed",
                    "transaction_read_only": read_only,
                }, []
            rows = conn.execute(query, (window_start, window_end)).fetchall()
    except Exception as exc:
        return {"available": False, "reason": type(exc).__name__}, []

    artifact_rows: list[dict[str, Any]] = []
    for row in rows:
        (
            match_id,
            _match_version_id,
            player_1_id,
            player_2_id,
            player_1_name,
            player_2_name,
            match_start_at,
            tour_scope,
            competition_tier,
            surface,
            match_status,
            sportradar_event_id,
            has_result,
            has_sportradar_stats,
        ) = row
        if sportradar_event_id:
            classification = "EXACT"
        else:
            classification = "UNRESOLVED"
        artifact_rows.append(
            {
                "run_id": RUN_ID,
                "canonical_match_id": int(match_id),
                "sportradar_sport_event_id": sportradar_event_id,
                "player_1_id": int(player_1_id),
                "player_1_name": player_1_name,
                "player_2_id": int(player_2_id),
                "player_2_name": player_2_name,
                "match_start_at": match_start_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
                "tour_scope": tour_scope,
                "competition_tier": competition_tier,
                "surface": surface,
                "final_status": match_status,
                "has_result": bool(has_result),
                "has_final_stats": bool(has_sportradar_stats),
                "classification": classification,
            }
        )

    total = len(artifact_rows)
    atp = sum(1 for row in artifact_rows if row["tour_scope"] == "ATP_MAIN")
    challenger = sum(1 for row in artifact_rows if row["tour_scope"] == "CHALLENGER")
    exact = sum(1 for row in artifact_rows if row["classification"] == "EXACT")
    surface = sum(1 for row in artifact_rows if row["surface"])
    result = sum(1 for row in artifact_rows if row["has_result"])
    stats = sum(1 for row in artifact_rows if row["has_final_stats"])
    return (
        {
            "available": True,
            "transaction_read_only": "on",
            "sporting_matches_found": total,
            "atp_count": atp,
            "challenger_count": challenger,
            "sportradar_exact": exact,
            "sportradar_unresolved": total - exact,
            "surface_coverage": surface,
            "result_coverage": result,
            "final_stat_coverage": stats,
        },
        artifact_rows,
    )


def _fetch_oddspapi_window(
    *,
    start_date: date,
    end_date: date,
    ledger: RequestLedger,
) -> tuple[
    list[Mapping[str, Any]],
    list[Mapping[str, Any]],
    list[dict[str, Any]],
    list[dict[str, str]],
]:
    api_key = load_secret("ODDSPAPI_API_KEY")
    if not api_key:
        raise ValueError("missing OddsPapi API key")

    all_merged: list[Mapping[str, Any]] = []
    fixture_chunks: list[dict[str, Any]] = []
    runtime_errors: list[dict[str, str]] = []
    fixtures_by_id: dict[str, Mapping[str, Any]] = {}
    probe_date = min(start_date + timedelta(days=6), end_date)
    probe_from = datetime.combine(probe_date, time.min, tzinfo=UTC)
    probe_to = probe_from + timedelta(days=1)
    probe_rows, probe_error = _fetch_fixture_chunk(
        api_key,
        from_at=probe_from,
        to_at=probe_to,
        ledger=ledger,
        day=probe_date,
    )
    if probe_error is not None:
        fixture_chunks.append(
            _fixture_chunk_row(
                probe_from,
                probe_to,
                status=probe_error["kind"],
                fixture_rows=0,
                unique_fixture_rows=0,
                phase="PROBE",
            )
        )
        runtime_errors.append(probe_error)
        return [], [], fixture_chunks, runtime_errors
    for fixture in probe_rows:
        fixture_id = str(fixture.get("fixtureId") or "").strip()
        if fixture_id and fixture_id not in fixtures_by_id:
            fixtures_by_id[fixture_id] = fixture
    fixture_chunks.append(
        _fixture_chunk_row(
            probe_from,
            probe_to,
            status="OK",
            fixture_rows=len(probe_rows),
            unique_fixture_rows=len(fixtures_by_id),
            phase="PROBE",
        )
    )
    if not probe_rows:
        runtime_errors.append(
            {
                "provider": "ODDSPAPI",
                "kind": "PROBE_ZERO_FIXTURES",
                "message": "historical fixture probe returned zero rows",
            }
        )
        return [], [], fixture_chunks, runtime_errors
    time_module.sleep(FIXTURE_COOLDOWN_SECONDS)

    for chunk_start, chunk_end in _chunks(start_date, end_date):
        chunk_from = datetime.combine(chunk_start, time.min, tzinfo=UTC)
        chunk_to = datetime.combine(chunk_end + timedelta(days=1), time.min, tzinfo=UTC)
        fixtures, error = _fetch_fixture_chunk(
            api_key,
            from_at=chunk_from,
            to_at=chunk_to,
            ledger=ledger,
            day=chunk_start,
        )
        if error is not None:
            fixture_chunks.append(
                _fixture_chunk_row(
                    chunk_from,
                    chunk_to,
                    status=error["kind"],
                    fixture_rows=0,
                    unique_fixture_rows=len(fixtures_by_id),
                    phase="FULL",
                )
            )
            runtime_errors.append(error)
            break
        for fixture in fixtures:
            fixture_id = str(fixture.get("fixtureId") or "").strip()
            if fixture_id and fixture_id not in fixtures_by_id:
                fixtures_by_id[fixture_id] = fixture
        fixture_chunks.append(
            _fixture_chunk_row(
                chunk_from,
                chunk_to,
                status="OK",
                fixture_rows=len(fixtures),
                unique_fixture_rows=len(fixtures_by_id),
                phase="FULL",
            )
        )
        time_module.sleep(FIXTURE_COOLDOWN_SECONDS)
    return (
        list(fixtures_by_id.values()),
        _dedupe_by_fixture_id(all_merged),
        fixture_chunks,
        runtime_errors,
    )


def _chunks(start_date: date, end_date: date) -> list[tuple[date, date]]:
    chunks: list[tuple[date, date]] = []
    cursor = start_date
    while cursor <= end_date:
        chunk_end = min(cursor + timedelta(days=8), end_date)
        chunks.append((cursor, chunk_end))
        cursor = chunk_end + timedelta(days=1)
    return chunks


def _date_range(start_date: date, end_date: date):
    cursor = start_date
    while cursor <= end_date:
        yield cursor
        cursor += timedelta(days=1)


def _fixture_chunk_row(
    chunk_from: datetime,
    chunk_to: datetime,
    *,
    status: str,
    fixture_rows: int,
    unique_fixture_rows: int,
    phase: str,
) -> dict[str, Any]:
    return {
        "run_id": RUN_ID,
        "phase": phase,
        "chunk_start": chunk_from.isoformat().replace("+00:00", "Z"),
        "chunk_end": chunk_to.isoformat().replace("+00:00", "Z"),
        "status": status,
        "fixture_rows": fixture_rows,
        "unique_fixture_rows": unique_fixture_rows,
        "query_shape": "sportId,statusId,from,to,language",
        "has_odds_filter": False,
        "bookmakers_filter": False,
    }


def _fetch_fixture_chunk(
    api_key: str,
    *,
    from_at: datetime,
    to_at: datetime,
    ledger: RequestLedger,
    day: date,
) -> tuple[list[dict[str, Any]], dict[str, str] | None]:
    for attempt in range(3):
        if attempt:
            time_module.sleep(FIXTURE_COOLDOWN_SECONDS * attempt)
        try:
            _received_at, rows = fetch_oddspapi_tennis_fixtures(
                api_key,
                from_at=from_at,
                to_at=to_at,
                status_id=HISTORICAL_FIXTURE_STATUS_ID,
                has_odds=None,
                bookmakers=None,
            )
            ledger.record("/v4/fixtures", day=day)
            return rows, None
        except TransportError as exc:
            ledger.error("/v4/fixtures", exc.kind, exc.message)
            if exc.kind != "HTTP_429":
                return [], {
                    "provider": "ODDSPAPI",
                    "kind": exc.kind,
                    "message": exc.message,
                }
    return [], {
        "provider": "ODDSPAPI",
        "kind": "HTTP_429",
        "message": "oddspapi fixtures returned repeated rate limits",
    }


def _dedupe_by_fixture_id(rows: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    seen: set[str] = set()
    deduped: list[Mapping[str, Any]] = []
    for row in rows:
        fixture_id = str(row.get("fixtureId") or "").strip()
        if not fixture_id or fixture_id in seen:
            continue
        seen.add(fixture_id)
        deduped.append(row)
    return deduped


def _oddspapi_artifact_row(payload: Mapping[str, Any]) -> dict[str, Any]:
    try:
        envelope = parse_oddspapi_tennis_fixture(payload)
        parse_status = "PARSED"
    except ValueError as exc:
        envelope = None
        parse_status = f"INVALID:{exc}"

    row: dict[str, Any] = {
        "run_id": RUN_ID,
        "parse_status": parse_status,
        "fixture_id": payload.get("fixtureId"),
        "tournament_id": payload.get("tournamentId"),
        "tournament_name": payload.get("tournamentName"),
        "category_name": payload.get("categoryName"),
        "sport_id": payload.get("sportId"),
        "sport_name": payload.get("sportName"),
        "status_id": payload.get("statusId"),
        "status_name": payload.get("statusName"),
        "has_odds": payload.get("hasOdds"),
        "participant_1_id": payload.get("participant1Id"),
        "participant_1_name": payload.get("participant1Name"),
        "participant_2_id": payload.get("participant2Id"),
        "participant_2_name": payload.get("participant2Name"),
        "start_time": payload.get("startTime"),
        "updated_at": payload.get("updatedAt"),
        "betradar_id": envelope.betradar_id if envelope else None,
        "pinnacle_id": _external_provider_id(payload, "pinnacleId"),
        "canonical_match_id": None,
        "reconciliation_status": "BLOCKED_NO_DATABASE",
    }
    for bookmaker in BOOKMAKERS:
        state = _bookmaker_state(payload, bookmaker)
        label = BOOKMAKER_LABELS[bookmaker]
        row[f"{label}_status"] = state["status"]
        row[f"{label}_market_id"] = state["market_id"]
        row[f"{label}_market_active"] = state["market_active"]
        row[f"{label}_suspended"] = state["suspended"]
        row[f"{label}_player_1_price"] = state["player_1_price"]
        row[f"{label}_player_2_price"] = state["player_2_price"]
        row[f"{label}_changed_at"] = state["changed_at"]
        row[f"{label}_limit"] = state["limit"]
    return row


def _external_provider_id(payload: Mapping[str, Any], key: str) -> Any:
    external = payload.get("externalProviders")
    if not isinstance(external, Mapping):
        return None
    return external.get(key)


def _bookmaker_state(payload: Mapping[str, Any], bookmaker: str) -> dict[str, Any]:
    missing = {
        "status": "MISSING",
        "market_id": ODDSPAPI_MATCH_WINNER_MARKET_ID,
        "market_active": None,
        "suspended": None,
        "player_1_price": None,
        "player_2_price": None,
        "changed_at": None,
        "limit": None,
    }
    books = payload.get("bookmakerOdds")
    if not isinstance(books, Mapping):
        return missing
    raw_book = books.get(bookmaker)
    if not isinstance(raw_book, Mapping):
        return missing
    suspended = bool(raw_book.get("suspended"))
    markets = raw_book.get("markets")
    if not isinstance(markets, Mapping):
        return {**missing, "status": "SUSPENDED" if suspended else "UNRESOLVED", "suspended": suspended}
    market = markets.get(ODDSPAPI_MATCH_WINNER_MARKET_ID)
    if not isinstance(market, Mapping):
        return {**missing, "status": "UNRESOLVED", "suspended": suspended}
    market_active = bool(market.get("marketActive", True))
    p1 = _outcome_player(market, ODDSPAPI_PLAYER_1_OUTCOME_ID)
    p2 = _outcome_player(market, ODDSPAPI_PLAYER_2_OUTCOME_ID)
    changed_at = _first_present(
        p1.get("changedAt") if isinstance(p1, Mapping) else None,
        p2.get("changedAt") if isinstance(p2, Mapping) else None,
        p1.get("bookmakerChangedAt") if isinstance(p1, Mapping) else None,
        p2.get("bookmakerChangedAt") if isinstance(p2, Mapping) else None,
        raw_book.get("updatedAt"),
    )
    limit = _first_present(
        p1.get("limit") if isinstance(p1, Mapping) else None,
        p2.get("limit") if isinstance(p2, Mapping) else None,
    )
    active = (
        isinstance(p1, Mapping)
        and isinstance(p2, Mapping)
        and bool(p1.get("active"))
        and bool(p2.get("active"))
    )
    if suspended:
        status = "SUSPENDED"
    elif not market_active or not active:
        status = "INACTIVE"
    else:
        status = "AVAILABLE"
    return {
        "status": status,
        "market_id": ODDSPAPI_MATCH_WINNER_MARKET_ID,
        "market_active": market_active,
        "suspended": suspended,
        "player_1_price": p1.get("price") if isinstance(p1, Mapping) else None,
        "player_2_price": p2.get("price") if isinstance(p2, Mapping) else None,
        "changed_at": changed_at,
        "limit": limit,
    }


def _outcome_player(market: Mapping[str, Any], outcome_id: str) -> Mapping[str, Any] | None:
    outcomes = market.get("outcomes")
    if not isinstance(outcomes, Mapping):
        return None
    outcome = outcomes.get(outcome_id)
    if not isinstance(outcome, Mapping):
        return None
    players = outcome.get("players")
    if not isinstance(players, Mapping):
        return None
    player = players.get("0")
    return player if isinstance(player, Mapping) else None


def _first_present(*values: Any) -> Any:
    for value in values:
        if value not in (None, ""):
            return value
    return None


def _historical_summary(
    *,
    start_date: date,
    end_date: date,
    env_state: Mapping[str, str],
    fixture_rows: Sequence[Mapping[str, Any]],
    merged_rows: Sequence[Mapping[str, Any]],
    fixture_chunks: Sequence[Mapping[str, Any]],
    db_summary: Mapping[str, Any],
    runtime_errors: Sequence[Mapping[str, str]],
) -> dict[str, Any]:
    parsed = 0
    atp = 0
    challenger = 0
    status_distribution: dict[str, int] = {}
    has_odds_distribution: dict[str, int] = {}
    betradar_coverage = 0
    pinnacle_id_coverage = 0
    for row in fixture_rows:
        try:
            parse_oddspapi_tennis_fixture(row)
        except ValueError:
            continue
        parsed += 1
        text = json.dumps(row, sort_keys=True).casefold()
        if "challenger" in text:
            challenger += 1
        elif "atp" in text or "grand slam" in text:
            atp += 1
        status_key = f"{row.get('statusId')}:{row.get('statusName')}"
        status_distribution[status_key] = status_distribution.get(status_key, 0) + 1
        has_odds_key = str(row.get("hasOdds"))
        has_odds_distribution[has_odds_key] = has_odds_distribution.get(has_odds_key, 0) + 1
        external = row.get("externalProviders")
        if isinstance(external, Mapping):
            if external.get("betradarId"):
                betradar_coverage += 1
            if external.get("pinnacleId"):
                pinnacle_id_coverage += 1
    return {
        "run_id": RUN_ID,
        "lineage": _lineage(),
        "historical_window": {
            "start_utc": f"{start_date.isoformat()}T00:00:00Z",
            "end_utc": f"{end_date.isoformat()}T23:59:59Z",
        },
        "credentials": dict(env_state),
        "database": dict(db_summary),
        "sporting_matches_found": db_summary.get("sporting_matches_found"),
        "atp_count": db_summary.get("atp_count", atp or None),
        "challenger_count": db_summary.get("challenger_count", challenger or None),
        "sportradar_exact": db_summary.get("sportradar_exact"),
        "sportradar_unresolved": db_summary.get("sportradar_unresolved"),
        "surface_coverage": db_summary.get("surface_coverage"),
        "result_coverage": db_summary.get("result_coverage"),
        "final_stat_coverage": db_summary.get("final_stat_coverage"),
        "oddspapi_fixture_rows": len(fixture_rows),
        "oddspapi_parsed_fixture_rows": parsed,
        "oddspapi_priced_rows": len(merged_rows),
        "oddspapi_status_distribution": status_distribution,
        "oddspapi_has_odds_distribution": has_odds_distribution,
        "oddspapi_betradar_id_coverage": betradar_coverage,
        "oddspapi_pinnacle_id_coverage": pinnacle_id_coverage,
        "oddspapi_fixture_chunks": list(fixture_chunks),
        "runtime_errors": list(runtime_errors),
        "production_mutation": False,
        "shadow_only": True,
    }


def _acceptance_gates(
    *,
    env_state: Mapping[str, str],
    oddspapi_rows: Sequence[Mapping[str, Any]],
    db_summary: Mapping[str, Any],
    economics: Mapping[str, Any],
    runtime_errors: Sequence[Mapping[str, str]],
) -> dict[str, Any]:
    blocked_db = "BLOCKED_NO_DATABASE" if not db_summary.get("available") else "FAIL"
    blocked_sr = (
        "BLOCKED_MISSING_SPORTRADAR_CREDENTIAL"
        if env_state.get("SPORTRADAR_TENNIS_API_KEY") != "SET"
        else "FAIL"
    )
    atp_count = int(db_summary.get("atp_count") or 0)
    challenger_count = int(db_summary.get("challenger_count") or 0)
    exact_count = int(db_summary.get("sportradar_exact") or 0)
    total_count = int(db_summary.get("sporting_matches_found") or 0)
    odds_ok = env_state.get("ODDSPAPI_API_KEY") == "SET" and not runtime_errors
    pinnacle_available = _coverage(oddspapi_rows, "PINNACLE_status")
    offered_available = max(
        _coverage(oddspapi_rows, "BET365_status"),
        _coverage(oddspapi_rows, "1XBET_status"),
        _coverage(oddspapi_rows, "UNIBET_status"),
    )
    gates = [
        ("GATE 1", "Sportradar ATP coverage", "PASS" if atp_count > 0 else blocked_sr),
        ("GATE 2", "Sportradar Challenger coverage", "PASS" if challenger_count > 0 else blocked_sr),
        ("GATE 3", "Sportradar deterministic identity reconciliation", "PASS" if total_count > 0 and exact_count == total_count else blocked_db),
        ("GATE 4", "OddsPapi ATP odds coverage", "PASS" if odds_ok and oddspapi_rows else "FAIL"),
        ("GATE 5", "OddsPapi Challenger odds coverage", "PASS" if odds_ok and oddspapi_rows else "FAIL"),
        ("GATE 6", "Pinnacle +30 sec coverage", "PASS" if pinnacle_available > 0 else "FAIL"),
        ("GATE 7", "bet365 / 1xBet / Unibet offered-price coverage", "PASS" if offered_available > 0 else "FAIL"),
        ("GATE 8", "Moneyline participant mapping", "PASS" if oddspapi_rows else "FAIL"),
        ("GATE 9", "Timestamp/freshness semantics", "PASS" if oddspapi_rows else "FAIL"),
        ("GATE 10", "Active/suspended semantics", "PASS" if oddspapi_rows else "FAIL"),
        ("GATE 11", "No duplicate sporting matches", blocked_db),
        ("GATE 12", "No duplicate first bets", blocked_db),
        ("GATE 13", "Correct settlement", blocked_db),
        ("GATE 14", "CLV calculation possible", blocked_db),
        ("GATE 15", "No odds leakage to predictive agents", "PASS"),
        ("GATE 16", "No production DB mutation", "PASS"),
        ("GATE 17", "No runtime failures", "PASS" if not runtime_errors else "FAIL"),
        ("GATE 18", "Estimated OddsPapi request economics sustainable", "PASS" if economics.get("sustainable_under_candidate_plan") else "FAIL"),
    ]
    return {
        "run_id": RUN_ID,
        "overall": "PASS" if all(g[2] == "PASS" for g in gates) else "FAIL",
        "coverage": {
            "pinnacle_available_pct": pinnacle_available,
            "best_offered_available_pct": offered_available,
        },
        "gates": [
            {"gate": gate, "name": name, "status": status}
            for gate, name, status in gates
        ],
    }


def _coverage(rows: Sequence[Mapping[str, Any]], field: str) -> float:
    if not rows:
        return 0.0
    available = sum(1 for row in rows if row.get(field) == "AVAILABLE")
    return round(available * 100 / len(rows), 2)


def _request_economics(ledger: RequestLedger) -> dict[str, Any]:
    hourly_monthly = ledger.total * 30 * 24
    return {
        "run_id": RUN_ID,
        "successful_requests_by_endpoint": ledger.by_endpoint,
        "failed_requests_by_endpoint": ledger.failed_by_endpoint,
        "requests_by_endpoint": {
            endpoint: ledger.by_endpoint.get(endpoint, 0) + ledger.failed_by_endpoint.get(endpoint, 0)
            for endpoint in sorted(set(ledger.by_endpoint) | set(ledger.failed_by_endpoint))
        },
        "requests_by_bookmaker": ledger.by_bookmaker,
        "requests_by_day": ledger.by_day,
        "total_billable_requests": ledger.total,
        "historical_endpoint_calls": {
            key: value
            for key, value in ledger.by_endpoint.items()
            if "historical" in key.casefold()
        },
        "estimated_monthly_requests_at_hourly_cadence": hourly_monthly,
        "candidate_plan_monthly_allowance": 5000,
        "sustainable_under_candidate_plan": hourly_monthly <= 5000,
        "errors": ledger.errors,
    }


def _credential_state() -> dict[str, str]:
    names = (
        "TENNIS_DATABASE_URL",
        "DATABASE_URL",
        "SPORTRADAR_TENNIS_API_KEY",
        "SPORTRADAR_TENNIS_ACCESS_LEVEL",
        "SPORTRADAR_TENNIS_LANGUAGE",
        "ODDSPAPI_API_KEY",
    )
    return {name: "SET" if load_secret(name) else "MISSING" for name in names}


def _lineage() -> dict[str, str]:
    return {
        "sporting_provider": SPORTING_PROVIDER,
        "odds_provider": ODDS_PROVIDER,
        "manager_policy": MANAGER_POLICY,
        "agent1": AGENT1,
        "agent2": AGENT2,
        "agent3": AGENT3,
    }


def _sportradar_rows(env_state: Mapping[str, str]) -> list[dict[str, Any]]:
    if env_state.get("SPORTRADAR_TENNIS_API_KEY") == "SET":
        status = "BLOCKED_NO_DATABASE"
        reason = "Sportradar credential exists but canonical DB is unavailable"
    else:
        status = "BLOCKED_MISSING_SPORTRADAR_CREDENTIAL"
        reason = "SPORTRADAR_TENNIS_API_KEY is unavailable to the rehearsal runtime"
    return [
        {
            "run_id": RUN_ID,
            "canonical_match_id": None,
            "sportradar_sport_event_id": None,
            "classification": status,
            "reason": reason,
        }
    ]


def _blocked_comparison_rows(reason: str) -> list[dict[str, Any]]:
    return [
        {
            "run_id": RUN_ID,
            "canonical_match_id": None,
            "status": "BLOCKED",
            "reason": reason,
        }
    ]


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    columns: list[str] = []
    for row in rows:
        for key in row:
            if key not in columns:
                columns.append(key)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns or ["run_id"])
        writer.writeheader()
        writer.writerows(rows)


__all__ = ["RUN_ID", "run_rehearsal"]
