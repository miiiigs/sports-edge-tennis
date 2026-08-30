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
    retrieved_at: datetime | None = None,
    manifest_status: str = "ACQUIRED",
) -> SourceDocument:
    stat = path.stat()
    effective_retrieved_at = retrieved_at or datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
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
        retrieved_at=effective_retrieved_at,
        filename=path.name,
        sha256=sha256_path(path),
        size_bytes=stat.st_size,
        dataset_type=dataset_type,
        season=season,
        source_revision=source_revision,
        coverage_metadata=coverage_metadata or {},
        manifest_status=manifest_status,
    )


def _manifest_document(doc: SourceDocument) -> dict[str, Any]:
    return {
        "acquired_at": doc.retrieved_at.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        "archive_revision": doc.archive_revision,
        "canonical_source": doc.canonical_source,
        "canonical_lineage": doc.canonical_source,
        "coverage_metadata": doc.coverage_metadata,
        "dataset_type": doc.dataset_type,
        "file_sha256": doc.sha256,
        "filename": doc.filename,
        "hash_recipe_version": doc.hash_recipe_version,
        "license_identifier": doc.source_license,
        "provider": doc.source_system,
        "retrieval_source": doc.retrieval_source,
        "retrieved_at": doc.retrieved_at.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        "season": doc.season,
        "size_bytes": doc.size_bytes,
        "source_location": doc.source_url,
        "source_license": doc.source_license,
        "source_name": doc.source_name,
        "source_system": doc.source_system,
        "source_url": doc.source_url,
        "source_revision": doc.source_revision,
        "status": doc.manifest_status,
        "upstream_revision": doc.upstream_revision,
    }


def _manifest_identity(document: dict[str, Any]) -> tuple[object, ...]:
    return (
        document.get("provider") or document.get("source_system"),
        document.get("source_name"),
        document.get("canonical_lineage") or document.get("canonical_source"),
        document.get("retrieval_source"),
        document.get("dataset_type"),
        document.get("season"),
        document.get("filename"),
        document.get("file_sha256"),
        document.get("size_bytes"),
        document.get("source_location") or document.get("source_url"),
        document.get("source_revision"),
        document.get("archive_revision"),
        document.get("upstream_revision"),
        document.get("license_identifier") or document.get("source_license"),
        document.get("acquired_at") or document.get("retrieved_at"),
        document.get("status", "ACQUIRED"),
    )


def _manifest_sort_key(document: dict[str, Any]) -> tuple[object, ...]:
    return (
        document.get("canonical_lineage") or document.get("canonical_source") or "",
        document.get("retrieval_source") or "",
        document.get("dataset_type") or "",
        document.get("season") or -1,
        document.get("filename") or "",
        document.get("source_revision") or "",
        document.get("file_sha256") or "",
        document.get("acquired_at") or document.get("retrieved_at") or "",
        document.get("status", "ACQUIRED"),
    )


def _load_existing_manifest_sources(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"invalid source manifest payload at {path}")
    sources = payload.get("sources", [])
    if not isinstance(sources, list):
        raise ValueError(f"invalid source manifest sources at {path}")
    return [dict(entry) for entry in sources]


def write_manifest(
    path: Path,
    documents: list[SourceDocument],
    *,
    generated_at: datetime | None = None,
) -> None:
    existing_sources = _load_existing_manifest_sources(path)
    by_identity = {_manifest_identity(entry): entry for entry in existing_sources}
    for document in documents:
        serialized = _manifest_document(document)
        by_identity[_manifest_identity(serialized)] = serialized
    serialized_sources = sorted(by_identity.values(), key=_manifest_sort_key)
    payload = {
        "hash_recipe_version": "SE_SOURCE_HASH_V1",
        "generated_at": (generated_at or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        "sources": serialized_sources,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
