from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import sports_edge_tennis.performance.live_shadow as live_shadow


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _Conn:
    def __init__(self):
        self.sql = ""
        self.params = None

    def execute(self, sql, params=None):
        self.sql = sql
        self.params = params
        return _Result([(17,)])


def test_live_shadow_selector_excludes_experiment_runs(monkeypatch):
    observed_at = datetime(2026, 9, 17, 1, 0, tzinfo=timezone.utc)
    conn = _Conn()
    monkeypatch.setattr(
        live_shadow,
        "activate_shadow_strategy",
        lambda conn, activated_at: datetime(2026, 9, 17, 0, 0, tzinfo=timezone.utc),
    )
    monkeypatch.setattr(
        live_shadow,
        "load_missing_sharp_reference_blockers",
        lambda conn, prediction_run_id: (),
    )
    seen = []
    monkeypatch.setattr(
        live_shadow,
        "observe_shadow_run",
        lambda conn, **kwargs: seen.append(kwargs["prediction_run_id"])
        or SimpleNamespace(prediction_run_id=kwargs["prediction_run_id"]),
    )

    outcomes = live_shadow.observe_pending_live_shadow_runs(
        conn,
        observed_at=observed_at,
        sharp_reference=SimpleNamespace(),
    )

    assert "pr.experiment_id IS NULL" in conn.sql
    assert seen == [17]
    assert outcomes[0].prediction_run_id == 17


def test_live_shadow_logs_sharp_blockers_before_observation(monkeypatch, capsys):
    observed_at = datetime(2026, 9, 17, 1, 0, tzinfo=timezone.utc)
    conn = _Conn()
    monkeypatch.setattr(
        live_shadow,
        "activate_shadow_strategy",
        lambda conn, activated_at: datetime(2026, 9, 17, 0, 0, tzinfo=timezone.utc),
    )
    blocker = SimpleNamespace(match_id=99)
    monkeypatch.setattr(
        live_shadow,
        "load_missing_sharp_reference_blockers",
        lambda conn, prediction_run_id: (blocker,),
    )
    monkeypatch.setattr(
        live_shadow,
        "format_sharp_blocker",
        lambda row: f"SHARP_BLOCKER match_id={row.match_id}",
    )
    monkeypatch.setattr(
        live_shadow,
        "observe_shadow_run",
        lambda conn, **kwargs: SimpleNamespace(prediction_run_id=kwargs["prediction_run_id"]),
    )

    live_shadow.observe_pending_live_shadow_runs(
        conn,
        observed_at=observed_at,
        sharp_reference=SimpleNamespace(),
    )

    assert "SHARP_BLOCKER match_id=99" in capsys.readouterr().out
