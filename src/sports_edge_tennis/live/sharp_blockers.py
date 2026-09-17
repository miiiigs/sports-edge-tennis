"""Read-only diagnostics for Manager consensus rows blocked by missing sharp odds.

The production Market layer deliberately fails closed when a Manager CONSENSUS has no
complete Pinnacle reference pair. This module explains that failure without changing
reconciliation, pricing, Manager, staking, publication, or settlement behavior.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

import psycopg


@dataclass(frozen=True)
class SharpReferenceBlocker:
    prediction_run_id: int
    decision_id: int
    match_id: int
    selected_side: int
    consensus_probability: Decimal
    player_1_name: str
    player_2_name: str
    tournament_name: str
    match_start_at: datetime
    pinnapi_mapping_count: int
    pinnapi_provider_event_id: str | None
    sharp_quotes_at_decision: int
    sharp_player_1_quotes_at_decision: int
    sharp_player_2_quotes_at_decision: int
    sharp_quotes_total: int
    sharp_player_1_quotes_total: int
    sharp_player_2_quotes_total: int
    latest_sharp_observed_at: datetime | None

    @property
    def classification(self) -> str:
        """Classify the missing-sharp blocker without changing live policy."""
        if self.pinnapi_mapping_count == 0:
            if self.sharp_quotes_total > 0:
                return "UNMAPPED_WITH_SHARP_QUOTES"
            return "NO_PINNAPI_MAPPING"

        p1_at_decision = self.sharp_player_1_quotes_at_decision > 0
        p2_at_decision = self.sharp_player_2_quotes_at_decision > 0
        p1_total = self.sharp_player_1_quotes_total > 0
        p2_total = self.sharp_player_2_quotes_total > 0

        if p1_at_decision and p2_at_decision:
            # Market still rejected the decision despite a persisted two-way pair.
            # That points away from identity reconciliation and toward freshness /
            # decision-boundary selection logic, so surface it explicitly.
            return "COMPLETE_PAIR_PRESENT_AT_DECISION"

        if not p1_at_decision and not p2_at_decision:
            if p1_total and p2_total:
                return "LATE_COMPLETE_PAIR"
            if p1_total or p2_total:
                return "LATE_PARTIAL_SHARP"
            return "MAPPED_NO_SHARP_QUOTES"

        # Exactly one side existed at the decision boundary.
        if p1_total and p2_total:
            return "PARTIAL_AT_DECISION_COMPLETE_LATER"
        return "PARTIAL_SHARP_AT_DECISION"


def load_missing_sharp_reference_blockers(
    conn: psycopg.Connection,
    *,
    prediction_run_id: int,
) -> tuple[SharpReferenceBlocker, ...]:
    """Return only canonical consensus decisions blocked by missing Pinnacle truth.

    Decision-boundary side counts are kept separate from later observations. This
    distinguishes a reconciliation miss from a mapped event with no odds, a one-sided
    market, or a complete Pinnacle pair that arrived only after the decision.
    """
    rows = conn.execute(
        """
        SELECT
            d.prediction_run_id,
            d.decision_id,
            d.match_id,
            mcr.selected_side,
            mcr.consensus_probability,
            p1.canonical_name,
            p2.canonical_name,
            t.canonical_name,
            mv.match_start_at,
            (
                SELECT count(*)
                FROM match_source_ids msi
                WHERE msi.match_id = d.match_id
                  AND msi.source_system = 'PINNAPI'
                  AND msi.source_match_id IS NOT NULL
            ) AS pinnapi_mapping_count,
            (
                SELECT msi.source_match_id
                FROM match_source_ids msi
                WHERE msi.match_id = d.match_id
                  AND msi.source_system = 'PINNAPI'
                  AND msi.source_match_id IS NOT NULL
                ORDER BY msi.match_source_id DESC
                LIMIT 1
            ) AS pinnapi_provider_event_id,
            (
                SELECT count(*)
                FROM odds_snapshots os
                WHERE os.match_id = d.match_id
                  AND os.source_system = 'SHARP'
                  AND os.bookmaker_code = 'PINNACLE'
                  AND os.market_type = 'MATCH_WINNER'
                  AND os.source_observed_at <= d.as_of
                  AND os.as_of <= d.as_of
            ) AS sharp_quotes_at_decision,
            (
                SELECT count(*)
                FROM odds_snapshots os
                WHERE os.match_id = d.match_id
                  AND os.source_system = 'SHARP'
                  AND os.bookmaker_code = 'PINNACLE'
                  AND os.market_type = 'MATCH_WINNER'
                  AND os.selection_type = 'PLAYER_1'
                  AND os.source_observed_at <= d.as_of
                  AND os.as_of <= d.as_of
            ) AS sharp_player_1_quotes_at_decision,
            (
                SELECT count(*)
                FROM odds_snapshots os
                WHERE os.match_id = d.match_id
                  AND os.source_system = 'SHARP'
                  AND os.bookmaker_code = 'PINNACLE'
                  AND os.market_type = 'MATCH_WINNER'
                  AND os.selection_type = 'PLAYER_2'
                  AND os.source_observed_at <= d.as_of
                  AND os.as_of <= d.as_of
            ) AS sharp_player_2_quotes_at_decision,
            (
                SELECT count(*)
                FROM odds_snapshots os
                WHERE os.match_id = d.match_id
                  AND os.source_system = 'SHARP'
                  AND os.bookmaker_code = 'PINNACLE'
                  AND os.market_type = 'MATCH_WINNER'
            ) AS sharp_quotes_total,
            (
                SELECT count(*)
                FROM odds_snapshots os
                WHERE os.match_id = d.match_id
                  AND os.source_system = 'SHARP'
                  AND os.bookmaker_code = 'PINNACLE'
                  AND os.market_type = 'MATCH_WINNER'
                  AND os.selection_type = 'PLAYER_1'
            ) AS sharp_player_1_quotes_total,
            (
                SELECT count(*)
                FROM odds_snapshots os
                WHERE os.match_id = d.match_id
                  AND os.source_system = 'SHARP'
                  AND os.bookmaker_code = 'PINNACLE'
                  AND os.market_type = 'MATCH_WINNER'
                  AND os.selection_type = 'PLAYER_2'
            ) AS sharp_player_2_quotes_total,
            (
                SELECT max(os.source_observed_at)
                FROM odds_snapshots os
                WHERE os.match_id = d.match_id
                  AND os.source_system = 'SHARP'
                  AND os.bookmaker_code = 'PINNACLE'
                  AND os.market_type = 'MATCH_WINNER'
            ) AS latest_sharp_observed_at
        FROM decisions d
        JOIN manager_consensus_results mcr
          ON mcr.manager_consensus_result_id = d.manager_consensus_result_id
        JOIN matches m
          ON m.match_id = d.match_id
        JOIN tournament_editions te
          ON te.tournament_edition_id = m.tournament_edition_id
        JOIN tournaments t
          ON t.tournament_id = te.tournament_id
        JOIN LATERAL (
            SELECT
                v.player_1_id,
                v.player_2_id,
                v.match_start_at
            FROM match_versions v
            WHERE v.match_id = d.match_id
              AND v.as_of <= d.as_of
              AND v.source_observed_at <= d.as_of
            ORDER BY v.as_of DESC, v.source_observed_at DESC, v.match_version_id DESC
            LIMIT 1
        ) mv ON TRUE
        JOIN players p1 ON p1.player_id = mv.player_1_id
        JOIN players p2 ON p2.player_id = mv.player_2_id
        WHERE d.prediction_run_id = %s
          AND d.signal_type = 'TYPE_A_MODEL_EDGE'
          AND d.market_type = 'MATCH_WINNER'
          AND d.decision_status = 'NO_BET'
          AND d.reason_code = 'MISSING_SHARP_REFERENCE'
          AND mcr.manager_status = 'CONSENSUS'
          AND mcr.selected_side IN (1, 2)
          AND mcr.consensus_probability IS NOT NULL
        ORDER BY d.match_id, d.decision_id
        """,
        (prediction_run_id,),
    ).fetchall()
    return tuple(
        SharpReferenceBlocker(
            prediction_run_id=int(row[0]),
            decision_id=int(row[1]),
            match_id=int(row[2]),
            selected_side=int(row[3]),
            consensus_probability=row[4],
            player_1_name=str(row[5]),
            player_2_name=str(row[6]),
            tournament_name=str(row[7]),
            match_start_at=row[8],
            pinnapi_mapping_count=int(row[9]),
            pinnapi_provider_event_id=None if row[10] is None else str(row[10]),
            sharp_quotes_at_decision=int(row[11]),
            sharp_player_1_quotes_at_decision=int(row[12]),
            sharp_player_2_quotes_at_decision=int(row[13]),
            sharp_quotes_total=int(row[14]),
            sharp_player_1_quotes_total=int(row[15]),
            sharp_player_2_quotes_total=int(row[16]),
            latest_sharp_observed_at=row[17],
        )
        for row in rows
    )


def _safe_text(value: str) -> str:
    return " ".join(value.replace("\n", " ").replace("\r", " ").split())[:120]


def format_sharp_blocker(blocker: SharpReferenceBlocker) -> str:
    provider_event_id = blocker.pinnapi_provider_event_id or "-"
    latest_sharp_at = (
        blocker.latest_sharp_observed_at.isoformat()
        if blocker.latest_sharp_observed_at is not None
        else "-"
    )
    return (
        "SHARP_BLOCKER "
        f"prediction_run_id={blocker.prediction_run_id} "
        f"decision_id={blocker.decision_id} "
        f"match_id={blocker.match_id} "
        f"classification={blocker.classification} "
        f"player1=\"{_safe_text(blocker.player_1_name).replace(chr(34), chr(39))}\" "
        f"player2=\"{_safe_text(blocker.player_2_name).replace(chr(34), chr(39))}\" "
        f"tournament=\"{_safe_text(blocker.tournament_name).replace(chr(34), chr(39))}\" "
        f"start={blocker.match_start_at.isoformat()} "
        f"side={blocker.selected_side} probability={blocker.consensus_probability} "
        f"pinnapi_mapping_count={blocker.pinnapi_mapping_count} "
        f"pinnapi_event_id={provider_event_id} "
        f"sharp_quotes_at_decision={blocker.sharp_quotes_at_decision} "
        f"sharp_p1_at_decision={blocker.sharp_player_1_quotes_at_decision} "
        f"sharp_p2_at_decision={blocker.sharp_player_2_quotes_at_decision} "
        f"sharp_quotes_total={blocker.sharp_quotes_total} "
        f"sharp_p1_total={blocker.sharp_player_1_quotes_total} "
        f"sharp_p2_total={blocker.sharp_player_2_quotes_total} "
        f"latest_sharp_at={latest_sharp_at}"
    )


__all__ = [
    "SharpReferenceBlocker",
    "format_sharp_blocker",
    "load_missing_sharp_reference_blockers",
]
