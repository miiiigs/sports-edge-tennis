"""Coordinated API-Tennis and OddsPapi acquisition planning.

This module never calls a provider. It turns an already-authorised fixture
cohort into the minimum set of distinct provider requests, so the same
tournament or date window is not requested twice by two independent code
paths. Billable work remains behind the existing ledger/budget guards.

Entitlement is a fact about the account, not an assumption:

* ``VERIFIED``      both persisted entitlement and current quota are proven;
* ``ENTITLEMENT_ONLY`` the provider was demonstrably usable in the past, but
  the current allowance is unproven;
* ``UNVERIFIED``    neither is proven.

Only ``VERIFIED`` may authorise a new billable collection run. The audit that
produced the current evidence made zero billable calls and did not establish
the remaining quota, so the default status is ``ENTITLEMENT_ONLY``.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Sequence

PLANNER_VERSION = "SIMULTANEOUS_PROVIDER_ACQUISITION_PLAN_V2"
API_TENNIS_MAX_ODDS_DAYS = 10
ODDSPAPI_MAX_TOURNAMENTS_PER_REQUEST = 5

STATUS_VERIFIED = "VERIFIED"
STATUS_ENTITLEMENT_ONLY = "ENTITLEMENT_ONLY"
STATUS_UNVERIFIED = "UNVERIFIED"


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("timestamps must be timezone-aware")
    return value.astimezone(timezone.utc)


@dataclass(frozen=True)
class ProviderEntitlement:
    provider_code: str
    status: str
    entitlement_established: bool
    quota_established: bool
    evidence_reference: str | None = None

    def __post_init__(self) -> None:
        if self.status not in (STATUS_VERIFIED, STATUS_ENTITLEMENT_ONLY, STATUS_UNVERIFIED):
            raise ValueError(f"unsupported entitlement status {self.status!r}")

    @property
    def may_make_billable_requests(self) -> bool:
        return (
            self.status == STATUS_VERIFIED
            and self.entitlement_established is True
            and self.quota_established is True
        )


def assess_entitlement(
    evidence: Mapping[str, Any],
    *,
    provider_code: str = "API_TENNIS",
    evidence_reference: str | None = None,
) -> ProviderEntitlement:
    method = evidence.get("method") if isinstance(evidence.get("method"), Mapping) else {}
    entitlement = bool(method.get("entitlement_established_from_persisted_rows"))
    quota = bool(method.get("quota_established"))
    if entitlement and quota:
        status = STATUS_VERIFIED
    elif entitlement:
        status = STATUS_ENTITLEMENT_ONLY
    else:
        status = STATUS_UNVERIFIED
    return ProviderEntitlement(
        provider_code=provider_code,
        status=status,
        entitlement_established=entitlement,
        quota_established=quota,
        evidence_reference=evidence_reference,
    )


@dataclass(frozen=True)
class FixtureCandidate:
    match_id: int
    tour: str
    scheduled_start_at: datetime
    provider_fixture_id: str | None = None
    provider_tournament_id: str | None = None
    mapped: bool = False
    two_sided: bool = False
    api_tennis_match_key: str | None = None
    oddspapi_fixture_id: str | None = None

    def __post_init__(self) -> None:
        if self.tour not in ("ATP_MAIN", "CHALLENGER"):
            raise ValueError("balanced cohort accepts only ATP_MAIN and CHALLENGER")
        if self.scheduled_start_at.tzinfo is None:
            raise ValueError("scheduled_start_at must be timezone-aware")


@dataclass(frozen=True)
class AcquisitionRequest:
    provider_code: str
    endpoint: str
    purpose: str
    identifiers: tuple[str, ...]
    window_start: datetime | None = None
    window_end: datetime | None = None
    bookmaker: str | None = None

    @property
    def dedupe_key(self) -> tuple[Any, ...]:
        return (
            self.provider_code,
            self.endpoint,
            self.purpose,
            self.identifiers,
            _utc(self.window_start) if self.window_start else None,
            _utc(self.window_end) if self.window_end else None,
            self.bookmaker,
        )

    @property
    def estimated_units(self) -> int:
        return 1


def dedupe_requests(requests: Iterable[AcquisitionRequest]) -> list[AcquisitionRequest]:
    """Keep insertion order while removing identical provider requests."""
    seen: set[tuple[Any, ...]] = set()
    out: list[AcquisitionRequest] = []
    for request in requests:
        if request.dedupe_key in seen:
            continue
        seen.add(request.dedupe_key)
        out.append(request)
    return out


def plan_oddspapi_requests(
    tournament_ids: Iterable[int | str],
    *,
    bookmakers: Sequence[str],
    include_discovery: bool,
    window_start: datetime | None = None,
    window_end: datetime | None = None,
) -> list[AcquisitionRequest]:
    """One discovery request plus one odds request per (book, five tournaments)."""
    ids = sorted({str(int(value)) for value in tournament_ids})
    out: list[AcquisitionRequest] = []
    if include_discovery:
        out.append(AcquisitionRequest(
            provider_code="ODDSPAPI",
            endpoint="/v4/fixtures",
            purpose="DISCOVERY",
            identifiers=tuple(ids),
            window_start=window_start,
            window_end=window_end,
            bookmaker=(bookmakers[0] if bookmakers else None),
        ))
    for bookmaker in bookmakers:
        for offset in range(0, len(ids), ODDSPAPI_MAX_TOURNAMENTS_PER_REQUEST):
            out.append(AcquisitionRequest(
                provider_code="ODDSPAPI",
                endpoint="/v4/odds-by-tournaments",
                purpose="QUALIFICATION",
                identifiers=tuple(ids[offset:offset + ODDSPAPI_MAX_TOURNAMENTS_PER_REQUEST]),
                bookmaker=bookmaker,
            ))
    return dedupe_requests(out)


def plan_api_tennis_requests(
    *,
    date_start: datetime,
    date_stop: datetime,
    tournament_keys: Sequence[str] = (),
    use_event_odds: bool = False,
    include_events: bool = True,
) -> list[AcquisitionRequest]:
    """Plan one get_events call and one odds call per distinct scope.

    API-Tennis documents ``get_odds`` by match key, by tournament key and by
    event-type/date range. Date-scoped odds are the cheapest usable scope and
    are requested once. Tournament-scoped odds are only added when a caller
    explicitly supplies tournament keys, and a match key is never expanded into
    per-match billable calls here.
    """
    if _utc(date_stop) <= _utc(date_start):
        raise ValueError("date_stop must be after date_start")
    if (_utc(date_stop) - _utc(date_start)) > _seconds_days(API_TENNIS_MAX_ODDS_DAYS):
        raise ValueError("API-Tennis get_odds date scope exceeds the documented maximum")
    out: list[AcquisitionRequest] = []
    if include_events:
        out.append(AcquisitionRequest(
            provider_code="API_TENNIS",
            endpoint="get_events",
            purpose="COVERAGE",
            identifiers=(),
            window_start=date_start,
            window_end=date_stop,
        ))
    if not use_event_odds:
        out.append(AcquisitionRequest(
            provider_code="API_TENNIS",
            endpoint="get_odds",
            purpose="COVERAGE",
            identifiers=("DATE_RANGE",),
            window_start=date_start,
            window_end=date_stop,
        ))
    for key in sorted({str(value) for value in tournament_keys if str(value)}):
        out.append(AcquisitionRequest(
            provider_code="API_TENNIS",
            endpoint="get_odds",
            purpose="COVERAGE",
            identifiers=(f"TOURNAMENT:{key}",),
            window_start=date_start,
            window_end=date_stop,
        ))
    return dedupe_requests(out)


def _seconds_days(days: int):
    from datetime import timedelta

    return timedelta(days=days)


def select_balanced_cohort(
    fixtures: Iterable[FixtureCandidate],
    *,
    per_tour: int = 80,
) -> dict[str, Any]:
    """Select 80 ATP and 80 Challenger fixtures without substitution.

    Selection is deterministic: earliest scheduled start, then match id. A
    shortfall is reported and never filled from another competition.
    """
    pools: dict[str, list[FixtureCandidate]] = {"ATP_MAIN": [], "CHALLENGER": []}
    seen = set()
    for fixture in fixtures:
        if fixture.match_id in seen:
            raise ValueError("duplicate canonical fixture")
        seen.add(fixture.match_id)
        pools[fixture.tour].append(fixture)
    selected: list[FixtureCandidate] = []
    report: dict[str, Any] = {}
    for tour, pool in pools.items():
        ordered = sorted(pool, key=lambda item: (_utc(item.scheduled_start_at), item.match_id))
        chosen = ordered[:per_tour]
        selected.extend(chosen)
        report[tour] = {
            "eligible": len(ordered),
            "selected": len(chosen),
            "shortfall": max(0, per_tour - len(chosen)),
            "mapped": sum(1 for item in chosen if item.mapped),
            "two_sided": sum(1 for item in chosen if item.two_sided),
        }
    report["total_selected"] = len(selected)
    report["requested_per_tour"] = per_tour
    report["substituted_from_other_competition"] = False
    report["fixtures"] = [item.match_id for item in selected]
    return report


def plan_simultaneous_acquisition(
    *,
    fixtures: Sequence[FixtureCandidate],
    oddspapi_tournament_ids: Iterable[int | str],
    oddspapi_bookmakers: Sequence[str],
    coalesce_discovery: bool,
    api_tennis_date_start: datetime,
    api_tennis_date_stop: datetime,
    api_tennis_tournament_keys: Sequence[str] = (),
    api_tennis_use_event_odds: bool = False,
    entitlements: Sequence[ProviderEntitlement] = (),
    oddspapi_monthly_remaining: int | None = None,
    api_tennis_remaining: int | None = None,
    authorization_reference: str | None = None,
) -> dict[str, Any]:
    """Plan both providers; missing proof or budget blocks execution.

    A positive readiness result is a planning gate, not spending authority
    or a replacement for the live request ledger and atomic budget guard.
    """
    if len({f.match_id for f in fixtures}) != len(fixtures):
        raise ValueError("duplicate canonical fixture")
    if len({e.provider_code for e in entitlements}) != len(entitlements):
        raise ValueError("duplicate provider entitlement")
    requests = plan_oddspapi_requests(
        oddspapi_tournament_ids,
        bookmakers=oddspapi_bookmakers,
        include_discovery=coalesce_discovery,
    ) + plan_api_tennis_requests(
        date_start=api_tennis_date_start,
        date_stop=api_tennis_date_stop,
        tournament_keys=api_tennis_tournament_keys,
        use_event_odds=api_tennis_use_event_odds,
    )
    unique = dedupe_requests(requests)
    by_provider: dict[str, int] = {}
    by_endpoint: dict[str, int] = {}
    for request in unique:
        by_provider[request.provider_code] = by_provider.get(request.provider_code, 0) + 1
        key = f"{request.provider_code}:{request.endpoint}"
        by_endpoint[key] = by_endpoint.get(key, 0) + 1
    overlap = {
        "fixtures_with_both_provider_ids": sum(
            1 for fixture in fixtures
            if fixture.api_tennis_match_key and fixture.oddspapi_fixture_id
            and fixture.mapped
        ),
        "fixtures_in_cohort": len(fixtures),
    }
    entitlement_by_provider = {
        item.provider_code: {
            "status": item.status,
            "entitlement_established": item.entitlement_established,
            "quota_established": item.quota_established,
            "may_make_billable_requests": item.may_make_billable_requests,
            "evidence_reference": item.evidence_reference,
        }
        for item in entitlements
    }
    blocked = [
        code for code in by_provider
        if code not in entitlement_by_provider
        or not entitlement_by_provider[code]["may_make_billable_requests"]
        or not entitlement_by_provider[code]["evidence_reference"]
    ]
    allowances = {
        "ODDSPAPI": oddspapi_monthly_remaining,
        "API_TENNIS": api_tennis_remaining,
    }
    blocked_budgets = [
        code for code, required in by_provider.items()
        if type(allowances.get(code)) is not int or allowances[code] < required
    ]
    budget_blocked = bool(blocked_budgets)
    entitlement_verified = bool(by_provider) and not blocked
    approved = isinstance(authorization_reference, str) and bool(
        authorization_reference.strip())
    return {
        "version": PLANNER_VERSION,
        "requests": [
            {
                "provider_code": request.provider_code,
                "endpoint": request.endpoint,
                "purpose": request.purpose,
                "identifiers": list(request.identifiers),
                "bookmaker": request.bookmaker,
                "window_start": request.window_start,
                "window_end": request.window_end,
            }
            for request in unique
        ],
        "request_count": len(unique),
        "by_provider": dict(sorted(by_provider.items())),
        "by_endpoint": dict(sorted(by_endpoint.items())),
        "duplicate_requests_removed": len(requests) - len(unique),
        "provider_overlap": overlap,
        "entitlements": entitlement_by_provider,
        "blocked_providers": sorted(blocked),
        "entitlements_supplied": bool(entitlements),
        "budget_blocked": budget_blocked,
        "blocked_budget_providers": sorted(blocked_budgets),
        "owner_authorization_recorded": approved,
        "execution_authorised": entitlement_verified and not budget_blocked and approved,
        "provider_calls_made": 0,
        "note": (
            "Planning only. Execution still requires VERIFIED entitlement and the "
            "existing OddsPapi ledger/budget guards."
        ),
    }


def coverage_gate(
    fixtures: Sequence[FixtureCandidate],
    *,
    mapping_threshold: float = 0.90,
    two_sided_threshold: float = 0.80,
) -> dict[str, Any]:
    """Report the operational targets without claiming they are achieved."""
    if len({f.match_id for f in fixtures}) != len(fixtures):
        raise ValueError("duplicate canonical fixture")
    total = len(fixtures)
    if not total:
        return {
            "fixtures": 0,
            "mapping_rate": None,
            "two_sided_rate": None,
            "mapping_target_met": False,
            "two_sided_target_met": False,
        }
    mapped = sum(1 for item in fixtures if item.mapped)
    two_sided = sum(1 for item in fixtures if item.two_sided)
    return {
        "fixtures": total,
        "mapped": mapped,
        "two_sided": two_sided,
        "mapping_rate": mapped / total,
        "two_sided_rate": two_sided / total,
        "mapping_target": mapping_threshold,
        "two_sided_target": two_sided_threshold,
        "mapping_target_met": mapped / total >= mapping_threshold,
        "two_sided_target_met": two_sided / total >= two_sided_threshold,
        "targets_are_targets_not_achievements": True,
    }
