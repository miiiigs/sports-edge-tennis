import json
from pathlib import Path

import sports_edge_tennis

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"

FORBIDDEN_IMPORTS = ["sports_ai"]
FORBIDDEN_DB_REFS = [
    "football_dashboard.sqlite",
    "football_paper_trading.sqlite",
]
FORBIDDEN_ABS_PATHS = ["C:/dev/sports-edge-ai", "C:\\dev\\sports-edge-ai"]
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


def _python_sources():
    return sorted(SRC_DIR.rglob("*.py"))


def test_import_package():
    assert sports_edge_tennis.__version__ == "0.1.0"


def test_no_football_runtime_imports():
    for path in _python_sources():
        text = path.read_text(encoding="utf-8")
        for forbidden in FORBIDDEN_IMPORTS:
            assert forbidden not in text, f"{path} imports {forbidden}"


def test_no_football_db_references():
    for path in _python_sources():
        text = path.read_text(encoding="utf-8")
        for forbidden in FORBIDDEN_DB_REFS:
            assert forbidden not in text, f"{path} references {forbidden}"


def test_no_absolute_football_runtime_paths():
    for path in _python_sources():
        text = path.read_text(encoding="utf-8")
        for forbidden in FORBIDDEN_ABS_PATHS:
            assert forbidden not in text, f"{path} references {forbidden}"


def test_manifest_artifacts_exist():
    manifest = json.loads(
        (REPO_ROOT / "specs" / "SOURCE_MANIFEST.json").read_text(encoding="utf-8")
    )
    for artifact in manifest["artifacts"]:
        dest = REPO_ROOT / artifact["destination_path"]
        assert dest.exists(), f"missing artifact {artifact['destination_path']}"


def test_manifest_checksums_populated():
    manifest = json.loads(
        (REPO_ROOT / "specs" / "SOURCE_MANIFEST.json").read_text(encoding="utf-8")
    )
    for artifact in manifest["artifacts"]:
        assert artifact.get("sha256"), f"missing sha256 for {artifact['destination_path']}"
        assert artifact.get("git_blob_sha"), f"missing git_blob_sha for {artifact['destination_path']}"


def test_no_legacy_scaffold_in_src():
    for path in LEGACY_FILES:
        assert not path.exists(), f"legacy scaffold must not exist: {path}"
