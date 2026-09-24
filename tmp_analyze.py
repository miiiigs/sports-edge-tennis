import json
import re
from datetime import datetime, timezone

import psycopg

from sports_edge_tennis.data.agent2_history import load_agent2_history, build_history_snapshot

DSN = open(r"C:\Users\Desktop 2\AppData\Local\Temp\opencode\tennis_dsn.txt").read().strip()
NAMES = json.load(open(r"C:\dev\sports-edge-tennis\tmp\diag\cohort.json"))["names"]
PLACEHOLDER = re.compile(r"^(R\d+P\d+|Qf\d+|WQF\d+)$", re.IGNORECASE)
NOW = datetime.now(timezone.utc)

with psycopg.connect(DSN, autocommit=True, options="-c default_transaction_read_only=on -c statement_timeout=420000") as c:
    latest = c.execute("select prediction_run_id, started_at from prediction_runs order by prediction_run_id desc limit 1").fetchone()
    match_ids = [int(r[0]) for r in c.execute(
        "select distinct t.match_id from prediction_run_targets t join prediction_runs r on r.prediction_run_id=t.prediction_run_id "
        "where r.started_at >= %s::timestamptz - interval '30 minutes'", (latest[1],)).fetchall()]
    players = set()
    for mid in match_ids:
        row = c.execute(
            "select distinct on (mv.match_id) mv.player_1_id, mv.player_2_id from match_versions mv "
            "where mv.match_id=%s and mv.source_observed_at<=%s and mv.as_of<=%s "
            "order by mv.match_id, mv.as_of desc, mv.match_version_id desc", (mid, NOW, NOW)).fetchone()
        if row is None:
            continue
        names = {int(r[0]): r[1] for r in c.execute("select player_id, canonical_name from players where player_id in (%s,%s)", row).fetchall()}
        if PLACEHOLDER.match(names.get(int(row[0])) or "") or PLACEHOLDER.match(names.get(int(row[1])) or ""):
            continue
        players.add(int(row[0])); players.add(int(row[1]))
    players = sorted(players)
    snap = build_history_snapshot(load_agent2_history(c, cutoff=NOW), cutoff=NOW)
    ready = 0
    for pid in players:
        p = snap.players.get(pid)
        m = p.prior_usable_matches if p else 0
        s = p.overall_serve_points_played if p else 0
        r = p.overall_return_points_played if p else 0
        if m >= 5 and s >= 250 and r >= 250:
            ready += 1
        else:
            print(f"   NOT_READY {NAMES.get(str(pid), pid)} ({pid}): matches={m} serve={s} return={r}", flush=True)
    print(f"concrete_players={len(players)} A2_ready={ready} not_ready={len(players)-ready}", flush=True)
