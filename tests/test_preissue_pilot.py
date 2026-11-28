from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path


from sports_edge_tennis.live.preissue_pilot import (
    AcknowledgementLost,
    InMemoryLease,
    InMemoryReservationStore,
    JsonObservationSink,
    PilotObservation,
    PilotPolicy,
    PilotRequest,
    RecordingSink,
    RehearsalTransport,
    SqliteReservationStore,
    execute_pilot,
    requests_from_plan,
)

NOW = datetime(2026, 11, 27, 12, tzinfo=UTC)
DEADLINE = NOW + timedelta(hours=1)


class MutableClock:
    def __init__(self, value: datetime = NOW) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value

    def advance(self, delta: timedelta) -> None:
        self.value += delta


def request(key: str, *, provider: str = "ODDSPAPI", deadline: datetime = DEADLINE,
            match_ids: tuple[int, ...] = (1,)) -> PilotRequest:
    return PilotRequest(
        provider_code=provider,
        endpoint="/v4/odds-by-tournaments",
        purpose="QUALIFICATION",
        identifiers=(key,),
        match_ids=match_ids,
        deadline=deadline,
    )


def qualified(*, observation: PilotObservation, request: PilotRequest) -> dict:
    payload = observation.payload or {}
    if payload.get("two_sided"):
        return {"status": "ORIGINAL_TIME_QUALIFIED", "reasons": []}
    return {"status": "NO_TWO_SIDED_PRICE", "reasons": ["MISSING_OPPOSITE_SELECTION"]}


def run(requests, *, store=None, transport=None, sink=None, clock=None,
        policy=PilotPolicy(), entitlement=True, authorization="owner-approved",
        lease=None):
    return execute_pilot(
        requests,
        store=store or InMemoryReservationStore(allowances={"ODDSPAPI": 100, "API_TENNIS": 100}),
        lease=lease or InMemoryLease(),
        transport=transport or RehearsalTransport(responses={}),
        sink=sink or RecordingSink(),
        clock=clock or (lambda: NOW),
        policy=policy,
        entitlement_verified=entitlement,
        authorization_reference=authorization,
        qualification=qualified,
    )


def test_happy_path_executes_one_reservation_per_request_and_qualifies():
    one, two = request("a"), request("b", provider="API_TENNIS")
    transport = RehearsalTransport(responses={
        one.dedupe_key: {"two_sided": True},
        two.dedupe_key: {"two_sided": False},
    })
    store = InMemoryReservationStore(allowances={"ODDSPAPI": 100, "API_TENNIS": 100})
    report = run([one, two], store=store, transport=transport)
    assert report["status"] == "COMPLETED_WITH_FAILURES"
    assert report["provider_calls"] == 2
    assert report["reservations"] == 2
    assert report["observations"] == 2
    assert report["qualified"] == 1
    assert report["status_counts"] == {"CAPTURED": 1, "NO_TWO_SIDED_PRICE": 1}
    assert len(store.all_reservations()) == 2
    assert all(r.status == "SUCCESS" for r in store.all_reservations())


def test_missing_entitlement_or_authorization_makes_zero_requests():
    transport = RehearsalTransport(responses={})
    store = InMemoryReservationStore(allowances={"ODDSPAPI": 100, "API_TENNIS": 100})
    blocked = run([request("a")], store=store, transport=transport,
                  entitlement=False)
    assert blocked["status"] == "BLOCKED_ENTITLEMENT"
    assert blocked["provider_calls"] == 0
    assert store.all_reservations() == []

    unauthorised = run([request("a")], store=store, transport=transport,
                       authorization="")
    assert unauthorised["status"] == "BLOCKED_NOT_AUTHORISED"
    assert transport.calls == []


def test_deadline_and_budget_fail_before_any_provider_call():
    transport = RehearsalTransport(responses={})
    store = InMemoryReservationStore(allowances={"ODDSPAPI": 100, "API_TENNIS": 100})
    late = run([request("a", deadline=NOW + timedelta(seconds=5))],
               store=store, transport=transport)
    assert late["status_counts"] == {"DEADLINE_MISSED": 1}
    assert transport.calls == []
    assert store.all_reservations() == []

    tight = InMemoryReservationStore(allowances={"ODDSPAPI": 1})
    report = run([request("a"), request("b")], store=tight, transport=transport)
    assert report["status_counts"]["BUDGET_EXHAUSTED"] == 1
    # The missing response is a timeout, but only one reservation was granted.
    assert len(tight.all_reservations()) == 1


def test_duplicate_reservation_prevents_a_second_provider_call():
    transport = RehearsalTransport(responses={})
    store = InMemoryReservationStore(allowances={"ODDSPAPI": 100, "API_TENNIS": 100})
    _first = run([request("a")], store=store, transport=transport)
    calls_after_first = list(transport.calls)
    second = run([request("a")], store=store, transport=transport)
    assert second["status_counts"] == {"DUPLICATE_REQUEST": 1}
    assert transport.calls == calls_after_first
    assert len(store.all_reservations()) == 1


def test_worker_lease_excludes_concurrent_worker():
    lease = InMemoryLease()
    lease.acquire(now=NOW)
    report = run([request("a")], lease=lease)
    assert report["status"] == "WORKER_BUSY"
    assert report["provider_calls"] == 0


def test_late_response_is_persisted_but_never_qualified():
    clock = MutableClock()
    one = request("late")
    transport = RehearsalTransport(
        responses={one.dedupe_key: {"two_sided": True}},
        advance_clock=lambda: clock.advance(timedelta(hours=2)),
    )
    sink = RecordingSink()
    report = run([one], transport=transport, sink=sink, clock=clock)
    assert report["status_counts"] == {"LATE_RESPONSE": 1}
    assert report["qualified"] == 0
    assert sink.records[0].qualification == "LATE_RESPONSE"


def test_timeout_and_provider_error_are_bounded_and_accounted():
    one = request("timeout")
    transport = RehearsalTransport(responses={one.dedupe_key: TimeoutError("slow")})
    store = InMemoryReservationStore(allowances={"ODDSPAPI": 100, "API_TENNIS": 100})
    report = run([one], store=store, transport=transport,
                 policy=PilotPolicy(max_attempts=2))
    assert report["status_counts"] == {"TIMEOUT": 1}
    assert report["provider_calls"] == 2
    assert len(store.all_reservations()) == 2
    assert [r.retry_sequence for r in store.all_reservations()] == [0, 1]


def test_lost_acknowledgement_is_not_reported_as_captured():
    one = request("ack")
    transport = RehearsalTransport(responses={one.dedupe_key: {"two_sided": True}})
    report = run([one], transport=transport,
                 sink=RecordingSink(failure=AcknowledgementLost("fsync lost")))
    assert report["status_counts"] == {"ACKNOWLEDGEMENT_LOST": 1}
    assert report["qualified"] == 0


def test_sqlite_reservations_survive_a_new_store_instance(tmp_path: Path):
    path = str(tmp_path / "pilot.sqlite")
    store = SqliteReservationStore(path, allowances={"ODDSPAPI": 1})
    first = run([request("a")], store=store)
    assert first["reservations"] == 1
    store.close()
    reopened = SqliteReservationStore(path)
    duplicate = run([request("a")], store=reopened)
    assert duplicate["status_counts"] == {"DUPLICATE_REQUEST": 1}
    assert reopened.attempts(request("a").dedupe_key) == 1
    reopened.close()


def test_json_sink_records_durable_timestamps(tmp_path: Path):
    path = str(tmp_path / "observations.jsonl")
    sink = JsonObservationSink(path)
    observed = datetime.now(UTC)
    observation = PilotObservation(
        provider_code="ODDSPAPI", endpoint="/v4/odds", dedupe_key="k",
        match_ids=(1,), received_at=observed, observed_at=observed,
        persisted_at=None, payload_hash="sha256:x", qualification="QUALIFIED",
    )
    persisted = sink.persist(observation)
    assert persisted.tzinfo is not None
    assert persisted >= observation.observed_at
    text = (tmp_path / "observations.jsonl").read_text()
    assert '"persisted_at": null' in text
    assert '"dedupe_key": "k"' in text


def test_requests_from_plan_apply_earliest_member_deadline():
    plan = {"cycles": [{
        "match_ids": [1, 2],
        "request_plan": [{
            "provider_code": "ODDSPAPI", "endpoint": "/v4/odds-by-tournaments",
            "purpose": "QUALIFICATION", "identifiers": ["5"], "bookmaker": "pinnacle+30",
        }],
    }]}
    requests = requests_from_plan(
        plan,
        deadlines_by_match={1: NOW + timedelta(hours=2), 2: NOW + timedelta(hours=1)},
        request_timeout_seconds=10,
    )
    assert len(requests) == 1
    assert requests[0].deadline == NOW + timedelta(hours=1, seconds=-10)
    assert requests[0].match_ids == (1, 2)


def test_missing_allowance_is_not_unlimited(tmp_path):
    one = request("unknown", provider="API_TENNIS")
    transport = RehearsalTransport(responses={one.dedupe_key: {"two_sided": True}})
    stores = (InMemoryReservationStore(allowances={"ODDSPAPI": 5}),
              SqliteReservationStore(str(tmp_path / "missing.sqlite"), allowances={"ODDSPAPI": 5}))
    for store in stores:
        report = run([one], store=store, transport=transport)
        assert report["status_counts"] == {"BUDGET_EXHAUSTED": 1}
        assert report["provider_calls"] == report["reservations"] == 0
    stores[1].close()
    assert transport.calls == []


def test_late_durable_ack_never_qualifies():
    clock = MutableClock()
    one = request("late-ack")
    class SlowSink(RecordingSink):
        def persist(self, observation):
            super().persist(observation)
            clock.advance(timedelta(hours=2))
            return clock()
    sink = SlowSink()
    transport = RehearsalTransport(responses={one.dedupe_key: {"two_sided": True}})
    report = run([one], transport=transport, sink=sink, clock=clock)
    assert report["qualified"] == 0
    assert report["results"][0]["reason"] == "PERSISTENCE_ACKNOWLEDGED_AFTER_DEADLINE"
    assert sink.records[0].qualification == "PENDING_DURABLE_QUALIFICATION"


def test_retry_stops_at_global_wait_budget():
    clock = MutableClock()
    one = request("slow-retry")
    class SlowTimeout(RehearsalTransport):
        def fetch(self, request, *, timeout_seconds, clock):
            clock.advance(timedelta(minutes=3))
            raise TimeoutError()
    report = run([one], transport=SlowTimeout(responses={}), clock=clock,
                 policy=PilotPolicy(max_attempts=2))
    assert report["provider_calls"] == report["reservations"] == 1
    assert report["status_counts"] == {"WAIT_BUDGET_EXHAUSTED": 1}
