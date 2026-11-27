"""Read-only Australian Open root-cause and dependency evidence.

Brian's 25 November 2026 audit requires the exact historical fixture population,
the retained provider-payload status, the ingestion code cause, the 2026 edition
reconciliation, and a measured dependency analysis instead of an assumption that
the best-of-three defect invalidates Agent 1.

The script never writes to the database. It emits an evidence JSON plus optional
CSV fixture manifests. Run it with an authorised read-only DSN:

    $env:TENNIS_DATABASE_URL = <authorised read-only DSN>
    python ops/validation/ao_root_cause_and_dependency_20261126.py \
      --out-json docs/research/evidence/ao_root_cause_and_dependency_20261126.json \
      --out-csv-dir docs/research/evidence/ao_root_cause_fixtures_20261126

No provider call, no 2027 rule assumption, no historical rewrite.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import psycopg

VERSION = "AO_ROOT_CAUSE_AND_DEPENDENCY_20261126_V1"
MAIN_ROUNDS = ("R128", "R64", "R32", "R16", "QF", "SF", "F")
QUAL_ROUNDS = ("Q1", "Q2", "Q3")
# The A1 identity-bridge workstream (PR #388) defines these three reference
# populations. They are date-scoped to 2026-09-10 and contain no AO fixture.
DECLARED_RESEARCH_POPULATIONS = (3152, 3201, 3223)


def independent_set_match_probability(set_win_probability: float, best_of_sets: int) -> float:
    """Probability of winning a match when set outcomes are i.i.d. Bernoulli.

    This is Brian's requested simple reference, not the serve-based production
    Markov engine. It is deliberately independent of serve, tiebreak and
    server-order mechanics.
    """
    if best_of_sets not in (3, 5):
        raise ValueError("best_of_sets must be 3 or 5")
    if not 0.0 <= set_win_probability <= 1.0:
        raise ValueError("set_win_probability must be within [0, 1]")
    sets_to_win = best_of_sets // 2 + 1
    probability = 0.0
    for losses in range(sets_to_win):
        wins = sets_to_win
        combinations = math.comb(wins + losses - 1, losses)
        probability += (
            combinations
            * set_win_probability**wins
            * (1.0 - set_win_probability) ** losses
        )
    return probability


def _dicts(conn, sql, params=None):
    # Only pass a parameter mapping when one is needed: a bare LIKE '%...%'
    # pattern is otherwise parsed as a psycopg placeholder.
    cur = conn.execute(sql, params) if params else conn.execute(sql)
    names = [d.name for d in cur.description]
    return [dict(zip(names, row, strict=True)) for row in cur.fetchall()]


def _sha256_ids(ids) -> str:
    joined = ",".join(str(value) for value in sorted(ids))
    return hashlib.sha256(joined.encode()).hexdigest()


def _write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    key: ";".join(str(item) for item in value)
                    if isinstance(value, list)
                    else value
                    for key, value in row.items()
                }
            )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-json", required=True)
    parser.add_argument("--out-csv-dir")
    parser.add_argument(
        "--a1-sensitivity-json",
        help="optional output of ao_a1_surface_sensitivity_20261126.py to embed",
    )
    args = parser.parse_args(argv)
    dsn = os.getenv("TENNIS_DATABASE_URL")
    if not dsn:
        parser.error("TENNIS_DATABASE_URL required")
    a1_sensitivity = None
    if args.a1_sensitivity_json:
        a1_sensitivity = json.loads(
            Path(args.a1_sensitivity_json).read_text(encoding="utf-8")
        )

    with psycopg.connect(dsn, autocommit=True, connect_timeout=20) as conn:
        conn.execute("SET default_transaction_read_only=on")
        conn.execute("SET statement_timeout='900s'")

        editions = _dicts(
            conn,
            """
            select t.tournament_id, t.canonical_name, t.tour_scope::text as tour_scope,
                   t.competition_tier::text as competition_tier,
                   te.tournament_edition_id, te.season_year, te.edition_name,
                   te.surface::text as edition_surface, te.start_date, te.end_date,
                   te.created_at, te.updated_at,
                   (select count(distinct m.match_id) from matches m
                     where m.tournament_edition_id=te.tournament_edition_id) as matches
            from tournaments t
            join tournament_editions te on te.tournament_id=t.tournament_id
            where t.canonical_name ilike '%australian%' or t.canonical_name ilike '%aus open%'
            order by te.season_year, te.tournament_edition_id
            """,
        )

        ao_versions = _dicts(
            conn,
            """
            with v as (
              select t.canonical_name, t.competition_tier::text as tournament_tier,
                     te.season_year, te.tournament_edition_id,
                     te.surface::text as edition_surface,
                     mv.match_version_id, mv.match_id, mv.player_1_id, mv.player_2_id,
                     p1.canonical_name as player_1, p2.canonical_name as player_2,
                     mv.round_code, mv.best_of_sets, mv.qualifying_flag,
                     mv.match_status::text as match_status, mv.match_start_at,
                     mv.source_observed_at, mv.as_of, mv.ingested_at, mv.ingest_run_id,
                     mro.outcome_status::text as outcome_status, mro.winner_side,
                     mro.scoreline_text,
                     (select count(*) from match_stat_observations mso
                       where mso.match_id=mv.match_id) as stat_rows,
                     min(mv.match_start_at) filter (where mv.round_code='R128')
                       over (partition by te.tournament_edition_id) as main_draw_start
              from match_versions mv
              join matches m on m.match_id=mv.match_id
              join tournament_editions te on te.tournament_edition_id=m.tournament_edition_id
              join tournaments t on t.tournament_id=te.tournament_id
              join players p1 on p1.player_id=mv.player_1_id
              join players p2 on p2.player_id=mv.player_2_id
              left join lateral (
                select outcome_status, winner_side, scoreline_text
                from match_result_observations r
                where r.match_id=mv.match_id
                order by r.as_of desc, r.source_observed_at desc,
                         r.match_result_observation_id desc
                limit 1
              ) mro on true
              where t.canonical_name ilike '%australian%'
            )
            select *, case
                when round_code in ('Q1','Q2','Q3') then 'QUALIFYING'
                when round_code in ('R128','R64','R32','R16','QF','SF','F')
                  and match_start_at is not null and main_draw_start is not null
                  and match_start_at < main_draw_start then 'QUALIFYING'
                when round_code in ('R128','R64','R32','R16','QF','SF','F')
                  then 'MAIN_DRAW'
                else 'UNKNOWN' end as draw_stage
            from v
            order by season_year, tournament_edition_id, match_start_at, match_id
            """,
        )

        source_rows = _dicts(
            conn,
            """
            select msi.match_id, msi.match_version_id, msi.source_system,
                   msi.source_match_id, msi.source_record_hash,
                   msi.source_record_locator, msi.source_file_name,
                   msi.ingest_run_id, msi.created_at
            from match_source_ids msi
            join matches m on m.match_id=msi.match_id
            join tournament_editions te on te.tournament_edition_id=m.tournament_edition_id
            join tournaments t on t.tournament_id=te.tournament_id
            where t.canonical_name ilike '%australian%'
            """,
        )
        sources_by_match: dict[int, list[dict]] = {}
        for row in source_rows:
            sources_by_match.setdefault(row["match_id"], []).append(row)

        version_sources = _dicts(
            conn,
            """
            select mvs.match_version_id, mvs.source_system, mvs.source_match_id,
                   mvs.source_record_hash, mvs.source_record_locator,
                   mvs.source_file_name, mvs.source_version_hash, mvs.ingest_run_id
            from match_version_sources mvs
            join match_versions mv on mv.match_version_id=mvs.match_version_id
            join matches m on m.match_id=mv.match_id
            join tournament_editions te on te.tournament_edition_id=m.tournament_edition_id
            join tournaments t on t.tournament_id=te.tournament_id
            where t.canonical_name ilike '%australian%'
            """,
        )
        vsource_by_version: dict[int, list[dict]] = {}
        for row in version_sources:
            vsource_by_version.setdefault(row["match_version_id"], []).append(row)

        provider_retention = _dicts(
            conn,
            """
            select count(*) as rows,
                   count(distinct (source_system, source_match_id)) as fixtures,
                   min(observed_at) as first_observed_at,
                   max(observed_at) as last_observed_at,
                   min(created_at) as first_created_at,
                   max(created_at) as last_created_at
            from provider_fixture_metadata
            """,
        )[0]
        ao_provider_retention = _dicts(
            conn,
            """
            select msi.source_system, count(*) as source_rows,
                   count(pfm.provider_fixture_metadata_id) as provider_rows,
                   count(distinct pfm.payload_hash) as payload_hashes
            from match_source_ids msi
            join matches m on m.match_id=msi.match_id
            join tournament_editions te on te.tournament_edition_id=m.tournament_edition_id
            join tournaments t on t.tournament_id=te.tournament_id
            left join provider_fixture_metadata pfm
              on pfm.source_system=msi.source_system
             and pfm.source_match_id=msi.source_match_id
            where (t.canonical_name ilike '%australian%')
              and te.season_year=2026
            group by 1 order by 1
            """,
        )

        dependencies = _dicts(
            conn,
            """
            select 'predictions' as kind, count(*) as rows
            from predictions p
            join matches m on m.match_id=p.match_id
            join tournament_editions te on te.tournament_edition_id=m.tournament_edition_id
            join tournaments t on t.tournament_id=te.tournament_id
            where t.canonical_name ilike '%australian%'
            union all
            select 'prediction_run_targets', count(*)
            from prediction_run_targets prt
            join matches m on m.match_id=prt.match_id
            join tournament_editions te on te.tournament_edition_id=m.tournament_edition_id
            join tournaments t on t.tournament_id=te.tournament_id
            where t.canonical_name ilike '%australian%'
            union all
            select 'manager_consensus_results', count(*)
            from manager_consensus_results mr
            join matches m on m.match_id=mr.match_id
            join tournament_editions te on te.tournament_edition_id=m.tournament_edition_id
            join tournaments t on t.tournament_id=te.tournament_id
            where t.canonical_name ilike '%australian%'
            union all
            select 'decisions', count(*)
            from decisions d
            join matches m on m.match_id=d.match_id
            join tournament_editions te on te.tournament_edition_id=m.tournament_edition_id
            join tournaments t on t.tournament_id=te.tournament_id
            where t.canonical_name ilike '%australian%'
            """,
        )

        a1_completed = _dicts(
            conn,
            """
            select te.season_year, count(distinct mv.match_id) as completed_matches,
                   count(distinct mv.player_1_id) as player_1_count,
                   count(distinct mv.player_2_id) as player_2_count,
                   count(distinct mv.match_id) filter (where mv.surface is null) as null_surface_matches
            from match_versions mv
            join matches m on m.match_id=mv.match_id
            join tournament_editions te on te.tournament_edition_id=m.tournament_edition_id
            join tournaments t on t.tournament_id=te.tournament_id
            join lateral (
              select outcome_status from match_result_observations r
              where r.match_id=mv.match_id
              order by r.as_of desc, r.source_observed_at desc,
                       r.match_result_observation_id desc limit 1
            ) r on true
            where t.canonical_name ilike '%australian%' and r.outcome_status='COMPLETED'
            group by 1 order by 1
            """,
        )

        a2_stats = _dicts(
            conn,
            """
            select te.season_year, count(distinct mso.match_id) as matches_with_stats,
                   count(*) as stat_rows
            from match_stat_observations mso
            join matches m on m.match_id=mso.match_id
            join tournament_editions te on te.tournament_edition_id=m.tournament_edition_id
            join tournaments t on t.tournament_id=te.tournament_id
            where t.canonical_name ilike '%australian%'
            group by 1 order by 1
            """,
        )

        a2_qf_eligibility = _dicts(
            conn,
            """
            select count(*) as qf_rows,
                   count(*) filter (where r.outcome_status='COMPLETED')
                     as completed_rows,
                   count(*) filter (where s.stat_rows=2) as two_stat_rows,
                   count(*) filter (
                     where r.outcome_status='COMPLETED' and s.stat_rows=2
                   ) as a2_eligible_rows
            from match_versions mv
            join matches m on m.match_id=mv.match_id
            join tournament_editions te on te.tournament_edition_id=m.tournament_edition_id
            join tournaments t on t.tournament_id=te.tournament_id
            join lateral (
              select outcome_status from match_result_observations rr
              where rr.match_id=mv.match_id
              order by rr.as_of desc, rr.source_observed_at desc,
                       rr.match_result_observation_id desc limit 1
            ) r on true
            join lateral (
              select count(*) as stat_rows from match_stat_observations ss
              where ss.match_id=mv.match_id
            ) s on true
            where t.canonical_name='Australian Open'
              and mv.round_code='QF' and mv.qualifying_flag
              and te.season_year between 2016 and 2025
            """,
        )[0]

        split_editions_2026 = _dicts(
            conn,
            """
            select t.tournament_id, t.canonical_name, te.season_year,
                   count(*) as edition_count,
                   array_agg(te.tournament_edition_id order by te.tournament_edition_id) as edition_ids,
                   array_agg(te.edition_name order by te.tournament_edition_id) as edition_names,
                   array_agg(te.surface::text order by te.tournament_edition_id) as surfaces,
                   array_agg(te.end_date order by te.tournament_edition_id) as end_dates
            from tournament_editions te
            join tournaments t on t.tournament_id=te.tournament_id
            where te.season_year=2026
            group by 1,2,3 having count(*) > 1
            order by 1
            """,
        )

        edition_hygiene_2026 = _dicts(
            conn,
            """
            select t.tour_scope::text as tour_scope,
                   t.competition_tier::text as competition_tier,
                   count(*) as editions,
                   count(*) filter (where te.surface is null) as null_surface,
                   count(*) filter (where te.end_date is null) as null_end_date
            from tournament_editions te
            join tournaments t on t.tournament_id=te.tournament_id
            where te.season_year=2026
            group by 1,2 order by 1,2
            """,
        )

        january_2026_classification = _dicts(
            conn,
            """
            select t.canonical_name, t.tour_scope::text as tour_scope,
                   t.competition_tier::text as competition_tier,
                   te.tournament_edition_id, te.edition_name,
                   te.surface::text as edition_surface,
                   te.start_date, te.end_date,
                   count(distinct mv.match_id) as matches,
                   count(distinct mv.match_id) filter (where mv.qualifying_flag)
                     as qualifying_matches,
                   count(distinct mv.match_id) filter (where not mv.qualifying_flag)
                     as main_draw_matches,
                   count(distinct mv.match_id) filter (where mv.best_of_sets=5)
                     as bo5_matches,
                   count(distinct mv.match_id) filter (where mv.best_of_sets=3)
                     as bo3_matches
            from tournament_editions te
            join tournaments t on t.tournament_id=te.tournament_id
            left join matches m on m.tournament_edition_id=te.tournament_edition_id
            left join match_versions mv on mv.match_id=m.match_id
            where te.season_year=2026
              and t.canonical_name in (
                'Brisbane','Hong Kong','Adelaide','Auckland',
                'Australian Open','ATP United Cup'
              )
            group by 1,2,3,4,5,6,7,8
            order by 7,1,4
            """,
        )

        january_2026_activity = _dicts(
            conn,
            """
            select t.canonical_name,
                   count(distinct p.prediction_id) as predictions,
                   count(distinct prt.match_id) as prediction_targets,
                   count(distinct mr.manager_consensus_result_id) as manager_consensus,
                   count(distinct d.decision_id) as decisions,
                   count(distinct d.decision_id) filter (
                     where d.published_at is not null
                   ) as published_decisions
            from tournaments t
            join tournament_editions te on te.tournament_id=t.tournament_id
            left join matches m on m.tournament_edition_id=te.tournament_edition_id
            left join predictions p on p.match_id=m.match_id
            left join prediction_run_targets prt on prt.match_id=m.match_id
            left join manager_consensus_results mr on mr.match_id=m.match_id
            left join decisions d on d.match_id=m.match_id
            where te.season_year=2026
              and t.canonical_name in (
                'Brisbane','Hong Kong','Adelaide','Auckland',
                'Australian Open','ATP United Cup'
              )
            group by 1 order by 1
            """,
        )

        # The declared A1 research populations are date-scoped to 2026-09-10.
        # Reproduce the current database population and its exact intersection
        # with the AO fixture set. Exact equality to 3,152/3,201/3,223 requires
        # the frozen fixture-id manifest from the A1 workstream.
        target_rows = _dicts(
            conn,
            """
            with lv as (
              select distinct on (match_id) match_id, match_start_at
              from match_versions
              order by match_id, as_of desc, source_observed_at desc, match_version_id desc
            ), a1 as (
              select distinct on (p.match_id) p.prediction_id, p.match_id, p.as_of,
                     p.selection_side, p.win_probability, lv.match_start_at
              from predictions p
              join prediction_runs pr using (prediction_run_id)
              join lv using (match_id)
              where pr.experiment_id is null and pr.run_status='COMPLETED'
                and p.agent_code='AGENT_1_SURFACE_ELO'
                and p.prediction_status='PREDICTED'
                and p.created_at < lv.match_start_at
                and p.as_of >= %(since)s
              order by p.match_id, p.created_at desc
            )
            select a1.match_id,
                   coalesce(r.outcome_status::text, 'UNSETTLED') as outcome_status,
                   r.winner_side
            from a1
            left join lateral (
              select outcome_status, winner_side from match_result_observations r0
              where r0.match_id=a1.match_id
                and r0.outcome_status in ('COMPLETED','RETIRED','WALKOVER','CANCELLED')
              order by r0.as_of desc, r0.source_observed_at desc,
                       r0.match_result_observation_id desc limit 1
            ) r on true
            """,
            {"since": datetime(2026, 9, 10, tzinfo=UTC)},
        )

    main_rows = [r for r in ao_versions if r["draw_stage"] == "MAIN_DRAW"]
    qual_rows = [r for r in ao_versions if r["draw_stage"] == "QUALIFYING"]
    bo3_main = [r for r in main_rows if r["best_of_sets"] == 3]
    qual_2026 = [r for r in qual_rows if r["season_year"] == 2026]
    qf_misflagged = [
        r for r in main_rows if r["round_code"] == "QF" and r["qualifying_flag"] is True
    ]
    phantom = [r for r in ao_versions if r["tournament_edition_id"] == 2249]
    edition_2248 = [r for r in ao_versions if r["tournament_edition_id"] == 2248]
    ao_2026_rows = [r for r in ao_versions if r["season_year"] == 2026]

    def slim(rows: list[dict]) -> list[dict]:
        output = []
        for row in rows:
            sources = sources_by_match.get(row["match_id"], [])
            source = sources[0] if sources else {}
            version_sources = vsource_by_version.get(row["match_version_id"], [])
            version_source = version_sources[0] if version_sources else {}
            output.append(
                {
                    "match_id": row["match_id"],
                    "match_version_id": row["match_version_id"],
                    "tournament_edition_id": row["tournament_edition_id"],
                    "season_year": row["season_year"],
                    "round_code": row["round_code"],
                    "best_of_sets": row["best_of_sets"],
                    "qualifying_flag": row["qualifying_flag"],
                    "match_status": row["match_status"],
                    "match_start_at": row["match_start_at"],
                    "outcome_status": row["outcome_status"],
                    "player_1": row["player_1"],
                    "player_2": row["player_2"],
                    "player_1_id": row["player_1_id"],
                    "player_2_id": row["player_2_id"],
                    "stat_rows": row["stat_rows"],
                    "source_system": source.get("source_system"),
                    "source_match_id": source.get("source_match_id"),
                    "source_record_hash": source.get("source_record_hash"),
                    "source_record_locator": source.get("source_record_locator"),
                    "source_ingest_run_id": source.get("ingest_run_id"),
                    "version_source_locator": version_source.get("source_record_locator"),
                    "version_source_file": version_source.get("source_file_name"),
                    "version_source_hash": version_source.get("source_version_hash"),
                    "api_tennis_event_ids": [
                        item["source_match_id"]
                        for item in sources
                        if item["source_system"] == "API_TENNIS"
                    ],
                }
            )
        return output

    # Bracket reconstruction: which R32 winners never appear in an R16 row?
    r32_winners = []
    r16_players = set()
    for row in edition_2248:
        if row["round_code"] == "R16":
            r16_players.update((row["player_1_id"], row["player_2_id"]))
    for row in edition_2248:
        if row["round_code"] == "R32" and row["outcome_status"] == "COMPLETED":
            if row["winner_side"] == 1:
                r32_winners.append((row["match_id"], row["player_1_id"], row["player_1"]))
            elif row["winner_side"] == 2:
                r32_winners.append((row["match_id"], row["player_2_id"], row["player_2"]))
    missing_r16 = [
        {"r32_match_id": match_id, "player_id": player_id, "player": name}
        for match_id, player_id, name in r32_winners
        if player_id not in r16_players
    ]

    ao_ids = {row["match_id"] for row in ao_versions}
    ao_2026_ids = {row["match_id"] for row in ao_2026_rows}
    target_ids = {row["match_id"] for row in target_rows}
    settled_ids = {
        row["match_id"]
        for row in target_rows
        if row["outcome_status"] == "COMPLETED" and row["winner_side"] in (1, 2)
    }

    reference = {
        "set_win_probability": 0.60,
        "bo3_match_probability": independent_set_match_probability(0.60, 3),
        "bo5_match_probability": independent_set_match_probability(0.60, 5),
        "note": "Independent set-level Bernoulli reference only; not the serve-based Markov engine.",
    }

    report = {
        "version": VERSION,
        "report_as_of_utc": datetime.now(UTC).isoformat(),
        "read_only": True,
        "no_historical_rows_rewritten": True,
        "no_2027_rule_assumption": True,
        "reference_populations_declared_by_a1_workstream": list(
            DECLARED_RESEARCH_POPULATIONS
        ),
        "editions": editions,
        "population": {
            "ao_versions": len(ao_versions),
            "ao_unique_matches": len({row["match_id"] for row in ao_versions}),
            "main_draw_versions": len(main_rows),
            "main_draw_unique_matches": len({row["match_id"] for row in main_rows}),
            "qualifying_versions": len(qual_rows),
            "qualifying_unique_matches": len({row["match_id"] for row in qual_rows}),
            "main_draw_bo3_matches": len({row["match_id"] for row in bo3_main}),
            "main_draw_bo3_by_edition_round": dict(
                sorted(
                    Counter(
                        f"{row['season_year']}:{row['tournament_edition_id']}:{row['round_code']}"
                        for row in bo3_main
                    ).items()
                )
            ),
            "qualifying_round_labels": dict(
                sorted(Counter(row["round_code"] for row in qual_rows).items())
            ),
            "qualifying_round_labels_2026": dict(
                sorted(Counter(row["round_code"] for row in qual_2026).items())
            ),
            "main_draw_qf_misflagged_qualifying_matches": len(
                {row["match_id"] for row in qf_misflagged}
            ),
            "main_draw_qf_misflagged_by_year": dict(
                sorted(Counter(str(row["season_year"]) for row in qf_misflagged).items())
            ),
        },
        "exact_fixture_sets": {
            "ao_2026_main_draw_bo3_sha256": _sha256_ids(
                row["match_id"] for row in bo3_main
            ),
            "ao_2026_main_draw_bo3_ids": sorted({row["match_id"] for row in bo3_main}),
            "ao_2026_qualifying_sha256": _sha256_ids(
                row["match_id"] for row in qual_2026
            ),
            "ao_2026_qualifying_ids": sorted({row["match_id"] for row in qual_2026}),
            "ao_2026_all_fixtures_sha256": _sha256_ids(
                row["match_id"] for row in ao_2026_rows
            ),
            "ao_2026_all_fixture_ids": sorted(
                {row["match_id"] for row in ao_2026_rows}
            ),
            "ao_2016_2025_main_draw_qf_misflag_sha256": _sha256_ids(
                row["match_id"] for row in qf_misflagged
            ),
            "ao_2016_2025_main_draw_qf_misflag_ids": sorted(
                {row["match_id"] for row in qf_misflagged}
            ),
            "ao_2026_edition_2249_phantom_sha256": _sha256_ids(
                row["match_id"] for row in phantom
            ),
            "ao_2026_edition_2249_phantom_ids": sorted(
                {row["match_id"] for row in phantom}
            ),
        },
        "provider_payload_retention": {
            "provider_fixture_metadata_overall": provider_retention,
            "ao_provider_coverage": ao_provider_retention,
            "ao_2026_original_payload_retained": any(
                row["provider_rows"] for row in ao_provider_retention
            ),
            "retained_fields": (
                "match_source_ids/match_version_sources keep source id, record hash, "
                "row/file locator, source-file hash and ingest run. The full provider "
                "payload is not retained for the 2026 AO backfill."
            ),
            "api_tennis_public_fixture_schema_fields": [
                "event_key",
                "event_date",
                "event_time",
                "event_first_player",
                "first_player_key",
                "event_second_player",
                "second_player_key",
                "event_final_result",
                "event_game_result",
                "event_serve",
                "event_winner",
                "event_status",
                "event_type_type",
                "tournament_name",
                "tournament_key",
                "tournament_round",
                "tournament_season",
                "event_live",
                "event_qualification",
            ],
            "api_tennis_schema_has_best_of_field": False,
            "api_tennis_public_schema_url": "https://api-tennis.com/documentation",
            "api_tennis_public_schema_reviewed_at_utc": "2026-11-26",
            "api_tennis_public_schema_note": (
                "Public documentation, not a provider API call. The fixture "
                "response documents tournament_round and event_qualification but "
                "no best-of field."
            ),
        },
        "ingestion_code_cause": {
            "api_tennis_default_at_base_commit": (
                "ad65894: api_tennis_live.py `_best_of_sets` returned 3 when "
                "`best_of_sets`/`event_best_of_sets` was missing."
            ),
            "api_tennis_current_2027_guard": (
                "PR #389 derives AO 2027 main-draw BO5 from the recognised round and "
                "rejects missing/conflicting format; the generic 2026 path still "
                "documents the historical default."
            ),
            "sackmann_qf_flag_at_base_commit": (
                "ad65894: `qualifying_flag = round_code.startswith(\"Q\")`, so the "
                "main-draw round `QF` matched. The branch now uses an explicit "
                "Q1/Q2/Q3 set."
            ),
            "qualifying_label_cause": (
                "2026 qualifying rows carry qualifying_flag=true with round codes "
                "QF/SF/F and pre-main-draw starts. The Q-prefix fallback would have "
                "returned false, so the true flag came from the provider "
                "event_qualification field while the provider round label had no Q "
                "prefix. The original payload is not retained, so this is inferred "
                "from the stored flag/round/date combination and the parser path."
            ),
        },
        "edition_reconciliation": {
            "edition_2248": {
                "matches": len({row["match_id"] for row in edition_2248}),
                "by_round_all_stages": dict(
                    sorted(Counter(row["round_code"] for row in edition_2248).items())
                ),
                "main_draw_by_round": dict(
                    sorted(
                        Counter(
                            row["round_code"]
                            for row in edition_2248
                            if row["draw_stage"] == "MAIN_DRAW"
                        ).items()
                    )
                ),
                "qualifying_by_round": dict(
                    sorted(
                        Counter(
                            row["round_code"]
                            for row in edition_2248
                            if row["draw_stage"] == "QUALIFYING"
                        ).items()
                    )
                ),
                "by_status": dict(
                    sorted(Counter(row["match_status"] for row in edition_2248).items())
                ),
                "main_draw": len(
                    {row["match_id"] for row in edition_2248 if row["draw_stage"] == "MAIN_DRAW"}
                ),
                "qualifying": len(
                    {row["match_id"] for row in edition_2248 if row["draw_stage"] == "QUALIFYING"}
                ),
                "surface": next(
                    (row["edition_surface"] for row in edition_2248), None
                ),
                "end_date": next((row["end_date"] for row in editions
                                  if row["tournament_edition_id"] == 2248), None),
            },
            "edition_2249": {
                "matches": len({row["match_id"] for row in phantom}),
                "rows": slim(phantom),
                "all_cancelled": all(row["match_status"] == "CANCELLED" for row in phantom),
                "main_draw": len(
                    {row["match_id"] for row in phantom if row["draw_stage"] == "MAIN_DRAW"}
                ),
                "qualifying": len(
                    {row["match_id"] for row in phantom if row["draw_stage"] == "QUALIFYING"}
                ),
            },
            "expected_2026_main_draw_rounds": {
                "R128": 64, "R64": 32, "R32": 16, "R16": 8, "QF": 4, "SF": 2, "F": 1
            },
            "stored_2026_main_draw_rounds": dict(
                sorted(Counter(row["round_code"] for row in main_rows).items())
            ),
            "stored_2026_main_draw_rounds_excluding_edition_2249": dict(
                sorted(
                    Counter(
                        row["round_code"] for row in main_rows
                        if row["tournament_edition_id"] == 2248
                    ).items()
                )
            ),
            "missing_r16_candidates": missing_r16,
            "bracket_conclusion": (
                "Edition 2248 holds the genuine draw but is missing the Djokovic-"
                "Mensik R16 match (both R32 winners are absent from R16). Edition "
                "2249 contributes two cancelled phantom R128 rows, producing 66 "
                "R128 rows and 128 main-draw rows in total. Excluding the phantom "
                "rows, 126 genuine main-draw rows remain: one fewer than the "
                "official 127."
            ),
            "qualifying_conclusion": (
                "Edition 2248 holds 112 genuine qualifying rows (64 QF, 32 SF, "
                "16 F). Edition 2249 adds three cancelled phantom QF rows, giving "
                "115. The official qualifying draw is 112."
            ),
        },
        "systemic_2026_edition_hygiene": {
            "split_edition_pairs": len(split_editions_2026),
            "split_editions": split_editions_2026,
            "null_metadata_by_tier": edition_hygiene_2026,
            "cause": (
                "The edition key in `repository.get_or_create_tournament_edition` "
                "includes `edition_name`. API-Tennis display-name variants such as "
                "`Australian Open 2026` and `ATP Australian Open 2026` resolve to "
                "the same tournament_id through the source-tournament mapping but "
                "create two editions. The same 'ATP ' prefix split recurs in 33 "
                "tournament/season pairs in 2026, so this is not AO-specific."
            ),
            "surface_end_date_cause": (
                "The API-Tennis scheduled-fixture payload has no edition surface or "
                "end date; the historical adapter stored NULL and, at the base "
                "commit, had no edition-end fallback. All 2026 editions have a NULL "
                "end date and most have a NULL surface. This is a metadata gap "
                "separate from the best-of defect."
            ),
        },
        "january_2026_fixture_classification": {
            "editions": january_2026_classification,
            "activity": january_2026_activity,
            "verification": {
                "Brisbane": "ATP_MAIN / ATP_TOUR / BO3 main draw; stored OK, split into 2234/2235 with NULL surface/end date.",
                "Hong Kong": "ATP_MAIN / ATP_TOUR / BO3 main draw; stored OK, NULL surface/end date.",
                "Adelaide": "ATP_MAIN / ATP_TOUR / BO3 main draw; stored OK, NULL surface/end date.",
                "Auckland": "ATP_MAIN / ATP_TOUR / BO3 main draw; stored OK, split into 2242/2243 with NULL surface/end date.",
                "Australian Open": "GRAND_SLAM identity is wrong in storage (ATP_TOUR), surface/end date NULL, 2026 main draw is BO3; the 2027 parser/resolver candidate is separate.",
                "United Cup": "ATP_MAIN / ATP_TOUR / BO3; outside AO 2027 scope and not enabled.",
            },
            "official_fixture_completeness": "UNKNOWN",
            "internal_coverage": "Stored internal fixture counts only; not an official tour denominator.",
            "original_manager_consensus": "Stored original rows only; January 2026 activity is zero in this database.",
            "tracking_only_selections": "Separate dashboard population; not promoted to Value Bets; January 2026 activity is zero.",
            "published_model_picks": "Separate published population; January 2026 activity is zero.",
            "warning": (
                "Do not describe internal fixture coverage as official tour "
                "coverage. The independent official denominator remains unresolved."
            ),
        },
        "dependencies": {
            "original_prediction_objects": dependencies,
            "a1_history_completed_ao_matches": a1_completed,
            "a2_ao_stat_rows": a2_stats,
            "a2_qf_flag_rows_eligibility": a2_qf_eligibility,
            "a1_best_of_dependency": (
                "NONE. Agent 1 validates best_of_sets > 0 but never uses the value "
                "in rating updates or probability prediction; the regression "
                "`test_best_of_sets_does_not_change_v1_probability` asserts equality."
            ),
            "a1_surface_dependency": (
                "REAL AND SEPARATE. 229 completed AO 2026 matches enter A1 history "
                "with NULL surface, so they update overall Elo only. This is a "
                "surface-metadata effect, not a best-of effect."
            ),
            "a2_dependency": (
                "NONE FOR 2026. API-Tennis AO 2026 has zero match_stat_observations, "
                "so no AO 2026 row can be used in A2 serve/return fitting. The 40 "
                "2016-2025 QF rows have best_of=5 and round QF; A2 resolves the main-"
                "draw BO5 rule and the flag disagreement does not block the update. "
                "The 115 qualifying rows are BO3 and have no 2026 stats."
            ),
            "manager_dependency": "ZERO. No AO prediction target, consensus row or decision exists.",
            "research_experiment_dependency": (
                "The A1 identity-bridge and label-shuffle experiments use A1 history, "
                "not AO as an evaluation target. Their BO3 dependency is zero; their "
                "surface dependency is bounded by the A1 surface sensitivity evidence."
            ),
        },
        "a1_surface_sensitivity": a1_sensitivity,
        "research_population_handshake": {
            "declared_populations": list(DECLARED_RESEARCH_POPULATIONS),
            "declared_scope": "A1 targets since 2026-09-10 (identity-bridge workstream).",
            "reproduced_total_targets": len(target_rows),
            "reproduced_settled_completed": len(settled_ids),
            "target_set_sha256": _sha256_ids(target_ids),
            "settled_set_sha256": _sha256_ids(settled_ids),
            "ao_fixture_set_sha256": _sha256_ids(ao_ids),
            "ao_fixture_ids_count": len(ao_ids),
            "ao_2026_fixture_set_sha256": _sha256_ids(ao_2026_ids),
            "ao_2026_fixture_ids_count": len(ao_2026_ids),
            "ao_intersection_with_targets": len(ao_ids & target_ids),
            "ao_intersection_with_settled": len(ao_ids & settled_ids),
            "ao_2026_intersection_with_targets": len(ao_2026_ids & target_ids),
            "ao_2026_intersection_with_settled": len(ao_2026_ids & settled_ids),
            "exact_comparison_status": (
                "PREPARED, NOT CONFIRMED. The AO set hash and the reproduced A1 "
                "target/settled hashes are emitted. Exact equality to 3,152/3,201/"
                "3,223 requires DeepSeek 1's frozen fixture-id manifest or set hash; "
                "the AO intersection is already zero because AO is January 2026 and "
                "the research populations start 2026-09-10."
            ),
        },
        "reference_probability_regression": reference,
        "fixture_manifests": {
            "main_draw_bo3": slim(bo3_main),
            "qualifying_2026": slim(qual_2026),
            "main_draw_qf_misflag": slim(qf_misflagged),
            "edition_2249_phantom": slim(phantom),
        },
        "experiments_requiring_reconsideration": [
            {
                "experiment": "A1 identity-bridge retrospective/forward shadow",
                "reconsider_because_of_bo3": False,
                "reason": "A1 ignores best_of_sets; AO is not an evaluation target.",
                "reconsider_because_of_surface": True,
                "action": "Apply the measured A1 surface sensitivity bound; do not re-score as a BO3 defect.",
            },
            {
                "experiment": "A1 real-label-shuffle negative control",
                "reconsider_because_of_bo3": False,
                "reason": "Training history includes AO 2026 rows but A1 ignores best_of.",
                "reconsider_because_of_surface": True,
                "action": "Re-run only if the surface sensitivity bound is material.",
            },
            {
                "experiment": "Agent 2 Grand Slam support / BO5 rehearsal",
                "reconsider_because_of_bo3": False,
                "reason": "2026 AO has zero A2 stats and was never an AO prediction target; the rehearsal uses synthetic 2027 pairing.",
                "reconsider_because_of_surface": False,
                "action": "No re-score; retain synthetic-only status.",
            },
            {
                "experiment": "Manager C / Challenger forward experiments",
                "reconsider_because_of_bo3": False,
                "reason": "Challenger scope; no AO fixture in the population.",
                "reconsider_because_of_surface": False,
                "action": "None.",
            },
        ],
    }

    out_path = Path(args.out_json)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(report, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )

    if args.out_csv_dir:
        csv_dir = Path(args.out_csv_dir)
        _write_csv(csv_dir / "ao_2026_main_draw_bo3.csv", slim(bo3_main))
        _write_csv(csv_dir / "ao_2026_qualifying.csv", slim(qual_2026))
        _write_csv(csv_dir / "ao_2016_2025_main_draw_qf_misflag.csv", slim(qf_misflagged))
        _write_csv(csv_dir / "ao_2026_edition_2249_phantom.csv", slim(phantom))

    print(
        json.dumps(
            {
                "wrote": str(out_path),
                "ao_versions": len(ao_versions),
                "main_bo3": len({row["match_id"] for row in bo3_main}),
                "qualifying": len({row["match_id"] for row in qual_2026}),
                "qf_misflag": len({row["match_id"] for row in qf_misflagged}),
                "phantom_2249": len({row["match_id"] for row in phantom}),
                "missing_r16": missing_r16,
                "reference": reference,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
