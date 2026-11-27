from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sports_edge_tennis.validation.api_tennis_acquisition_plan import (
    STATUS_ENTITLEMENT_ONLY,
    STATUS_VERIFIED,
    FixtureCandidate,
    assess_entitlement,
    coverage_gate,
    dedupe_requests,
    plan_api_tennis_requests,
    plan_oddspapi_requests,
    plan_simultaneous_acquisition,
    select_balanced_cohort,
)

T = datetime(2026, 11, 27, 0, 0, tzinfo=timezone.utc)


def test_entitlement_requires_quota_not_only_historical_access():
    entitlement = assess_entitlement({
        "method": {
            "entitlement_established_from_persisted_rows": True,
            "quota_established": False,
        }
    })
    assert entitlement.status == STATUS_ENTITLEMENT_ONLY
    assert entitlement.may_make_billable_requests is False

    verified = assess_entitlement({
        "method": {
            "entitlement_established_from_persisted_rows": True,
            "quota_established": True,
        }
    })
    assert verified.status == STATUS_VERIFIED
    assert verified.may_make_billable_requests is True


def test_oddspapi_plan_batches_five_tournaments_and_deduplicates():
    requests = plan_oddspapi_requests(
        [1, 2, 3, 4, 5, 6, 1],
        bookmakers=("pinnacle+30", "1xbet"),
        include_discovery=True,
        window_start=T,
        window_end=T + timedelta(days=1),
    )
    odds = [request for request in requests if request.endpoint == "/v4/odds-by-tournaments"]
    assert len(odds) == 4  # two batches per book, six unique tournaments
    assert all(len(request.identifiers) <= 5 for request in odds)
    assert len(dedupe_requests(requests)) == len(requests)


def test_api_tennis_plan_uses_one_date_scoped_request_and_no_match_calls():
    requests = plan_api_tennis_requests(
        date_start=T,
        date_stop=T + timedelta(days=3),
    )
    assert [request.endpoint for request in requests] == ["get_events", "get_odds"]
    assert requests[1].identifiers == ("DATE_RANGE",)
    assert all(not request.identifiers or not request.identifiers[0].startswith("MATCH:")
               for request in requests)


def test_simultaneous_plan_never_duplicates_a_provider_request():
    plan = plan_simultaneous_acquisition(
        fixtures=[],
        oddspapi_tournament_ids=[10, 11],
        oddspapi_bookmakers=("pinnacle+30",),
        coalesce_discovery=True,
        api_tennis_date_start=T,
        api_tennis_date_stop=T + timedelta(days=1),
        entitlements=(assess_entitlement({"method": {
            "entitlement_established_from_persisted_rows": True,
            "quota_established": False,
        }}),),
    )
    assert plan["duplicate_requests_removed"] == 0
    assert plan["request_count"] == 4  # discovery, 1 odds, get_events, get_odds
    assert plan["execution_authorised"] is False
    assert plan["blocked_providers"] == ["API_TENNIS"]
    assert plan["provider_calls_made"] == 0

    no_entitlement = plan_simultaneous_acquisition(
        fixtures=[],
        oddspapi_tournament_ids=[10],
        oddspapi_bookmakers=("pinnacle+30",),
        coalesce_discovery=False,
        api_tennis_date_start=T,
        api_tennis_date_stop=T + timedelta(days=1),
    )
    assert no_entitlement["entitlements_supplied"] is False
    assert no_entitlement["execution_authorised"] is False


def test_balanced_cohort_does_not_substitute_other_competitions():
    fixtures = [
        FixtureCandidate(match_id=i, tour="ATP_MAIN", scheduled_start_at=T + timedelta(hours=i))
        for i in range(50)
    ] + [
        FixtureCandidate(match_id=1000 + i, tour="CHALLENGER",
                         scheduled_start_at=T + timedelta(hours=i))
        for i in range(90)
    ]
    cohort = select_balanced_cohort(fixtures, per_tour=80)
    assert cohort["ATP_MAIN"]["selected"] == 50
    assert cohort["ATP_MAIN"]["shortfall"] == 30
    assert cohort["CHALLENGER"]["selected"] == 80
    assert cohort["total_selected"] == 130
    assert cohort["substituted_from_other_competition"] is False


def test_coverage_gate_states_targets_are_not_achievements():
    fixtures = [
        FixtureCandidate(match_id=i, tour="ATP_MAIN", scheduled_start_at=T,
                         mapped=i < 8, two_sided=i < 7)
        for i in range(10)
    ]
    gate = coverage_gate(fixtures)
    assert gate["mapping_target_met"] is False
    assert gate["two_sided_target_met"] is False
    assert gate["targets_are_targets_not_achievements"] is True
