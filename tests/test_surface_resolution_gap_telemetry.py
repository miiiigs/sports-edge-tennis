from __future__ import annotations

from sports_edge_tennis.runner.targets import (
    _api_tennis_tournament_source_ids,
    _safe_log_text,
)


class _Cursor:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _Conn:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def execute(self, sql, params=()):
        self.calls.append((str(sql), params))
        return _Cursor(self.rows)


def test_api_tennis_tournament_source_ids_are_deterministic_and_deduplicated():
    conn = _Conn([("14290",), ("14290",), ("14291",), (None,)])

    result = _api_tennis_tournament_source_ids(conn, tournament_id=77)

    assert result == ("14290", "14291")
    assert conn.calls[0][1] == (77,)
    assert "source_system = 'API_TENNIS'" in conn.calls[0][0]
    assert "ORDER BY source_tournament_id" in conn.calls[0][0]


def test_safe_log_text_removes_newlines_and_bounds_length():
    value = "  Example\nTournament\rName  " + ("x" * 200)

    rendered = _safe_log_text(value)

    assert "\n" not in rendered
    assert "\r" not in rendered
    assert rendered.startswith("Example Tournament Name")
    assert len(rendered) == 120
