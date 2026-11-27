import sqlite3

import pytest

from sports_edge_tennis.validation.forward_archive_backup import backup_archive, verify_backup
from sports_edge_tennis.validation.forward_original_collector import OriginalReceiptStore
from tests.test_forward_archive_http import envelope
from sports_edge_tennis.validation.forward_evidence_outbox import digest


def test_online_backup_restore_preserves_exact_receipts(tmp_path):
    source, backup, restored = (tmp_path / p for p in ("source.sqlite", "backup.sqlite", "restored.sqlite"))
    store = OriginalReceiptStore(source)
    _, payload = envelope()
    first = store.receive(payload, digest(payload))
    manifest = backup_archive(source, backup)
    assert manifest["count"] == 1
    assert manifest == verify_backup(backup)
    assert not manifest["independent_clock_verified"]
    _, another = envelope(match=43)
    store.receive(another, digest(another))
    assert store.verify_inventory()["count"] == 2
    assert backup_archive(backup, restored, expected_manifest=manifest) == manifest
    retry = OriginalReceiptStore(restored).receive(payload, digest(payload))
    assert retry == {**first, "duplicate_delivery": True}
    with sqlite3.connect(restored) as db:
        assert db.execute("SELECT payload FROM original_receipts").fetchone()[0] == payload
    with pytest.raises(FileExistsError):
        backup_archive(source, backup)
    assert verify_backup(backup) == manifest


def test_restore_manifest_mismatch_preserves_source(tmp_path):
    source, target = tmp_path / "source.sqlite", tmp_path / "target.sqlite"
    OriginalReceiptStore(source)
    manifest = verify_backup(source)
    with pytest.raises(ValueError, match="manifest"):
        backup_archive(source, target, expected_manifest={**manifest, "count": 1})
    assert not target.exists()
    assert verify_backup(source) == manifest
