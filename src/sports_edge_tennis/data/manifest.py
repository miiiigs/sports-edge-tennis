from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .contracts import SourceDocument


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(65536):
            digest.update(chunk)
    return digest.hexdigest()


def build_source_document(
    *,
    path: Path,
    source_system: str,
    source_name: str,
    dataset_type: str,
    season: int | None,
    source_url: str | None = None,
    source_revision: str | None = None,
    coverage_metadata: dict[str, Any] | None = None,
) -> SourceDocument:
    stat = path.stat()
    retrieved_at = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
    return SourceDocument(
        source_system=source_system,
        source_name=source_name,
        source_url=source_url,
        local_path=path,
        retrieved_at=retrieved_at,
        filename=path.name,
        sha256=sha256_path(path),
        size_bytes=stat.st_size,
        dataset_type=dataset_type,
        season=season,
        source_revision=source_revision,
        coverage_metadata=coverage_metadata or {},
    )


def write_manifest(path: Path, documents: list[SourceDocument]) -> None:
    payload = {
        "hash_recipe_version": "SE_SOURCE_HASH_V1",
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        "sources": [
            {
                "source_system": doc.source_system,
                "source_name": doc.source_name,
                "source_url": doc.source_url,
                "retrieved_at": doc.retrieved_at.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
                "source_revision": doc.source_revision,
                "filename": doc.filename,
                "sha256": doc.sha256,
                "size_bytes": doc.size_bytes,
                "dataset_type": doc.dataset_type,
                "season": doc.season,
                "coverage_metadata": doc.coverage_metadata,
                "hash_recipe_version": doc.hash_recipe_version,
            }
            for doc in documents
        ],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
