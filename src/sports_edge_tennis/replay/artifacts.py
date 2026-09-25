"""Immutable replay artifact storage.

Replay output is written to a versioned directory and is never silently
overwritten. Each artifact directory contains the manifest plus the four ledger
files and a SHA-256 checksum file.

No replay artifact is ever written into a production decision table.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from sports_edge_tennis.replay.contracts import ReplayResult
from sports_edge_tennis.replay.manifest import (
    ReplayManifest,
    manifest_canonical_json,
    reproducibility_sha256,
)
from sports_edge_tennis.replay.serde import sha256_hex, to_jsonable


class ReplayArtifactExistsError(FileExistsError):
    """Raised when a replay artifact would overwrite an existing execution."""


@dataclass(frozen=True)
class ReplayArtifactPaths:
    root: Path
    manifest_path: Path
    model_picks_path: Path
    value_bets_path: Path
    exclusions_path: Path
    opportunities_path: Path
    checksums_path: Path
    reproducibility_sha256: str


def _write_json(path: Path, payload) -> None:
    path.write_text(
        json.dumps(to_jsonable(payload), indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def artifact_directory_name(replay_id: str, manifest: ReplayManifest) -> str:
    return f"{replay_id}__{reproducibility_sha256(manifest)[:16]}"


def write_replay_artifact(
    root: Path,
    *,
    result: ReplayResult,
    manifest: ReplayManifest,
    directory_name: str | None = None,
) -> ReplayArtifactPaths:
    """Write one immutable replay artifact directory.

    ``root`` is the artifacts base (for example ``artifacts/replay``). The final
    directory is ``<root>/<replay_id>__<hash>`` unless an explicit name is given.
    """

    root = Path(root)
    name = directory_name or artifact_directory_name(manifest.replay_id, manifest)
    destination = root / name

    root.mkdir(parents=True, exist_ok=True)
    try:
        destination.mkdir(exist_ok=False)
    except FileExistsError as exc:
        raise ReplayArtifactExistsError(
            f"replay artifact already exists and will not be overwritten: {destination}"
        ) from exc

    manifest_payload = dict(to_jsonable(manifest))
    manifest_payload["reproducibility_sha256"] = reproducibility_sha256(manifest)
    manifest_payload["manifest_canonical_sha256"] = sha256_hex(manifest_canonical_json(manifest))

    files = {
        "manifest.json": manifest_payload,
        "model_picks.json": result.model_picks,
        "value_bets.json": result.value_bets,
        "exclusions.json": result.exclusions,
        "opportunities.json": result.opportunities,
    }

    try:
        checksum_lines: list[str] = []
        for filename, payload in files.items():
            path = destination / filename
            _write_json(path, payload)
            checksum_lines.append(f"{sha256_hex(path.read_text(encoding='utf-8'))}  {filename}")
        checksums_path = destination / "SHA256SUMS"
        checksums_path.write_text("\n".join(checksum_lines) + "\n", encoding="utf-8")
    except Exception:
        # Leave no half-written artifact that could be mistaken for a complete run.
        for child in destination.iterdir():
            os.remove(child)
        destination.rmdir()
        raise

    return ReplayArtifactPaths(
        root=destination,
        manifest_path=destination / "manifest.json",
        model_picks_path=destination / "model_picks.json",
        value_bets_path=destination / "value_bets.json",
        exclusions_path=destination / "exclusions.json",
        opportunities_path=destination / "opportunities.json",
        checksums_path=destination / "SHA256SUMS",
        reproducibility_sha256=reproducibility_sha256(manifest),
    )


__all__ = [
    "ReplayArtifactExistsError",
    "ReplayArtifactPaths",
    "artifact_directory_name",
    "write_replay_artifact",
]
