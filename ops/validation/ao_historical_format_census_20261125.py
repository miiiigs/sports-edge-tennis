"""Read-only Australian Open / Grand Slam historical match-format census.

Executes against the authorised read-only PostgreSQL database. It never
rewrites a match version, never treats a missing row as a correction and never
assumes 2027 regulations. Output is evidence JSON only.
"""
from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from datetime import UTC, datetime

import psycopg

VERSION = "AO_GRAND_SLAM_FORMAT_CENSUS_20261125_V1"
MAIN_ROUNDS = ("R128", "R64", "R32", "R16", "QF", "SF", "F")


def _rows(conn, sql, params=None):
    return conn.execute(sql, params or {}).fetchall()


def _dicts(conn, sql, params=None):
    cur = conn.execute(sql, params or {})
    names = [d.name for d in cur.description]
    return [dict(zip(names, row, strict=True)) for row in cur.fetchall()]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-json", required=True)
    args = parser.parse_args(argv)
    dsn = os.getenv("TENNIS_DATABASE_URL")
    if not dsn:
        parser.error("TENNIS_DATABASE_URL required")

    with psycopg.connect(dsn, autocommit=True, connect_timeout=15) as conn:
        conn.execute("SET default_transaction_read_only=on")
        conn.execute("SET statement_timeout='300s'")

        editions = _dicts(
            conn,
            """
            select t.tournament_id, t.canonical_name, t.tour_scope::text as tour_scope,
                   t.competition_tier::text as competition_tier,
                   te.tournament_edition_id, te.season_year, te.edition_name,
                   te.surface::text as edition_surface, te.start_date, te.end_date
            from tournaments t
            join tournament_editions te on te.tournament_id=t.tournament_id
            where t.competition_tier::text='GRAND_SLAM'
               or t.canonical_name ilike %s
            order by t.canonical_name, te.season_year, te.tournament_edition_id
            """,
            ("%australian%",),
        )

        slam_census = _dicts(
            conn,
            """
            with v as (
              select t.canonical_name, te.season_year, te.tournament_edition_id,
                     mv.match_id, mv.round_code, mv.best_of_sets, mv.qualifying_flag,
                     mv.match_start_at,
                     min(mv.match_start_at) filter (where mv.round_code='R128')
                       over (partition by te.tournament_edition_id) as main_draw_start
              from match_versions mv
              join matches m on m.match_id=mv.match_id
              join tournament_editions te on te.tournament_edition_id=m.tournament_edition_id
              join tournaments t on t.tournament_id=te.tournament_id
              where t.competition_tier::text='GRAND_SLAM'
            )
            select canonical_name, season_year, tournament_edition_id,
                   case when round_code in ('Q1','Q2','Q3') then 'QUALIFYING'
                        when round_code in ('R128','R64','R32','R16','QF','SF','F')
                          and match_start_at is not null
                          and main_draw_start is not null
                          and match_start_at < main_draw_start
                        then 'QUALIFYING'
                        when round_code in ('R128','R64','R32','R16','QF','SF','F')
                        then 'MAIN_DRAW'
                        else 'UNKNOWN' end as draw_stage,
                   round_code, best_of_sets, qualifying_flag,
                   count(distinct match_id) as matches,
                   min(match_start_at) as first_start,
                   max(match_start_at) as last_start
            from v
            group by 1,2,3,4,5,6,7
            order by 1,2,4,5,6,7
            """,
        )

        ao_rows = _dicts(
            conn,
            """
            with v as (
              select t.canonical_name, t.competition_tier::text as tournament_tier,
                     te.season_year, te.tournament_edition_id, te.surface::text as edition_surface,
                     mv.match_id, mv.match_version_id, mv.player_1_id, mv.player_2_id,
                     mv.round_code, mv.best_of_sets, mv.qualifying_flag, mv.match_start_at,
                     mv.source_observed_at, mv.ingested_at,
                     min(mv.match_start_at) filter (where mv.round_code='R128')
                       over (partition by te.tournament_edition_id) as main_draw_start
              from match_versions mv
              join matches m on m.match_id=mv.match_id
              join tournament_editions te on te.tournament_edition_id=m.tournament_edition_id
              join tournaments t on t.tournament_id=te.tournament_id
              where t.canonical_name ilike %s
            )
            select *, case when round_code in ('Q1','Q2','Q3') then 'QUALIFYING'
                            when round_code in ('R128','R64','R32','R16','QF','SF','F')
                              and match_start_at is not null
                              and main_draw_start is not null
                              and match_start_at < main_draw_start
                            then 'QUALIFYING'
                            when round_code in ('R128','R64','R32','R16','QF','SF','F')
                            then 'MAIN_DRAW'
                            else 'UNKNOWN' end as draw_stage
            from v order by season_year, tournament_edition_id, match_start_at, match_id
            """,
            ("%australian%",),
        )

        ao_result_counts = _dicts(
            conn,
            """
            select te.season_year, te.tournament_edition_id, mro.outcome_status::text,
                   count(distinct mro.match_id) as matches
            from match_versions mv
            join matches m on m.match_id=mv.match_id
            join tournament_editions te on te.tournament_edition_id=m.tournament_edition_id
            join tournaments t on t.tournament_id=te.tournament_id
            left join match_result_observations mro on mro.match_id=mv.match_id
            where t.canonical_name ilike %s
            group by 1,2,3 order by 1,2,3
            """,
            ("%australian%",),
        )

        ao_original_activity = _dicts(
            conn,
            """
            select 'predictions' as kind, count(*) as rows
            from predictions p
            join matches m on m.match_id=p.match_id
            join tournament_editions te on te.tournament_edition_id=m.tournament_edition_id
            join tournaments t on t.tournament_id=te.tournament_id
            where t.canonical_name ilike %s
            union all
            select 'prediction_run_targets', count(*)
            from prediction_run_targets prt
            join matches m on m.match_id=prt.match_id
            join tournament_editions te on te.tournament_edition_id=m.tournament_edition_id
            join tournaments t on t.tournament_id=te.tournament_id
            where t.canonical_name ilike %s
            union all
            select 'manager_consensus_results', count(*)
            from manager_consensus_results mr
            join matches m on m.match_id=mr.match_id
            join tournament_editions te on te.tournament_edition_id=m.tournament_edition_id
            join tournaments t on t.tournament_id=te.tournament_id
            where t.canonical_name ilike %s
            union all
            select 'decisions', count(*)
            from decisions d
            join matches m on m.match_id=d.match_id
            join tournament_editions te on te.tournament_edition_id=m.tournament_edition_id
            join tournaments t on t.tournament_id=te.tournament_id
            where t.canonical_name ilike %s
            """,
            ("%australian%", "%australian%", "%australian%", "%australian%"),
        )

        ao_sources = _dicts(
            conn,
            """
            select te.season_year, msi.source_system, count(*) as rows,
                   count(distinct msi.match_id) as matches
            from match_source_ids msi
            join matches m on m.match_id=msi.match_id
            join tournament_editions te on te.tournament_edition_id=m.tournament_edition_id
            join tournaments t on t.tournament_id=te.tournament_id
            where t.canonical_name ilike %s
            group by 1,2 order by 1,2
            """,
            ("%australian%",),
        )

        january = _dicts(
            conn,
            """
            select te.season_year, t.canonical_name, t.competition_tier::text,
                   te.surface::text, te.start_date, te.end_date,
                   count(distinct mv.match_id) as matches,
                   min(mv.match_start_at) as first_start,
                   max(mv.match_start_at) as last_start
            from tournament_editions te
            join tournaments t on t.tournament_id=te.tournament_id
            left join matches m on m.tournament_edition_id=te.tournament_edition_id
            left join match_versions mv on mv.match_id=m.match_id
            where (te.start_date >= '2026-01-01' and te.start_date < '2026-03-01')
               or (te.start_date >= '2027-01-01' and te.start_date < '2027-03-01')
            group by 1,2,3,4,5,6 order by 5,2
            """,
        )

        ao_2026_editions = _dicts(
            conn,
            """
            select te.tournament_edition_id, te.edition_name, te.surface::text,
                   te.start_date, te.end_date, t.competition_tier::text
            from tournament_editions te
            join tournaments t on t.tournament_id=te.tournament_id
            where te.season_year=2026 and t.canonical_name ilike %s
            order by te.tournament_edition_id
            """,
            ("%australian%",),
        )

        ao_2026_rounds = _dicts(
            conn,
            """
            select te.tournament_edition_id, mv.round_code, mv.best_of_sets,
                   mv.qualifying_flag, count(distinct mv.match_id) as matches,
                   min(mv.match_start_at) as first_start, max(mv.match_start_at) as last_start
            from match_versions mv
            join matches m on m.match_id=mv.match_id
            join tournament_editions te on te.tournament_edition_id=m.tournament_edition_id
            join tournaments t on t.tournament_id=te.tournament_id
            where te.season_year=2026 and t.canonical_name ilike %s
            group by 1,2,3,4 order by 1,2,3,4
            """,
            ("%australian%",),
        )

        doha_2021 = _dicts(
            conn,
            """
            select te.tournament_edition_id, t.canonical_name, mv.round_code,
                   mv.best_of_sets, mv.qualifying_flag, count(distinct mv.match_id) as matches
            from match_versions mv
            join matches m on m.match_id=mv.match_id
            join tournament_editions te on te.tournament_edition_id=m.tournament_edition_id
            join tournaments t on t.tournament_id=te.tournament_id
            where t.canonical_name = 'Doha Aus Open Qualies'
            group by 1,2,3,4,5 order by 3
            """,
        )

        slam_original_predictions = _dicts(
            conn,
            """
            select t.canonical_name, te.season_year, count(distinct p.match_id) as matches,
                   count(*) as rows
            from predictions p
            join matches m on m.match_id=p.match_id
            join tournament_editions te on te.tournament_edition_id=m.tournament_edition_id
            join tournaments t on t.tournament_id=te.tournament_id
            where t.competition_tier::text='GRAND_SLAM' and p.experiment_id is null
            group by 1,2 order by 2,1
            """,
        )

        ao_source_ids = _dicts(
            conn,
            """
            select t.tournament_id, t.canonical_name, tsi.source_system,
                   tsi.source_tournament_id, count(*) as rows
            from tournament_source_ids tsi
            join tournaments t on t.tournament_id=tsi.tournament_id
            where t.canonical_name ilike %s or t.canonical_name ilike %s
            group by 1,2,3,4 order by 1,3,4
            """,
            ("%australian%", "%aus open%"),
        )

        ao_2026_anomalies = _dicts(
            conn,
            """
            with v as (
              select mv.match_id, te.tournament_edition_id, mv.round_code,
                     mv.match_start_at, mv.player_1_id, mv.player_2_id
              from match_versions mv
              join matches m on m.match_id=mv.match_id
              join tournament_editions te on te.tournament_edition_id=m.tournament_edition_id
              join tournaments t on t.tournament_id=te.tournament_id
              where te.season_year=2026 and t.canonical_name ilike %s
                and mv.qualifying_flag = false
            )
            select round_code, count(*) as matches,
                   count(*) filter (where tournament_edition_id=2249) as in_edition_2249
            from v group by 1 order by 1
            """,
            ("%australian%",),
        )


        provider_metadata = _dicts(
            conn,
            """
            select count(*) as rows,
                   count(distinct (source_system, source_match_id)) as provider_fixtures,
                   min(observed_at) as first_observed, max(observed_at) as last_observed,
                   min(created_at) as first_created, max(created_at) as last_created
            from provider_fixture_metadata
            """,
        )

    main = [r for r in ao_rows if r["draw_stage"] == "MAIN_DRAW"]
    qual = [r for r in ao_rows if r["draw_stage"] == "QUALIFYING"]
    incorrect_bo3 = [r for r in main if r["best_of_sets"] == 3]
    main_not_bo5 = [r for r in main if r["best_of_sets"] != 5]
    qual_not_bo3 = [r for r in qual if r["best_of_sets"] != 3]
    qf_main_misflagged = [
        r for r in main if r["round_code"] == "QF" and r["qualifying_flag"] is True
    ]
    qual_round_labels = Counter(r["round_code"] for r in qual)
    ao_2026_main = [r for r in ao_rows if r["season_year"] == 2026 and r["draw_stage"] == "MAIN_DRAW"]
    ao_2026_round_counts = dict(sorted(Counter(r["round_code"] for r in ao_2026_main).items()))
    expected_2026 = {"R128": 64, "R64": 32, "R32": 16, "R16": 8, "QF": 4, "SF": 2, "F": 1}
    ao_2026_round_delta = {
        key: ao_2026_round_counts.get(key, 0) - expected_2026[key]
        for key in expected_2026
    }
    per_year = {}
    for year in sorted({r["season_year"] for r in ao_rows}):
        rows = [r for r in ao_rows if r["season_year"] == year]
        per_year[str(year)] = {
            "versions": len(rows),
            "matches": len({r["match_id"] for r in rows}),
            "main_draw_matches": len({r["match_id"] for r in rows if r["draw_stage"] == "MAIN_DRAW"}),
            "qualifying_matches": len({r["match_id"] for r in rows if r["draw_stage"] == "QUALIFYING"}),
            "main_draw_best_of": dict(sorted(Counter(
                r["best_of_sets"] for r in rows if r["draw_stage"] == "MAIN_DRAW"
            ).items())),
            "qualifying_best_of": dict(sorted(Counter(
                r["best_of_sets"] for r in rows if r["draw_stage"] == "QUALIFYING"
            ).items())),
            "main_draw_rounds": dict(sorted(Counter(
                r["round_code"] for r in rows if r["draw_stage"] == "MAIN_DRAW"
            ).items())),
            "qualifying_rounds": dict(sorted(Counter(
                r["round_code"] for r in rows if r["draw_stage"] == "QUALIFYING"
            ).items())),
            "edition_surfaces": sorted({str(r["edition_surface"]) for r in rows}),
            "tournament_tiers": sorted({r["tournament_tier"] for r in rows}),
            "edition_count": len({r["tournament_edition_id"] for r in rows}),
        }

    report = {
        "version": VERSION,
        "report_as_of_utc": datetime.now(UTC).isoformat(),
        "read_only": True,
        "no_historical_rows_corrected": True,
        "no_2027_rule_assumption": True,
        "editions": editions,
        "grand_slam_census": slam_census,
        "ao_census": {
            "versions": len(ao_rows),
            "unique_matches": len({r["match_id"] for r in ao_rows}),
            "main_draw_matches": len({r["match_id"] for r in main}),
            "qualifying_matches": len({r["match_id"] for r in qual}),
            "main_draw_incorrect_bo3_matches": len({r["match_id"] for r in incorrect_bo3}),
            "main_draw_not_bo5_matches": len({r["match_id"] for r in main_not_bo5}),
            "qualifying_not_bo3_matches": len({r["match_id"] for r in qual_not_bo3}),
            "main_draw_qf_misflagged_qualifying_matches": len({r["match_id"] for r in qf_main_misflagged}),
            "main_draw_best_of": dict(sorted(Counter(r["best_of_sets"] for r in main).items())),
            "qualifying_best_of": dict(sorted(Counter(r["best_of_sets"] for r in qual).items())),
            "qualifying_round_labels": dict(sorted(qual_round_labels.items())),
            "incorrect_bo3_by_year_round": dict(sorted(Counter(
                f"{r['season_year']}:{r['round_code']}" for r in incorrect_bo3
            ).items())),
            "incorrect_bo3_editions": sorted({
                (r["season_year"], r["tournament_edition_id"], r["tournament_tier"])
                for r in incorrect_bo3
            }),
            "main_draw_qf_misflag_by_year": dict(sorted(Counter(
                str(r["season_year"]) for r in qf_main_misflagged
            ).items())),
            "per_year": per_year,
            "ao_2026_main_draw_round_counts": ao_2026_round_counts,
            "ao_2026_expected_main_draw_round_counts": expected_2026,
            "ao_2026_main_draw_round_delta": ao_2026_round_delta,
            "original_activity": ao_original_activity,
            "sources": ao_sources,
            "result_counts_by_edition": ao_result_counts,
        },
        "january_discovery": january,
        "ao_2026_editions": ao_2026_editions,
        "ao_2026_rounds": ao_2026_rounds,
        "ao_2021_doha_qualifying": doha_2021,
        "grand_slam_original_predictions": slam_original_predictions,
        "ao_tournament_source_ids": ao_source_ids,
        "ao_2026_main_draw_round_counts": ao_2026_anomalies,
        "provider_fixture_metadata": provider_metadata,
        "comparison_blocker": {
            "lawful_original_ao_prematch_predictions": 0,
            "reason": "NO_ORIGINAL_PREMATCH_AO_FIXTURES_IN_PREDICTION_RUNS",
            "note": "The 2016-2026 AO rows are retrospective history, not original issued forecasts. "
                    "No matched original BO3/BO5 forecast pair exists to score.",
        },
    }
    with open(args.out_json, "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, sort_keys=True, default=str)
    print("AO_FORMAT_CENSUS_WROTE " + args.out_json)
    print(json.dumps({
        "ao_versions": report["ao_census"]["versions"],
        "ao_matches": report["ao_census"]["unique_matches"],
        "main_draw_matches": report["ao_census"]["main_draw_matches"],
        "qualifying_matches": report["ao_census"]["qualifying_matches"],
        "main_draw_incorrect_bo3_matches": report["ao_census"]["main_draw_incorrect_bo3_matches"],
        "main_draw_qf_misflagged": report["ao_census"]["main_draw_qf_misflagged_qualifying_matches"],
        "qualifying_round_labels": report["ao_census"]["qualifying_round_labels"],
        "original_activity": report["ao_census"]["original_activity"],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
