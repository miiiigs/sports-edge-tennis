"""Prospective, append-only Agent 3 support diagnostics.

A3 abstains on nearly every live fixture because the matchup graph cannot meet the
locked support gates. This module records, per A3 evaluation, exactly what the graph
saw so the cause can be measured in production rather than reconstructed offline.

It is observability only. It never changes A3 thresholds, config, or any prediction
or betting decision, and it stores no provider payloads or secrets.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import psycopg

MIGRATION_PATH = Path("db/migrations/0020_a3_support_diagnostics.up.sql")
APPEND_ONLY_TRIGGER = "trg_a3_support_diagnostics_append_only_mutation"

#: Applied to a player whose model ID is not present in the A3 history snapshot,
#: which is the observable signature of a live-only (unresolved) identity.
IDENTITY_RESOLVED = "RESOLVED"
IDENTITY_LIVE_UNRESOLVED = "LIVE_UNRESOLVED"


@dataclass(frozen=True)
class A3SupportDiagnostic:
    match_id: int
    evaluated_at: datetime
    player_1_model_id: int | None
    player_2_model_id: int | None
    identity_status: str
    direct_raw_support: int | None
    direct_effective_support: Decimal | None
    raw_shared_opponents: int | None
    eligible_shared_opponents: int | None
    q_count: Decimal | None
    support_path: str | None
    gate_failed: str | None
    final_status: str
    graph_degree_player1: int | None
    graph_degree_player2: int | None
    history_count_player1: int | None
    history_count_player2: int | None


def build_a3_support_diagnostic(*, target, snapshot, result, as_of: datetime) -> A3SupportDiagnostic:
    """Extract a support diagnostic from one evaluated A3 request/result pair."""
    player_1_id = target.player_1_id
    player_2_id = target.player_2_id

    neighbors_1 = set(snapshot.adjacency.get(player_1_id, ()))
    neighbors_2 = set(snapshot.adjacency.get(player_2_id, ()))
    raw_shared = len((neighbors_1 & neighbors_2) - {player_1_id, player_2_id})

    player_1 = snapshot.players.get(player_1_id)
    player_2 = snapshot.players.get(player_2_id)
    identity_status = (
        IDENTITY_RESOLVED
        if (player_1 is not None or player_2 is not None)
        else IDENTITY_LIVE_UNRESOLVED
    )

    diagnostics = result.diagnostics
    return A3SupportDiagnostic(
        match_id=int(target.match_id),
        evaluated_at=as_of,
        player_1_model_id=player_1_id,
        player_2_model_id=player_2_id,
        identity_status=identity_status,
        direct_raw_support=diagnostics.direct_raw_count_all,
        direct_effective_support=diagnostics.direct_effective_count_all,
        raw_shared_opponents=raw_shared,
        eligible_shared_opponents=diagnostics.network_shared_opponents_all,
        q_count=diagnostics.network_q_all,
        support_path=(diagnostics.activation_mode or "NONE"),
        gate_failed=(None if result.abstain_reason is None else result.abstain_reason.value),
        final_status=result.prediction_status,
        graph_degree_player1=len(neighbors_1),
        graph_degree_player2=len(neighbors_2),
        history_count_player1=(0 if player_1 is None else len(player_1.all_observations)),
        history_count_player2=(0 if player_2 is None else len(player_2.all_observations)),
    )


def record_a3_support_diagnostic(
    conn: psycopg.Connection,
    diagnostic: A3SupportDiagnostic,
) -> bool:
    """Append one diagnostic, deduped on identical observations.

    Returns True when a new row was written, False when an identical observation
    already existed. Prior rows are never rewritten.
    """
    row = conn.execute(
        """
        INSERT INTO a3_support_diagnostics (
            match_id,
            evaluated_at,
            player_1_model_id,
            player_2_model_id,
            identity_status,
            direct_raw_support,
            direct_effective_support,
            raw_shared_opponents,
            eligible_shared_opponents,
            q_count,
            support_path,
            gate_failed,
            final_status,
            graph_degree_player1,
            graph_degree_player2,
            history_count_player1,
            history_count_player2
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (match_id, evaluated_at, player_1_model_id, player_2_model_id)
        DO NOTHING
        RETURNING a3_support_diagnostic_id
        """,
        (
            diagnostic.match_id,
            diagnostic.evaluated_at,
            diagnostic.player_1_model_id,
            diagnostic.player_2_model_id,
            diagnostic.identity_status,
            diagnostic.direct_raw_support,
            diagnostic.direct_effective_support,
            diagnostic.raw_shared_opponents,
            diagnostic.eligible_shared_opponents,
            diagnostic.q_count,
            diagnostic.support_path,
            diagnostic.gate_failed,
            diagnostic.final_status,
            diagnostic.graph_degree_player1,
            diagnostic.graph_degree_player2,
            diagnostic.history_count_player1,
            diagnostic.history_count_player2,
        ),
    ).fetchone()
    return row is not None


__all__ = [
    "A3SupportDiagnostic",
    "APPEND_ONLY_TRIGGER",
    "IDENTITY_LIVE_UNRESOLVED",
    "IDENTITY_RESOLVED",
    "MIGRATION_PATH",
    "build_a3_support_diagnostic",
    "record_a3_support_diagnostic",
]
