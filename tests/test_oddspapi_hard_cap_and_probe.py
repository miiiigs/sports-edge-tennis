"""Hard OddsPapi attempt cap, ingestion identity rules, fail-closed batch, and the probe."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
import uuid

import psycopg
import pytest

import sports_edge_tennis.live.batch as batch
import sports_edge_tennis.live.oddspapi as oddspapi
from sports_edge_tennis.live.oddspapi_ledger import (
    OddspapiAttemptCap,
    OddspapiBudgetExceeded,
    OddspapiLedgerBinding,
    OddspapiLedgerConfig,
)
from sports_edge_tennis.live.oddspapi_odds import ingest_oddspapi_tennis_odds
from sports_edge_tennis.live.oddspapi_probe import (
    HARD_MAX_REQUESTS,
    run_probe,
)
from tests.test_api_tennis_oddspapi_pairing import (
    AS_OF,
    START,
    _canonical_match,
    _install_api_tennis_oddspapi_batch,
    _oddspapi_links,
    _run,
)

CONFIG = OddspapiLedgerConfig(monthly_limit=5000, reserve=500)


def _ledger_rows(conn: psycopg.Connection, correlation_id: str) -> list[tuple]:
    # The ledger is not truncated between tests; scope by correlation id.
    return conn.execute(
        "SELECT request_purpose, classification, retry_sequence, attempt_status::text "
        "FROM oddspapi_request_ledger WHERE correlation_id = %s "
        "ORDER BY oddspapi_request_ledger_id",
        (correlation_id,),
    ).fetchall()


def _cid() -> str:
    return f"test-{uuid.uuid4().hex}"


# --- attempt cap ----------------------------------------------------------


def test_attempt_cap_raises_at_limit():
    cap = OddspapiAttemptCap(2)
    cap.take()
    cap.take()
    with pytest.raises(OddspapiBudgetExceeded, match="attempt cap"):
        cap.take()
    assert cap.used == 2 and cap.remaining == 0


def test_attempt_cap_rejects_negative_limit():
    with pytest.raises(ValueError):
        OddspapiAttemptCap(-1)


class _Response:
    def __init__(self, status: int, payload=None):
        self.status_code = status
        self._payload = payload if payload is not None else {}
        self.headers = {}

    def json(self):
        return self._payload


def test_retries_draw_from_the_cap_before_any_socket_opens(db: psycopg.Connection, monkeypatch):
    """Three 429s with a cap of 2: only two HTTP attempts and two ledger rows."""
    sent: list[str] = []
    monkeypatch.setattr(
        oddspapi.requests, "get", lambda url, **kw: sent.append(url) or _Response(429)
    )
    monkeypatch.setattr(oddspapi.time, "sleep", lambda seconds: None)
    cap = OddspapiAttemptCap(2)
    cid = _cid()
    ledger = OddspapiLedgerBinding(
        conn=db,
        config=CONFIG,
        request_purpose="DISCOVERY",
        classification="DIAGNOSTIC",
        correlation_id=cid,
        attempt_cap=cap,
    )
    with pytest.raises(OddspapiBudgetExceeded):
        oddspapi.fetch_oddspapi_tennis_fixtures(
            "k",
            from_at=START - timedelta(hours=1),
            to_at=START + timedelta(hours=1),
            ledger=ledger,
        )
    assert len(sent) == 2
    assert [row[2] for row in _ledger_rows(db, cid)] == [0, 1]


def test_shared_cap_spans_discovery_and_qualification_bindings(db: psycopg.Connection):
    cap = OddspapiAttemptCap(5)
    cid = _cid()
    bindings = [
        OddspapiLedgerBinding(
            conn=db, config=CONFIG, request_purpose=purpose, classification="PRODUCTION",
            correlation_id=cid, attempt_cap=cap,
        )
        for purpose in ("DISCOVERY", "QUALIFICATION")
    ]
    bindings[0].reserve(endpoint="fixtures")
    for _ in range(4):
        bindings[1].reserve(endpoint="odds-by-tournaments")
    with pytest.raises(OddspapiBudgetExceeded):
        bindings[1].reserve(endpoint="odds-by-tournaments", retry_sequence=1)
    assert len(_ledger_rows(db, cid)) == 5


# --- ingestion identity rules ---------------------------------------------


def _payload(fixture_id: str, *, p1_id: int, p2_id: int, start: datetime = START) -> dict:
    return {
        "fixtureId": fixture_id,
        "participant1Id": p1_id,
        "participant2Id": p2_id,
        "participant1Name": "Novak Djokovic",
        "participant2Name": "Daniil Medvedev",
        "sportId": 12,
        "tournamentId": 303,
        "startTime": start.isoformat().replace("+00:00", "Z"),
        "bookmakerOdds": {},
    }


def test_match_claimed_by_two_fixtures_in_one_batch_links_neither(db: psycopg.Connection):
    match_id, _, _ = _canonical_match(db)
    for rows in (
        [_payload("id-a", p1_id=1, p2_id=2), _payload("id-b", p1_id=3, p2_id=4)],
        [_payload("id-b", p1_id=3, p2_id=4), _payload("id-a", p1_id=1, p2_id=2)],
    ):
        outcome = ingest_oddspapi_tennis_odds(
            db, payload_rows=rows, received_at=AS_OF, candidate_match_ids=(match_id,)
        )
        assert (outcome.exact_events, outcome.conflict_events) == (0, 2)
        assert _oddspapi_links(db, match_id) == []


def test_ingestion_honours_sixty_minute_window_for_api_tennis(db: psycopg.Connection):
    match_id, _, _ = _canonical_match(db)
    rows = [_payload("id-late", p1_id=1, p2_id=2, start=START + timedelta(minutes=90))]
    strict = ingest_oddspapi_tennis_odds(
        db, payload_rows=rows, received_at=AS_OF, candidate_match_ids=(match_id,),
        max_start_delta_minutes=60,
    )
    assert (strict.exact_events, strict.unmatched_events) == (0, 1)
    assert _oddspapi_links(db, match_id) == []
    default = ingest_oddspapi_tennis_odds(
        db, payload_rows=rows, received_at=AS_OF, candidate_match_ids=(match_id,)
    )
    assert default.exact_events == 1  # legacy 120-minute default unchanged


# --- batch wiring ---------------------------------------------------------


def test_batch_passes_sixty_minute_window_and_shared_cap(monkeypatch, tmp_path):
    calls = _install_api_tennis_oddspapi_batch(monkeypatch, consensus=True)
    caps: list = []

    class _Recording:
        def __init__(self, **kwargs):
            caps.append(kwargs.get("attempt_cap"))

        def reserve(self, **kwargs):
            return 1

        def record(self, *args, **kwargs):
            return None

    monkeypatch.setattr(batch, "OddspapiLedgerBinding", _Recording)
    assert _run(tmp_path) == 0
    assert calls["ingest"][0]["max_start_delta_minutes"] == 60
    assert len(caps) == 2 and caps[0] is caps[1] and caps[0].limit == 5


def test_unexpected_oddspapi_failure_fails_closed_and_keeps_the_cycle(
    monkeypatch, tmp_path, capsys
):
    calls = _install_api_tennis_oddspapi_batch(monkeypatch, consensus=True)
    market: list = []

    def broken_ingest(conn, **kwargs):
        raise psycopg.errors.CheckViolation("integrity")

    monkeypatch.setattr(batch, "ingest_oddspapi_tennis_odds", broken_ingest)
    monkeypatch.setattr(
        batch,
        "evaluate_and_persist_market_slate",
        lambda *a, **k: market.append(k)
        or SimpleNamespace(status="PERSISTED", new_decision_count=0, qualified_bet_count=0),
    )
    assert _run(tmp_path) == 0
    out = capsys.readouterr().out
    assert "OFFERED_ODDS_UNAVAILABLE provider=ODDSPAPI_TENNIS kind=IMPLEMENTATION class=CheckViolation" in out
    assert "IMPLEMENTATION_FAILED" not in out
    assert market, "Market evaluation must still run (and find no sharp price)"


# --- readiness ------------------------------------------------------------


def test_readiness_requires_the_exact_sharp_key():
    import sports_edge_tennis.live.provider_authority as authority

    seen: dict = {}

    class _Conn:
        def execute(self, sql, params=None):
            seen["params"] = params
            return SimpleNamespace(fetchone=lambda: (False, True))

    assert authority._oddspapi_mapping_state(_Conn()) == (False, True)
    assert seen["params"]["sharp_key"] == oddspapi.ODDSPAPI_SHARP_BOOKMAKER
    assert tuple(seen["params"]["offered_keys"]) == tuple(
        book for book in oddspapi.ODDSPAPI_PLAN_BOOKMAKERS if book != oddspapi.ODDSPAPI_SHARP_BOOKMAKER
    )


# --- probe ----------------------------------------------------------------

NOW = datetime(2026, 9, 30, 6, 0, tzinfo=UTC)


def _forbidden(*args, **kwargs):
    raise AssertionError("probe must not call OddsPapi here")


def test_probe_rejects_more_than_five_requests(db: psycopg.Connection):
    with pytest.raises(ValueError):
        run_probe(db, api_key="k", now=NOW, max_requests=HARD_MAX_REQUESTS + 1, execute=True)


def test_probe_with_zero_consensus_spends_nothing(db: psycopg.Connection):
    report = run_probe(
        db, api_key="k", now=NOW, execute=True,
        fetch_fixtures=_forbidden, fetch_odds=_forbidden,
    )
    assert report.stop_reason == "ZERO_UPCOMING_CONSENSUS"
    assert report.attempts_used == 0
    assert _ledger_rows(db, report.correlation_id) == []
    assert report.acceptance["9_within_request_cap"] == "PASS"
    assert report.acceptance["10_qualified_evidence_without_pinnapi"] == "NOT_EVALUATED"


def _seed_consensus(db: psycopg.Connection, monkeypatch) -> int:
    import sports_edge_tennis.live.oddspapi_probe as probe

    match_id, _, _ = _canonical_match(db)
    monkeypatch.setattr(
        probe,
        "load_upcoming_consensus_scope",
        lambda conn, now, horizon_hours=48: [{"match_id": match_id, "scheduled_start": START}],
    )
    return match_id


def test_probe_dry_run_makes_no_request(db: psycopg.Connection, monkeypatch):
    _seed_consensus(db, monkeypatch)
    report = run_probe(
        db, api_key="k", now=NOW, execute=False,
        fetch_fixtures=_forbidden, fetch_odds=_forbidden,
    )
    assert report.stop_reason == "DRY_RUN_NO_REQUESTS"
    assert report.consensus_upcoming == 1 and report.attempts_used == 0


def test_probe_full_run_within_cap_writes_only_ledger_rows(db: psycopg.Connection, monkeypatch):
    match_id = _seed_consensus(db, monkeypatch)
    received = AS_OF

    def fixtures(key, *, ledger, **kw):
        ledger.reserve(endpoint="fixtures")
        return received, [_payload("id-probe", p1_id=1, p2_id=2)]

    def odds(key, *, tournament_ids, bookmakers, ledger, fallback_budget, **kw):
        assert fallback_budget == 0 and tuple(tournament_ids) == (303,)
        for book in bookmakers:
            ledger.reserve(endpoint="odds-by-tournaments", bookmaker=book)
        return received, [], len(bookmakers)

    report = run_probe(db, api_key="k", now=NOW, execute=True, fetch_fixtures=fixtures, fetch_odds=odds)

    assert report.stop_reason == "COMPLETED"
    assert report.attempts_used == 5
    assert [m["match_id"] for m in report.matched] == [match_id]
    assert report.matched[0]["orientation"] == "DIRECT"
    assert report.acceptance["3_canonical_match"] == "PASS"
    assert report.acceptance["9_within_request_cap"] == "PASS"
    assert {row[1] for row in _ledger_rows(db, report.correlation_id)} == {"DIAGNOSTIC"}
    # persist=False: no durable OddsPapi link is written by the probe.
    assert _oddspapi_links(db, match_id) == []


def test_probe_stops_at_cap_when_retries_consume_it(db: psycopg.Connection, monkeypatch):
    _seed_consensus(db, monkeypatch)

    def fixtures(key, *, ledger, **kw):
        ledger.reserve(endpoint="fixtures")
        return AS_OF, [_payload("id-probe", p1_id=1, p2_id=2)]

    def odds_with_retries(key, *, ledger, **kw):
        for _ in range(10):  # a provider that keeps rate-limiting
            ledger.reserve(endpoint="odds-by-tournaments")
        raise AssertionError("unreachable")

    report = run_probe(
        db, api_key="k", now=NOW, execute=True,
        fetch_fixtures=fixtures, fetch_odds=odds_with_retries,
    )
    assert report.stop_reason.startswith("CAP_REACHED")
    assert report.attempts_used == 5
    assert len(_ledger_rows(db, report.correlation_id)) == 5


def test_probe_reports_auth_failure(db: psycopg.Connection, monkeypatch):
    from sports_edge_tennis.live.transport import TransportError

    _seed_consensus(db, monkeypatch)

    def denied(key, *, ledger, **kw):
        ledger.reserve(endpoint="fixtures")
        raise TransportError("HTTP_403", "forbidden")

    report = run_probe(
        db, api_key="k", now=NOW, execute=True, fetch_fixtures=denied, fetch_odds=_forbidden
    )
    assert report.stop_reason == "TRANSPORT_HTTP_403"
    assert report.acceptance["1_auth"] == "FAIL"
    assert report.attempts_used == 1


def _load_local_probe_wrapper():
    import importlib.util
    from pathlib import Path

    script = Path(__file__).resolve().parents[1] / "ops" / "validation" / "oddspapi_readiness_probe_local.py"
    spec = importlib.util.spec_from_file_location("oddspapi_readiness_probe_local", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_local_probe_wrapper_builds_public_dsn_in_memory_and_keeps_the_cap(monkeypatch, capsys):
    import json
    import sports_edge_tennis.live.oddspapi_probe as probe_module

    wrapper = _load_local_probe_wrapper()
    # Placeholder with reserved characters, which the wrapper must percent-encode.
    fake_pw = "-".join(("not", "a", "real", "value")) + "@/"
    names = ("PGUSER", "PG" + "PASSWORD", "RAILWAY_TCP_PROXY_DOMAIN", "RAILWAY_TCP_PROXY_PORT", "PGDATABASE")
    db_vars = dict(zip(names, ("u", fake_pw, "proxy.example", "12345", "railway")))
    monkeypatch.setattr(wrapper.shutil, "which", lambda name: "railway")
    monkeypatch.setattr(
        wrapper.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(stdout=json.dumps(db_vars)),
    )
    monkeypatch.setenv("TENNIS_DATABASE_URL", "unreachable-internal-host")
    seen = {}
    monkeypatch.setattr(probe_module, "main", lambda argv: seen.setdefault("argv", argv) and 0)

    assert wrapper.main([]) == 0
    assert seen["argv"] == ["--max-requests", "5"]
    import os

    dsn = os.environ["TENNIS_DATABASE_URL"]
    from urllib.parse import quote

    assert quote(fake_pw, safe="") in dsn and fake_pw not in dsn
    assert dsn.endswith("proxy.example:12345/railway")
    out = capsys.readouterr().out
    assert "execute=False" in out and fake_pw not in out and "proxy.example" not in out


def test_local_probe_wrapper_refuses_an_explicit_dsn():
    wrapper = _load_local_probe_wrapper()
    with pytest.raises(SystemExit, match="DSN_IS_RESOLVED_BY_WRAPPER"):
        wrapper.main(["--dsn", "somewhere-else"])
