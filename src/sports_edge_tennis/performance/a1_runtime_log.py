"""Compact runtime rendering for production-live Agent 1 Challenger evidence."""

from __future__ import annotations

from sports_edge_tennis.performance.a1_diagnostic import A1LiveChallengerDiagnostic


RUNTIME_DIMENSIONS = frozenset(
    {
        "overall",
        "fixture_surface",
        "draw_status",
        "confidence_band",
        "entry_lead_time",
        "direct_identity_provenance",
    }
)


def _metric(value: float | None) -> str:
    return "NA" if value is None else f"{value:.6f}"


def build_a1_runtime_evidence_lines(
    diagnostic: A1LiveChallengerDiagnostic,
) -> tuple[str, ...]:
    """Render a bounded subset of A1 evidence for Railway runtime logs."""
    if not diagnostic.initialized:
        return ("A1_LIVE_EVIDENCE initialized=0",)

    lines: list[str] = []
    for row in diagnostic.slices:
        if row.dimension not in RUNTIME_DIMENSIONS:
            continue
        value = row.value.replace(" ", "_")
        lines.append(
            "A1_LIVE_EVIDENCE "
            f"dimension={row.dimension} value={value} "
            f"sample={row.sample} scored={row.scored} correct={row.correct} "
            f"accuracy={_metric(row.accuracy)} brier={_metric(row.brier_score)} "
            f"mean_confidence={_metric(row.mean_confidence)}"
        )

    if diagnostic.unavailable_fields:
        lines.append(
            "A1_LIVE_EVIDENCE_UNAVAILABLE fields="
            + ",".join(diagnostic.unavailable_fields)
        )
    return tuple(lines)


__all__ = ["RUNTIME_DIMENSIONS", "build_a1_runtime_evidence_lines"]
