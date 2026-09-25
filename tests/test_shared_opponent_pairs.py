"""Optimized shared-opponent measurement must equal the old O(n^2) scan."""

from __future__ import annotations

import random

from sports_edge_tennis.live.api_tennis_history_reconciliation import (
    count_shared_opponent_pairs,
)


def _brute_force(opponents_by_player: dict[int, set[int]]) -> int:
    items = list(opponents_by_player.items())
    count = 0
    for index, (_, opponents_a) in enumerate(items):
        for _, opponents_b in items[index + 1 :]:
            if opponents_a & opponents_b:
                count += 1
    return count


def test_equivalence_on_fixed_cases():
    cases = [
        {},
        {1: set(), 2: set()},
        {1: {10}, 2: {10}},
        {1: {10, 11}, 2: {11, 12}, 3: {13}},
        {1: {10}, 2: {10}, 3: {10}, 4: {10}},  # all four share opponent 10
        {1: {10, 11}, 2: {10, 11}},  # share two opponents, counted once
    ]
    for case in cases:
        assert count_shared_opponent_pairs(case) == _brute_force(case)


def test_equivalence_on_random_graphs():
    rng = random.Random(20260925)
    for _ in range(50):
        players = list(range(1, rng.randint(2, 25)))
        opponents = list(range(100, 100 + rng.randint(1, 12)))
        graph = {
            player: {opp for opp in opponents if rng.random() < 0.35}
            for player in players
        }
        assert count_shared_opponent_pairs(graph) == _brute_force(graph)
