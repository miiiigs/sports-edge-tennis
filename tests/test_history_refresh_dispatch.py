from __future__ import annotations

from types import SimpleNamespace

import sports_edge_tennis.live.history_refresh_dispatch as dispatch


def test_dispatch_legacy_uses_api_tennis_refresh(monkeypatch):
    monkeypatch.setattr(
        dispatch,
        "load_provider_authority",
        lambda env: SimpleNamespace(
            data_mode=dispatch.LEGACY_DATA_MODE,
            odds_mode="LEGACY_SPLIT",
        ),
    )

    import sports_edge_tennis.live.history_refresh_resilient as legacy

    calls = []
    monkeypatch.setattr(
        legacy,
        "main",
        lambda argv=None: calls.append(argv) or 0,
    )
    assert dispatch.main(["--end-date", "2026-09-18"]) == 0
    assert calls == [["--end-date", "2026-09-18"]]


def test_dispatch_target_uses_only_sportradar_refresh(monkeypatch):
    monkeypatch.setattr(
        dispatch,
        "load_provider_authority",
        lambda env: SimpleNamespace(
            data_mode=dispatch.TARGET_DATA_MODE,
            odds_mode="GOALSERVE_TENNIS",
        ),
    )

    import sports_edge_tennis.live.sportradar_history_refresh as target

    calls = []
    monkeypatch.setattr(
        target,
        "main",
        lambda argv=None: calls.append(argv) or 0,
    )
    assert dispatch.main(["--days", "3"]) == 0
    assert calls == [["--days", "3"]]


def test_dispatch_rejects_half_cutover(monkeypatch, capsys):
    monkeypatch.setattr(
        dispatch,
        "load_provider_authority",
        lambda env: (_ for _ in ()).throw(
            ValueError("Tennis provider cutover must switch data and odds authority together")
        ),
    )
    assert dispatch.main([]) == 2
    assert "HISTORY_REFRESH_DISPATCH_BLOCKED" in capsys.readouterr().out
