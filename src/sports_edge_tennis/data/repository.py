from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal

import psycopg

from .contracts import IdentityResolution, MatchStatLine
from .cross_provider_reconciliation import (
    PROSPECTIVE_REUSE_SOURCE_SYSTEMS,
    REUSE_STATUS_AMBIGUOUS,
    REUSE_STATUS_REUSED,
    CrossProviderIdentityAmbiguous,
    SportingIdentity,
    resolve_cross_provider_canonical_reuse,
)
from .normalize import normalize_person_lookup, tennis_data_aliases
from .tournament_identity import resolve_canonical_tournament_name


@dataclass
class MatchLocator:
    match_id: int
    match_version_id: int
    player_1_id: int
    player_2_id: int


class HistoricalRepository:
    def __init__(self, conn: psycopg.Connection, ingest_run_id: int):
        self.conn = conn
        self.ingest_run_id = ingest_run_id

    def create_player_with_source_id(
        self,
        *,
        source_system: str,
        source_player_id: str,
        canonical_name: str,
        observed_at: datetime,
    ) -> int:
        existing = self.conn.execute(
            """
            SELECT player_id
            FROM player_source_ids
            WHERE source_system = %s AND source_player_id = %s
            """,
            (source_system, source_player_id),
        ).fetchone()
        if existing is not None:
            player_id = existing[0]
        else:
            player_id = self.conn.execute(
                """
                INSERT INTO players (canonical_name, primary_tour_scope, status)
                VALUES (%s, NULL, 'ACTIVE')
                RETURNING player_id
                """,
                (canonical_name,),
            ).fetchone()[0]
            self.conn.execute(
                """
                INSERT INTO player_source_ids (
                    player_id,
                    source_system,
                    source_player_id,
                    ingest_run_id
                )
                VALUES (%s, %s, %s, %s)
                """,
                (player_id, source_system, source_player_id, self.ingest_run_id),
            )

        self.ensure_player_alias(player_id, source_system, canonical_name, observed_at)
        for alias in tennis_data_aliases(canonical_name):
            self.ensure_player_alias(player_id, "TENNIS_DATA", alias, observed_at)
        return player_id

    def ensure_player_alias(
        self,
        player_id: int,
        source_system: str,
        alias: str,
        observed_at: datetime,
    ) -> None:
        normalized = normalize_person_lookup(alias)
        self.conn.execute(
            """
            INSERT INTO player_aliases (
                player_id,
                source_system,
                alias,
                normalized_alias,
                first_observed_at,
                last_observed_at,
                ingest_run_id
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (player_id, source_system, normalized_alias)
            DO UPDATE SET
                last_observed_at = GREATEST(player_aliases.last_observed_at, EXCLUDED.last_observed_at),
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                player_id,
                source_system,
                alias,
                normalized,
                observed_at,
                observed_at,
                self.ingest_run_id,
            ),
        )

    def resolve_player(
        self,
        *,
        source_system: str,
        raw_name: str,
        source_record_id: str,
        context_tournament_name: str | None,
        context_match_start_at: datetime | None,
        source_observed_at: datetime,
        as_of: datetime,
        stable_source_player_id: str | None = None,
        create_if_missing_with_stable_id: bool = False,
        require_stable_source_player_id: bool = False,
    ) -> IdentityResolution:
        if require_stable_source_player_id and not stable_source_player_id:
            self._ensure_identity_event(
                source_system=source_system,
                source_record_id=source_record_id,
                raw_name=raw_name,
                normalized_name=normalize_person_lookup(raw_name),
                resolution_status="UNMATCHED",
                resolved_player_id=None,
                context_tournament_name=context_tournament_name,
                context_match_start_at=context_match_start_at,
                source_observed_at=source_observed_at,
                as_of=as_of,
            )
            return IdentityResolution(status="UNMATCHED", player_id=None)

        if stable_source_player_id:
            row = self.conn.execute(
                """
                SELECT player_id
                FROM player_source_ids
                WHERE source_system = %s AND source_player_id = %s
                """,
                (source_system, stable_source_player_id),
            ).fetchone()
            if row is not None:
                return IdentityResolution(status="RESOLVED", player_id=row[0])
            if create_if_missing_with_stable_id:
                player_id = self.create_player_with_source_id(
                    source_system=source_system,
                    source_player_id=stable_source_player_id,
                    canonical_name=raw_name,
                    observed_at=source_observed_at,
                )
                return IdentityResolution(status="RESOLVED", player_id=player_id)

        normalized = normalize_person_lookup(raw_name)
        candidate_ids = {
            row[0]
            for row in self.conn.execute(
                """
                SELECT player_id
                FROM player_aliases
                WHERE source_system = %s AND normalized_alias = %s
                """,
                (source_system, normalized),
            ).fetchall()
        }
        if not candidate_ids:
            for player_id, canonical_name in self.conn.execute(
                "SELECT player_id, canonical_name FROM players"
            ).fetchall():
                if normalize_person_lookup(canonical_name) == normalized:
                    candidate_ids.add(player_id)

        if len(candidate_ids) == 1:
            return IdentityResolution(status="RESOLVED", player_id=next(iter(candidate_ids)))

        status = "UNMATCHED" if not candidate_ids else "AMBIGUOUS"
        event_id = self._ensure_identity_event(
            source_system=source_system,
            source_record_id=source_record_id,
            raw_name=raw_name,
            normalized_name=normalized,
            resolution_status=status,
            resolved_player_id=None,
            context_tournament_name=context_tournament_name,
            context_match_start_at=context_match_start_at,
            source_observed_at=source_observed_at,
            as_of=as_of,
        )
        if candidate_ids:
            self.conn.executemany(
                """
                INSERT INTO identity_resolution_candidates (
                    identity_resolution_event_id,
                    player_id
                )
                VALUES (%s, %s)
                ON CONFLICT DO NOTHING
                """,
                [(event_id, player_id) for player_id in sorted(candidate_ids)],
            )
        return IdentityResolution(
            status=status,
            player_id=None,
            candidate_player_ids=tuple(sorted(candidate_ids)),
        )

    def _ensure_identity_event(
        self,
        *,
        source_system: str,
        source_record_id: str,
        raw_name: str,
        normalized_name: str,
        resolution_status: str,
        resolved_player_id: int | None,
        context_tournament_name: str | None,
        context_match_start_at: datetime | None,
        source_observed_at: datetime,
        as_of: datetime,
    ) -> int:
        existing = self.conn.execute(
            """
            SELECT identity_resolution_event_id
            FROM identity_resolution_events
            WHERE source_system = %s
              AND source_record_id = %s
              AND raw_player_name = %s
              AND source_observed_at = %s
            """,
            (source_system, source_record_id, raw_name, source_observed_at),
        ).fetchone()
        if existing is not None:
            return existing[0]
        return self.conn.execute(
            """
            INSERT INTO identity_resolution_events (
                source_system,
                source_record_id,
                raw_player_name,
                normalized_player_name,
                resolution_status,
                resolved_player_id,
                context_tournament_name,
                context_match_start_at,
                source_observed_at,
                as_of,
                ingest_run_id
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING identity_resolution_event_id
            """,
            (
                source_system,
                source_record_id,
                raw_name,
                normalized_name,
                resolution_status,
                resolved_player_id,
                context_tournament_name,
                context_match_start_at,
                source_observed_at,
                as_of,
                self.ingest_run_id,
            ),
        ).fetchone()[0]

    def get_or_create_tournament(
        self,
        *,
        source_system: str,
        source_tournament_id: str | None,
        canonical_name: str,
        tour_scope: str,
        competition_tier: str,
    ) -> int:
        if source_tournament_id:
            row = self.conn.execute(
                """
                SELECT tournament_id
                FROM tournament_source_ids
                WHERE source_system = %s AND source_tournament_id = %s
                """,
                (source_system, source_tournament_id),
            ).fetchone()
            if row is not None:
                return row[0]

        # Deterministic canonical alias reuse (reviewed, exact-match only).
        # Prevents a provider display name (e.g. API-Tennis "Shanghai") from
        # creating a duplicate canonical tournament alongside the existing
        # canonical identity (e.g. "Shanghai Masters"). Fails closed on
        # ambiguity; zero candidates falls through to normal create.
        resolved_name = resolve_canonical_tournament_name(
            source_system, canonical_name, tour_scope
        )
        if resolved_name != canonical_name:
            candidates = self.conn.execute(
                """
                SELECT tournament_id
                FROM tournaments
                WHERE canonical_name = %s AND tour_scope = %s
                ORDER BY tournament_id
                """,
                (resolved_name, tour_scope),
            ).fetchall()
            if len(candidates) > 1:
                raise ValueError(
                    "ambiguous canonical tournament alias "
                    f"{canonical_name!r} -> {resolved_name!r} in {tour_scope!r}"
                )
            if len(candidates) == 1:
                tournament_id = candidates[0][0]
                if source_tournament_id:
                    self.conn.execute(
                        """
                        INSERT INTO tournament_source_ids (
                            tournament_id, source_system, source_tournament_id, ingest_run_id
                        )
                        VALUES (%s, %s, %s, %s)
                        ON CONFLICT DO NOTHING
                        """,
                        (
                            tournament_id,
                            source_system,
                            source_tournament_id,
                            self.ingest_run_id,
                        ),
                    )
                return tournament_id

        row = self.conn.execute(
            """
            SELECT tournament_id
            FROM tournaments
            WHERE canonical_name = %s
              AND tour_scope = %s
              AND competition_tier = %s
            """,
            (canonical_name, tour_scope, competition_tier),
        ).fetchone()
        if row is not None:
            tournament_id = row[0]
        else:
            tournament_id = self.conn.execute(
                """
                INSERT INTO tournaments (canonical_name, tour_scope, competition_tier)
                VALUES (%s, %s, %s)
                RETURNING tournament_id
                """,
                (canonical_name, tour_scope, competition_tier),
            ).fetchone()[0]

        if source_tournament_id:
            self.conn.execute(
                """
                INSERT INTO tournament_source_ids (
                    tournament_id,
                    source_system,
                    source_tournament_id,
                    ingest_run_id
                )
                VALUES (%s, %s, %s, %s)
                ON CONFLICT DO NOTHING
                """,
                (tournament_id, source_system, source_tournament_id, self.ingest_run_id),
            )
        return tournament_id

    def get_or_create_tournament_edition(
        self,
        *,
        tournament_id: int,
        season_year: int,
        edition_name: str,
        surface: str | None,
        start_date: date,
        end_date: date | None,
    ) -> int:
        row = self.conn.execute(
            """
            SELECT tournament_edition_id, surface, start_date, end_date
            FROM tournament_editions
            WHERE tournament_id = %s
              AND season_year = %s
              AND edition_name = %s
            """,
            (tournament_id, season_year, edition_name),
        ).fetchone()
        if row is not None:
            tournament_edition_id, current_surface, current_start_date, current_end_date = row
            next_surface = current_surface or surface
            next_start_date = current_start_date or start_date
            next_end_date = current_end_date
            if next_end_date is None or (end_date is not None and end_date > next_end_date):
                next_end_date = end_date
            if (
                next_surface != current_surface
                or next_start_date != current_start_date
                or next_end_date != current_end_date
            ):
                self.conn.execute(
                    """
                    UPDATE tournament_editions
                    SET surface = %s,
                        start_date = %s,
                        end_date = %s,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE tournament_edition_id = %s
                    """,
                    (next_surface, next_start_date, next_end_date, tournament_edition_id),
                )
            return tournament_edition_id
        return self.conn.execute(
            """
            INSERT INTO tournament_editions (
                tournament_id,
                season_year,
                edition_name,
                surface,
                start_date,
                end_date
            )
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING tournament_edition_id
            """,
            (tournament_id, season_year, edition_name, surface, start_date, end_date),
        ).fetchone()[0]

    def _resolve_canonical_reuse(
        self,
        *,
        tournament_edition_id: int,
        source_system: str,
        source_match_id: str,
        player_1_id: int,
        player_2_id: int,
        match_start_at: datetime | None,
        tour_scope: str,
        competition_tier: str,
    ) -> int | None:
        """Deterministic prospective canonical reuse for an unseen provider event.

        Returns the existing canonical ``match_id`` to reuse, ``None`` to create a
        new canonical match, or raises ``CrossProviderIdentityAmbiguous`` to fail
        closed when more than one safe candidate exists.
        """
        if match_start_at is None or player_1_id == player_2_id:
            print(
                "CROSS_PROVIDER_CANONICAL_CREATE "
                f"source={source_system} source_match_id={source_match_id} "
                "candidates=0 reason=IDENTITY_CONFLICT",
                flush=True,
            )
            return None

        edition = self.conn.execute(
            """
            SELECT tournament_id, season_year
            FROM tournament_editions
            WHERE tournament_edition_id = %s
            """,
            (tournament_edition_id,),
        ).fetchone()
        if edition is None:
            return None

        incoming = SportingIdentity(
            player_1_id=player_1_id,
            player_2_id=player_2_id,
            tournament_id=int(edition[0]),
            season_year=int(edition[1]),
            tour_scope=tour_scope,
            competition_tier=competition_tier,
            match_start_at=match_start_at,
        )
        resolution = resolve_cross_provider_canonical_reuse(
            self.conn, incoming=incoming
        )
        evidence = (
            f"source={source_system} source_match_id={source_match_id} "
            f"candidates={resolution.candidate_count}"
        )
        if resolution.status == REUSE_STATUS_REUSED:
            delta = resolution.start_delta_minutes
            print(
                "CROSS_PROVIDER_CANONICAL_REUSE "
                f"{evidence} canonical_match_id={resolution.match_id} "
                f"orientation={resolution.orientation} "
                f"start_delta_minutes={delta if delta is None else format(delta, 'g')}",
                flush=True,
            )
            return resolution.match_id
        if resolution.status == REUSE_STATUS_AMBIGUOUS:
            print(
                f"CROSS_PROVIDER_CANONICAL_BLOCKED reason=AMBIGUOUS {evidence}",
                flush=True,
            )
            raise CrossProviderIdentityAmbiguous(
                "ambiguous cross-provider canonical candidates "
                f"source={source_system} source_match_id={source_match_id}"
            )
        print(
            f"CROSS_PROVIDER_CANONICAL_CREATE reason={resolution.reason} {evidence}",
            flush=True,
        )
        return None

    def upsert_match_version(
        self,
        *,
        tournament_edition_id: int,
        source_system: str,
        source_match_id: str | None,
        source_record_hash: str,
        source_record_locator: str,
        source_file_name: str,
        source_version_hash: str,
        player_1_id: int,
        player_2_id: int,
        match_start_at: datetime | None,
        tour_scope: str,
        competition_tier: str,
        surface: str | None,
        round_code: str | None,
        best_of_sets: int,
        qualifying_flag: bool,
        match_status: str,
        source_observed_at: datetime,
        as_of: datetime,
    ) -> MatchLocator:
        match_row = None
        if source_match_id:
            match_row = self.conn.execute(
                """
                SELECT match_id
                FROM match_source_ids
                WHERE source_system = %s AND source_match_id = %s
                """,
                (source_system, source_match_id),
            ).fetchone()
        if match_row is None:
            match_row = self.conn.execute(
                """
                SELECT match_id
                FROM match_source_ids
                WHERE source_system = %s AND source_record_hash = %s
                """,
                (source_system, source_record_hash),
            ).fetchone()

        reuse_match_id: int | None = None
        if (
            match_row is None
            and source_match_id
            and source_system in PROSPECTIVE_REUSE_SOURCE_SYSTEMS
        ):
            reuse_match_id = self._resolve_canonical_reuse(
                tournament_edition_id=tournament_edition_id,
                source_system=source_system,
                source_match_id=source_match_id,
                player_1_id=player_1_id,
                player_2_id=player_2_id,
                match_start_at=match_start_at,
                tour_scope=tour_scope,
                competition_tier=competition_tier,
            )

        if match_row is not None:
            match_id = match_row[0]
        elif reuse_match_id is not None:
            match_id = reuse_match_id
        else:
            match_id = self.conn.execute(
                """
                INSERT INTO matches (tournament_edition_id)
                VALUES (%s)
                RETURNING match_id
                """,
                (tournament_edition_id,),
            ).fetchone()[0]

        version_row = self.conn.execute(
            """
            SELECT match_version_id
            FROM match_versions
            WHERE match_id = %s
              AND player_1_id = %s
              AND player_2_id = %s
              AND match_start_at IS NOT DISTINCT FROM %s
              AND tour_scope = %s
              AND competition_tier = %s
              AND surface IS NOT DISTINCT FROM %s
              AND round_code IS NOT DISTINCT FROM %s
              AND best_of_sets = %s
              AND qualifying_flag = %s
              AND match_status = %s
              AND source_observed_at = %s
              AND as_of = %s
            """,
            (
                match_id,
                player_1_id,
                player_2_id,
                match_start_at,
                tour_scope,
                competition_tier,
                surface,
                round_code,
                best_of_sets,
                qualifying_flag,
                match_status,
                source_observed_at,
                as_of,
            ),
        ).fetchone()
        if version_row is not None:
            match_version_id = version_row[0]
        else:
            match_version_id = self.conn.execute(
                """
                INSERT INTO match_versions (
                    match_id,
                    player_1_id,
                    player_2_id,
                    match_start_at,
                    tour_scope,
                    competition_tier,
                    surface,
                    round_code,
                    best_of_sets,
                    qualifying_flag,
                    match_status,
                    source_observed_at,
                    as_of,
                    ingest_run_id
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING match_version_id
                """,
                (
                    match_id,
                    player_1_id,
                    player_2_id,
                    match_start_at,
                    tour_scope,
                    competition_tier,
                    surface,
                    round_code,
                    best_of_sets,
                    qualifying_flag,
                    match_status,
                    source_observed_at,
                    as_of,
                    self.ingest_run_id,
                ),
            ).fetchone()[0]

        self.conn.execute(
            """
            INSERT INTO match_source_ids (
                match_id,
                match_version_id,
                source_system,
                source_match_id,
                source_record_hash,
                source_record_locator,
                source_file_name,
                source_version_hash,
                ingest_run_id
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT DO NOTHING
            """,
            (
                match_id,
                match_version_id,
                source_system,
                source_match_id,
                source_record_hash,
                source_record_locator,
                source_file_name,
                source_version_hash,
                self.ingest_run_id,
            ),
        )
        self.conn.execute(
            """
            INSERT INTO match_version_sources (
                match_version_id,
                source_system,
                source_match_id,
                source_record_hash,
                source_record_locator,
                source_file_name,
                source_version_hash,
                source_observed_at,
                as_of,
                ingest_run_id
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT DO NOTHING
            """,
            (
                match_version_id,
                source_system,
                source_match_id,
                source_record_hash,
                source_record_locator,
                source_file_name,
                source_version_hash,
                source_observed_at,
                as_of,
                self.ingest_run_id,
            ),
        )
        return MatchLocator(
            match_id=match_id,
            match_version_id=match_version_id,
            player_1_id=player_1_id,
            player_2_id=player_2_id,
        )

    def insert_match_result(
        self,
        *,
        match_id: int,
        outcome_status: str,
        winner_side: int | None,
        retired_side: int | None,
        sets_won_player_1: int | None,
        sets_won_player_2: int | None,
        games_won_player_1: int | None,
        games_won_player_2: int | None,
        scoreline_text: str | None,
        source_observed_at: datetime,
        as_of: datetime,
    ) -> bool:
        existing = self.conn.execute(
            """
            SELECT 1
            FROM match_result_observations
            WHERE match_id = %s AND source_observed_at = %s AND as_of = %s
            """,
            (match_id, source_observed_at, as_of),
        ).fetchone()
        if existing is not None:
            return False
        self.conn.execute(
            """
            INSERT INTO match_result_observations (
                match_id,
                outcome_status,
                winner_side,
                retired_side,
                sets_won_player_1,
                sets_won_player_2,
                games_won_player_1,
                games_won_player_2,
                scoreline_text,
                source_observed_at,
                as_of,
                ingest_run_id
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                match_id,
                outcome_status,
                winner_side,
                retired_side,
                sets_won_player_1,
                sets_won_player_2,
                games_won_player_1,
                games_won_player_2,
                scoreline_text,
                source_observed_at,
                as_of,
                self.ingest_run_id,
            ),
        )
        return True

    def insert_match_stat(
        self,
        *,
        match_id: int,
        player_id: int,
        source_system: str,
        source_record_id: str | None,
        source_record_hash: str | None,
        line: MatchStatLine,
        source_observed_at: datetime,
        as_of: datetime,
    ) -> bool:
        existing = None
        if source_record_id:
            existing = self.conn.execute(
                """
                SELECT 1
                FROM match_stat_observations
                WHERE source_system = %s
                  AND source_record_id = %s
                  AND match_id = %s
                  AND player_id = %s
                  AND observation_kind = 'RAW_SOURCE'
                  AND source_observed_at = %s
                """,
                (source_system, source_record_id, match_id, player_id, source_observed_at),
            ).fetchone()
        elif source_record_hash:
            existing = self.conn.execute(
                """
                SELECT 1
                FROM match_stat_observations
                WHERE source_system = %s
                  AND source_record_hash = %s
                  AND match_id = %s
                  AND player_id = %s
                  AND observation_kind = 'RAW_SOURCE'
                  AND source_observed_at = %s
                """,
                (source_system, source_record_hash, match_id, player_id, source_observed_at),
            ).fetchone()
        if existing is not None:
            return False
        self.conn.execute(
            """
            INSERT INTO match_stat_observations (
                match_id,
                player_id,
                source_system,
                source_record_id,
                source_record_hash,
                observation_kind,
                first_serves_in,
                first_serve_points_won,
                second_serve_points_won,
                service_points_played,
                return_points_won,
                aces,
                double_faults,
                break_points_saved,
                break_points_faced,
                source_observed_at,
                as_of,
                ingest_run_id
            )
            VALUES (
                %s, %s, %s, %s, %s, 'RAW_SOURCE', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
            )
            """,
            (
                match_id,
                player_id,
                source_system,
                source_record_id,
                source_record_hash,
                line.first_serves_in,
                line.first_serve_points_won,
                line.second_serve_points_won,
                line.service_points_played,
                line.return_points_won,
                line.aces,
                line.double_faults,
                line.break_points_saved,
                line.break_points_faced,
                source_observed_at,
                as_of,
                self.ingest_run_id,
            ),
        )
        return True

    def find_match_for_odds(
        self,
        *,
        tournament_name: str,
        event_date: date,
        player_a_id: int,
        player_b_id: int,
        round_code: str | None,
        best_of_sets: int | None,
    ) -> list[tuple[int, int, int]]:
        exact_date_matches = self.conn.execute(
            """
            SELECT DISTINCT ON (m.match_id)
                m.match_id,
                mv.player_1_id,
                mv.player_2_id
            FROM matches m
            JOIN tournament_editions te ON te.tournament_edition_id = m.tournament_edition_id
            JOIN tournaments t ON t.tournament_id = te.tournament_id
            JOIN match_versions mv ON mv.match_id = m.match_id
            WHERE lower(t.canonical_name) = lower(%s)
              AND mv.match_start_at::date = %s
              AND (
                    (mv.player_1_id = %s AND mv.player_2_id = %s)
                 OR (mv.player_1_id = %s AND mv.player_2_id = %s)
              )
              AND (%s::text IS NULL OR mv.round_code = %s::text)
              AND (%s::smallint IS NULL OR mv.best_of_sets = %s::smallint)
            ORDER BY m.match_id, mv.as_of DESC, mv.source_observed_at DESC
            """,
            (
                tournament_name,
                event_date,
                player_a_id,
                player_b_id,
                player_b_id,
                player_a_id,
                round_code,
                round_code,
                best_of_sets,
                best_of_sets,
            ),
        ).fetchall()
        if exact_date_matches:
            return exact_date_matches

        return self.conn.execute(
            """
            SELECT DISTINCT ON (m.match_id)
                m.match_id,
                mv.player_1_id,
                mv.player_2_id
            FROM matches m
            JOIN tournament_editions te ON te.tournament_edition_id = m.tournament_edition_id
            JOIN tournaments t ON t.tournament_id = te.tournament_id
            JOIN match_versions mv ON mv.match_id = m.match_id
            WHERE lower(t.canonical_name) = lower(%s)
              AND mv.match_start_at IS NULL
              AND te.start_date IS NOT NULL
              AND te.start_date <= %s
              AND (te.end_date IS NULL OR te.end_date >= %s)
              AND (
                    (mv.player_1_id = %s AND mv.player_2_id = %s)
                 OR (mv.player_1_id = %s AND mv.player_2_id = %s)
              )
              AND (%s::text IS NULL OR mv.round_code = %s::text)
              AND (%s::smallint IS NULL OR mv.best_of_sets = %s::smallint)
            ORDER BY m.match_id, mv.as_of DESC, mv.source_observed_at DESC
            """,
            (
                tournament_name,
                event_date,
                event_date,
                player_a_id,
                player_b_id,
                player_b_id,
                player_a_id,
                round_code,
                round_code,
                best_of_sets,
                best_of_sets,
            ),
        ).fetchall()

    def insert_odds_snapshot(
        self,
        *,
        match_id: int,
        bookmaker_code: str,
        selection_type: str,
        decimal_price: Decimal,
        source_snapshot_id: str,
        source_record_hash: str,
        source_observed_at: datetime,
        as_of: datetime,
    ) -> bool:
        existing = self.conn.execute(
            """
            SELECT 1
            FROM odds_snapshots
            WHERE source_system = 'TENNIS_DATA'
              AND source_snapshot_id = %s
              AND bookmaker_code = %s
              AND match_id = %s
              AND market_type = 'MATCH_WINNER'
              AND selection_type = %s
              AND source_observed_at = %s
            """,
            (source_snapshot_id, bookmaker_code, match_id, selection_type, source_observed_at),
        ).fetchone()
        if existing is not None:
            return False
        self.conn.execute(
            """
            INSERT INTO odds_snapshots (
                match_id,
                source_system,
                bookmaker_code,
                market_type,
                selection_type,
                decimal_price,
                source_snapshot_id,
                source_record_hash,
                source_observed_at,
                as_of,
                ingest_run_id
            )
            VALUES (%s, 'TENNIS_DATA', %s, 'MATCH_WINNER', %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                match_id,
                bookmaker_code,
                selection_type,
                decimal_price,
                source_snapshot_id,
                source_record_hash,
                source_observed_at,
                as_of,
                self.ingest_run_id,
            ),
        )
        return True

    def insert_health_check(
        self,
        *,
        source_system: str,
        dataset_name: str,
        latest_observation_at: datetime | None,
        coverage_ratio: Decimal | None,
        unmatched_count: int,
        error_count: int,
        status: str,
        notes: str,
    ) -> None:
        checked_at = datetime.now(timezone.utc)
        freshness_state = "UNKNOWN"
        self.conn.execute(
            """
            INSERT INTO data_health_checks (
                source_system,
                dataset_name,
                checked_at,
                latest_observation_at,
                freshness_state,
                coverage_ratio,
                unmatched_count,
                error_count,
                status,
                notes
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                source_system,
                dataset_name,
                checked_at,
                latest_observation_at,
                freshness_state,
                coverage_ratio,
                unmatched_count,
                error_count,
                status,
                notes,
            ),
        )
