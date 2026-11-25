"""Actual in-run Agent3 shadow proposals, no manager override or replay."""
import hashlib
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from sports_edge_tennis.agents.agent3_intransitivity import (
    Agent3HistorySnapshot, PredictionRequest, predict,
)
from sports_edge_tennis.validation.a3_soft_common_opponent_ess_shadow import (
    PROFILES, capture_provisional_live_shadow,
)
from sports_edge_tennis.runner import full_slate

NOW=datetime(2026,11,23,12,0,tzinfo=UTC)


def request():
    return PredictionRequest(
        player_1_id=10,player_2_id=20,surface="HARD",
        history_snapshot=Agent3HistorySnapshot(),as_of=NOW,
    )


def test_live_prestart_shadow_preserves_original_abstention_and_three_variants():
    req=request()
    original=predict(req)
    r=capture_provisional_live_shadow(
        req,canonical_match_id=134291,
        capture_at=NOW+timedelta(seconds=15),
        match_start_at=NOW+timedelta(hours=5),
        original_result=original,
        production_commit="abc123",
    )
    assert r["canonical_match_id"]==134291
    assert r["version"]=="A3_LIVE_SOFT_PIT_PROVISIONAL_V1"
    assert r["original_agent3_as_computed"]["status"]=="ABSTAINED"
    assert set(r["research_soft_common_opponent_variants"])==set(PROFILES)
    assert r["no_result_winner_used"] is True
    assert r["actual_frozen_live_snapshot_not_retrospective_replay"] is True
    assert r["independent_underlying_source_ingestion_verification"] is False
    assert r["first_issued_unique_canonical_prediction_not_yet_verified"] is True
    assert r["production_model_pick_unchanged"] is True
    assert r["cannot_promote_to_production"] is True
    d={k:v for k,v in r.items() if k!="record_sha256"}
    digest=hashlib.sha256(json.dumps(d,sort_keys=True,separators=(",",":")).encode()).hexdigest()
    assert digest==r["record_sha256"]


def test_live_shadow_refuses_late_or_fabricated_capture():
    req=request()
    original=predict(req)
    with pytest.raises(ValueError,match="pre-match"):
        capture_provisional_live_shadow(
            req,canonical_match_id=9,
            capture_at=NOW+timedelta(minutes=31),
            match_start_at=NOW+timedelta(hours=1),
            original_result=original,
        )
    with pytest.raises(ValueError,match="canonical"):
        capture_provisional_live_shadow(
            req,canonical_match_id=0,
            capture_at=NOW,match_start_at=NOW+timedelta(hours=1),
            original_result=original,
        )


def test_full_slate_shadow_reads_exact_asof_original_match_version(monkeypatch,capsys):
    checks=[]
    class Conn:
        def execute(self,sql,params):
            checks.append((sql,params))
            return self
        def fetchone(self):
            return (NOW+timedelta(hours=6),)
    def stub_capture(request,*,canonical_match_id,capture_at,match_start_at,
                     original_result,production_commit,**metadata):
        checks.append((canonical_match_id,capture_at,match_start_at,production_commit,metadata))
        return {"version":"test","canonical_match_id":canonical_match_id}
    import sports_edge_tennis.validation.a3_soft_common_opponent_ess_shadow as shadow
    monkeypatch.setattr(shadow,"capture_provisional_live_shadow",stub_capture)
    monkeypatch.setenv("RAILWAY_GIT_COMMIT_SHA","verified-commit")
    record = full_slate._capture_optional_a3_soft_shadow(
        Conn(),target=SimpleNamespace(match_id=134291,match_version_id=42),
        request=request(),original_result=object(),as_of=NOW,prediction_run_id=77)
    assert checks[0][1]==(42,134291,NOW,NOW,NOW)
    assert checks[1][0]==134291
    assert checks[1][3]=="verified-commit"
    assert checks[1][4]["prediction_run_id"] == 77
    assert checks[1][4]["match_version_id"] == 42
    assert record["canonical_match_id"] == 134291


@pytest.mark.parametrize("autocommit", [False, True])
@pytest.mark.parametrize("failure", ["sql", "python", "none"])
def test_postgres_shadow_preserves_production_transaction(
    postgres_dsn, monkeypatch, capsys, autocommit, failure,
):
    import psycopg
    from psycopg.pq import TransactionStatus

    target = SimpleNamespace(match_id=134291, match_version_id=42)
    with psycopg.connect(postgres_dsn, autocommit=autocommit) as conn:
        conn.execute("CREATE TEMP TABLE a3_shadow_tx_probe (id integer PRIMARY KEY)")
        if not autocommit:
            conn.commit()
        conn.execute("INSERT INTO a3_shadow_tx_probe VALUES (1)")
        before = conn.info.transaction_status

        def probe(connection, **kwargs):
            assert connection is conn
            if failure == "sql":
                connection.execute("SELECT 1/0")
            if failure == "python":
                raise ValueError("bad optional research evidence")
            return {"canonical_match_id": target.match_id}

        monkeypatch.setattr(full_slate, "_capture_optional_a3_soft_shadow", probe)
        assert full_slate._observe_a3_soft_shadow(
            conn, target=target, request=request(), original_result=object(),
            as_of=NOW, prediction_run_id=77,
        ) is (failure == "none")
        assert conn.info.transaction_status == before
        assert before == (TransactionStatus.IDLE if autocommit else TransactionStatus.INTRANS)
        conn.execute("INSERT INTO a3_shadow_tx_probe VALUES (2)")
        assert conn.execute("SELECT id FROM a3_shadow_tx_probe ORDER BY id").fetchall() == [(1,), (2,)]
        conn.commit()
        assert conn.execute("SELECT count(*) FROM a3_shadow_tx_probe").fetchone() == (2,)
        output = capsys.readouterr().out
        if failure == "sql":
            assert '"sqlstate": "22012"' in output
        assert ("TENNIS_A3_SOFT_PROSPECTIVE_SHADOW" in output) is (failure == "none")


def test_live_shadow_metadata_identifies_evaluation_not_first_issue():
    req = request()
    kwargs = dict(canonical_match_id=134291, capture_at=NOW,
                  match_start_at=NOW+timedelta(hours=5),
                  original_result=predict(req), prediction_run_id=77,
                  match_version_id=42, tour_scope="CHALLENGER")
    one = capture_provisional_live_shadow(req, **kwargs)
    two = capture_provisional_live_shadow(req, **{
        **kwargs, "capture_at": NOW+timedelta(seconds=1)})
    assert one["evaluation_key"] == two["evaluation_key"]
    assert one["record_sha256"] != two["record_sha256"]
    assert one["original_match_version_id"] == 42
    assert one["first_issued_unique_canonical_prediction_not_yet_verified"]


def test_shadow_logging_failure_is_nonblocking(monkeypatch):
    def broken_print(*args, **kwargs):
        raise OSError("closed log pipe")
    monkeypatch.setattr("builtins.print", broken_print)
    assert full_slate._emit_a3_shadow_event("TEST", {}) is False


@pytest.mark.parametrize("violation", ["match_id", "status", "as_of", "observed", "ingested"])
def test_postgres_original_version_pit_rejects_invalid_row(
    postgres_dsn, monkeypatch, violation,
):
    import psycopg

    with psycopg.connect(postgres_dsn, autocommit=True) as conn:
        # Session-local table: exercises the exact observer SELECT without
        # altering the durable match-version schema or any fixtures.
        conn.execute("""
            CREATE TEMP TABLE match_versions (
                match_version_id bigint, match_id bigint, match_start_at timestamptz,
                match_status text, as_of timestamptz,
                source_observed_at timestamptz, ingested_at timestamptz
            )
        """)
        base = [42, 134291, NOW + timedelta(hours=5), "SCHEDULED", NOW, NOW, NOW]
        index = {"match_id": 1, "status": 3, "as_of": 4, "observed": 5, "ingested": 6}[violation]
        base[index] = (999 if index == 1 else "CANCELLED" if index == 3
                       else NOW + timedelta(seconds=1))
        conn.execute("INSERT INTO match_versions VALUES (%s,%s,%s,%s,%s,%s,%s)", base)
        monkeypatch.setattr(full_slate, "_utc_now", lambda: NOW)
        req = request()
        target = SimpleNamespace(match_id=134291, match_version_id=42)
        assert not full_slate._observe_a3_soft_shadow(
            conn, target=target, request=req, original_result=predict(req),
            as_of=NOW, prediction_run_id=77,
        )
        assert conn.execute("SELECT 1").fetchone() == (1,)


def test_postgres_observer_uses_selected_version_not_newer_version(postgres_dsn, monkeypatch):
    import psycopg

    with psycopg.connect(postgres_dsn, autocommit=True) as conn:
        conn.execute("""
            CREATE TEMP TABLE match_versions (
                match_version_id bigint, match_id bigint, match_start_at timestamptz,
                match_status text, as_of timestamptz,
                source_observed_at timestamptz, ingested_at timestamptz
            )
        """)
        start = NOW + timedelta(hours=5)
        conn.execute("INSERT INTO match_versions VALUES (%s,%s,%s,%s,%s,%s,%s)",
                     (42, 134291, start, "SCHEDULED", NOW, NOW, NOW))
        later = NOW + timedelta(seconds=1)
        conn.execute("INSERT INTO match_versions VALUES (%s,%s,%s,%s,%s,%s,%s)",
                     (43, 134291, NOW+timedelta(hours=6), "SCHEDULED", later, later, later))
        monkeypatch.setattr(full_slate, "_utc_now", lambda: NOW)
        req = request()
        record = full_slate._capture_optional_a3_soft_shadow(
            conn, target=SimpleNamespace(match_id=134291, match_version_id=42),
            request=req, original_result=predict(req), as_of=NOW, prediction_run_id=77,
        )
        assert record["original_scheduled_start_at_utc"] == start.isoformat()
        assert record["original_match_version_id"] == 42


def test_live_shadow_completion_clock_rejects_calculation_crossing_deadline():
    req = request()
    kwargs = dict(canonical_match_id=134291, capture_at=NOW,
                  match_start_at=NOW+timedelta(minutes=31), original_result=predict(req))
    with pytest.raises(ValueError, match="completion violates pre-match"):
        capture_provisional_live_shadow(req, **kwargs,
            completion_clock=lambda: NOW+timedelta(minutes=2))
    with pytest.raises(ValueError, match="completion violates pre-match"):
        capture_provisional_live_shadow(req, **kwargs,
            completion_clock=lambda: NOW-timedelta(seconds=1))
    record = capture_provisional_live_shadow(req, **kwargs,
        completion_clock=lambda: NOW+timedelta(seconds=5))
    assert record["research_started_at_utc"] == NOW.isoformat()
    assert record["research_captured_at_utc"] == (NOW+timedelta(seconds=5)).isoformat()
    assert record["completion_clock_observed"] is True


def test_postgres_representative_66_target_observer_benchmark(postgres_dsn, monkeypatch, capsys):
    """Synthetic workload, not production latency or a prediction-quality test."""
    from decimal import Decimal
    from time import perf_counter
    import psycopg
    from sports_edge_tennis.agents.agent3_intransitivity import PairHistorySnapshot, ResidualObservation

    samples = []
    monkeypatch.setattr(full_slate, "_utc_now", lambda: NOW)
    with psycopg.connect(postgres_dsn, autocommit=True) as conn:
        conn.execute("""
            CREATE TEMP TABLE match_versions (
                match_version_id bigint PRIMARY KEY, match_id bigint, match_start_at timestamptz,
                match_status text, as_of timestamptz,
                source_observed_at timestamptz, ingested_at timestamptz
            )
        """)
        for i in range(1, 67):
            conn.execute("INSERT INTO match_versions VALUES (%s,%s,%s,%s,%s,%s,%s)",
                         (i, i, NOW+timedelta(hours=5), "SCHEDULED", NOW, NOW, NOW))
        for common in (0, 10, 100):
            opponents = tuple(range(1000, 1000+common))
            pairs = {}
            for p in (10, 20):
                observations = tuple(
                    ResidualObservation(observed_at=NOW-timedelta(days=20*j+1),
                                        residual=Decimal("0.4" if p == 10 else "-0.4"))
                    for j in range(8)
                )
                for o in opponents:
                    pairs[(p, o)] = PairHistorySnapshot(
                        player_low_id=p, player_high_id=o,
                        all_observations=observations,
                        surface_observations={"HARD": observations[:4]},
                    )
            req = PredictionRequest(
                player_1_id=10, player_2_id=20, surface="HARD",
                history_snapshot=Agent3HistorySnapshot(pairs=pairs, adjacency={10: opponents, 20: opponents}),
                as_of=NOW,
            )
            original = predict(req)
            for i in range(len(samples)+1, len(samples)+23):
                started = perf_counter()
                assert full_slate._observe_a3_soft_shadow(
                    conn, target=SimpleNamespace(match_id=i, match_version_id=i, tour_scope="CHALLENGER"),
                    request=req, original_result=original, as_of=NOW, prediction_run_id=77,
                )
                samples.append((perf_counter()-started)*1000)
        assert len(samples) == 66
        assert conn.execute("SELECT count(*) FROM match_versions").fetchone() == (66,)
    lines = capsys.readouterr().out.splitlines()
    assert sum(line.startswith("TENNIS_A3_SOFT_PROSPECTIVE_SHADOW ") for line in lines) == 66
    ordered = sorted(samples)
    report = dict(targets=66, shared_opponents=[0, 10, 100], observations_per_edge=8,
                  total_ms=round(sum(samples), 3), p95_ms=round(ordered[62], 3),
                  max_ms=round(ordered[-1], 3), synthetic=True)
    with capsys.disabled():
        print("A3_SYNTHETIC_OBSERVER_BENCHMARK " + json.dumps(report, sort_keys=True))


@pytest.mark.parametrize("autocommit", [False, True])
@pytest.mark.parametrize("failure", ["statement_timeout", "lock_timeout", "none"])
@pytest.mark.parametrize("stricter", [False, True])
def test_postgres_observer_bounded_sql_restores_caller_settings(
    postgres_dsn, monkeypatch, capsys, autocommit, failure, stricter,
):
    """Real PG cancellation, preservation of settings and caller writes."""
    import psycopg
    from psycopg.pq import TransactionStatus

    target = SimpleNamespace(match_id=134291, match_version_id=42)
    with psycopg.connect(postgres_dsn, autocommit=True) as holder:
        key = holder.execute("SELECT pg_backend_pid()").fetchone()[0]
        holder.execute("SELECT pg_advisory_lock(%s)", (key,))
        with psycopg.connect(postgres_dsn, autocommit=autocommit) as conn:
            before_ms = ("80ms", "40ms") if stricter else ("0", "0")
            conn.execute("SELECT set_config('statement_timeout', %s, false), "
                         "set_config('lock_timeout', %s, false)", before_ms)
            conn.execute("CREATE TEMP TABLE a3_timeout_probe (id integer PRIMARY KEY)")
            if not autocommit:
                conn.commit()
            conn.execute("INSERT INTO a3_timeout_probe VALUES (1)")
            before = conn.info.transaction_status

            def settings(connection):
                return dict(connection.execute(
                    "SELECT name, setting FROM pg_settings "
                    "WHERE name IN ('statement_timeout', 'lock_timeout')"
                ).fetchall())

            caller = settings(conn)
            expected = {"statement_timeout": "80", "lock_timeout": "40"} if stricter else {
                "statement_timeout": "250", "lock_timeout": "100"}
            seen = []

            def probe(connection, **kwargs):
                seen.append(settings(connection))
                if failure == "statement_timeout":
                    connection.execute("SELECT pg_sleep(0.6)")
                elif failure == "lock_timeout":
                    connection.execute("SELECT pg_advisory_xact_lock(%s)", (key,))
                return {"canonical_match_id": target.match_id}

            monkeypatch.setattr(full_slate, "_capture_optional_a3_soft_shadow", probe)
            ok = full_slate._observe_a3_soft_shadow(
                conn, target=target, request=request(), original_result=object(),
                as_of=NOW, prediction_run_id=77,
            )
            assert seen == [expected]
            assert ok is (failure == "none")
            assert conn.info.transaction_status == before
            assert before == (TransactionStatus.IDLE if autocommit else TransactionStatus.INTRANS)
            assert settings(conn) == caller
            conn.execute("INSERT INTO a3_timeout_probe VALUES (2)")
            conn.commit()
            assert conn.execute("SELECT id FROM a3_timeout_probe ORDER BY id").fetchall() == [(1,), (2,)]
            assert settings(conn) == caller
            output = capsys.readouterr().out
            if failure != "none":
                code = "57014" if failure == "statement_timeout" else "55P03"
                assert f'"sqlstate": "{code}"' in output
                assert "TENNIS_A3_SOFT_PROSPECTIVE_SHADOW" not in output
