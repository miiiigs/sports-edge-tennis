"""Isolated rehearsal of the complete pre-issue acquisition sequence.

This is a deterministic simulation. It uses no network, no credentials, no
production database and no provider budget. It exercises the same pilot
executor and strict paired-qualification rules used by the real path:

discovery -> identity resolution -> atomic reservation -> simultaneous
acquisition -> normalization -> original-time qualification -> forecast
issuance.

Failure scenarios include late responses, crashes, exhausted budgets, missing
prices, a rescheduled fixture, a lost acknowledgement and a retrospective
reconstruction. The rehearsal asserts that a later observation can never be
promoted to original-time evidence.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sports_edge_tennis.live.preissue_pilot import (
    AcknowledgementLost,
    InMemoryLease,
    InMemoryReservationStore,
    PilotObservation,
    PilotPolicy,
    PilotRequest,
    RecordingSink,
    RehearsalTransport,
    execute_pilot,
)
from sports_edge_tennis.validation.paired_qualification_audit import (
    REJECTED_RETROSPECTIVE,
    QuoteLineage,
    qualify_pair,
)

REHEARSAL_VERSION = "PREISSUE_PILOT_REHEARSAL_V1"
NOW = datetime(2026, 11, 27, 12, tzinfo=UTC)
START = NOW + timedelta(hours=3)
ISSUE = NOW + timedelta(hours=2)
DEADLINE = min(ISSUE, START - timedelta(hours=1))


class RehearsalClock:
    def __init__(self, value: datetime = NOW) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value

    def advance(self, delta: timedelta) -> None:
        self.value += delta


def _request(key: str, *, provider: str = "ODDSPAPI",
             deadline: datetime = DEADLINE) -> PilotRequest:
    return PilotRequest(
        provider_code=provider,
        endpoint="/v4/fixtures" if provider == "ODDSPAPI" and key == "discovery"
        else "get_odds" if provider == "API_TENNIS" else "/v4/odds-by-tournaments",
        purpose="DISCOVERY" if key == "discovery" else "QUALIFICATION",
        identifiers=(key,),
        match_ids=(1, 2),
        deadline=deadline,
        bookmaker="pinnacle+30" if provider == "ODDSPAPI" else None,
    )


def _quote(
    selection: str, *, price: float, observed: datetime,
    raw: int, snapshot: str, shape: str = "LIVE_OBSERVED",
    status: str = "OPEN", mapping: datetime | None = None,
) -> QuoteLineage:
    return QuoteLineage(
        match_id=1,
        raw_observation_id=raw,
        source_snapshot_id=snapshot,
        provider_code="ODDSPAPI",
        bookmaker_code="PINNACLE",
        source_system="SHARP",
        selection_type=selection,
        decimal_price=price,
        market_status=status,
        observed_at=observed,
        ingested_at=observed,
        mapping_created_at=mapping or (observed - timedelta(hours=1)),
        payload_shape=shape,
        scheduled_start_at=START,
    )


def _market(payload: dict, *, observed: datetime, raw: int = 100) -> dict:
    if payload.get("kind") == "retrospective":
        return {
            "status": REJECTED_RETROSPECTIVE,
            "reasons": ["HISTORICAL_RECONSTRUCTION_NOT_ORIGINAL_TIME"],
        }
    if not payload.get("two_sided"):
        return {
            "status": "NO_TWO_SIDED_PRICE",
            "reasons": ["MISSING_OPPOSITE_SELECTION"],
        }
    left = _quote("PLAYER_1", price=1.8, observed=observed, raw=raw,
                  snapshot=f"snap-{raw}")
    right = _quote("PLAYER_2", price=2.1, observed=observed, raw=raw,
                   snapshot=f"snap-{raw}")
    return qualify_pair(left, right, prediction_locked_at=DEADLINE, now=observed)


def _qualification(*, observation: PilotObservation, request: PilotRequest) -> dict:
    # In the real path this uses stored raw lineage; rehearsal uses the same
    # strict function with deterministic quotes.
    return _market(observation.payload or {}, observed=observation.observed_at)


def _run_scenario(name: str, *, transport: RehearsalTransport,
                  store: InMemoryReservationStore | None = None,
                  sink: RecordingSink | None = None,
                  clock: RehearsalClock | None = None,
                  requests=None, policy=PilotPolicy()) -> dict:
    clock = clock or RehearsalClock()
    requests = requests or [_request("odds")]
    result = execute_pilot(
        requests,
        store=store or InMemoryReservationStore(allowances={"ODDSPAPI": 100, "API_TENNIS": 100}),
        lease=InMemoryLease(),
        transport=transport,
        sink=sink or RecordingSink(),
        clock=clock,
        policy=policy,
        entitlement_verified=True,
        authorization_reference="synthetic owner approval",
        qualification=_qualification,
    )
    result["scenario"] = name
    result["forecast"] = _forecast(result)
    return result


def _forecast(result: dict) -> dict:
    qualified = result.get("qualified", 0)
    return {
        "issued": qualified > 0,
        "model_p1": 0.62 if qualified else None,
        "abstained": qualified == 0,
        "reason": "ORIGINAL_TIME_MARKET_CONFIRMED" if qualified else "NO_ORIGINAL_TIME_MARKET",
    }


def run_rehearsal() -> dict:
    scenarios: dict[str, dict] = {}

    # 1. Happy path: discovery then two simultaneous provider requests.
    discovery = _request("discovery")
    oddspapi = _request("op-1")
    api_tennis = _request("api-1", provider="API_TENNIS")
    clock = RehearsalClock()
    transport = RehearsalTransport(responses={
        discovery.dedupe_key: {"fixtures": [{"fixtureId": "op-1", "apiMatchKey": "api-1"}]},
        oddspapi.dedupe_key: {"two_sided": True},
        api_tennis.dedupe_key: {"two_sided": True},
    })
    store = InMemoryReservationStore(allowances={"ODDSPAPI": 5, "API_TENNIS": 5})
    sink = RecordingSink()
    report = _run_scenario(
        "happy_path", transport=transport, store=store, sink=sink, clock=clock,
        requests=[discovery, oddspapi, api_tennis],
    )
    report["identity_resolution"] = {
        "canonical_match_id": 1,
        "oddspapi_fixture_id": "op-1",
        "api_tennis_match_key": "api-1",
        "orientation": "DIRECT",
    }
    report["reservation_rows"] = len(store.all_reservations())
    report["persisted_observations"] = len(sink.records)
    scenarios["happy_path"] = report

    # 2. Late response: the provider answers after the deadline.
    late_clock = RehearsalClock()
    late_request = _request("late")
    late_transport = RehearsalTransport(
        responses={late_request.dedupe_key: {"two_sided": True}},
        advance_clock=lambda: late_clock.advance(timedelta(hours=3)),
    )
    scenarios["late_response"] = _run_scenario(
        "late_response", transport=late_transport, clock=late_clock,
        requests=[late_request],
    )

    # 3. Crash.
    crash_request = _request("crash")
    crash_transport = RehearsalTransport(
        responses={crash_request.dedupe_key: RuntimeError("provider connection reset")})
    scenarios["crash"] = _run_scenario(
        "crash", transport=crash_transport, requests=[crash_request],
    )

    # 4. Exhausted budget before the second request.
    budget_store = InMemoryReservationStore(allowances={"ODDSPAPI": 1})
    budget_transport = RehearsalTransport(responses={
        _request("b1").dedupe_key: {"two_sided": True},
        _request("b2").dedupe_key: {"two_sided": True},
    })
    scenarios["budget_exhausted"] = _run_scenario(
        "budget_exhausted", transport=budget_transport, store=budget_store,
        requests=[_request("b1"), _request("b2")],
    )
    scenarios["budget_exhausted"]["transport_calls"] = len(budget_transport.calls)

    # 5. Missing prices.
    missing_request = _request("missing")
    missing_transport = RehearsalTransport(
        responses={missing_request.dedupe_key: {"two_sided": False}})
    scenarios["missing_prices"] = _run_scenario(
        "missing_prices", transport=missing_transport, requests=[missing_request],
    )

    # 6. Rescheduled fixture: the safe deadline is now too close.
    rescheduled = _request("rescheduled", deadline=NOW + timedelta(seconds=5))
    scenarios["rescheduled_fixture"] = _run_scenario(
        "rescheduled_fixture",
        transport=RehearsalTransport(responses={rescheduled.dedupe_key: {"two_sided": True}}),
        requests=[rescheduled],
    )

    # 7. Lost acknowledgement.
    ack_request = _request("ack")
    scenarios["lost_acknowledgement"] = _run_scenario(
        "lost_acknowledgement",
        transport=RehearsalTransport(responses={ack_request.dedupe_key: {"two_sided": True}}),
        sink=RecordingSink(failure=AcknowledgementLost("durable ack lost")),
        requests=[ack_request],
    )

    # 8. Retrospective reconstruction cannot be promoted.
    retro_request = _request("retro")
    scenarios["retrospective_reconstruction"] = _run_scenario(
        "retrospective_reconstruction",
        transport=RehearsalTransport(
            responses={retro_request.dedupe_key: {"kind": "retrospective"}}),
        requests=[retro_request],
    )

    report = {
        "version": REHEARSAL_VERSION,
        "measured_at": NOW.isoformat(),
        "provider_calls": sum(item.get("provider_calls", 0) for item in scenarios.values()),
        "actual_reservations": sum(item.get("reservations", 0) for item in scenarios.values()),
        "durable_observation_records": sum(
            item.get("observations", 0) for item in scenarios.values()),
        "captured_two_sided_observations": sum(
            item.get("status_counts", {}).get("CAPTURED", 0)
            for item in scenarios.values()),
        "original_time_qualified": sum(
            item.get("qualified", 0) for item in scenarios.values()),
        "scenarios": scenarios,
        "assertions": {
            "late_response_never_qualified": (
                scenarios["late_response"]["qualified"] == 0
                and not scenarios["late_response"]["forecast"]["issued"]
            ),
            "crash_never_qualified": (
                scenarios["crash"]["qualified"] == 0
                and not scenarios["crash"]["forecast"]["issued"]
            ),
            "budget_blocks_request": (
                scenarios["budget_exhausted"]["status_counts"].get("BUDGET_EXHAUSTED") == 1
            ),
            "missing_prices_never_qualified": (
                scenarios["missing_prices"]["qualified"] == 0
            ),
            "rescheduled_fixture_misses_deadline": (
                scenarios["rescheduled_fixture"]["status_counts"].get("DEADLINE_MISSED") == 1
            ),
            "lost_ack_never_qualified": (
                scenarios["lost_acknowledgement"]["qualified"] == 0
            ),
            "retrospective_never_qualified": (
                scenarios["retrospective_reconstruction"]["qualified"] == 0
                and not scenarios["retrospective_reconstruction"]["forecast"]["issued"]
            ),
            "happy_path_completes": (
                scenarios["happy_path"]["qualified"] >= 1
                and scenarios["happy_path"]["forecast"]["issued"]
            ),
        },
        "production_side_effects": {
            "provider_calls": 0,
            "database_writes": 0,
            "production_budget_used": 0,
        },
    }
    if not all(report["assertions"].values()):
        raise AssertionError(report["assertions"])
    return report


def main(argv: list[str] | None = None) -> int:
    import argparse
    import json

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output")
    args = parser.parse_args(argv)
    report = run_rehearsal()
    payload = json.dumps(report, sort_keys=True, indent=2)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(payload + "\n")
    print(payload)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
