from __future__ import annotations

import psycopg
import pytest

from sports_edge_tennis.live import production_lock
from sports_edge_tennis.live import production_batch
from sports_edge_tennis.live import batch


@pytest.fixture(autouse=True)
def _reset_process_lock_state():
    production_lock.reset_process_lock_state()
    yield
    production_lock.reset_process_lock_state()


@pytest.mark.real_production_lock
def test_production_batch_lock_is_single_owner_per_environment(postgres_dsn):
    first = production_lock.ProductionBatchLock(
        dsn=postgres_dsn, environment="prod-env", owner="first"
    )
    assert first.try_acquire() is True

    contender = production_lock.ProductionBatchLock(
        dsn=postgres_dsn, environment="prod-env", owner="second"
    )
    assert contender.try_acquire() is False

    first.release()

    after_release = production_lock.ProductionBatchLock(
        dsn=postgres_dsn, environment="prod-env", owner="third"
    )
    assert after_release.try_acquire() is True
    after_release.release()


@pytest.mark.real_production_lock
def test_production_batch_lock_isolates_environments(postgres_dsn):
    prod = production_lock.ProductionBatchLock(
        dsn=postgres_dsn, environment="prod-env", owner="prod"
    )
    dev = production_lock.ProductionBatchLock(
        dsn=postgres_dsn, environment="dev-env", owner="dev"
    )
    assert prod.try_acquire() is True
    try:
        assert dev.try_acquire() is True
    finally:
        dev.release()
        prod.release()


@pytest.mark.real_production_lock
def test_production_batch_lock_released_when_holder_connection_drops(postgres_dsn):
    holder = psycopg.connect(postgres_dsn, autocommit=True)
    object_id = production_lock._lock_object_id("drop-env")
    assert (
        holder.execute(
            "SELECT pg_try_advisory_lock(%s, %s)",
            (production_lock.PRODUCTION_BATCH_LOCK_NAMESPACE, object_id),
        ).fetchone()[0]
        is True
    )

    contender = production_lock.ProductionBatchLock(
        dsn=postgres_dsn, environment="drop-env", owner="late"
    )
    assert contender.try_acquire() is False

    holder.close()
    assert contender.try_acquire() is True
    contender.release()


@pytest.mark.real_production_lock
def test_wrapper_refuses_when_other_connection_owns_lock(postgres_dsn):
    owner = production_lock.ProductionBatchLock(
        dsn=postgres_dsn, environment="wrapper-env", owner="owner"
    )
    assert owner.try_acquire() is True

    contender = production_lock.acquire_production_batch_lock(
        postgres_dsn, environment="wrapper-env", owner="contender"
    )
    assert contender is None

    owner.release()


@pytest.mark.real_production_lock
def test_wrapper_reentrancy_is_reference_counted(postgres_dsn):
    outer = production_lock.acquire_production_batch_lock(
        postgres_dsn, environment="reentrant-env", owner="outer"
    )
    assert outer is not None and outer.reentrant is False

    inner = production_lock.acquire_production_batch_lock(
        postgres_dsn, environment="reentrant-env", owner="outer"
    )
    assert inner is not None and inner.reentrant is True

    production_lock.release_production_batch_lock(inner)

    still_owner = production_lock.ProductionBatchLock(
        dsn=postgres_dsn, environment="reentrant-env", owner="contender"
    )
    assert still_owner.try_acquire() is False

    production_lock.release_production_batch_lock(outer)

    after_release = production_lock.ProductionBatchLock(
        dsn=postgres_dsn, environment="reentrant-env", owner="contender"
    )
    assert after_release.try_acquire() is True
    after_release.release()


def test_live_batch_skips_before_provider_work_when_lock_refused(monkeypatch, capsys):
    provider_calls = []

    monkeypatch.setattr(
        production_lock,
        "acquire_production_batch_lock",
        lambda dsn, **kwargs: None,
    )
    monkeypatch.setattr(
        batch, "run_live_mvp", lambda *args, **kwargs: provider_calls.append(1)
    )

    rc = batch.main(["--days", "1", "--dsn", "postgresql://example"])

    assert rc == 0
    out = capsys.readouterr().out
    assert "LIVE_BATCH_SKIPPED" in out
    assert production_lock.SKIPPED_ALREADY_RUNNING in out
    assert provider_calls == []


def test_production_batch_skips_before_provider_work_when_lock_refused(
    monkeypatch, capsys
):
    def forbidden(*args, **kwargs):
        raise AssertionError("production work executed after lock refusal")

    monkeypatch.setattr(production_batch, "_db_dsn", lambda: "postgresql://example")
    monkeypatch.setattr(
        production_lock,
        "acquire_production_batch_lock",
        lambda dsn, **kwargs: None,
    )
    monkeypatch.setattr(production_batch, "live_batch_main", forbidden)
    monkeypatch.setattr(
        production_batch, "settle_type_a_match_winner_decisions", forbidden
    )
    monkeypatch.setattr(production_batch, "_shadow_cycle", forbidden)

    rc = production_batch.main([])

    assert rc == 0
    out = capsys.readouterr().out
    assert "PRODUCTION_BATCH_SKIPPED" in out
    assert production_lock.SKIPPED_ALREADY_RUNNING in out


def test_production_batch_releases_lock_when_batch_raises(monkeypatch):
    released = []
    fake_info = production_lock.ProductionLockInfo(
        environment="env",
        owner="owner",
        reentrant=False,
        lock_namespace=production_lock.PRODUCTION_BATCH_LOCK_NAMESPACE,
        lock_object=0,
    )
    monkeypatch.setattr(production_batch, "_db_dsn", lambda: "postgresql://example")
    monkeypatch.setattr(
        production_lock,
        "acquire_production_batch_lock",
        lambda dsn, **kwargs: fake_info,
    )
    monkeypatch.setattr(
        production_lock,
        "release_production_batch_lock",
        lambda info: released.append(info),
    )

    def boom(dsn, argv):
        raise RuntimeError("batch crashed")

    monkeypatch.setattr(production_batch, "_run_batch", boom)

    with pytest.raises(RuntimeError, match="batch crashed"):
        production_batch.main([])

    assert released == [fake_info]
