from __future__ import annotations

from sports_edge_tennis.performance.a1_diagnostic import (
    A1DiagnosticSlice,
    A1LiveChallengerDiagnostic,
)
from sports_edge_tennis.performance.a1_runtime_log import build_a1_runtime_evidence_lines


def _slice(dimension: str, value: str) -> A1DiagnosticSlice:
    return A1DiagnosticSlice(
        dimension=dimension,
        value=value,
        sample=10,
        scored=8,
        correct=5,
        accuracy=0.625,
        brier_score=0.21,
        mean_confidence=0.61,
    )


def test_runtime_log_is_bounded_to_diagnostic_dimensions():
    diagnostic = A1LiveChallengerDiagnostic(
        initialized=True,
        slices=(
            _slice("overall", "ALL"),
            _slice("fixture_surface", "HARD"),
            _slice("direct_identity_provenance", "BOTH DIRECT HISTORICAL"),
            _slice("tournament", "Tiburon"),
            _slice("calendar_week", "2026-W38"),
        ),
        unavailable_fields=("runtime_cold_start_flags",),
    )

    lines = build_a1_runtime_evidence_lines(diagnostic)

    assert any("dimension=overall value=ALL" in line for line in lines)
    assert any("dimension=fixture_surface value=HARD" in line for line in lines)
    assert any(
        "dimension=direct_identity_provenance value=BOTH_DIRECT_HISTORICAL" in line
        for line in lines
    )
    assert not any("dimension=tournament" in line for line in lines)
    assert not any("dimension=calendar_week" in line for line in lines)
    assert lines[-1] == "A1_LIVE_EVIDENCE_UNAVAILABLE fields=runtime_cold_start_flags"


def test_runtime_log_handles_uninitialized_evidence():
    assert build_a1_runtime_evidence_lines(A1LiveChallengerDiagnostic(initialized=False)) == (
        "A1_LIVE_EVIDENCE initialized=0",
    )
