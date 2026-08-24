import hashlib
import json
import re
from pathlib import Path

import sports_edge_tennis

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"

FORBIDDEN_IMPORT_PATTERNS = [re.compile(r"^\s*(from|import)\s+sports_ai\b", re.MULTILINE)]
FORBIDDEN_DB_REFS = [
    "football_dashboard.sqlite",
    "football_paper_trading.sqlite",
]
FORBIDDEN_PATH_FRAGMENTS = [
    "c:/dev/sports-edge-ai",
    "../sports-edge-ai",
    "..//sports-edge-ai",
]
LEGACY_FILES = [
    SRC_DIR / "sports_edge_tennis" / "elo.py",
    SRC_DIR / "sports_edge_tennis" / "surface_elo.py",
    SRC_DIR / "sports_edge_tennis" / "serve_return.py",
    SRC_DIR / "sports_edge_tennis" / "markov.py",
    SRC_DIR / "sports_edge_tennis" / "common_opponent.py",
    SRC_DIR / "sports_edge_tennis" / "intransitivity.py",
    SRC_DIR / "sports_edge_tennis" / "manager.py",
    SRC_DIR / "sports_edge_tennis" / "veto.py",
    SRC_DIR / "sports_edge_tennis" / "price_scanner.py",
]
LEGACY_IDENTIFIERS = [
    "EloBook",
    "PlayerStrengthAgent",
    "TennisSimulationAgent",
    "simulate_match",
]


def _python_sources():
    return sorted(SRC_DIR.rglob("*.py"))


def _normalized_runtime_text(text: str) -> str:
    return text.lower().replace("\\", "/")


def _artifact_digest(path: Path) -> str:
    data = path.read_bytes()
    if path.suffix.lower() in {".md", ".json", ".txt"}:
        data = data.replace(b"\r\n", b"\n")
    return hashlib.sha256(data).hexdigest()


def test_import_package():
    assert sports_edge_tennis.__version__ == "0.1.0"


def test_no_football_runtime_imports():
    for path in _python_sources():
        text = path.read_text(encoding="utf-8")
        for pattern in FORBIDDEN_IMPORT_PATTERNS:
            assert not pattern.search(text), f"{path} imports football runtime code"


def test_no_football_db_references():
    for path in _python_sources():
        text = path.read_text(encoding="utf-8")
        for forbidden in FORBIDDEN_DB_REFS:
            assert forbidden not in text, f"{path} references {forbidden}"


def test_no_absolute_football_runtime_paths():
    for path in _python_sources():
        text = _normalized_runtime_text(path.read_text(encoding="utf-8"))
        for forbidden in FORBIDDEN_PATH_FRAGMENTS:
            assert forbidden not in text, f"{path} references football runtime path {forbidden}"


def test_manifest_artifacts_exist():
    manifest = json.loads(
        (REPO_ROOT / "specs" / "SOURCE_MANIFEST.json").read_text(encoding="utf-8")
    )
    for artifact in manifest["artifacts"]:
        dest = REPO_ROOT / artifact["destination_path"]
        assert dest.exists(), f"missing artifact {artifact['destination_path']}"


def test_manifest_checksums_match_files():
    manifest = json.loads(
        (REPO_ROOT / "specs" / "SOURCE_MANIFEST.json").read_text(encoding="utf-8")
    )
    for artifact in manifest["artifacts"]:
        dest = REPO_ROOT / artifact["destination_path"]
        actual_sha256 = _artifact_digest(dest)
        assert artifact.get("sha256"), f"missing sha256 for {artifact['destination_path']}"
        assert artifact.get("git_blob_sha"), f"missing git_blob_sha for {artifact['destination_path']}"
        assert artifact["sha256"] == actual_sha256, f"sha256 mismatch for {artifact['destination_path']}"


def test_no_legacy_scaffold_in_src():
    for path in LEGACY_FILES:
        assert not path.exists(), f"legacy scaffold must not exist: {path}"


def test_no_legacy_identifiers_in_src():
    for path in _python_sources():
        text = path.read_text(encoding="utf-8")
        for identifier in LEGACY_IDENTIFIERS:
            assert identifier not in text, f"{path} contains legacy identifier {identifier}"
