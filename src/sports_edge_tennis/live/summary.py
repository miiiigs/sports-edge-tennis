"""Deterministic machine-readable + human-readable Live MVP summaries."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any


def build_live_summary(
    *,
    run_id: str,
    slate_date: str,
    prediction_as_of: datetime,
    fixture_count: int,
    target_count: int,
    manager_consensus_count: int,
    pinnapi_requests: int,
    matches: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "slate_date": slate_date,
        "prediction_as_of": prediction_as_of.isoformat(),
        "fixture_count": fixture_count,
        "manager_consensus_count": manager_consensus_count,
        "pinnapi_requests": pinnapi_requests,
        "matches": matches,
    }


def write_summary(
    summary: dict[str, Any],
    *,
    artifacts_dir: Path,
    run_id: str,
) -> Path:
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    path = artifacts_dir / f"tennis_live_mvp_{run_id}.json"
    path.write_text(json.dumps(summary, sort_keys=True, indent=2), encoding="utf-8")
    return path


def render_terminal(summary: dict[str, Any]) -> str:
    lines = [
        f"slate_date        : {summary['slate_date']}",
        f"run_id            : {summary['run_id']}",
        f"prediction_as_of  : {summary['prediction_as_of']}",
        f"fixture_count     : {summary['fixture_count']}",
        f"manager_consensus : {summary['manager_consensus_count']}",
        f"pinnapi_requests  : {summary['pinnapi_requests']}",
        "",
        f"{'MATCH':<14} {'PICK':<10} {'CONS%':>6} {'MANAGER':<12} {'PINNACLE':<14} STATUS",
    ]
    for match in summary["matches"]:
        manager = match.get("manager", {})
        sharp = match.get("sharp", {})
        pick = manager.get("selected_side") or "-"
        cons = manager.get("consensus_probability")
        cons_str = f"{float(cons) * 100:.0f}%" if cons is not None else "-"
        pinnacle = "-"
        if sharp.get("player_1_price") is not None and sharp.get("player_2_price") is not None:
            pinnacle = f"{sharp['player_1_price']}/{sharp['player_2_price']}"
        lines.append(
            f"{match['player_1'][:12]:<14} {pick:<10} {cons_str:>6} "
            f"{manager.get('result', '-'):<12} {pinnacle:<14} {match['client_state']}"
        )
    return "\n".join(lines)
