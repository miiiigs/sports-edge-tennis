from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sports_edge_tennis.odds.attribution import (
    BENCHMARK_BACKFILLED,
    BENCHMARK_LATE,
    BENCHMARK_QUALIFIED,
    BENCHMARK_RETROSPECTIVE,
    attribution_completeness,
    benchmark_qualification,
    classify_historical_reconstruction,
    load_attributions,
    observation_is_fresh,
    price_age_components,
    provider_coverage_report,
)
from sports_edge_tennis.odds.persistence import insert_bookmaker_mapping
from sports_edge_tennis.odds.providers.oddspapi_tennis import (
    build_oddspapi_tennis_observation,
)
from sports_edge_tennis.odds.service import ingest_provider_observation

NOW = datetime(2026, 11, 27, 1, 24, tzinfo=timezone.utc)


def _payload() -> dict:
    return {
        "fixtureId": "id1200670776914752",
        "sportId": 12,
        "participant1Id": 785974,
        "participant2Id": 169486,
        "participant1Name": "Player One",
        "participant2Name": "Player Two",
        "startTime": "2026-11-27T17:30:00.000Z",
        "updatedAt": "2026-11-27T01:23:59.084Z",
        "bookmakerOdds": {
            "pinnacle+30": {
                "bookmakerFixtureId": "1637190752",
                "suspended": False,
                "markets": {
                    "121": {
                        "marketActive": True,
                        "bookmakerMarketId": "1",
                        "outcomes": {
                            "121": {"players": {"0": {
                                "price": 1.69,
                                "active": True,
                                "changedAt": "2026-11-27T00:40:07.837Z",
                                "bookmakerChangedAt": None,
                            }}},
                            "122": {"players": {"0": {
                                "price": 2.02,
                                "active": True,
                                "changedAt": "2026-11-27T00:40:07.837Z",
                                "bookmakerChangedAt": None,
                            }}},
                        },
                    },
                },
            },
        },
    }


def _insert_real_format_match(conn) -> dict:
    player_1 = conn.execute(
        "INSERT INTO players (canonical_name) VALUES ('Player One') RETURNING player_id"
    ).fetchone()[0]
    player_2 = conn.execute(
        "INSERT INTO players (canonical_name) VALUES ('Player Two') RETURNING player_id"
    ).fetchone()[0]
    for player_id, provider_player_id in ((player_1, "785974"), (player_2, "169486")):
        conn.execute(
            "INSERT INTO player_source_ids (player_id, source_system, source_player_id) "
            "VALUES (%s, 'ODDSPAPI', %s)",
            (player_id, provider_player_id),
        )
    tournament_id = conn.execute(
        "INSERT INTO tournaments (canonical_name, tour_scope, competition_tier) "
        "VALUES ('Real Format Open', 'ATP_MAIN', 'ATP_250') RETURNING tournament_id"
    ).fetchone()[0]
    edition_id = conn.execute(
        "INSERT INTO tournament_editions "
        "(tournament_id, season_year, edition_name, surface, start_date, end_date) "
        "VALUES (%s, 2026, 'Real Format Open 2026', 'HARD', '2026-11-23', '2026-11-30') "
        "RETURNING tournament_edition_id",
        (tournament_id,),
    ).fetchone()[0]
    match_id = conn.execute(
        "INSERT INTO matches (tournament_edition_id) VALUES (%s) RETURNING match_id",
        (edition_id,),
    ).fetchone()[0]
    version_id = conn.execute(
        """
        INSERT INTO match_versions (
            match_id, player_1_id, player_2_id, match_start_at, tour_scope,
            competition_tier, surface, round_code, best_of_sets, qualifying_flag,
            match_status, source_observed_at, as_of
        )
        VALUES (%s, %s, %s, '2026-11-27T17:30:00Z', 'ATP_MAIN', 'ATP_250',
                'HARD', 'R32', 3, FALSE, 'SCHEDULED',
                '2026-11-26T00:00:00Z', '2026-11-26T00:00:00Z')
        RETURNING match_version_id
        """,
        (match_id, player_1, player_2),
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO match_source_ids "
        "(match_id, match_version_id, source_system, source_match_id) "
        "VALUES (%s, %s, 'ODDSPAPI', %s)",
        (match_id, version_id, "id1200670776914752"),
    )
    insert_bookmaker_mapping(
        conn,
        provider_code="ODDSPAPI",
        provider_bookmaker_key="pinnacle+30",
        mapping_version=1,
        bookmaker_code="PINNACLE",
        normalization_class="SHARP",
        effective_from=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    return {"match_id": match_id, "player_1_id": player_1, "player_2_id": player_2}


def test_classification_and_price_age_are_independent():
    assert classify_historical_reconstruction(_payload()) == "LIVE_OBSERVED"
    assert classify_historical_reconstruction({"bookmakers": {}}) == "LATEST_AT_CUTOFF_HISTORICAL"

    components = price_age_components(
        observation_time=NOW - timedelta(minutes=1),
        price_changed_at=NOW - timedelta(hours=10),
        as_of=NOW,
    )
    assert components["observation_age_seconds"] == 60
    assert components["price_change_age_seconds"] == 36000
    # The price is unchanged for ten hours but availability was reconfirmed a
    # minute ago: it is fresh, not stale.
    assert observation_is_fresh(
        observation_time=NOW - timedelta(minutes=1), as_of=NOW
    )
    assert components["price_unchanged_since_observation"] is True


def test_benchmark_qualification_never_promotes_late_or_historical_quotes():
    lock = NOW
    qualified = benchmark_qualification(
        observation_time=NOW - timedelta(minutes=30),
        ingested_at=NOW - timedelta(minutes=29),
        prediction_locked_at=lock,
        two_sided=True,
        side_span=timedelta(0),
        statuses=["OPEN", "OPEN"],
        payload=_payload(),
        booksum=1.086,
    )
    assert qualified["status"] == BENCHMARK_QUALIFIED

    late = benchmark_qualification(
        observation_time=NOW + timedelta(minutes=5),
        ingested_at=NOW + timedelta(minutes=5),
        prediction_locked_at=lock,
        two_sided=True,
        side_span=timedelta(0),
        statuses=["OPEN", "OPEN"],
        payload=_payload(),
        booksum=1.086,
    )
    assert late["status"] == BENCHMARK_LATE

    backfilled = benchmark_qualification(
        observation_time=NOW - timedelta(hours=10),
        ingested_at=NOW - timedelta(hours=9),
        prediction_locked_at=lock,
        two_sided=True,
        side_span=timedelta(0),
        statuses=["OPEN", "OPEN"],
        payload=_payload(),
        booksum=1.086,
    )
    assert backfilled["status"] == BENCHMARK_BACKFILLED

    historical = benchmark_qualification(
        observation_time=NOW - timedelta(minutes=30),
        ingested_at=NOW - timedelta(minutes=29),
        prediction_locked_at=lock,
        two_sided=True,
        side_span=timedelta(0),
        statuses=["OPEN", "OPEN"],
        payload={"bookmakers": {}},
        booksum=1.086,
    )
    assert historical["status"] == BENCHMARK_RETROSPECTIVE


def test_coverage_report_binds_every_placeholder_without_a_database():
    class ParamCountingConnection:
        def execute(self, sql, params=()):
            assert sql.count("%s") == len(params), (sql, params)
            self._last = sql
            return self

        def fetchall(self):
            return []

    report = provider_coverage_report(ParamCountingConnection(), provider_code="ODDSPAPI")
    assert report["providers"] == {}


def test_real_format_oddspapi_record_is_attributed_and_counted(db):
    match = _insert_real_format_match(db)
    observation = build_oddspapi_tennis_observation(
        event_payload=_payload(),
        received_at=NOW,
        response_id="test-response",
    )
    result = ingest_provider_observation(db, observation)
    assert result.normalized_inserted == 2
    assert result.raw_inserted == 1

    attribution = load_attributions(db, provider_code="ODDSPAPI", match_id=match["match_id"])
    assert len(attribution) == 2
    assert {row.selection_type for row in attribution} == {"PLAYER_1", "PLAYER_2"}
    assert {row.provider_bookmaker_key for row in attribution} == {"pinnacle+30"}
    assert {row.actual_bookmaker_code for row in attribution} == {"PINNACLE"}
    assert {row.provider_fixture_id for row in attribution} == {"id1200670776914752"}
    assert {row.historical_reconstruction_class for row in attribution} == {"LIVE_OBSERVED"}
    assert {row.canonical_player_1_id for row in attribution} == {match["player_1_id"]}
    assert {row.canonical_player_2_id for row in attribution} == {match["player_2_id"]}
    assert attribution_completeness(attribution)["completeness"] == 1.0

    report = provider_coverage_report(db, provider_code="ODDSPAPI")
    provider = report["providers"]["ODDSPAPI"]
    assert provider["raw_responses"] == 1
    assert provider["extracted_winner_observations"] == 1
    assert provider["normalized_quotes"] == 2
    assert provider["normalized_fixtures"] == 1
    assert provider["valid_two_sided_markets"] == 1
    assert provider["fixtures_with_two_sided_market"] == 1
    assert provider["rejection_reasons"] == {"NORMALIZED": 1}
    assert provider["rejected_observations"] == 0
    # No original prediction exists in this fixture, so strict original-time
    # qualification is zero rather than inferred.
    assert provider["original_time_qualified_comparisons"] == 0
    assert provider["normalized_by_bookmaker"][0]["bookmaker_code"] == "PINNACLE"
