"""Deterministic demo fixture for local visual QA of populated states.

Enabled only when SPORTS_EDGE_DASHBOARD_DEMO=1. Never shown in normal mode and
never persisted to any database.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

from sports_edge_tennis.dashboard.read_model import AgentView, MatchView, RunState


def _agent(code: str, status: str, selection: int | None, prob: str | None) -> AgentView:
    return AgentView(code=code, status=status, selection=selection, probability=Decimal(prob) if prob else None)


def _agents(a1s, a1p, a2s, a2p, a3s, a3p):
    return {
        "AGENT_1_SURFACE_ELO": _agent("AGENT_1_SURFACE_ELO", "PREDICTED", a1s, a1p),
        "AGENT_2_HIERARCHICAL_MARKOV": _agent("AGENT_2_HIERARCHICAL_MARKOV", "PREDICTED", a2s, a2p),
        "AGENT_3_INTRANSITIVITY": _agent("AGENT_3_INTRANSITIVITY", "PREDICTED", a3s, a3p),
    }


def _match(
    match_id, p1, p2, tournament, tier, start,
    agents, manager_status, selected_side, consensus, reason,
    active, abstaining, p1_price, p2_price,
):
    return MatchView(
        match_id=match_id, player_1=p1, player_2=p2, tournament=tournament,
        competition_tier=tier, scheduled_start=start, agents=agents,
        manager_status=manager_status, selected_side=selected_side,
        consensus_probability=Decimal(consensus) if consensus else None,
        reason_code=reason, active_agent_count=active, abstaining_agent_count=abstaining,
        player_1_price=Decimal(p1_price) if p1_price else None,
        player_2_price=Decimal(p2_price) if p2_price else None,
        sharp_source_observed_at=start,
    )


def build_demo_state() -> RunState:
    base = datetime(2026, 9, 7, 14, 0, 0, tzinfo=timezone.utc)
    matches = [
        _match(
            1, "Carlos Alcaraz", "Holger Rune", "ATP Masters 1000 Indian Wells", "MASTERS_1000",
            base.replace(hour=14, minute=0),
            _agents(1, "0.72", 1, "0.70", 1, "0.68"),
            "CONSENSUS", 1, "0.70", "NONE", 3, 0,
            "1.57", "2.40",
        ),
        _match(
            2, "Jannik Sinner", "Daniil Medvedev", "ATP 500 Rotterdam", "ATP_500",
            base.replace(hour=15, minute=30),
            _agents(1, "0.63", 1, "0.60", 1, "0.61"),
            "CONSENSUS", 1, "0.613", "NONE", 3, 0,
            None, None,
        ),
        _match(
            3, "Alexander Zverev", "Casper Ruud", "ATP 250 Munich", "ATP_250",
            base.replace(hour=17, minute=0),
            _agents(1, "0.58", 2, "0.55", None, None),
            "CONSENSUS", 1, "0.565", "NONE", 2, 1,
            "1.80", "2.05",
        ),
        _match(
            4, "Andrey Rublev", "Taylor Fritz", "Challenger Phoenix", "CHALLENGER",
            base.replace(hour=18, minute=0),
            _agents(1, "0.66", 2, "0.44", 2, "0.40"),
            "NO_BET", None, None, "DIRECTIONAL_DISAGREEMENT", 3, 0,
            None, None,
        ),
        _match(
            5, "Stefanos Tsitsipas", "Hubert Hurkacz", "ATP 500 Dubai", "ATP_500",
            base.replace(hour=19, minute=0),
            _agents(1, "0.52", 1, "0.95", 1, "0.50"),
            "NO_BET", None, None, "THREE_AGENT_SPREAD_EXCEEDED", 3, 0,
            "2.10", "1.75",
        ),
    ]
    return RunState(
        kind="POPULATED", run_date=date(2026, 9, 7),
        as_of=base, prediction_run_id=9999, run_status="COMPLETED", matches=matches,
    )
