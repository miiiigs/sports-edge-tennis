from datetime import UTC, datetime, timedelta

import pytest

from sports_edge_tennis.validation.api_tennis_acquisition_plan import (
    FixtureCandidate, ProviderEntitlement, STATUS_VERIFIED,
)
from sports_edge_tennis.validation.preissue_pilot_calendar import (
    UpcomingFixture, plan_preissue_pilot,
)

NOW = datetime(2026,11,27,10,tzinfo=UTC)
ENTITLEMENTS = tuple(ProviderEntitlement(code,STATUS_VERIFIED,True,True,"synthetic proof")
                     for code in ("API_TENNIS","ODDSPAPI"))


def fixture(mid, *, start_in=timedelta(hours=3), issue_in=timedelta(hours=2),
            mapped=True, discovered_at=NOW, tournament=1):
    return UpcomingFixture(FixtureCandidate(
        mid,"CHALLENGER",NOW+start_in,mapped=mapped,
        api_tennis_match_key=f"api-{mid}",oddspapi_fixture_id=f"op-{mid}"),
        discovered_at,NOW+issue_in,tournament)


def run(fixtures, **overrides):
    args = dict(fixtures=fixtures, now=NOW,
                scheduler_slots=[NOW+timedelta(minutes=15),NOW+timedelta(hours=1)],
                entitlements=ENTITLEMENTS,authorization_reference="synthetic approval",
                oddspapi_remaining=5,api_tennis_remaining=5)
    args.update(overrides)
    return plan_preissue_pilot(**args)


def test_batched_early_slot_preserves_both_deadlines_and_denominator():
    result = run([fixture(1),fixture(2),fixture(3,mapped=False),
                  fixture(4,start_in=timedelta(minutes=30)),
                  fixture(5,discovered_at=NOW+timedelta(minutes=1))])
    assert result["fixtures"] == 5
    assert result["planned_fixtures"] == 2
    assert result["cycles"][0]["match_ids"] == [1,2]
    assert result["cycles"][0]["planned_requests"] == {"API_TENNIS":2,"ODDSPAPI":1}
    assert [r["status"] for r in result["fixture_results"]] == [
        "PLANNED_NOT_EXECUTED","PLANNED_NOT_EXECUTED","DUAL_MAPPING_UNAVAILABLE",
        "DEADLINE_MISSED","DISCOVERY_NOT_YET_OBSERVED"]
    assert result["provider_calls"] == result["original_time_prices"] == 0


def test_authorization_and_missing_allowance_fail_closed():
    for overrides in ({"authorization_reference":None},
                      {"api_tennis_remaining":None},{"entitlements":ENTITLEMENTS[:1]}):
        result=run([fixture(1)],**overrides)
        assert result["planned_fixtures"]==0
        assert result["fixture_results"][0]["status"]=="AUTHORIZATION_OR_ALLOWANCE_BLOCKED"


def test_slot_after_issue_or_t60_is_not_used():
    result=run([fixture(1,issue_in=timedelta(minutes=20)),
                fixture(2,start_in=timedelta(hours=1,minutes=10))],
               scheduler_slots=[NOW+timedelta(minutes=30)])
    assert result["planned_fixtures"]==0
    assert all(r["status"]=="NO_FEASIBLE_SLOT_OR_CYCLE_CAP" for r in result["fixture_results"])


def test_cycle_limit_and_total_allowance_are_distinct():
    result=run([fixture(i,tournament=i) for i in range(1,7)],
               oddspapi_per_cycle=1,api_tennis_remaining=4)
    assert len(result["cycles"])==2
    assert sorted(mid for c in result["cycles"] for mid in c["match_ids"])==list(range(1,7))
    assert result["remaining_estimated_allowance"]["API_TENNIS"]==0


def test_duplicate_fixture_fails_closed():
    with pytest.raises(ValueError,match="duplicate"):
        run([fixture(1),fixture(1)])
