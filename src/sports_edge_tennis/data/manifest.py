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
    canonical_source: str | None = None,
    retrieval_source: str | None = None,
    archive_revision: str | None = None,
    upstream_revision: str | None = None,
    source_license: str | None = None,
    coverage_metadata: dict[str, Any] | None = None,
) -> SourceDocument:
    stat = path.stat()
    retrieved_at = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
    return SourceDocument(
        source_system=source_system,
        source_name=source_name,
        source_url=source_url,
        canonical_source=canonical_source or source_name,
        retrieval_source=retrieval_source or canonical_source or source_name,
        archive_revision=archive_revision or source_revision,
        upstream_revision=upstream_revision,
        source_license=source_license,
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


def _manifest_document(doc: SourceDocument) -> dict[str, Any]:
    return {
        "archive_revision": doc.archive_revision,
        "canonical_source": doc.canonical_source,
        "coverage_metadata": doc.coverage_metadata,
        "dataset_type": doc.dataset_type,
        "file_sha256": doc.sha256,
        "filename": doc.filename,
        "hash_recipe_version": doc.hash_recipe_version,
        "retrieval_source": doc.retrieval_source,
        "retrieved_at": doc.retrieved_at.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        "season": doc.season,
        "size_bytes": doc.size_bytes,
        "source_license": doc.source_license,
        "source_name": doc.source_name,
        "source_system": doc.source_system,
        "source_url": doc.source_url,
        "source_revision": doc.source_revision,
        "upstream_revision": doc.upstream_revision,
    }


def write_manifest(
    path: Path,
    documents: list[SourceDocument],
    *,
    generated_at: datetime | None = None,
) -> None:
    serialized_sources = sorted(
        (_manifest_document(doc) for doc in documents),
        key=lambda doc: (
            doc["canonical_source"] or "",
            doc["retrieval_source"] or "",
            doc["filename"],
            doc["dataset_type"],
            doc["season"] or -1,
        ),
    )
    payload = {
        "hash_recipe_version": "SE_SOURCE_HASH_V1",
        "generated_at": (generated_at or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        "sources": serialized_sources,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
