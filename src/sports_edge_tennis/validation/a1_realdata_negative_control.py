"""Read-only, first-2026-cutoff real-data launcher for PR365 A1 label shuffle.

Training is limited to results genuinely observed AND ingested before the
frozen cutoff. Training historical match versions may be retrospective as long
as known at the 2026 cutoff; evaluation fixture versions instead require
source observation, as-of, AND ingestion by 30 min before original start.

Not a reconstruction of a 2018 live forecast, not an audit certificate for
the whole Manager, and NOT an independent untouched final test.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import UTC, datetime, timedelta

import psycopg

from sports_edge_tennis.validation.agent1_walk_forward import ValidationObservation
from sports_edge_tennis.validation.a1_shuffled_label_full_retrain import (
    run_negative_control,
)

VERSION = "A1_REAL_SOURCE_PREMATCH_NEGATIVE_CONTROL_RUNNER_V1"

SQL = """
WITH results AS (
    SELECT DISTINCT ON (r.match_id)
           r.match_id, r.outcome_status::text, r.winner_side,
           r.source_observed_at, r.as_of, r.ingested_at,
           GREATEST(r.source_observed_at, r.as_of, r.ingested_at) AS result_visible_at
    FROM match_result_observations r
    WHERE r.source_observed_at <= %(as_of)s
      AND r.as_of <= %(as_of)s
      AND r.ingested_at <= %(as_of)s
    ORDER BY r.match_id, r.as_of DESC, r.source_observed_at DESC,
             r.ingested_at DESC, r.match_result_observation_id DESC
)
SELECT te.season_year, m.match_id, v.player_1_id, v.player_2_id,
       v.surface::text, v.tour_scope::text, v.competition_tier::text,
       v.best_of_sets, v.qualifying_flag, v.match_start_at,
       r.outcome_status, r.winner_side, r.source_observed_at,
       r.result_visible_at,
       r.ingested_at, v.as_of, v.source_observed_at, v.ingested_at
FROM results r
JOIN matches m ON m.match_id = r.match_id
JOIN tournament_editions te ON te.tournament_edition_id = m.tournament_edition_id
JOIN LATERAL (
    SELECT mv.player_1_id, mv.player_2_id, mv.surface,
           mv.tour_scope, mv.competition_tier, mv.best_of_sets,
           mv.qualifying_flag, mv.match_start_at,
           mv.as_of, mv.source_observed_at, mv.ingested_at
    FROM match_versions mv
    WHERE mv.match_id = r.match_id
      AND mv.match_start_at IS NOT NULL
      AND mv.as_of <= CASE
          WHEN mv.match_start_at < %(cutoff)s
          THEN %(cutoff)s
          ELSE mv.match_start_at - INTERVAL '30 minutes'
      END
      AND mv.source_observed_at <= CASE
          WHEN mv.match_start_at < %(cutoff)s
          THEN %(cutoff)s
          ELSE mv.match_start_at - INTERVAL '30 minutes'
      END
      AND mv.ingested_at <= CASE
          WHEN mv.match_start_at < %(cutoff)s
          THEN %(cutoff)s
          ELSE mv.match_start_at - INTERVAL '30 minutes'
      END
    ORDER BY mv.as_of DESC, mv.source_observed_at DESC, mv.ingested_at DESC,
             mv.match_version_id DESC
    LIMIT 1
) v ON TRUE
WHERE r.outcome_status = 'COMPLETED'
  AND r.winner_side IN (1, 2)
  AND r.result_visible_at >= v.match_start_at
  AND v.match_start_at < %(evaluation_end)s
  AND (
    (v.match_start_at < %(cutoff)s
       AND r.result_visible_at <= %(cutoff)s)
    OR
    (v.match_start_at >= %(cutoff)s
       AND r.result_visible_at <= %(as_of)s)
  )
ORDER BY v.match_start_at, m.match_id
"""


def read_records(conn, *, train_cutoff: datetime, evaluation_end: datetime,
                 as_of: datetime) -> tuple[list[ValidationObservation], str]:
    if any(t.tzinfo is None for t in (train_cutoff, evaluation_end, as_of)):
        raise ValueError("all cutoff timestamps must be UTC-aware")
    if not train_cutoff < evaluation_end <= as_of:
        raise ValueError("train cutoff < evaluation end <= as-of required")
    conn.execute("SET default_transaction_read_only=on")
    raw = conn.execute(SQL, {
        "cutoff": train_cutoff.astimezone(UTC),
        "evaluation_end": evaluation_end.astimezone(UTC),
        "as_of": as_of.astimezone(UTC),
    }).fetchall()
    digest = hashlib.sha256()
    records = []
    for row in raw:
        (season, mid, p1, p2, surface, tour, tier, best_of, qualifying,
         start, status, winner, observed, known,
         ingested, version_asof, version_observed, version_ingested) = row
        if (start is None or p1 is None or p2 is None or
            best_of is None or qualifying is None or tour is None or
            tier is None):
            # Do not replace unverifiable historical features with guessed defaults.
            continue
        if start >= train_cutoff:
            deadline = start - timedelta(minutes=30)
            if max(version_asof, version_observed, version_ingested) > deadline:
                raise ValueError("post-issue fixture metadata in evaluation")
        if start < train_cutoff and max(version_asof, version_observed, version_ingested) > train_cutoff:
            raise ValueError("post-cutoff historical fixture feature in training")
        if known < start or ingested > as_of:
            raise ValueError("invalid result availability chronology")
        rec = ValidationObservation(
            season_year=int(season), match_id=int(mid),
            player_1_id=int(p1), player_2_id=int(p2),
            surface=surface, tour_scope=str(tour),
            competition_tier=str(tier), best_of_sets=int(best_of),
            qualifying_flag=bool(qualifying),
            match_start_at=start, outcome_status=str(status),
            winner_side=int(winner), source_observed_at=observed,
            as_of=known,
        )
        digest.update((
            f"{mid}|{p1}|{p2}|{start.isoformat()}|{winner}|"
            f"{known.isoformat()}|{version_asof.isoformat()}|"
            f"{version_ingested.isoformat()}\n"
        ).encode())
        records.append(rec)
    if len({r.match_id for r in records}) != len(records):
        raise ValueError("duplicate source canonical match IDs")
    return records, digest.hexdigest()


def run(conn, *, train_cutoff: datetime, evaluation_end: datetime,
        as_of: datetime, repeats: int = 5, seed: int = 91367) -> dict:
    records, sha = read_records(
        conn, train_cutoff=train_cutoff,
        evaluation_end=evaluation_end, as_of=as_of)
    training = [r for r in records if r.match_start_at < train_cutoff]
    holdout = [r for r in records if train_cutoff <= r.match_start_at < evaluation_end]
    report = run_negative_control(
        records, train_cutoff=train_cutoff, evaluation_end=evaluation_end,
        seed=seed, repeats=repeats)
    labels_1 = sum(r.winner_side == 1 for r in training)
    report.update({
        "loader_version": VERSION,
        "source_rows_sha256": sha,
        "report_as_of_utc": as_of.isoformat(),
        "observed_and_ingested_before_training_cutoff": True,
        "evaluation_fixtures_have_original_30min_PIT_visibility": True,
        "training_side_1_fraction": labels_1 / len(training) if training else None,
        "potential_winner_first_orientation_flag": (
            labels_1 / len(training) >= .80 if training else None
        ),
        "evaluation_fixture_count": len(holdout),
        "historical_data_original_import_not_original_live_forecast": True,
        "cannot_validate_original_14000_match_claim": True,
        "no_db_mutations_or_provider_calls": True,
    })
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("train_cutoff", "evaluation_end", "as_of"):
        parser.add_argument("--" + name.replace("_", "-") + "-utc", required=True)
    parser.add_argument("--seed", type=int, default=91367)
    parser.add_argument("--repeats", type=int, default=5)
    a = parser.parse_args(argv)
    stamps = [getattr(a, name+"_utc") for name in
              ("train_cutoff", "evaluation_end", "as_of")]
    if any(not t.endswith("Z") for t in stamps):
        parser.error("all times must be explicit UTC Z")
    times = [datetime.fromisoformat(t.replace("Z","+00:00"))
             for t in stamps]
    dsn = os.environ.get("TENNIS_DATABASE_URL")
    if not dsn:
        parser.error("TENNIS_DATABASE_URL required")
    with psycopg.connect(dsn, autocommit=True, connect_timeout=10) as conn:
        result = run(conn, train_cutoff=times[0], evaluation_end=times[1],
                     as_of=times[2], seed=a.seed, repeats=a.repeats)
    print("TENNIS_A1_REAL_LABEL_SHUFFLE " + json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
