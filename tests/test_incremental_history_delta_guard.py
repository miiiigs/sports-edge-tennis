from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import sports_edge_tennis.runner.inputs as inputs


def _utc(hour: int) -> datetime:
    return datetime(2026, 9, 13, hour, 0, tzinfo=timezone.utc)


def _checkpoint(*, count: int = 100):
    return SimpleNamespace(
        checkpoint_cutoff=_utc(1),
        payload={"cached": True},
        observation_count=count,
    )


def test_visibility_guard_uses_exact_incremental_visibility_window():
    calls = []

    class Result:
        def fetchone(self):
            return (True,)

    class Conn:
        def execute(self, sql, params):
            calls.append((sql, params))
            return Result()

    assert inputs._has_visible_result_delta(
        Conn(),
        previous_cutoff=_utc(1),
        cutoff=_utc(2),
    ) is True
    assert len(calls) == 1
    sql, params = calls[0]
    assert "match_result_observations" in sql
    assert "source_observed_at >= %s" in sql
    assert "as_of >= %s" in sql
    assert params == (_utc(2), _utc(2), _utc(1), _utc(1))


def test_visibility_guard_short_circuits_non_forward_window():
    class Conn:
        def execute(self, *_args, **_kwargs):
            raise AssertionError("non-forward window must not query PostgreSQL")

    assert inputs._has_visible_result_delta(
        Conn(),
        previous_cutoff=_utc(2),
        cutoff=_utc(2),
    ) is False


def test_agent2_empty_delta_skips_expensive_history_load_but_advances_cutoff(monkeypatch):
    checkpoint = _checkpoint(count=123)
    cached_snapshot = object()
    advanced_snapshot = object()
    saved = {}

    monkeypatch.setattr(inputs, "load_checkpoint", lambda *args, **kwargs: checkpoint)
    monkeypatch.setattr(inputs, "checkpoint_requires_rebuild", lambda *args, **kwargs: None)
    monkeypatch.setattr(inputs, "decode_agent2_snapshot", lambda payload: cached_snapshot)
    monkeypatch.setattr(inputs, "_has_visible_result_delta", lambda *args, **kwargs: False)
    monkeypatch.setattr(
        inputs,
        "load_agent2_history",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("empty delta must not run the expensive Agent 2 history query")
        ),
    )

    def advance(snapshot, delta, *, cutoff):
        assert snapshot is cached_snapshot
        assert delta == []
        assert cutoff == _utc(2)
        return advanced_snapshot

    monkeypatch.setattr(inputs, "advance_agent2_history_snapshot", advance)
    monkeypatch.setattr(inputs, "encode_agent2_snapshot", lambda snapshot: {"encoded": snapshot is advanced_snapshot})

    def save(*args, **kwargs):
        saved.update(kwargs)

    monkeypatch.setattr(inputs, "save_checkpoint", save)

    result = inputs.build_agent2_history_snapshot(object(), _utc(2))

    assert result is advanced_snapshot
    assert saved["checkpoint_cutoff"] == _utc(2)
    assert saved["observation_count"] == 123
    assert saved["payload"] == {"encoded": True}


def test_agent3_empty_delta_skips_expensive_history_load_but_expires_at_new_cutoff(monkeypatch):
    checkpoint = _checkpoint(count=456)
    cached_snapshot = object()
    advanced_snapshot = object()
    saved = {}

    monkeypatch.setattr(inputs, "load_checkpoint", lambda *args, **kwargs: checkpoint)
    monkeypatch.setattr(inputs, "checkpoint_requires_rebuild", lambda *args, **kwargs: None)
    monkeypatch.setattr(inputs, "decode_agent3_snapshot", lambda payload: cached_snapshot)
    monkeypatch.setattr(inputs, "_has_visible_result_delta", lambda *args, **kwargs: False)
    monkeypatch.setattr(
        inputs,
        "load_agent3_history",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("empty delta must not run the expensive Agent 3 history query")
        ),
    )

    def advance(snapshot, delta, *, previous_cutoff, cutoff):
        assert snapshot is cached_snapshot
        assert delta == []
        assert previous_cutoff == _utc(1)
        assert cutoff == _utc(2)
        return advanced_snapshot

    monkeypatch.setattr(inputs, "advance_live_agent3_history_snapshot", advance)
    monkeypatch.setattr(inputs, "encode_agent3_snapshot", lambda snapshot: {"encoded": snapshot is advanced_snapshot})

    def save(*args, **kwargs):
        saved.update(kwargs)

    monkeypatch.setattr(inputs, "save_checkpoint", save)

    result = inputs.build_agent3_history_snapshot(object(), _utc(2))

    assert result is advanced_snapshot
    assert saved["checkpoint_cutoff"] == _utc(2)
    assert saved["observation_count"] == 456
    assert saved["payload"] == {"encoded": True}


def test_real_delta_keeps_existing_agent2_loader_path(monkeypatch):
    checkpoint = _checkpoint(count=10)
    cached_snapshot = object()
    observation = SimpleNamespace(visibility_cohort=_utc(1) + timedelta(minutes=30))
    seen = {}

    monkeypatch.setattr(inputs, "load_checkpoint", lambda *args, **kwargs: checkpoint)
    monkeypatch.setattr(inputs, "checkpoint_requires_rebuild", lambda *args, **kwargs: None)
    monkeypatch.setattr(inputs, "decode_agent2_snapshot", lambda payload: cached_snapshot)
    monkeypatch.setattr(inputs, "_has_visible_result_delta", lambda *args, **kwargs: True)

    def load_history(conn, *, cutoff, date_start):
        seen["cutoff"] = cutoff
        seen["date_start"] = date_start
        return [observation]

    monkeypatch.setattr(inputs, "load_agent2_history", load_history)

    def advance(snapshot, delta, *, cutoff):
        assert snapshot is cached_snapshot
        assert delta == [observation]
        return cached_snapshot

    monkeypatch.setattr(inputs, "advance_agent2_history_snapshot", advance)
    monkeypatch.setattr(inputs, "encode_agent2_snapshot", lambda snapshot: {})
    monkeypatch.setattr(inputs, "save_checkpoint", lambda *args, **kwargs: None)

    assert inputs.build_agent2_history_snapshot(object(), _utc(2)) is cached_snapshot
    assert seen["cutoff"] == _utc(2)
    assert seen["date_start"] == (_utc(1) - timedelta(days=1)).date()


def test_real_delta_keeps_existing_agent3_loader_path(monkeypatch):
    checkpoint = _checkpoint(count=20)
    cached_snapshot = object()
    observation = SimpleNamespace(visibility_cohort=_utc(1) + timedelta(minutes=30))
    seen = {}

    monkeypatch.setattr(inputs, "load_checkpoint", lambda *args, **kwargs: checkpoint)
    monkeypatch.setattr(inputs, "checkpoint_requires_rebuild", lambda *args, **kwargs: None)
    monkeypatch.setattr(inputs, "decode_agent3_snapshot", lambda payload: cached_snapshot)
    monkeypatch.setattr(inputs, "_has_visible_result_delta", lambda *args, **kwargs: True)

    def load_history(conn, *, cutoff, date_start):
        seen["cutoff"] = cutoff
        seen["date_start"] = date_start
        return [observation]

    monkeypatch.setattr(inputs, "load_agent3_history", load_history)

    def advance(snapshot, delta, *, previous_cutoff, cutoff):
        assert snapshot is cached_snapshot
        assert delta == [observation]
        assert previous_cutoff == _utc(1)
        assert cutoff == _utc(2)
        return cached_snapshot

    monkeypatch.setattr(inputs, "advance_live_agent3_history_snapshot", advance)
    monkeypatch.setattr(inputs, "encode_agent3_snapshot", lambda snapshot: {})
    monkeypatch.setattr(inputs, "save_checkpoint", lambda *args, **kwargs: None)

    assert inputs.build_agent3_history_snapshot(object(), _utc(2)) is cached_snapshot
    assert seen["cutoff"] == _utc(2)
    assert seen["date_start"] == (_utc(1) - timedelta(days=1)).date()
