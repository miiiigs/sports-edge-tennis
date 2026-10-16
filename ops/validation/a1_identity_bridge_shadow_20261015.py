"""Read-only Agent 1 shadow comparison: identity bridge and cross-provider dedupe.

Replays the unchanged production Agent 1 code locally over production history and
compares, for the last pre-start production A1 prediction per match since
2026-09-10:

  CURRENT  history exactly as production loads it today;
  DEDUP    CURRENT minus the later-visible copy of each cross-provider duplicate;
  BRIDGED  history loaded with ``bridge_model_identities=True``;
  BOTH     BRIDGED + DEDUP.

It then applies the combined Model Pick rule (A1/A2 same side, A3 abstained,
A1/A2 mean >= 60%; the MANAGER_CONSENSUS and A1_A2_WIDE_SPREAD types of
``dashboard/aligned_model_picks.py``) using the stored A2/A3 predictions from the
same run, so only A1 differs.

Nothing is written. The connection is read-only. Run from the linked checkout:
  PYTHONPATH=src railway run --service Postgres-Sdqz -- python ops/validation/a1_identity_bridge_shadow_20261015.py
"""

from __future__ import annotations

import math
import os
import random
from datetime import UTC, datetime

import psycopg

from sports_edge_tennis.agents.agent1_surface_elo import (
    PredictionRequest,
    SurfaceEloValidationError,
    advance_replay_history,
    predict_from_replay,
    replay_history,
)
from sports_edge_tennis.data.agent1_history import load_agent1_history
from sports_edge_tennis.data.model_identity import resolve_model_player_ids

SINCE = datetime(2026, 9, 10, tzinfo=UTC)
RULE_MERGED_AT = datetime(2026, 9, 26, 10, 9, tzinfo=UTC)  # 60% aligned rule (#189/#191)

TARGETS_SQL = """
WITH lv AS (SELECT DISTINCT ON (match_id) match_id, match_start_at FROM match_versions
            ORDER BY match_id, as_of DESC, source_observed_at DESC, match_version_id DESC),
a1 AS (
  SELECT DISTINCT ON (p.match_id) p.prediction_id, p.match_id, p.prediction_run_id, p.as_of,
         p.selection_side, p.win_probability, lv.match_start_at
  FROM predictions p JOIN prediction_runs pr USING (prediction_run_id) JOIN lv USING (match_id)
  WHERE pr.experiment_id IS NULL AND pr.run_status = 'COMPLETED'
    AND p.agent_code = 'AGENT_1_SURFACE_ELO' AND p.prediction_status = 'PREDICTED'
    AND p.created_at < lv.match_start_at AND p.as_of >= %(since)s
  ORDER BY p.match_id, p.created_at DESC)
SELECT a1.prediction_id, a1.as_of, a1.selection_side, a1.win_probability,
       mv.player_1_id, mv.player_2_id, mv.surface::text, mv.tour_scope::text,
       mv.competition_tier::text, mv.best_of_sets, mv.qualifying_flag,
       r.outcome_status::text, r.winner_side,
       (SELECT o.decimal_price FROM odds_snapshots o WHERE o.match_id = a1.match_id
          AND o.source_system = 'SHARP' AND o.bookmaker_code = 'PINNACLE' AND o.selection_type = 'PLAYER_1'
          AND o.source_observed_at < a1.match_start_at ORDER BY o.source_observed_at DESC, o.odds_snapshot_id DESC LIMIT 1),
       (SELECT o.decimal_price FROM odds_snapshots o WHERE o.match_id = a1.match_id
          AND o.source_system = 'SHARP' AND o.bookmaker_code = 'PINNACLE' AND o.selection_type = 'PLAYER_2'
          AND o.source_observed_at < a1.match_start_at ORDER BY o.source_observed_at DESC, o.odds_snapshot_id DESC LIMIT 1),
       a2.prediction_status::text, a2.selection_side, a2.win_probability, a3.prediction_status::text
FROM a1
JOIN LATERAL (SELECT * FROM match_versions mv0 WHERE mv0.match_id = a1.match_id
              AND mv0.as_of <= a1.as_of AND mv0.source_observed_at <= a1.as_of
              ORDER BY mv0.as_of DESC, mv0.source_observed_at DESC, mv0.match_version_id DESC LIMIT 1) mv ON TRUE
LEFT JOIN LATERAL (SELECT outcome_status, winner_side FROM match_result_observations r0
              WHERE r0.match_id = a1.match_id AND r0.outcome_status IN ('COMPLETED','RETIRED','WALKOVER','CANCELLED')
              ORDER BY r0.as_of DESC, r0.source_observed_at DESC, r0.match_result_observation_id DESC LIMIT 1) r ON TRUE
LEFT JOIN predictions a2 ON a2.prediction_run_id = a1.prediction_run_id AND a2.match_id = a1.match_id
     AND a2.agent_code = 'AGENT_2_HIERARCHICAL_MARKOV'
LEFT JOIN predictions a3 ON a3.prediction_run_id = a1.prediction_run_id AND a3.match_id = a1.match_id
     AND a3.agent_code = 'AGENT_3_INTRANSITIVITY'
ORDER BY a1.as_of
"""

# Same sporting match stored twice: same unordered pair, starts within 36 h, both latest
# results COMPLETED with the same winner, linked to different provider source systems.
DUP_SQL = """
WITH lv AS (SELECT DISTINCT ON (match_id) * FROM match_versions
            ORDER BY match_id, as_of DESC, source_observed_at DESC, match_version_id DESC),
lr AS (SELECT DISTINCT ON (match_id) match_id, outcome_status, winner_side FROM match_result_observations
       ORDER BY match_id, as_of DESC, source_observed_at DESC, match_result_observation_id DESC),
fr AS (SELECT match_id, min(greatest(as_of, source_observed_at)) first_visible FROM match_result_observations
       WHERE outcome_status = 'COMPLETED' GROUP BY match_id),
src AS (SELECT match_id, string_agg(DISTINCT source_system::text, '/' ORDER BY source_system::text) systems
        FROM match_source_ids GROUP BY match_id)
SELECT a.match_id, b.match_id, fa.first_visible, fb.first_visible
FROM lv a JOIN lv b
  ON least(a.player_1_id, a.player_2_id) = least(b.player_1_id, b.player_2_id)
 AND greatest(a.player_1_id, a.player_2_id) = greatest(b.player_1_id, b.player_2_id)
 AND a.match_id < b.match_id AND abs(extract(epoch FROM a.match_start_at - b.match_start_at)) < 36 * 3600
JOIN lr ra ON ra.match_id = a.match_id AND ra.outcome_status = 'COMPLETED'
JOIN lr rb ON rb.match_id = b.match_id AND rb.outcome_status = 'COMPLETED'
JOIN fr fa ON fa.match_id = a.match_id JOIN fr fb ON fb.match_id = b.match_id
LEFT JOIN src sa ON sa.match_id = a.match_id LEFT JOIN src sb ON sb.match_id = b.match_id
WHERE (CASE WHEN a.player_1_id = b.player_1_id THEN ra.winner_side ELSE 3 - ra.winner_side END) = rb.winner_side
  AND sa.systems IS DISTINCT FROM sb.systems
"""


def _dsn() -> str:
    explicit = os.environ.get("TENNIS_DATABASE_URL")
    if explicit:
        return explicit
    # `railway run --service Postgres-Sdqz` injects an internal-only DATABASE_URL;
    # from a workstation the public TCP proxy is the reachable endpoint.
    if os.environ.get("RAILWAY_TCP_PROXY_DOMAIN"):
        return (
            f"postgresql://{os.environ['PGUSER']}:{os.environ['PGPASSWORD']}"
            f"@{os.environ['RAILWAY_TCP_PROXY_DOMAIN']}:{os.environ['RAILWAY_TCP_PROXY_PORT']}"
            f"/{os.environ['PGDATABASE']}"
        )
    return os.environ["DATABASE_URL"]


def _novig_p1(p1, p2) -> float | None:
    if not p1 or not p2:
        return None
    a, b = 1 / float(p1), 1 / float(p2)
    return a / (a + b)


def _metrics(pairs) -> str:
    n = len(pairs)
    if not n:
        return "n=0"
    brier = sum((p - y) ** 2 for p, y in pairs) / n
    ll = -sum(y * math.log(max(p, 1e-6)) + (1 - y) * math.log(max(1 - p, 1e-6)) for p, y in pairs) / n
    acc = sum((p > 0.5) == (y == 1) for p, y in pairs) / n
    return f"n={n} acc={acc:.4f} brier={brier:.4f} logloss={ll:.4f}"


def _wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if not n:
        return float("nan"), float("nan")
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return c - h, c + h


def _replay_series(history, targets, target_ids) -> dict[int, float]:
    """Player-1 probability per target, replaying chronologically with production A1 code."""
    hist = sorted(history, key=lambda o: (max(o.as_of, o.source_observed_at), o.match_id))
    out: dict[int, float] = {}
    replay, prev, idx = None, None, 0
    for t in targets:
        as_of = t[1]
        if replay is None:
            visible = [o for o in hist if o.as_of <= as_of and o.source_observed_at <= as_of]
            replay, idx = replay_history(visible, as_of), len(visible)
        else:
            new = []
            while idx < len(hist) and max(hist[idx].as_of, hist[idx].source_observed_at) <= as_of:
                new.append(hist[idx])
                idx += 1
            if new:
                try:
                    replay = advance_replay_history(
                        replay, new, previous_prediction_as_of=prev, prediction_as_of=as_of
                    )
                except SurfaceEloValidationError:
                    replay = replay_history(hist[:idx], as_of)
        prev = as_of
        p1, p2 = target_ids.get(t[4], t[4]), target_ids.get(t[5], t[5])
        if p1 == p2:
            p1, p2 = t[4], t[5]
        try:
            request = PredictionRequest(
                player_1_id=p1, player_2_id=p2, surface=t[6], tour_scope=t[7],
                competition_tier=t[8], best_of_sets=t[9], qualifying_flag=bool(t[10]), as_of=as_of,
            )
            out[t[0]] = float(predict_from_replay(request, replay).player_1_probability)
        except SurfaceEloValidationError:
            continue
    return out


def _model_pick(a1_p1: float, t) -> tuple[int, float] | None:
    if t[15] != "PREDICTED" or t[17] is None or t[18] != "ABSTAINED":
        return None
    side = 1 if a1_p1 >= 0.5 else 2
    a1_sel = a1_p1 if side == 1 else 1 - a1_p1
    a2_p = float(t[17])
    if t[16] != side or a2_p < 0.5:
        return None
    mean = (a1_sel + a2_p) / 2
    return (side, mean) if mean >= 0.6 else None


def _pick_summary(label: str, picks) -> None:
    scored = [(t, side) for t, side in picks if t[11] == "COMPLETED" and t[12] in (1, 2)]
    wins = sum(t[12] == side for t, side in scored)
    lo, hi = _wilson(wins, len(scored))
    rate = wins / len(scored) if scored else float("nan")
    print(f"  {label:<34} picks={len(picks):4d} scored={len(scored):4d} W={wins:4d} "
          f"L={len(scored) - wins:4d} win={rate:.1%} 95%CI [{lo:.1%}, {hi:.1%}]")


def main() -> int:
    now = datetime.now(UTC)
    with psycopg.connect(_dsn(), options="-c default_transaction_read_only=on -c statement_timeout=900000") as conn:
        current = load_agent1_history(conn, cutoff=now)
        bridged = load_agent1_history(conn, cutoff=now, bridge_model_identities=True)
        targets = conn.execute(TARGETS_SQL, {"since": SINCE}).fetchall()
        dups = conn.execute(DUP_SQL).fetchall()
        # Targets are resolved to model IDs exactly as runner/targets.py does.
        target_ids = resolve_model_player_ids(
            conn, player_ids={t[4] for t in targets} | {t[5] for t in targets}, as_of=now
        )
    drop = {b if (fa, a) <= (fb, b) else a for a, b, fa, fb in dups}
    moved = sum(1 for c, b in zip(current, bridged) if (c.player_1_id, c.player_2_id) != (b.player_1_id, b.player_2_id))
    print(f"history rows={len(current)} rows moved by bridge={moved} cross-provider duplicate copies dropped={len(drop)}")

    variants = {
        "CURRENT": current,
        "DEDUP": [o for o in current if o.match_id not in drop],
        "BRIDGED": bridged,
        "BOTH": [o for o in bridged if o.match_id not in drop],
    }
    probs = {name: _replay_series(hist, targets, target_ids) for name, hist in variants.items()}

    stored = {t[0]: (float(t[3]) if t[2] == 1 else 1 - float(t[3])) for t in targets}
    diffs = [abs(probs["CURRENT"][k] - v) for k, v in stored.items() if k in probs["CURRENT"]]
    print(f"harness reproduction vs stored production A1 (n={len(diffs)}): "
          f"exact {sum(d <= 1e-6 for d in diffs) / len(diffs):.1%}, within 0.005 {sum(d <= 0.005 for d in diffs) / len(diffs):.1%}")

    settled = [t for t in targets if t[11] == "COMPLETED" and t[12] in (1, 2) and all(t[0] in p for p in probs.values())]
    y = {t[0]: 1.0 if t[12] == 1 else 0.0 for t in settled}
    print(f"\nAgent 1 on settled targets (n={len(settled)})")
    print(f"  STORED   {_metrics([(stored[t[0]], y[t[0]]) for t in settled])}")
    for name, p in probs.items():
        print(f"  {name:<8} {_metrics([(p[t[0]], y[t[0]]) for t in settled])}")
    priced = [t for t in settled if _novig_p1(t[13], t[14]) is not None]
    print(f"  PINNACLE {_metrics([(_novig_p1(t[13], t[14]), y[t[0]]) for t in priced])} (priced subset)")
    rnd = random.Random(5)
    for name in ("DEDUP", "BRIDGED", "BOTH"):
        d = [(probs[name][t[0]] - y[t[0]]) ** 2 - (probs["CURRENT"][t[0]] - y[t[0]]) ** 2 for t in settled]
        boots = sorted(sum(rnd.choice(d) for _ in d) / len(d) for _ in range(4000))
        print(f"  {name} - CURRENT Brier {sum(d) / len(d):+.5f} 95% CI [{boots[100]:+.5f}, {boots[3900]:+.5f}]")

    for scope, keep in (("since Sep 10", lambda t: True), ("forward after 60% rule", lambda t: t[1] >= RULE_MERGED_AT)):
        print(f"\nModel Picks, {scope} (fixed run per match)")
        for name, source in (("A production A1 (stored)", stored), ("B bridged A1 (shadow)", probs["BRIDGED"]),
                             ("B+dedup A1 (shadow)", probs["BOTH"])):
            picks = []
            for t in targets:
                if keep(t) and t[0] in source:
                    pick = _model_pick(source[t[0]], t)
                    if pick:
                        picks.append((t, pick[0]))
            _pick_summary(name, picks)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
