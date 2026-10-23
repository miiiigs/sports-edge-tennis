"""Offline, fail-closed ranking enrichment of a frozen first-production cohort.

No fetching, database access, ranking imputation, publication, or live rule changes.
Consumers must independently audit source licensing, published timestamps, and IDs.
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from research.tennis_pit_rank_evidence_20261022.contract import rank_band, score_completed

ALLOWED_POPULATIONS = {"MANAGER": "MANAGER_PRIMARY", "A1_A2_TRACKING": "A1_A2_WIDE_SPREAD"}
TOURS = {"ATP", "WTA"}
MAX_RANK_AGE_DAYS = 35


def timestamp(value: str) -> datetime:
    """Require an unambiguous ISO UTC-offset timestamp (not a bare ranking date)."""
    if not isinstance(value, str):
        raise ValueError("timestamp must be an ISO timestamp with timezone")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("timestamp requires explicit UTC offset")
    return result.astimezone(timezone.utc)


def iso_day(value: str) -> date:
    if not isinstance(value, str):
        raise ValueError("date must be YYYY-MM-DD")
    return date.fromisoformat(value)


def nonempty(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def valid_rank_evidence(r: Mapping[str, Any]) -> bool:
    """Only explicit, commercially authorized, official singles records qualify."""
    if r.get("tour") not in TOURS or r.get("authority") != r.get("tour"):
        return False
    if r.get("source_kind") != "OFFICIAL" or r.get("ranking_type") != "SINGLES":
        return False
    if r.get("license_authorized") is not True or r.get("identity_verified") is not True:
        return False
    if r.get("published_at_verified") is not True:
        return False
    for field in ("player_id", "identity_evidence_id", "release_evidence_id",
                  "source_record_id", "source_url", "snapshot_sha256"):
        if not nonempty(r.get(field)):
            return False
    digest = r["snapshot_sha256"]
    if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        return False
    if type(r.get("rank")) is not int or r["rank"] <= 0:
        return False
    try:
        effective = iso_day(r["effective_date"])
        published = timestamp(r["source_published_at"])
    except (ValueError, TypeError, KeyError):
        return False
    return published.date() >= effective


def validate_pick(p: Mapping[str, Any]) -> None:
    """Reject unfrozen or non-production picks instead of silently pooling strata."""
    for key in ("pick_id", "canonical_match_id", "prediction_run_id",
                "model_version", "selected_player_id", "opponent_player_id",
                "side1_player_id", "side2_player_id"):
        if not nonempty(p.get(key)):
            raise ValueError(f"missing canonical or model evidence: {key}")
    population = p.get("population")
    if population not in ALLOWED_POPULATIONS:
        raise ValueError("unknown selection population")
    if p.get("selection_definition") != ALLOWED_POPULATIONS[population]:
        raise ValueError("selection definition does not match population")
    if p.get("first_production") is not True or p.get("prediction_finalized") is not True:
        raise ValueError("cohort requires original finalized first-production picks")
    if p.get("is_experiment") is not False:
        raise ValueError("experiment membership is not a production selection")
    if p.get("tour") not in TOURS:
        raise ValueError("unknown tour")
    if p["side1_player_id"] == p["side2_player_id"]:
        raise ValueError("self-match")
    manager_side = p.get("manager_side")
    if manager_side not in (1, 2):
        raise ValueError("unknown original Manager selection side")
    expected_selected = p[f"side{manager_side}_player_id"]
    expected_opponent = p[f"side{3 - manager_side}_player_id"]
    if (p["selected_player_id"], p["opponent_player_id"]) != (expected_selected, expected_opponent):
        raise ValueError("Manager selection identity/side mismatch")
    picked = timestamp(p["picked_at"])
    scheduled = timestamp(p["scheduled_start_at"])
    if picked > scheduled:
        raise ValueError("prediction after the match's scheduled start")
    event = iso_day(p["event_date"])
    if abs((scheduled.date() - event).days) > 1:
        raise ValueError("event date mismatches scheduled UTC match day")


def freeze_first_production(picks: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """One pick per canonical sporting match per declared production population."""
    materialized = [dict(p) for p in picks]
    for p in materialized:
        validate_pick(p)
    materialized.sort(key=lambda p: (timestamp(p["picked_at"]), p["pick_id"]))
    first: dict[tuple[str, str], dict[str, Any]] = {}
    for p in materialized:
        key = (p["population"], p["canonical_match_id"])
        first.setdefault(key, p)
    return list(first.values())


def asof_one(p: Mapping[str, Any], player_id: str,
             rankings: Iterable[Mapping[str, Any]]) -> tuple[int | None, str, dict[str, Any] | None]:
    """Fail closed for missing, stale, future, unauthorized, or conflicting evidence."""
    picked = timestamp(p["picked_at"])
    event = iso_day(p["event_date"])
    admissible = []
    for r in rankings:
        if r.get("tour") != p["tour"] or r.get("player_id") != player_id:
            continue
        if not valid_rank_evidence(r):
            continue
        effective = iso_day(r["effective_date"])
        published = timestamp(r["source_published_at"])
        if effective > event or published > picked:
            continue
        if event - effective > timedelta(days=MAX_RANK_AGE_DAYS):
            continue
        admissible.append((effective, published, r))
    if not admissible:
        return None, "NO_AUTHORIZED_VERIFIED_PRE_PICK_RANK", None
    newest_date = max(x[0] for x in admissible)
    newest = [r for effective, _, r in admissible if effective == newest_date]
    ranks = {r["rank"] for r in newest}
    if len(ranks) != 1:
        return None, "CONFLICTING_OFFICIAL_RANK", None
    newest.sort(key=lambda r: (timestamp(r["source_published_at"]), r["source_record_id"]))
    chosen = newest[-1]
    return chosen["rank"], "VERIFIED", {
        "source_record_id": chosen["source_record_id"],
        "effective_date": chosen["effective_date"],
        "published_at": chosen["source_published_at"],
        "release_evidence_id": chosen["release_evidence_id"],
        "identity_evidence_id": chosen["identity_evidence_id"],
        "snapshot_sha256": chosen["snapshot_sha256"],
    }


def enrich(picks: Iterable[Mapping[str, Any]],
           rankings: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Preserve outcomes and model version; add auditable selected/opponent ranks."""
    frozen = freeze_first_production(picks)
    ranks_by_key: dict[tuple[str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for r in rankings:
        if nonempty(r.get("player_id")) and r.get("tour") in TOURS:
            ranks_by_key[(r["tour"], r["player_id"])].append(r)
    results = []
    for p in frozen:
        row = dict(p)
        for side in ("selected", "opponent"):
            player_id = p[f"{side}_player_id"]
            rank, status, evidence = asof_one(
                p, player_id, ranks_by_key.get((p["tour"], player_id), ())
            )
            row[f"{side}_rank"] = rank
            row[f"{side}_rank_band"] = rank_band(rank)
            row[f"{side}_rank_status"] = status
            row[f"{side}_rank_evidence"] = evidence
        row["rank_gap_opponent_minus_selected"] = (
            row["opponent_rank"] - row["selected_rank"]
            if row["opponent_rank"] is not None and row["selected_rank"] is not None
            else None
        )
        results.append(row)
    return results



def agent_rank_view(row: Mapping[str, Any], agent: str) -> dict[str, Any]:
    """Orient official ranking ranks to EACH agent's own original selected side."""
    if agent not in ("manager", "a1", "a2"):
        raise ValueError("unknown agent")
    manager_side = row.get("manager_side")
    agent_side = row.get(f"{agent}_side")
    if manager_side not in (1, 2) or agent_side not in (1, 2):
        return {"selected_rank": None, "opponent_rank": None,
                "selected_rank_band": "MISSING_OR_UNRANKED",
                "opponent_rank_band": "MISSING_OR_UNRANKED",
                "selected_rank_status": "UNKNOWN_AGENT_SIDE",
                "opponent_rank_status": "UNKNOWN_AGENT_SIDE",
                "gap": None}
    selected = "selected" if manager_side == agent_side else "opponent"
    opponent = "opponent" if manager_side == agent_side else "selected"
    selected_rank = row.get(f"{selected}_rank")
    opponent_rank = row.get(f"{opponent}_rank")
    return {
        "selected_rank": selected_rank, "opponent_rank": opponent_rank,
        "selected_rank_band": row.get(f"{selected}_rank_band", rank_band(selected_rank)),
        "opponent_rank_band": row.get(f"{opponent}_rank_band", rank_band(opponent_rank)),
        "selected_rank_status": row.get(f"{selected}_rank_status"),
        "opponent_rank_status": row.get(f"{opponent}_rank_status"),
        "gap": opponent_rank - selected_rank
               if selected_rank is not None and opponent_rank is not None else None,
    }


def coverage(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    by_group: dict[tuple[str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for r in rows:
        by_group[(r["population"], r["tour"])].append(r)
    out = []
    for (population, tour), group in sorted(by_group.items()):
        out.append({
            "population": population, "tour": tour, "total": len(group),
            "selected_verified": sum(r["selected_rank_status"] == "VERIFIED" for r in group),
            "opponent_verified": sum(r["opponent_rank_status"] == "VERIFIED" for r in group),
            "both_verified": sum(
                r["selected_rank_status"] == r["opponent_rank_status"] == "VERIFIED"
                for r in group
            ),
            "selected_missing_reasons": dict(Counter(
                r["selected_rank_status"] for r in group if r["selected_rank_status"] != "VERIFIED"
            )),
            "opponent_missing_reasons": dict(Counter(
                r["opponent_rank_status"] for r in group if r["opponent_rank_status"] != "VERIFIED"
            )),
        })
    return out


def group_scores(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Separate A1, A2, Manager. UNKNOWN never claims a 401-600 result."""
    groups: dict[tuple[str, str, str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for r in rows:
        for axis in ("selected", "opponent"):
            for agent in ("manager", "a1", "a2"):
                groups[(r["population"], agent, axis, r[f"{axis}_rank_band"])].append(r)
    result = []
    for (population, agent, axis, band), group in sorted(groups.items()):
        metrics = score_completed(
            group, selected_side_key=f"{agent}_side", probability_key=f"{agent}_p"
        )
        result.append({"population": population, "agent": agent,
                       "rank_axis": axis, "band": band, **metrics})
    return result


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def main() -> None:
    """Offline CLI with pinned input hashes; never connects to live systems."""
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frozen-picks", required=True, type=Path)
    parser.add_argument("--rank-evidence", required=True, type=Path)
    parser.add_argument("--expected-picks-sha256", required=True)
    parser.add_argument("--expected-ranks-sha256", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args()
    for path, expected in ((args.frozen_picks, args.expected_picks_sha256),
                           (args.rank_evidence, args.expected_ranks_sha256)):
        if file_sha256(path) != expected:
            parser.error(f"input fingerprint mismatch: {path}")
    rows = enrich(read_jsonl(args.frozen_picks), read_jsonl(args.rank_evidence))
    args.output.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows), encoding="utf-8"
    )
    args.report.write_text(
        json.dumps({"coverage": coverage(rows), "cohorts": group_scores(rows),
                    "picks_sha256": args.expected_picks_sha256,
                    "ranks_sha256": args.expected_ranks_sha256,
                    "enriched_rows": len(rows)}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8"
    )


if __name__ == "__main__":
    main()
