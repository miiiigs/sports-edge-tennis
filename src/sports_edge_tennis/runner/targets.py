from __future__ import annotations

from datetime import datetime

import psycopg

from sports_edge_tennis.data.normalize import normalize_person_lookup, tennis_data_aliases
from sports_edge_tennis.runner.contracts import (
    ResolvedTargetV1,
    TargetResolutionError,
)


def _has_non_api_source_identity(conn: psycopg.Connection, player_id: int) -> bool:
    """Return True when this canonical player already belongs to historical identity truth."""
    return (
        conn.execute(
            """
            SELECT 1
            FROM player_source_ids
            WHERE player_id = %s
              AND source_system <> 'API_TENNIS'
            LIMIT 1
            """,
            (player_id,),
        ).fetchone()
        is not None
    )


def _resolve_model_player_id(
    conn: psycopg.Connection,
    *,
    player_id: int,
    as_of: datetime,
) -> int:
    """Resolve a live-only API-Tennis player to one unambiguous historical identity.

    API-Tennis stable player IDs are authoritative for live fixture identity, but the
    restored historical corpus uses its own source IDs. Earlier live ingestion created
    a new canonical player when an API-Tennis ID was first seen, which disconnected the
    frozen predictive agents from that player's historical observations.

    This bridge is model-input-only: it does not rewrite append-only source mappings or
    live match identity. It only substitutes a historical player ID when the normalized
    TENNIS_DATA aliases resolve to exactly one non-API player with PIT-visible completed
    result truth. Zero or multiple candidates fail closed by retaining the live player
    ID.

    Historical Sackmann match timestamps may intentionally be unknown. Therefore model
    eligibility must follow the authoritative result-observation PIT boundary rather
    than requiring match_start_at to be populated.
    """
    if _has_non_api_source_identity(conn, player_id):
        return player_id

    row = conn.execute(
        "SELECT canonical_name FROM players WHERE player_id = %s",
        (player_id,),
    ).fetchone()
    if row is None:
        return player_id

    normalized_aliases = sorted(
        {
            normalize_person_lookup(alias)
            for alias in tennis_data_aliases(row[0])
            if alias and normalize_person_lookup(alias)
        }
    )
    if not normalized_aliases:
        return player_id

    candidates = conn.execute(
        """
        SELECT DISTINCT pa.player_id
        FROM player_aliases pa
        WHERE pa.source_system = 'TENNIS_DATA'
          AND pa.normalized_alias = ANY(%s)
          AND pa.player_id <> %s
          AND EXISTS (
              SELECT 1
              FROM player_source_ids psi
              WHERE psi.player_id = pa.player_id
                AND psi.source_system <> 'API_TENNIS'
          )
          AND EXISTS (
              SELECT 1
              FROM match_result_observations hist_mro
              JOIN match_versions hist_mv
                ON hist_mv.match_id = hist_mro.match_id
               AND hist_mv.source_observed_at <= hist_mro.source_observed_at
               AND hist_mv.as_of <= hist_mro.as_of
              WHERE (hist_mv.player_1_id = pa.player_id OR hist_mv.player_2_id = pa.player_id)
                AND hist_mro.outcome_status = 'COMPLETED'
                AND hist_mro.source_observed_at <= %s
                AND hist_mro.as_of <= %s
          )
        ORDER BY pa.player_id
        """,
        (normalized_aliases, player_id, as_of, as_of),
    ).fetchall()

    if len(candidates) != 1:
        return player_id
    return int(candidates[0][0])


def _model_identity_status(
    conn: psycopg.Connection,
    *,
    live_player_id: int,
    model_player_id: int,
) -> str:
    if model_player_id != live_player_id:
        return "BRIDGED"
    if _has_non_api_source_identity(conn, live_player_id):
        return "HISTORICAL"
    return "LIVE_UNRESOLVED"


def _model_identity_gap_diagnostic(
    conn: psycopg.Connection,
    *,
    player_id: int,
    as_of: datetime,
) -> tuple[str, int, int, tuple[int, ...]]:
    """Explain why the existing fail-closed live-to-history bridge did not resolve.

    This helper is observation only. It deliberately mirrors the aliases and PIT
    history conditions used by `_resolve_model_player_id` and never mutates identity.
    """
    row = conn.execute(
        "SELECT canonical_name FROM players WHERE player_id = %s",
        (player_id,),
    ).fetchone()
    if row is None:
        return "NO_PLAYER_ROW", 0, 0, ()

    normalized_aliases = sorted(
        {
            normalize_person_lookup(alias)
            for alias in tennis_data_aliases(row[0])
            if alias and normalize_person_lookup(alias)
        }
    )
    if not normalized_aliases:
        return "NO_NORMALIZED_ALIAS", 0, 0, ()

    alias_candidates = conn.execute(
        """
        SELECT DISTINCT pa.player_id
        FROM player_aliases pa
        WHERE pa.source_system = 'TENNIS_DATA'
          AND pa.normalized_alias = ANY(%s)
          AND pa.player_id <> %s
          AND EXISTS (
              SELECT 1
              FROM player_source_ids psi
              WHERE psi.player_id = pa.player_id
                AND psi.source_system <> 'API_TENNIS'
          )
        ORDER BY pa.player_id
        """,
        (normalized_aliases, player_id),
    ).fetchall()
    alias_ids = tuple(int(candidate[0]) for candidate in alias_candidates)
    if not alias_ids:
        return "NO_HISTORICAL_ALIAS", 0, 0, ()

    pit_candidates = conn.execute(
        """
        SELECT DISTINCT pa.player_id
        FROM player_aliases pa
        WHERE pa.source_system = 'TENNIS_DATA'
          AND pa.normalized_alias = ANY(%s)
          AND pa.player_id <> %s
          AND EXISTS (
              SELECT 1
              FROM player_source_ids psi
              WHERE psi.player_id = pa.player_id
                AND psi.source_system <> 'API_TENNIS'
          )
          AND EXISTS (
              SELECT 1
              FROM match_result_observations hist_mro
              JOIN match_versions hist_mv
                ON hist_mv.match_id = hist_mro.match_id
               AND hist_mv.source_observed_at <= hist_mro.source_observed_at
               AND hist_mv.as_of <= hist_mro.as_of
              WHERE (hist_mv.player_1_id = pa.player_id OR hist_mv.player_2_id = pa.player_id)
                AND hist_mro.outcome_status = 'COMPLETED'
                AND hist_mro.source_observed_at <= %s
                AND hist_mro.as_of <= %s
          )
        ORDER BY pa.player_id
        """,
        (normalized_aliases, player_id, as_of, as_of),
    ).fetchall()
    pit_ids = tuple(int(candidate[0]) for candidate in pit_candidates)
    if not pit_ids:
        return "ALIAS_WITHOUT_PIT_HISTORY", len(alias_ids), 0, ()
    if len(pit_ids) > 1:
        return "AMBIGUOUS_PIT_CANDIDATES", len(alias_ids), len(pit_ids), pit_ids
    return "UNEXPECTED_UNIQUE_PIT_CANDIDATE", len(alias_ids), 1, pit_ids


def _log_model_identity_gap(
    conn: psycopg.Connection,
    *,
    match_id: int,
    slot: str,
    player_id: int,
    as_of: datetime,
) -> None:
    reason, alias_count, pit_count, pit_ids = _model_identity_gap_diagnostic(
        conn,
        player_id=player_id,
        as_of=as_of,
    )
    print(
        "MODEL_IDENTITY_GAP "
        f"match_id={match_id} slot={slot} live_player_id={player_id} "
        f"reason={reason} alias_candidates={alias_count} pit_candidates={pit_count} "
        f"pit_candidate_ids={','.join(str(value) for value in pit_ids[:3]) or '-'} "
        "mapping_changed=0",
        flush=True,
    )


def _api_tennis_tournament_source_ids(
    conn: psycopg.Connection,
    *,
    tournament_id: int,
) -> tuple[str, ...]:
    """Return deterministic API-Tennis tournament ids for diagnostics only."""
    rows = conn.execute(
        """
        SELECT source_tournament_id
        FROM tournament_source_ids
        WHERE tournament_id = %s
          AND source_system = 'API_TENNIS'
        ORDER BY source_tournament_id
        """,
        (tournament_id,),
    ).fetchall()
    return tuple(dict.fromkeys(str(row[0]) for row in rows if row[0] is not None))


def _safe_log_text(value: object) -> str:
    return " ".join(str(value).replace("\n", " ").replace("\r", " ").split())[:120] or "-"


def _resolve_model_surface(
    conn: psycopg.Connection,
    *,
    tournament_id: int,
    canonical_name: str,
    tour_scope: str,
    season_year: int,
    live_surface: str | None,
    edition_surface: str | None,
    as_of: datetime,
) -> tuple[str | None, str]:
    """Resolve missing live surface from existing PIT-visible tournament history only.

    API-Tennis fixtures do not consistently carry court surface. We never invent one
    and do not make an extra provider call here. A missing match surface may use the
    already-enriched current edition surface, or one unique surface from the latest
    PIT-visible historical season for the same canonical tournament identity. Conflicts
    fail closed.
    """
    if live_surface is not None:
        return live_surface, "LIVE"
    if edition_surface is not None:
        return edition_surface, "CURRENT_EDITION"

    rows = conn.execute(
        """
        SELECT
            te_hist.season_year,
            hist_mv.surface::text
        FROM tournaments t_hist
        JOIN tournament_editions te_hist
          ON te_hist.tournament_id = t_hist.tournament_id
        JOIN matches m_hist
          ON m_hist.tournament_edition_id = te_hist.tournament_edition_id
        JOIN match_versions hist_mv
          ON hist_mv.match_id = m_hist.match_id
        WHERE hist_mv.surface IS NOT NULL
          AND hist_mv.source_observed_at <= %s
          AND hist_mv.as_of <= %s
          AND te_hist.season_year <= %s
          AND (
              t_hist.tournament_id = %s
              OR (
                  t_hist.canonical_name = %s
                  AND t_hist.tour_scope::text = %s
              )
          )
        ORDER BY te_hist.season_year DESC, hist_mv.as_of DESC, hist_mv.match_version_id DESC
        """,
        (as_of, as_of, season_year, tournament_id, canonical_name, tour_scope),
    ).fetchall()

    if not rows:
        return None, "UNRESOLVED"

    latest_historical_year = int(rows[0][0])
    surfaces = {str(row[1]) for row in rows if int(row[0]) == latest_historical_year}
    if len(surfaces) != 1:
        return None, "AMBIGUOUS_HISTORY"
    return next(iter(surfaces)), "HISTORICAL_TOURNAMENT"


def resolve_target(
    conn: psycopg.Connection,
    *,
    match_id: int,
    as_of: datetime,
) -> ResolvedTargetV1:
    """Resolve the single latest PIT-visible match version for one target.

    PIT rule: source_observed_at <= as_of AND as_of <= as_of. ingested_at is
    never used as a model cutoff.
    """
    row = conn.execute(
        """
        SELECT
            mv.match_id,
            mv.match_version_id,
            mv.player_1_id,
            mv.player_2_id,
            mv.surface::text,
            te.surface::text,
            mv.tour_scope::text,
            mv.competition_tier::text,
            mv.round_code,
            mv.best_of_sets,
            mv.qualifying_flag,
            mv.match_status::text,
            t.tournament_id,
            t.canonical_name,
            te.season_year
        FROM match_versions mv
        JOIN matches m ON m.match_id = mv.match_id
        JOIN tournament_editions te ON te.tournament_edition_id = m.tournament_edition_id
        JOIN tournaments t ON t.tournament_id = te.tournament_id
        WHERE mv.match_id = %s
          AND mv.source_observed_at <= %s
          AND mv.as_of <= %s
        ORDER BY mv.as_of DESC, mv.source_observed_at DESC, mv.match_version_id DESC
        LIMIT 1
        """,
        (match_id, as_of, as_of),
    ).fetchone()

    if row is None:
        raise TargetResolutionError(
            f"match {match_id} has no PIT-visible match_version at as_of={as_of}"
        )

    (
        match_id,
        match_version_id,
        player_1_id,
        player_2_id,
        surface,
        edition_surface,
        tour_scope,
        competition_tier,
        round_code,
        best_of_sets,
        qualifying_flag,
        match_status,
        tournament_id,
        canonical_name,
        season_year,
    ) = row

    if match_status != "SCHEDULED":
        raise TargetResolutionError(
            f"match {match_id} latest PIT-visible status is {match_status}, not SCHEDULED"
        )

    model_player_1_id = _resolve_model_player_id(conn, player_id=player_1_id, as_of=as_of)
    model_player_2_id = _resolve_model_player_id(conn, player_id=player_2_id, as_of=as_of)
    if model_player_1_id == model_player_2_id:
        # Ambiguous/colliding bridges must never collapse a match to one player.
        model_player_1_id = player_1_id
        model_player_2_id = player_2_id

    player_1_status = _model_identity_status(
        conn,
        live_player_id=player_1_id,
        model_player_id=model_player_1_id,
    )
    player_2_status = _model_identity_status(
        conn,
        live_player_id=player_2_id,
        model_player_id=model_player_2_id,
    )
    print(
        "MODEL_IDENTITY "
        f"match_id={match_id} "
        f"p1_live={player_1_id} p1_model={model_player_1_id} p1_status={player_1_status} "
        f"p2_live={player_2_id} p2_model={model_player_2_id} p2_status={player_2_status}",
        flush=True,
    )
    if player_1_status == "LIVE_UNRESOLVED":
        _log_model_identity_gap(
            conn,
            match_id=int(match_id),
            slot="P1",
            player_id=int(player_1_id),
            as_of=as_of,
        )
    if player_2_status == "LIVE_UNRESOLVED":
        _log_model_identity_gap(
            conn,
            match_id=int(match_id),
            slot="P2",
            player_id=int(player_2_id),
            as_of=as_of,
        )

    model_surface, surface_status = _resolve_model_surface(
        conn,
        tournament_id=int(tournament_id),
        canonical_name=canonical_name,
        tour_scope=tour_scope,
        season_year=int(season_year),
        live_surface=surface,
        edition_surface=edition_surface,
        as_of=as_of,
    )
    print(
        "MODEL_SURFACE "
        f"match_id={match_id} live={surface or '-'} edition={edition_surface or '-'} "
        f"model={model_surface or '-'} status={surface_status}",
        flush=True,
    )
    if model_surface is None:
        api_tennis_source_ids = _api_tennis_tournament_source_ids(
            conn,
            tournament_id=int(tournament_id),
        )
        print(
            "SURFACE_RESOLUTION_GAP "
            f"match_id={match_id} tournament_id={tournament_id} "
            f"tournament=\"{_safe_log_text(canonical_name).replace(chr(34), chr(39))}\" "
            f"tour_scope={tour_scope} season={season_year} "
            f"api_tennis_source_ids={','.join(api_tennis_source_ids[:3]) or '-'} "
            f"api_tennis_source_id_count={len(api_tennis_source_ids)} "
            f"fallback_status={surface_status}",
            flush=True,
        )

    from sports_edge_tennis.data.agent2_history import normalize_tournament_alias

    return ResolvedTargetV1(
        match_id=match_id,
        match_version_id=match_version_id,
        player_1_id=model_player_1_id,
        player_2_id=model_player_2_id,
        surface=model_surface,
        tour_scope=tour_scope,
        competition_tier=competition_tier,
        round_code=round_code,
        best_of_sets=int(best_of_sets),
        qualifying_flag=bool(qualifying_flag),
        match_status=match_status,
        canonical_tournament_name=canonical_name,
        normalized_tournament_alias=normalize_tournament_alias(canonical_name),
        season_year=int(season_year),
    )


def resolve_targets(
    conn: psycopg.Connection,
    *,
    match_ids: tuple[int, ...],
    as_of: datetime,
) -> list[ResolvedTargetV1]:
    """Resolve every target in request order, preserving caller ordering."""
    return [resolve_target(conn, match_id=match_id, as_of=as_of) for match_id in match_ids]
