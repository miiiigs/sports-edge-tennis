from __future__ import annotations

from datetime import UTC, datetime

import pytest

from sports_edge_tennis.live import sportradar_identity_bridge_apply as module
from sports_edge_tennis.live.sportradar_benchmark import (
    CanonicalGapTarget,
    ProviderNameIdentityEvidence,
    SportradarLinkCandidate,
)
from sports_edge_tennis.live.sportradar_identity_bridge_apply import (
    ACTOR_ENV,
    AUTH_ENV,
    AUTH_TOKEN,
    ENABLED_ENV,
    EVENT_ID_ENV,
    HISTORICAL_PLAYER_ID_ENV,
    LIVE_PLAYER_ID_ENV,
    MATCH_ID_ENV,
    PROVIDER_PLAYER_ID_ENV,
    REASON_ENV,
    run_optional_identity_bridge,
)


class _Conn:
    def __init__(self):
        self.commits = 0
        self.rollbacks = 0

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def _target() -> CanonicalGapTarget:
    return CanonicalGapTarget(
        match_id=130469,
        prediction_as_of=datetime(2026, 9, 18, 18, 2, tzinfo=UTC),
        player_1_id=4644,
        player_1_name="Live Player",
        player_2_id=88,
        player_2_name="Other Player",
        match_start_at=datetime(2026, 9, 18, 19, 0, tzinfo=UTC),
        tournament_id=9,
        tournament_name="Davis Cup",
        season_year=2026,
        player_1_identity_status="LIVE_UNRESOLVED",
        player_2_identity_status="BRIDGED",
        surface_status="UNRESOLVED",
    )


def _candidate() -> SportradarLinkCandidate:
    return SportradarLinkCandidate(
        event_id="sr:sport_event:74801010",
        competition_id="sr:competition:2100",
        competition_name="Davis Cup",
        start_at=datetime(2026, 9, 18, 19, 20, tzinfo=UTC),
        start_delta_minutes=20,
        player_1_sportradar_id="sr:competitor:195800",
        player_1_sportradar_name="Example, Player",
        player_2_sportradar_id="sr:competitor:90798",
        player_2_sportradar_name="Other, Player",
    )


def _env() -> dict[str, str]:
    return {
        ENABLED_ENV: "true",
        AUTH_ENV: AUTH_TOKEN,
        MATCH_ID_ENV: "130469",
        LIVE_PLAYER_ID_ENV: "4644",
        HISTORICAL_PLAYER_ID_ENV: "622",
        EVENT_ID_ENV: "sr:sport_event:74801010",
        PROVIDER_PLAYER_ID_ENV: "sr:competitor:195800",
        ACTOR_ENV: "operator:sports-edge",
        REASON_ENV: "exact Sportradar event plus unique PIT candidate",
    }


def _install_success_path(monkeypatch, conn: _Conn, *, latest=None):
    monkeypatch.setattr(module.psycopg, "connect", lambda *args, **kwargs: conn)
    monkeypatch.setattr(module, "ensure_schema", lambda conn: "EXISTING")
    monkeypatch.setattr(
        module,
        "has_non_api_source_identity",
        lambda conn, player_id: player_id == 622,
    )
    monkeypatch.setattr(
        module,
        "provider_name_identity_evidence",
        lambda *args, **kwargs: ProviderNameIdentityEvidence(
            "UNIQUE_PIT_CANDIDATE",
            (622,),
            (622,),
        ),
    )
    monkeypatch.setattr(
        module,
        "latest_bridge_event",
        lambda conn, live_player_id: latest,
    )


def test_identity_bridge_apply_is_disabled_by_default(monkeypatch):
    monkeypatch.setattr(
        module.psycopg,
        "connect",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("no connect")),
    )

    assert (
        run_optional_identity_bridge(
            dsn="postgresql://example",
            exact_links=((_target(), _candidate()),),
            env={},
        )
        == 0
    )


def test_identity_bridge_apply_requires_exact_authorization():
    env = _env()
    env[AUTH_ENV] = "wrong"

    with pytest.raises(ValueError, match="authorization token mismatch"):
        run_optional_identity_bridge(
            dsn="postgresql://example",
            exact_links=((_target(), _candidate()),),
            env=env,
        )


def test_identity_bridge_apply_refuses_provider_competitor_mismatch():
    env = _env()
    env[PROVIDER_PLAYER_ID_ENV] = "sr:competitor:wrong"

    with pytest.raises(ValueError, match="provider competitor mismatch"):
        run_optional_identity_bridge(
            dsn="postgresql://example",
            exact_links=((_target(), _candidate()),),
            env=env,
        )


def test_identity_bridge_apply_inserts_after_unique_pit_evidence(monkeypatch, capsys):
    conn = _Conn()
    _install_success_path(monkeypatch, conn)
    captured = {}

    def append(conn, **kwargs):
        captured.update(kwargs)
        return 41

    monkeypatch.setattr(module, "append_bridge", append)
    activated_at = datetime(2026, 9, 18, 18, 35, tzinfo=UTC)

    status = run_optional_identity_bridge(
        dsn="postgresql://example",
        exact_links=((_target(), _candidate()),),
        env=_env(),
        now=activated_at,
    )

    assert status == 0
    assert conn.commits == 1
    assert conn.rollbacks == 0
    assert captured["live_player_id"] == 4644
    assert captured["historical_player_id"] == 622
    assert captured["evidence_match_id"] == 130469
    assert captured["evidence_provider_event_id"] == "sr:sport_event:74801010"
    assert captured["evidence_provider_player_id"] == "sr:competitor:195800"
    assert captured["evidence_as_of"] == _target().prediction_as_of
    assert captured["activated_at"] == activated_at
    out = capsys.readouterr().out
    assert "SPORTRADAR_IDENTITY_BRIDGE_APPLY status=INSERTED" in out
    assert "writes=1" in out


def test_identity_bridge_apply_is_idempotent_for_same_existing_bridge(
    monkeypatch,
    capsys,
):
    conn = _Conn()
    latest = (
        "BRIDGE",
        622,
        "sr:sport_event:74801010",
        "sr:competitor:195800",
    )
    _install_success_path(monkeypatch, conn, latest=latest)
    monkeypatch.setattr(
        module,
        "append_bridge",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("no insert")),
    )

    status = run_optional_identity_bridge(
        dsn="postgresql://example",
        exact_links=((_target(), _candidate()),),
        env=_env(),
    )

    assert status == 0
    assert conn.commits == 0
    assert conn.rollbacks == 1
    assert "status=EXISTING" in capsys.readouterr().out


def test_identity_bridge_apply_refuses_conflicting_existing_event(monkeypatch):
    conn = _Conn()
    _install_success_path(
        monkeypatch,
        conn,
        latest=("BRIDGE", 777, "sr:sport_event:old", "sr:competitor:old"),
    )

    with pytest.raises(ValueError, match="conflicting existing bridge event"):
        run_optional_identity_bridge(
            dsn="postgresql://example",
            exact_links=((_target(), _candidate()),),
            env=_env(),
        )

    assert conn.commits == 0
