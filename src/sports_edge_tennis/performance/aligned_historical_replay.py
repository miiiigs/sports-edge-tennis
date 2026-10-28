"""Original ALIGNED Model Picks, Oct 24–27: read-only counterfactual diagnostics.

Select only using persisted pre-match attributes. Never update historical results,
model policies, settlement, wager status, probabilities or the Results dashboard.
The baseline is *all original aligned picks* (Manager + A1/A2 Wide-Spread).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

import psycopg

from sports_edge_tennis.dashboard.aligned_model_picks import (
    aligned_production_model_picks_cte,
)

DAYS = tuple(date(2026, 10, x) for x in (24, 25, 26, 27))
RULES = (
    "original",
    "confidence70",
    "confidence80",
    "known_surface",
    "confidence70_known_surface",
    "a1a2_same_side_gap_lt05",
    "confidence70_a1a2_gap_lt10",
)
VALID_RESULTS = frozenset(("WON", "LOST", "VOID", "PENDING"))
VALID_PICKS = frozenset(("MANAGER_CONSENSUS", "A1_A2_WIDE_SPREAD"))
KNOWN_SURFACES = frozenset(("HARD", "CLAY", "GRASS"))


@dataclass(frozen=True)
class ReplayPick:
    match_id: int
    day: date
    pick_type: str
    probability: Decimal
    side: int
    surface: str
    outcome: str
    a1_side: int | None
    a2_side: int | None
    a1_probability: Decimal | None
    a2_probability: Decimal | None


def _allows(rule: str, p: ReplayPick) -> bool:
    conf70 = p.probability >= Decimal("0.70")
    known = p.surface in KNOWN_SURFACES
    both_agree = (
        p.a1_side == p.side
        and p.a2_side == p.side
        and p.a1_probability is not None
        and p.a2_probability is not None
    )
    gap = abs(p.a1_probability - p.a2_probability) if both_agree else None
    if rule == "original":
        return True
    if rule == "confidence70":
        return conf70
    if rule == "confidence80":
        return p.probability >= Decimal("0.80")
    if rule == "known_surface":
        return known
    if rule == "confidence70_known_surface":
        return conf70 and known
    if rule == "a1a2_same_side_gap_lt05":
        return gap is not None and gap < Decimal("0.05")
    if rule == "confidence70_a1a2_gap_lt10":
        return conf70 and gap is not None and gap < Decimal("0.10")
    raise ValueError("unrecognized research-only replay rule")


def aligned_replay_sql() -> str:
    """Reuse exactly the dashboard's earliest qualifying live aligned pick.

    Original match start, surface, and run-level agent values are point-in-time.
    Only the outcome is read after match completion.
    """
    return f"""
        WITH {aligned_production_model_picks_cte()},
        original AS (
            SELECT
                p.match_id,
                p.aligned_pick_type,
                p.selected_side,
                p.aligned_probability,
                COALESCE(
                    (mv.match_start_at AT TIME ZONE 'Asia/Manila')::date,
                    (p.picked_at AT TIME ZONE 'Asia/Manila')::date
                ) AS manila_date,
                mv.surface::text AS surface,
                a1.selection_side AS a1_side,
                a2.selection_side AS a2_side,
                a1.win_probability AS a1_prob,
                a2.win_probability AS a2_prob
            FROM aligned_model_picks p
            LEFT JOIN LATERAL (
                SELECT mv0.match_start_at, mv0.surface
                FROM match_versions mv0
                WHERE mv0.match_id = p.match_id
                  AND mv0.as_of <= p.picked_at
                  AND mv0.source_observed_at <= p.picked_at
                ORDER BY mv0.as_of DESC, mv0.source_observed_at DESC,
                         mv0.match_version_id DESC
                LIMIT 1
            ) mv ON TRUE
            LEFT JOIN predictions a1 ON
                a1.prediction_run_id = p.prediction_run_id
                AND a1.match_id = p.match_id
                AND a1.agent_code = 'AGENT_1_SURFACE_ELO'
            LEFT JOIN predictions a2 ON
                a2.prediction_run_id = p.prediction_run_id
                AND a2.match_id = p.match_id
                AND a2.agent_code = 'AGENT_2_HIERARCHICAL_MARKOV'
        ),
        terminal AS (
            SELECT orig.*, res.outcome_status, res.winner_side
            FROM original orig
            LEFT JOIN LATERAL (
                SELECT mro.outcome_status::text AS outcome_status,
                       mro.winner_side
                FROM match_result_observations mro
                WHERE mro.match_id = orig.match_id
                  AND mro.outcome_status IN
                      ('COMPLETED', 'RETIRED', 'WALKOVER', 'CANCELLED')
                ORDER BY mro.as_of DESC, mro.source_observed_at DESC,
                         mro.match_result_observation_id DESC
                LIMIT 1
            ) res ON TRUE
            WHERE orig.manila_date BETWEEN %s::date AND %s::date
        )
        SELECT match_id, manila_date, aligned_pick_type, aligned_probability,
               selected_side, surface, a1_side, a2_side, a1_prob, a2_prob,
               CASE
                 WHEN outcome_status = 'COMPLETED'
                  AND winner_side = selected_side THEN 'WON'
                 WHEN outcome_status = 'COMPLETED'
                  AND winner_side IN (1,2)
                  AND winner_side <> selected_side THEN 'LOST'
                 WHEN outcome_status IN ('RETIRED','WALKOVER','CANCELLED')
                  THEN 'VOID'
                 ELSE 'PENDING'
               END AS result
        FROM terminal
        ORDER BY manila_date, match_id
    """


def load_aligned_replay_picks(conn: psycopg.Connection) -> tuple[ReplayPick, ...]:
    rows = conn.execute(aligned_replay_sql(), (DAYS[0], DAYS[-1])).fetchall()
    seen = set()
    out = []
    for (match_id, day, ptype, probability, side, surface, a1_side,
         a2_side, a1p, a2p, outcome) in rows:
        if int(match_id) in seen:
            raise ValueError("duplicate earliest sporting match")
        seen.add(int(match_id))
        if ptype not in VALID_PICKS or outcome not in VALID_RESULTS:
            raise ValueError("unexpected aligned population or outcome")
        if side not in (1, 2):
            raise ValueError("invalid selected side")
        p = Decimal(probability)
        if not p.is_finite() or not 0 <= p <= 1:
            raise ValueError("invalid original probability")
        for a in (a1p, a2p):
            if a is not None and (
                not Decimal(a).is_finite() or not 0 <= Decimal(a) <= 1
            ):
                raise ValueError("invalid original agent confidence")
        out.append(ReplayPick(
            int(match_id), day, str(ptype), p, int(side),
            str(surface or "UNKNOWN").upper(), str(outcome),
            int(a1_side) if a1_side in (1,2) else None,
            int(a2_side) if a2_side in (1,2) else None,
            Decimal(a1p) if a1p is not None else None,
            Decimal(a2p) if a2p is not None else None,
        ))
    return tuple(out)


def _counts(rows: tuple[ReplayPick, ...], rule: str) -> dict:
    included = tuple(p for p in rows if _allows(rule, p))
    omitted = tuple(p for p in rows if not _allows(rule, p))
    count = lambda items, result: sum(p.outcome == result for p in items)
    wins, losses = count(included, "WON"), count(included, "LOST")
    void, pending = count(included, "VOID"), count(included, "PENDING")
    if wins + losses + void + pending != len(included):
        raise ValueError("replay accounting mismatch")
    return {
        "original": len(rows), "retained": len(included),
        "wins": wins, "losses": losses,
        "void": void, "pending": pending,
        "scored": wins + losses,
        "missed_winners": count(omitted, "WON"),
        "avoided_losses": count(omitted, "LOST"),
    }


def render_aligned_replay(picks: tuple[ReplayPick, ...]) -> tuple[str, ...]:
    lines = [
        "ALIGNED_REPLAY_AUDIT "
        "scope=ALL_ORIGINAL_ALIGNED_MODEL_PICKS dates=2026-10-24..2026-10-27 "
        "mode=READ_ONLY_COUNTERFACTUAL source=ORIGINAL_PIT selection=HISTORICAL_NOT_DEPLOYED"
    ]
    for day in DAYS:
        day_picks = tuple(p for p in picks if p.day == day)
        for rule in RULES:
            c = _counts(day_picks, rule)
            percentage = (
                f"{100*c['wins']/c['scored']:.1f}" if c["scored"] else "-"
            )
            coverage = f"{100*c['retained']/c['original']:.1f}" if c["original"] else "-"
            status = (
                "NO_ORIGINAL_PICKS" if not c["original"] else
                "NO_RETAINED_PICKS" if not c["retained"] else
                "UNSETTLED" if c["pending"] else
                "NO_GRADED" if not c["scored"] else
                "RETROSPECTIVE_MET73_NOT_LIVE" if c["wins"] * 100 >= 73*c["scored"]
                else "RETROSPECTIVE_BELOW73"
            )
            lines.append(
                "ALIGNED_REPLAY_DAY "
                f"day={day.isoformat()} rule={rule} "
                f"original={c['original']} retained={c['retained']} "
                f"coverage_pct={coverage} "
                f"won={c['wins']} lost={c['losses']} void={c['void']} "
                f"pending={c['pending']} win_rate_pct={percentage} "
                f"avoided_losses={c['avoided_losses']} "
                f"missed_winners={c['missed_winners']} "
                f"status={status}"
            )
    return tuple(lines)
