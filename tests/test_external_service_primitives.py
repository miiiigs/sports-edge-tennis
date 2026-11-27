"""External-service operational primitives: clock skew and durable backup."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from sports_edge_tennis.ops.clock_health import (
    CLOCK_OK,
    CLOCK_SKEW_EXCEEDED,
    CLOCK_UNVERIFIED,
    assess_clock,
    read_http_date,
    verify_clock,
)
from sports_edge_tennis.ops.durable_store import (
    append_receipt,
    backup_store,
    create_store,
    restore_store,
    verify_store,
)

NOW = datetime(2026, 11, 27, 12, 0, 0, tzinfo=UTC)


def _http_date(moment: datetime) -> str:
    return moment.strftime("%a, %d %b %Y %H:%M:%S GMT")


def test_clock_within_threshold_is_ok():
    reading = read_http_date(
        source="hc-ping",
        headers={"Date": _http_date(NOW - timedelta(seconds=20))},
        local_now=NOW,
    )
    assert reading.skew_seconds == 20
    assessment = assess_clock([reading], max_skew_seconds=60)
    assert assessment.status == CLOCK_OK
    assert assessment.max_abs_skew_seconds == 20


def test_clock_skew_over_threshold_is_rejected():
    reading = read_http_date(
        source="hc-ping",
        headers={"Date": _http_date(NOW + timedelta(minutes=5))},
        local_now=NOW,
    )
    assessment = assess_clock([reading], max_skew_seconds=60)
    assert assessment.status == CLOCK_SKEW_EXCEEDED


def test_missing_clock_source_is_unverified_not_ok():
    assessment = assess_clock([])
    assert assessment.status == CLOCK_UNVERIFIED


def test_missing_date_header_fails_closed():
    with pytest.raises(ValueError, match="Date header"):
        read_http_date(source="x", headers={}, local_now=NOW)


def test_multiple_sources_use_the_worst_skew():
    assessment = verify_clock(
        sources={
            "a": lambda: {"Date": _http_date(NOW)},
            "b": lambda: {"Date": _http_date(NOW - timedelta(seconds=90))},
        },
        local_now=NOW,
        max_skew_seconds=60,
    )
    assert assessment.status == CLOCK_SKEW_EXCEEDED
    assert len(assessment.readings) == 2


def test_durable_store_is_append_only(tmp_path):
    path = create_store(tmp_path / "receipts.db")
    append_receipt(
        path,
        receipt_id="r1",
        slot=NOW.isoformat(),
        payload=b"payload",
        received_at=NOW.isoformat(),
    )
    import sqlite3

    with sqlite3.connect(path) as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE receipts SET slot='tampered' WHERE receipt_id='r1'")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("DELETE FROM receipts WHERE receipt_id='r1'")


def test_backup_and_restore_preserve_the_inventory(tmp_path):
    source = create_store(tmp_path / "source.db")
    for index in range(3):
        append_receipt(
            source,
            receipt_id=f"r{index}",
            slot=NOW.isoformat(),
            payload=f"payload-{index}".encode(),
            received_at=NOW.isoformat(),
        )
    manifest = verify_store(source)
    backup = tmp_path / "backup.db"
    assert backup_store(source, backup) == manifest
    assert verify_store(backup) == manifest

    restored = tmp_path / "restored.db"
    assert restore_store(backup, restored, expected_manifest=manifest) == manifest
    assert verify_store(restored) == manifest


def test_backup_does_not_overwrite_or_leave_a_partial_file(tmp_path):
    source = create_store(tmp_path / "source.db")
    append_receipt(
        source, receipt_id="r1", slot=NOW.isoformat(), payload=b"x",
        received_at=NOW.isoformat(),
    )
    destination = tmp_path / "existing.db"
    destination.write_bytes(b"do not overwrite")
    with pytest.raises(FileExistsError):
        backup_store(source, destination)
    assert destination.read_bytes() == b"do not overwrite"


def test_manifest_mismatch_fails_closed(tmp_path):
    source = create_store(tmp_path / "source.db")
    append_receipt(
        source, receipt_id="r1", slot=NOW.isoformat(), payload=b"x",
        received_at=NOW.isoformat(),
    )
    manifest = verify_store(source)
    manifest["count"] = 99
    with pytest.raises(ValueError, match="manifest mismatch"):
        backup_store(source, tmp_path / "backup.db", expected_manifest=manifest)
    assert not (tmp_path / "backup.db").exists()
