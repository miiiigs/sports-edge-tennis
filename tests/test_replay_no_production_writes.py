"""Structural guarantees that replay can never become a production writer.

These tests are intentionally architecture-level: replay must reuse current
predictive/manager/market logic without importing any persistence path that
would write production decision tables.
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path

import sports_edge_tennis.replay as replay_package
from sports_edge_tennis.agents import agent1_surface_elo, agent2_hierarchical_markov, agent3_intransitivity
from sports_edge_tennis.replay import engine

FORBIDDEN_SQL = re.compile(
    r"\b(INSERT\s+INTO|UPDATE\s+\w+\s+SET|DELETE\s+FROM|TRUNCATE|DROP\s+TABLE)\b",
    re.IGNORECASE,
)

FORBIDDEN_PERSISTENCE_NAMES = (
    "persist_and_finalize",
    "persist_predictive_agent_result",
    "persist_veto_audit",
    "evaluate_and_persist",
    "save_checkpoint",
    "publish_new_bet_decision",
    "settle_type_a_match_winner_decisions",
)


def _replay_sources() -> dict[str, str]:
    package_dir = Path(replay_package.__file__).parent
    return {
        path.name: path.read_text(encoding="utf-8")
        for path in sorted(package_dir.glob("*.py"))
    }


def test_replay_package_contains_no_write_sql():
    for name, source in _replay_sources().items():
        assert not FORBIDDEN_SQL.search(source), f"replay module {name} contains write SQL"


def test_replay_package_does_not_import_production_write_paths():
    for name, source in _replay_sources().items():
        for forbidden in FORBIDDEN_PERSISTENCE_NAMES:
            assert forbidden not in source, f"replay module {name} references {forbidden}"


def test_engine_does_not_accept_a_database_connection():
    for function in (engine.run_replay, engine.run_replay_run):
        parameters = set(inspect.signature(function).parameters)
        assert not ({"conn", "connection", "cursor"} & parameters)


def test_engine_reuses_the_current_predictive_agents():
    assert engine.predict_agent1 is agent1_surface_elo.predict_from_replay
    assert engine.predict_agent2 is agent2_hierarchical_markov.predict
    assert engine.predict_agent3 is agent3_intransitivity.predict
