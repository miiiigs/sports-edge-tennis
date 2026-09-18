from __future__ import annotations

from datetime import UTC, datetime

import pytest

from sports_edge_tennis.live import sportradar_surface_apply as module
from sports_edge_tennis.live.sportradar_surface_apply import (
    AUTH_ENV,
    AUTH_TOKEN,
    COMPETITION_ID_ENV,
    ENABLED_ENV,
    MATCH_IDS_ENV,
    SEASON_ID_ENV,
    SURFACE_ENV,
    apply_surface_fill,
    run_optional_surface_fill,
)


class _Cursor:
    def __init__(self, *, row=None, rows=None):
        self._row = row
        self._rows = [] if rows is None else rows

    def fetchone(self):
        return self._row

    def fetchall(self):
        return self._rows


class _FakeConn:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []
        self.commits = 0

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def commit(self):
        self.commits += 1

    def execute(self, sql, params=()):
        self.calls.append((sql, params))
        if "FROM matches m" in sql:
            return _Cursor(rows=self.rows)
        if "UPDATE tournament_editions" in sql:
            return _Cursor(row=(self.rows[0][1],))
        if "INSERT INTO data_health_checks" in sql:
            return _Cursor()
        raise AssertionError(f"unexpected SQL: {sql}")


def _rows(surface=None, edition_id=77):
    return [
        (130441, edition_id, surface, 2026, "Rennes 2026", "Rennes"),
        (130491, edition_id, surface, 2026, "Rennes 2026", "Rennes"),
    ]


def test_surface_fill_updates_null_shared_edition_and_audits(capsys):
    conn = _FakeConn(_rows())
    observed_at = datetime(2026, 9, 18, 17, 9, 24, tzinfo=UTC)
    checked_at = datetime(2026, 9, 18, 17, 10, tzinfo=UTC)

    status, edition_id = apply_surface_fill(
        conn,
        match_ids=(130441, 130491),
        competition_id="sr:competition:3899",
        season_id="sr:season:142010",
        canonical_surface="HARD",
        raw_surface="hardcourt_indoor",
        provider_observed_at=observed_at,
        checked_at=checked_at,
    )

    assert status == "UPDATED"
    assert edition_id == 77
    assert sum("UPDATE tournament_editions" in sql for sql, _ in conn.calls) == 1
    assert sum("INSERT INTO data_health_checks" in sql for sql, _ in conn.calls) == 1
    output = capsys.readouterr().out
    assert "SPORTRADAR_SURFACE_APPLY status=UPDATED" in output
    assert "surface=HARD" in output
    assert "matches=130441,130491" in output


def test_surface_fill_is_idempotent_when_expected_surface_already_exists(capsys):
    conn = _FakeConn(_rows(surface="HARD"))

    status, edition_id = apply_surface_fill(
        conn,
        match_ids=(130441, 130491),
        competition_id="sr:competition:3899",
        season_id="sr:season:142010",
        canonical_surface="HARD",
        raw_surface="hardcourt_indoor",
        provider_observed_at=datetime(2026, 9, 18, 17, 9, tzinfo=UTC),
        checked_at=datetime(2026, 9, 18, 17, 10, tzinfo=UTC),
    )

    assert (status, edition_id) == ("EXISTING", 77)
    assert not any("UPDATE tournament_editions" in sql for sql, _ in conn.calls)
    assert not any("INSERT INTO data_health_checks" in sql for sql, _ in conn.calls)
    assert "writes=0" in capsys.readouterr().out


def test_surface_fill_refuses_conflicting_known_surface():
    conn = _FakeConn(_rows(surface="CLAY"))

    with pytest.raises(ValueError, match="refuses conflicting existing surface"):
        apply_surface_fill(
            conn,
            match_ids=(130441, 130491),
            competition_id="sr:competition:3899",
            season_id="sr:season:142010",
            canonical_surface="HARD",
            raw_surface="hardcourt_indoor",
            provider_observed_at=datetime(2026, 9, 18, 17, 9, tzinfo=UTC),
            checked_at=datetime(2026, 9, 18, 17, 10, tzinfo=UTC),
        )


def test_surface_fill_requires_one_shared_edition():
    conn = _FakeConn(
        [
            (130441, 77, None, 2026, "Rennes 2026", "Rennes"),
            (130491, 88, None, 2026, "Other", "Rennes"),
        ]
    )

    with pytest.raises(ValueError, match="do not share one tournament edition"):
        apply_surface_fill(
            conn,
            match_ids=(130441, 130491),
            competition_id="sr:competition:3899",
            season_id="sr:season:142010",
            canonical_surface="HARD",
            raw_surface="hardcourt_indoor",
            provider_observed_at=datetime(2026, 9, 18, 17, 9, tzinfo=UTC),
            checked_at=datetime(2026, 9, 18, 17, 10, tzinfo=UTC),
        )


def test_optional_surface_fill_is_disabled_by_default():
    calls = []

    status = run_optional_surface_fill(
        dsn="postgresql://example",
        api_key="secret",
        access_level="trial",
        language="en",
        env={},
        fetch_seasons=lambda *args, **kwargs: calls.append(("seasons", args, kwargs)),
        fetch_info=lambda *args, **kwargs: calls.append(("info", args, kwargs)),
        sleep=lambda seconds: None,
    )

    assert status == 0
    assert calls == []


def test_optional_surface_fill_requires_exact_authorization():
    with pytest.raises(ValueError, match="authorization token mismatch"):
        run_optional_surface_fill(
            dsn="postgresql://example",
            api_key="secret",
            access_level="trial",
            language="en",
            env={
                ENABLED_ENV: "true",
                AUTH_ENV: "wrong",
            },
            sleep=lambda seconds: None,
        )


def test_optional_surface_fill_refuses_provider_surface_mismatch():
    observed = datetime(2026, 9, 18, 17, 9, tzinfo=UTC)

    with pytest.raises(ValueError, match="provider surface mismatch"):
        run_optional_surface_fill(
            dsn="postgresql://example",
            api_key="secret",
            access_level="trial",
            language="en",
            env={
                ENABLED_ENV: "true",
                AUTH_ENV: AUTH_TOKEN,
                MATCH_IDS_ENV: "130441,130491",
                COMPETITION_ID_ENV: "sr:competition:3899",
                SEASON_ID_ENV: "sr:season:142010",
                SURFACE_ENV: "HARD",
            },
            fetch_seasons=lambda *args, **kwargs: (
                observed,
                {"seasons": [{"id": "sr:season:142010"}]},
            ),
            fetch_info=lambda *args, **kwargs: (
                observed,
                {"season": {"info": {"surface": "clay"}}},
            ),
            sleep=lambda seconds: None,
        )


def test_optional_surface_fill_reverifies_provider_then_commits(monkeypatch):
    observed = datetime(2026, 9, 18, 17, 9, tzinfo=UTC)
    conn = _FakeConn(_rows())
    sleeps = []

    monkeypatch.setattr(module.psycopg, "connect", lambda *args, **kwargs: conn)

    status = run_optional_surface_fill(
        dsn="postgresql://example",
        api_key="secret",
        access_level="trial",
        language="en",
        env={
            ENABLED_ENV: "true",
            AUTH_ENV: AUTH_TOKEN,
            MATCH_IDS_ENV: "130491,130441",
            COMPETITION_ID_ENV: "sr:competition:3899",
            SEASON_ID_ENV: "sr:season:142010",
            SURFACE_ENV: "HARD",
        },
        fetch_seasons=lambda api_key, competition_id, **kwargs: (
            observed,
            {"seasons": [{"id": "sr:season:142010"}]},
        ),
        fetch_info=lambda api_key, season_id, **kwargs: (
            observed,
            {"season": {"info": {"surface": "hardcourt_indoor"}}},
        ),
        sleep=sleeps.append,
    )

    assert status == 0
    assert sleeps == [2.0]
    assert conn.commits == 1
    assert any("UPDATE tournament_editions" in sql for sql, _ in conn.calls)
    assert any("INSERT INTO data_health_checks" in sql for sql, _ in conn.calls)
