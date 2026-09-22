from __future__ import annotations

import csv
import json
import time as time_module
from collections import Counter, defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any

import psycopg

from sports_edge_tennis.live.credentials import load_secret
from sports_edge_tennis.live.oddspapi import (
    fetch_oddspapi_tennis_fixtures,
    parse_oddspapi_tennis_fixture,
)
from sports_edge_tennis.live.sportradar import (
    SportradarTransportError,
    fetch_daily_summaries,
)
from sports_edge_tennis.live.sportradar_benchmark import (
    DEFAULT_LINK_MAX_START_DELTA_MINUTES,
    CanonicalGapTarget,
    GapLinkResult,
    link_gap_target,
)
from sports_edge_tennis.live.sportradar_fixtures import SportradarFixtureError, parse_summary
from sports_edge_tennis.live.transport import TransportError


RUN_ID = "sportradar_canonical_link_audit_v1"
SOURCE_SYSTEM = "SPORTRADAR"
ODDSPAPI_STATUS_FINISHED = 2
ODDSPAPI_FIXTURE_COOLDOWN_SECONDS = 2.1


@dataclass(frozen=True)
class CanonicalAuditTarget:
    target: CanonicalGapTarget
    tour_scope: str
    competition_tier: str
    canonical_surface: str | None
    canonical_status: str | None
    has_result: bool


def event_id_variants(event_id: str | None) -> frozenset[str]:
    text = str(event_id or "").strip()
    if not text:
        return frozenset()
    values = {text}
    prefix = "sr:sport_event:"
    if text.startswith(prefix):
        suffix = text.removeprefix(prefix)
        if suffix:
            values.add(suffix)
    elif text.isdigit():
        values.add(f"{prefix}{text}")
    return frozenset(values)


def event_id_key(event_id: str | None) -> str:
    variants = event_id_variants(event_id)
    for value in variants:
        if value.isdigit():
            return value
    return sorted(variants)[0] if variants else ""


def delta_bucket(delta: int | None) -> str:
    if delta is None:
        return "missing"
    if delta == 0:
        return "0"
    if delta <= 15:
        return "1-15"
    if delta <= 30:
        return "16-30"
    if delta <= 60:
        return "31-60"
    if delta <= 120:
        return "61-120"
    if delta <= 180:
        return "121-180"
    return ">180"


def classify_oddspapi_crosscheck(
    *,
    event_id: str | None,
    oddspapi_by_betradar: Mapping[str, Sequence[Mapping[str, Any]]],
) -> tuple[str, Mapping[str, Any] | None]:
    key = event_id_key(event_id)
    if not key:
        return "SPORTRADAR_ONLY", None
    rows = list(oddspapi_by_betradar.get(key, ()))
    if len(rows) == 1:
        return "TRIPLE_EXACT", rows[0]
    if len(rows) > 1:
        return "BETRADAR_CONFLICT", rows[0]
    return "SPORTRADAR_ONLY", None


def summarize_player_bindings(rows: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    unique: set[tuple[int, str]] = set()
    existing = 0
    candidates = 0
    conflicts = 0
    ambiguous = 0
    source_to_players: dict[str, set[int]] = defaultdict(set)
    for row in rows:
        for side in ("player_1", "player_2"):
            player_id = row.get(f"canonical_{side}_id", row.get(f"{side}_canonical_id"))
            source_id = row.get(f"{side}_sportradar_id")
            if player_id in (None, "") or not source_id:
                continue
            binding = (int(player_id), str(source_id))
            if binding in unique:
                continue
            unique.add(binding)
            source_to_players[str(source_id)].add(int(player_id))
            status = row.get(f"{side}_binding")
            if status == "EXISTING":
                existing += 1
            elif status == "CANDIDATE_NEW":
                candidates += 1
            elif status == "CONFLICT":
                conflicts += 1
            elif status == "AMBIGUOUS":
                ambiguous += 1
    ambiguous += sum(1 for players in source_to_players.values() if len(players) > 1)
    return {
        "unique_exact_player_bindings": len(unique),
        "existing_player_mappings": existing,
        "candidate_new_player_mappings": candidates,
        "identity_conflicts": conflicts,
        "ambiguous_player_bindings": ambiguous,
    }


def audit_sportradar_canonical_links(
    *,
    output_dir: Path,
    start_date: date,
    end_date: date,
    fetch_sportradar: Callable[..., tuple[datetime, dict[str, Any]]] = fetch_daily_summaries,
    fetch_oddspapi: Callable[..., tuple[datetime, list[dict[str, Any]]]] = fetch_oddspapi_tennis_fixtures,
    sleep: Callable[[float], None] = time_module.sleep,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    if end_date < start_date:
        raise ValueError("end_date must be on or after start_date")

    dsn = load_secret("TENNIS_DATABASE_URL") or load_secret("DATABASE_URL")
    if not dsn:
        raise RuntimeError("TENNIS_DATABASE_URL/DATABASE_URL is unavailable")
    api_key = load_secret("SPORTRADAR_TENNIS_API_KEY")
    if not api_key:
        raise RuntimeError("SPORTRADAR_TENNIS_API_KEY is unavailable")

    access_level = load_secret("SPORTRADAR_TENNIS_ACCESS_LEVEL") or "trial"
    language = load_secret("SPORTRADAR_TENNIS_LANGUAGE") or "en"

    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("SET default_transaction_read_only = on")
        read_only = str(conn.execute("SHOW transaction_read_only").fetchone()[0])
        if read_only.lower() != "on":
            raise RuntimeError(f"transaction_read_only was {read_only!r}")
        targets = load_canonical_targets(conn, start_date=start_date, end_date=end_date)
        existing_players = load_existing_player_source_ids(conn)
        existing_matches = load_existing_match_source_ids(conn)

    daily_payloads: dict[date, Mapping[str, Any]] = {}
    sportradar_errors: list[dict[str, str]] = []
    for slate_date in _date_range(start_date, end_date):
        try:
            _received_at, payload = fetch_sportradar(
                api_key,
                slate_date,
                access_level=access_level,
                language=language,
            )
            daily_payloads[slate_date] = payload
        except SportradarTransportError as exc:
            sportradar_errors.append(
                {"date": slate_date.isoformat(), "kind": exc.kind, "message": exc.message}
            )

    oddspapi_rows, oddspapi_errors, oddspapi_calls = fetch_oddspapi_fixtures_for_window(
        start_date=start_date,
        end_date=end_date,
        fetch_oddspapi=fetch_oddspapi,
        sleep=sleep,
    )
    oddspapi_by_betradar = oddspapi_betradar_index(oddspapi_rows)

    artifact_rows: list[dict[str, Any]] = []
    for target in targets:
        payload = daily_payloads.get(target.target.match_start_at.astimezone(UTC).date(), {})
        result = link_gap_target(target.target, payload)
        row = artifact_row(
            target=target,
            result=result,
            payload=payload,
            existing_players=existing_players,
            existing_matches=existing_matches,
            oddspapi_by_betradar=oddspapi_by_betradar,
        )
        artifact_rows.append(row)

    summary = build_summary(
        read_only=read_only,
        start_date=start_date,
        end_date=end_date,
        targets=targets,
        rows=artifact_rows,
        sportradar_calls=len(daily_payloads),
        sportradar_errors=sportradar_errors,
        oddspapi_calls=oddspapi_calls,
        oddspapi_errors=oddspapi_errors,
        oddspapi_rows=len(oddspapi_rows),
    )

    write_csv(output_dir / "sportradar-canonical-link-audit.csv", artifact_rows)
    write_json(output_dir / "sportradar-canonical-link-summary.json", summary)
    return summary


def load_canonical_targets(
    conn: psycopg.Connection,
    *,
    start_date: date,
    end_date: date,
) -> tuple[CanonicalAuditTarget, ...]:
    window_start = datetime.combine(start_date, time.min, tzinfo=UTC)
    window_end = datetime.combine(end_date + timedelta(days=1), time.min, tzinfo=UTC)
    rows = conn.execute(
        """
        WITH latest AS (
            SELECT DISTINCT ON (mv.match_id)
                mv.match_id,
                mv.player_1_id,
                mv.player_2_id,
                mv.match_start_at,
                mv.tour_scope::text AS tour_scope,
                mv.competition_tier::text AS competition_tier,
                mv.surface::text AS match_surface,
                mv.match_status::text AS match_status,
                p1.canonical_name AS player_1_name,
                p2.canonical_name AS player_2_name,
                t.tournament_id,
                t.canonical_name AS tournament_name,
                te.season_year,
                te.surface::text AS edition_surface
            FROM match_versions mv
            JOIN players p1 ON p1.player_id = mv.player_1_id
            JOIN players p2 ON p2.player_id = mv.player_2_id
            JOIN matches m ON m.match_id = mv.match_id
            JOIN tournament_editions te ON te.tournament_edition_id = m.tournament_edition_id
            JOIN tournaments t ON t.tournament_id = te.tournament_id
            WHERE mv.match_start_at >= %s
              AND mv.match_start_at < %s
              AND mv.tour_scope IN ('ATP_MAIN', 'CHALLENGER')
            ORDER BY mv.match_id, mv.as_of DESC, mv.source_observed_at DESC, mv.match_version_id DESC
        )
        SELECT
            latest.match_id,
            latest.player_1_id,
            latest.player_1_name,
            latest.player_2_id,
            latest.player_2_name,
            latest.match_start_at,
            latest.tour_scope,
            latest.competition_tier,
            latest.match_surface,
            latest.edition_surface,
            latest.match_status,
            latest.tournament_id,
            latest.tournament_name,
            latest.season_year,
            EXISTS (
                SELECT 1
                FROM match_result_observations mro
                WHERE mro.match_id = latest.match_id
            ) AS has_result
        FROM latest
        ORDER BY latest.match_start_at, latest.match_id
        """,
        (window_start, window_end),
    ).fetchall()
    targets: list[CanonicalAuditTarget] = []
    for row in rows:
        (
            match_id,
            player_1_id,
            player_1_name,
            player_2_id,
            player_2_name,
            match_start_at,
            tour_scope,
            competition_tier,
            match_surface,
            edition_surface,
            match_status,
            tournament_id,
            tournament_name,
            season_year,
            has_result,
        ) = row
        canonical_surface = match_surface or edition_surface
        target = CanonicalGapTarget(
            match_id=int(match_id),
            prediction_as_of=match_start_at,
            player_1_id=int(player_1_id),
            player_1_name=str(player_1_name),
            player_2_id=int(player_2_id),
            player_2_name=str(player_2_name),
            match_start_at=match_start_at,
            tournament_id=int(tournament_id),
            tournament_name=str(tournament_name),
            season_year=int(season_year),
            player_1_identity_status="AUDIT",
            player_2_identity_status="AUDIT",
            surface_status="KNOWN" if canonical_surface else "MISSING",
        )
        targets.append(
            CanonicalAuditTarget(
                target=target,
                tour_scope=str(tour_scope),
                competition_tier=str(competition_tier),
                canonical_surface=str(canonical_surface) if canonical_surface else None,
                canonical_status=str(match_status) if match_status else None,
                has_result=bool(has_result),
            )
        )
    return tuple(targets)


def load_existing_player_source_ids(conn: psycopg.Connection) -> dict[str, int]:
    rows = conn.execute(
        """
        SELECT source_player_id, player_id
        FROM player_source_ids
        WHERE source_system = %s
          AND source_player_id IS NOT NULL
        """,
        (SOURCE_SYSTEM,),
    ).fetchall()
    return {str(source_id): int(player_id) for source_id, player_id in rows}


def load_existing_match_source_ids(conn: psycopg.Connection) -> dict[str, int]:
    rows = conn.execute(
        """
        SELECT source_match_id, match_id
        FROM match_source_ids
        WHERE source_system = %s
          AND source_match_id IS NOT NULL
        """,
        (SOURCE_SYSTEM,),
    ).fetchall()
    result: dict[str, int] = {}
    for source_id, match_id in rows:
        for variant in event_id_variants(str(source_id)):
            result[variant] = int(match_id)
    return result


def fetch_oddspapi_fixtures_for_window(
    *,
    start_date: date,
    end_date: date,
    fetch_oddspapi: Callable[..., tuple[datetime, list[dict[str, Any]]]],
    sleep: Callable[[float], None],
) -> tuple[list[Mapping[str, Any]], list[dict[str, str]], int]:
    api_key = load_secret("ODDSPAPI_API_KEY")
    if not api_key:
        return [], [{"kind": "MISSING_CREDENTIAL", "message": "ODDSPAPI_API_KEY missing"}], 0
    rows_by_id: dict[str, Mapping[str, Any]] = {}
    errors: list[dict[str, str]] = []
    calls = 0
    for chunk_start, chunk_end in _chunks(start_date, end_date):
        from_at = datetime.combine(chunk_start, time.min, tzinfo=UTC)
        to_at = datetime.combine(chunk_end + timedelta(days=1), time.min, tzinfo=UTC)
        try:
            _received_at, rows = fetch_oddspapi(
                api_key,
                from_at=from_at,
                to_at=to_at,
                status_id=ODDSPAPI_STATUS_FINISHED,
                has_odds=None,
                bookmakers=None,
            )
            calls += 1
        except TransportError as exc:
            errors.append({"kind": exc.kind, "message": exc.message})
            break
        for row in rows:
            fixture_id = str(row.get("fixtureId") or "").strip()
            if fixture_id and fixture_id not in rows_by_id:
                rows_by_id[fixture_id] = row
        sleep(ODDSPAPI_FIXTURE_COOLDOWN_SECONDS)
    return list(rows_by_id.values()), errors, calls


def oddspapi_betradar_index(rows: Sequence[Mapping[str, Any]]) -> dict[str, list[Mapping[str, Any]]]:
    by_id: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        try:
            envelope = parse_oddspapi_tennis_fixture(row)
        except ValueError:
            continue
        key = event_id_key(envelope.betradar_id)
        if key:
            by_id[key].append(row)
    return dict(by_id)


def artifact_row(
    *,
    target: CanonicalAuditTarget,
    result: GapLinkResult,
    payload: Mapping[str, Any],
    existing_players: Mapping[str, int],
    existing_matches: Mapping[str, int],
    oddspapi_by_betradar: Mapping[str, Sequence[Mapping[str, Any]]],
) -> dict[str, Any]:
    base = {
        "run_id": RUN_ID,
        "canonical_match_id": target.target.match_id,
        "tour_scope": target.tour_scope,
        "competition_tier": target.competition_tier,
        "canonical_player_1_id": target.target.player_1_id,
        "canonical_player_1_name": target.target.player_1_name,
        "canonical_player_2_id": target.target.player_2_id,
        "canonical_player_2_name": target.target.player_2_name,
        "canonical_start_at": target.target.match_start_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "canonical_tournament_id": target.target.tournament_id,
        "canonical_tournament_name": target.target.tournament_name,
        "canonical_season_year": target.target.season_year,
        "canonical_surface": target.canonical_surface,
        "canonical_status": target.canonical_status,
        "canonical_has_result": target.has_result,
        "link_status": result.status,
        "candidate_count": len(result.candidates),
        "sportradar_event_id": None,
        "sportradar_competition_id": None,
        "sportradar_competition_name": None,
        "sportradar_start_at": None,
        "start_delta_minutes": None,
        "player_1_sportradar_id": None,
        "player_1_sportradar_name": None,
        "player_2_sportradar_id": None,
        "player_2_sportradar_name": None,
        "player_1_binding": None,
        "player_2_binding": None,
        "match_source_id_state": None,
        "oddspapi_crosscheck": None,
        "oddspapi_fixture_id": None,
        "oddspapi_betradar_id": None,
        "evidence_type": None,
        "sportradar_surface": None,
        "sportradar_status": None,
        "surface_gap_resolvable": False,
        "result_gap_resolvable": False,
    }
    if not result.candidates:
        return base
    candidate = result.candidates[0]
    summary = summary_by_event_id(payload).get(candidate.event_id)
    provider_surface = None
    provider_status = None
    if summary is not None:
        try:
            parsed = parse_summary(summary, received_at=datetime.now(UTC))
            provider_surface = parsed.surface
            provider_status = parsed.match_status
        except (SportradarFixtureError, ValueError):
            pass
    crosscheck, oddspapi = classify_oddspapi_crosscheck(
        event_id=candidate.event_id,
        oddspapi_by_betradar=oddspapi_by_betradar,
    )
    event_variants = event_id_variants(candidate.event_id)
    existing_match_ids = {existing_matches[value] for value in event_variants if value in existing_matches}
    if not existing_match_ids:
        match_state = "CANDIDATE_NEW"
    elif existing_match_ids == {target.target.match_id}:
        match_state = "EXISTING"
    else:
        match_state = "CONFLICT"
    base.update(
        {
            "sportradar_event_id": candidate.event_id,
            "sportradar_competition_id": candidate.competition_id,
            "sportradar_competition_name": candidate.competition_name,
            "sportradar_start_at": candidate.start_at.astimezone(UTC).isoformat().replace("+00:00", "Z") if candidate.start_at else None,
            "start_delta_minutes": candidate.start_delta_minutes,
            "player_1_sportradar_id": candidate.player_1_sportradar_id,
            "player_1_sportradar_name": candidate.player_1_sportradar_name,
            "player_2_sportradar_id": candidate.player_2_sportradar_id,
            "player_2_sportradar_name": candidate.player_2_sportradar_name,
            "player_1_binding": player_binding_state(
                player_id=target.target.player_1_id,
                source_player_id=candidate.player_1_sportradar_id,
                existing_players=existing_players,
            ),
            "player_2_binding": player_binding_state(
                player_id=target.target.player_2_id,
                source_player_id=candidate.player_2_sportradar_id,
                existing_players=existing_players,
            ),
            "match_source_id_state": match_state,
            "oddspapi_crosscheck": crosscheck,
            "oddspapi_fixture_id": oddspapi.get("fixtureId") if oddspapi else None,
            "oddspapi_betradar_id": _oddspapi_betradar_id(oddspapi) if oddspapi else None,
            "evidence_type": "TRIPLE_EXACT_BETRADAR" if crosscheck == "TRIPLE_EXACT" else "EXACT_SPORTRADAR_NAME_TIME",
            "sportradar_surface": provider_surface,
            "sportradar_status": provider_status,
            "surface_gap_resolvable": target.canonical_surface is None and provider_surface is not None,
            "result_gap_resolvable": not target.has_result and provider_status in {"COMPLETED", "RETIRED", "WALKOVER", "CANCELLED"},
        }
    )
    return base


def player_binding_state(
    *,
    player_id: int,
    source_player_id: str,
    existing_players: Mapping[str, int],
) -> str:
    existing = existing_players.get(source_player_id)
    if existing is None:
        return "CANDIDATE_NEW"
    if int(existing) == int(player_id):
        return "EXISTING"
    return "CONFLICT"


def summary_by_event_id(payload: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    rows: dict[str, Mapping[str, Any]] = {}
    summaries = payload.get("summaries")
    if not isinstance(summaries, Sequence) or isinstance(summaries, (str, bytes, bytearray)):
        return rows
    for summary in summaries:
        if not isinstance(summary, Mapping):
            continue
        event = summary.get("sport_event")
        if not isinstance(event, Mapping):
            continue
        event_id = str(event.get("id") or "").strip()
        if event_id:
            rows[event_id] = summary
    return rows


def build_summary(
    *,
    read_only: str,
    start_date: date,
    end_date: date,
    targets: Sequence[CanonicalAuditTarget],
    rows: Sequence[Mapping[str, Any]],
    sportradar_calls: int,
    sportradar_errors: Sequence[Mapping[str, str]],
    oddspapi_calls: int,
    oddspapi_errors: Sequence[Mapping[str, str]],
    oddspapi_rows: int,
) -> dict[str, Any]:
    statuses = Counter(str(row["link_status"]) for row in rows)
    exact_rows = [row for row in rows if row.get("link_status") == "EXACT"]
    atp_rows = [row for row in rows if row.get("tour_scope") == "ATP_MAIN"]
    challenger_rows = [row for row in rows if row.get("tour_scope") == "CHALLENGER"]
    exact_atp = sum(1 for row in atp_rows if row.get("link_status") == "EXACT")
    exact_challenger = sum(1 for row in challenger_rows if row.get("link_status") == "EXACT")
    crosschecks = Counter(str(row.get("oddspapi_crosscheck") or "NOT_EXACT") for row in rows)
    match_candidates = sum(1 for row in exact_rows if row.get("match_source_id_state") == "CANDIDATE_NEW")
    player_summary = summarize_player_bindings(exact_rows)
    delta_distribution = Counter(delta_bucket(row.get("start_delta_minutes")) for row in exact_rows)
    return {
        "run_id": RUN_ID,
        "historical_window": {
            "start_utc": f"{start_date.isoformat()}T00:00:00Z",
            "end_utc": f"{end_date.isoformat()}T23:59:59Z",
        },
        "database": {
            "transaction_read_only": read_only,
            "writes": 0,
        },
        "targets": {
            "total": len(targets),
            "atp": len(atp_rows),
            "challenger": len(challenger_rows),
        },
        "link_status_counts": dict(sorted(statuses.items())),
        "exact": {
            "count": len(exact_rows),
            "pct": _pct(len(exact_rows), len(rows)),
            "atp_count": exact_atp,
            "atp_pct": _pct(exact_atp, len(atp_rows)),
            "challenger_count": exact_challenger,
            "challenger_pct": _pct(exact_challenger, len(challenger_rows)),
        },
        "oddspapi": {
            "fixture_rows": oddspapi_rows,
            "fixture_calls": oddspapi_calls,
            "crosscheck_counts": dict(sorted(crosschecks.items())),
            "betradar_conflicts": crosschecks.get("BETRADAR_CONFLICT", 0),
            "errors": list(oddspapi_errors),
        },
        "sportradar": {
            "daily_summary_calls": sportradar_calls,
            "errors": list(sportradar_errors),
        },
        "player_identity": player_summary,
        "match_identity": {
            "candidate_match_source_ids": match_candidates,
            "event_identity_conflicts": sum(1 for row in exact_rows if row.get("match_source_id_state") == "CONFLICT"),
        },
        "surface": {
            "current_known": sum(1 for target in targets if target.canonical_surface is not None),
            "current_missing": sum(1 for target in targets if target.canonical_surface is None),
            "gaps_resolvable": sum(1 for row in exact_rows if row.get("surface_gap_resolvable")),
        },
        "result": {
            "current_known": sum(1 for target in targets if target.has_result),
            "current_missing": sum(1 for target in targets if not target.has_result),
            "gaps_resolvable": sum(1 for row in exact_rows if row.get("result_gap_resolvable")),
        },
        "start_delta_distribution": {
            bucket: delta_distribution.get(bucket, 0)
            for bucket in ("0", "1-15", "16-30", "31-60", "61-120", "121-180")
        },
        "runtime_errors": list(sportradar_errors) + list(oddspapi_errors),
        "production_mutation": False,
    }


def _oddspapi_betradar_id(row: Mapping[str, Any] | None) -> str | None:
    if row is None:
        return None
    try:
        return parse_oddspapi_tennis_fixture(row).betradar_id
    except ValueError:
        return None


def _pct(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(numerator * 100 / denominator, 2)


def _date_range(start_date: date, end_date: date):
    cursor = start_date
    while cursor <= end_date:
        yield cursor
        cursor += timedelta(days=1)


def _chunks(start_date: date, end_date: date) -> list[tuple[date, date]]:
    chunks: list[tuple[date, date]] = []
    cursor = start_date
    while cursor <= end_date:
        chunk_end = min(cursor + timedelta(days=8), end_date)
        chunks.append((cursor, chunk_end))
        cursor = chunk_end + timedelta(days=1)
    return chunks


def write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    columns: list[str] = []
    for row in rows:
        for key in row:
            if key not in columns:
                columns.append(key)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns or ["run_id"])
        writer.writeheader()
        writer.writerows(rows)


__all__ = [
    "RUN_ID",
    "audit_sportradar_canonical_links",
    "classify_oddspapi_crosscheck",
    "delta_bucket",
    "event_id_key",
    "event_id_variants",
    "player_binding_state",
    "summarize_player_bindings",
]
