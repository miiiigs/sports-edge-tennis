from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import urllib.error
import urllib.request
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg
from psycopg.conninfo import conninfo_to_dict

from .agent1_history import load_agent1_history
from .contracts import SourceDocument
from .manifest import build_source_document, write_manifest
from .pipeline import ingest_sackmann_document
from .sources.sackmann import parse_sackmann_csv


APPROVED_PROVIDER = "SACKMANN"
APPROVED_SOURCE_NAME = "Jeff Sackmann tennis_atp historical matches"
APPROVED_CANONICAL_LINEAGE = "Jeff Sackmann / Tennis Abstract ATP historical datasets"
APPROVED_RETRIEVAL_SOURCE = "Aneeshers/tennis-sackmann-archive"
APPROVED_LICENSE = "CC BY-NC-SA 4.0"
APPROVED_USAGE_CLASSIFICATION = "LOCAL_RESEARCH_VALIDATION_ONLY"
APPROVED_RETRIEVAL_API_ROOT = "https://api.github.com/repos/Aneeshers/tennis-sackmann-archive/contents"
APPROVED_RETRIEVAL_TREE_ROOT = "https://api.github.com/repos/Aneeshers/tennis-sackmann-archive/git/trees"
APPROVED_RETRIEVAL_RAW_ROOT = "https://raw.githubusercontent.com/Aneeshers/tennis-sackmann-archive"
_APPROVED_REJECTION_REASONS = frozenset(
    {
        "ambiguous_advancement_marker",
        "missing_score_or_status",
        "out_of_scope_tourney_level:D",
        "out_of_scope_tourney_level:S",
        "self_match_source_player_id",
    }
)
_DB_TABLES = {
    "identity_events": "identity_resolution_events",
    "ingest_runs": "ingest_runs",
    "match_versions": "match_versions",
    "matches": "matches",
    "players": "players",
    "results": "match_result_observations",
    "stats": "match_stat_observations",
    "tournaments": "tournaments",
}
_SMOKE_CUTOFF = datetime(2025, 12, 31, 23, 59, 59, tzinfo=UTC)
_REQUEST_TIMEOUT_SECONDS = 60
_USER_AGENT = "sports-edge-tennis-historical-validation/1.0"
_REPO_ROOT = Path(__file__).resolve().parents[3]
_MIGRATION_MANIFEST_PATH = _REPO_ROOT / "db" / "migrations" / "manifest.json"
_ALLOWED_RESET_DB_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
_ALLOWED_RESET_DB_PREFIXES = ("sports_edge_tennis_history_", "sports_edge_tennis_test_")
_ALLOWED_RESET_DB_NAMES = frozenset({"sports_edge_tennis_history_agent1"})


@dataclass(frozen=True)
class HistoricalDatasetSpec:
    dataset: str
    year: int
    filename: str

    @property
    def dataset_type(self) -> str:
        return "historical_matches"

    def raw_path(self, raw_root: Path) -> Path:
        return raw_root / self.dataset / str(self.year) / self.filename

    @property
    def remote_path(self) -> str:
        return f"atp/{self.filename}"

    def source_url(self, source_revision: str) -> str:
        return f"{APPROVED_RETRIEVAL_RAW_ROOT}/{source_revision}/{self.remote_path}"

    def verification_url(self, source_revision: str) -> str:
        return f"{APPROVED_RETRIEVAL_API_ROOT}/{self.remote_path}?ref={source_revision}"


@dataclass(frozen=True)
class DisposableHistoryDatabaseTarget:
    host: str
    dbname: str


def validate_disposable_history_database_target(dsn: str) -> DisposableHistoryDatabaseTarget:
    try:
        params = conninfo_to_dict(dsn)
    except psycopg.ProgrammingError as exc:
        raise ValueError("invalid_postgresql_dsn") from exc
    hosts = [str(host).strip() for host in str(params.get("host", "")).split(",") if str(host).strip()]
    if len(hosts) != 1 or hosts[0] not in _ALLOWED_RESET_DB_HOSTS:
        raise ValueError("reset_requires_local_postgresql_host")
    dbname = str(params.get("dbname", "")).strip()
    if not dbname:
        raise ValueError("reset_requires_explicit_dbname")
    if dbname not in _ALLOWED_RESET_DB_NAMES and not any(
        dbname.startswith(prefix) for prefix in _ALLOWED_RESET_DB_PREFIXES
    ):
        raise ValueError("reset_requires_disposable_history_database_name")
    return DisposableHistoryDatabaseTarget(host=hosts[0], dbname=dbname)


def build_dataset_specs(years: range | tuple[int, ...] | list[int]) -> list[HistoricalDatasetSpec]:
    normalized_years = sorted(set(years))
    specs: list[HistoricalDatasetSpec] = []
    for year in normalized_years:
        specs.append(
            HistoricalDatasetSpec(
                dataset="atp_main",
                year=year,
                filename=f"atp_matches_{year}.csv",
            )
        )
        specs.append(
            HistoricalDatasetSpec(
                dataset="atp_qual_chall",
                year=year,
                filename=f"atp_matches_qual_chall_{year}.csv",
            )
        )
    return specs


def _http_json(url: str) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": _USER_AGENT,
        },
    )
    with urllib.request.urlopen(request, timeout=_REQUEST_TIMEOUT_SECONDS) as response:
        return json.loads(response.read().decode("utf-8"))


def _verify_remote_file(spec: HistoricalDatasetSpec, source_revision: str) -> dict[str, Any]:
    payload = _http_json(spec.verification_url(source_revision))
    if payload.get("type") != "file":
        raise ValueError(f"source entry is not a file for {spec.filename}")
    if payload.get("name") != spec.filename:
        raise ValueError(f"unexpected remote filename for {spec.filename}: {payload.get('name')}")
    return payload


def _remote_tree_paths(source_revision: str) -> set[str]:
    payload = _http_json(f"{APPROVED_RETRIEVAL_TREE_ROOT}/{source_revision}?recursive=1")
    if payload.get("truncated"):
        raise ValueError(f"remote tree truncated for revision {source_revision}")
    return {
        str(entry["path"])
        for entry in payload.get("tree", [])
        if entry.get("type") == "blob" and isinstance(entry.get("path"), str)
    }


def acquire_sackmann_file(
    spec: HistoricalDatasetSpec,
    *,
    raw_root: Path,
    source_revision: str,
    remote_paths: set[str] | None = None,
) -> SourceDocument:
    if remote_paths is None:
        _verify_remote_file(spec, source_revision)
    elif spec.remote_path not in remote_paths:
        raise ValueError(f"remote file missing at pinned revision: {spec.remote_path}")

    raw_path = spec.raw_path(raw_root)
    if raw_path.suffix.lower() != ".csv":
        raise ValueError(f"unexpected raw extension for {raw_path}")
    raw_path.parent.mkdir(parents=True, exist_ok=True)

    if not raw_path.exists():
        temp_path = raw_path.with_suffix(raw_path.suffix + ".partial")
        if temp_path.exists():
            temp_path.unlink()
        request = urllib.request.Request(
            spec.source_url(source_revision),
            headers={"User-Agent": _USER_AGENT},
        )
        with urllib.request.urlopen(request, timeout=_REQUEST_TIMEOUT_SECONDS) as response:
            with temp_path.open("wb") as handle:
                shutil.copyfileobj(response, handle)
        if temp_path.stat().st_size <= 0:
            temp_path.unlink(missing_ok=True)
            raise ValueError(f"downloaded empty file for {spec.filename}")
        temp_path.replace(raw_path)

    if raw_path.stat().st_size <= 0:
        raise ValueError(f"local raw file is empty: {raw_path}")

    return _build_sackmann_document(spec, raw_path=raw_path, source_revision=source_revision)


def _build_sackmann_document(
    spec: HistoricalDatasetSpec,
    *,
    raw_path: Path,
    source_revision: str,
) -> SourceDocument:
    return build_source_document(
        path=raw_path,
        source_system=APPROVED_PROVIDER,
        source_name=APPROVED_SOURCE_NAME,
        dataset_type=spec.dataset_type,
        season=spec.year,
        source_url=spec.source_url(source_revision),
        source_revision=source_revision,
        canonical_source=APPROVED_CANONICAL_LINEAGE,
        retrieval_source=APPROVED_RETRIEVAL_SOURCE,
        archive_revision=source_revision,
        upstream_revision=None,
        source_license=APPROVED_LICENSE,
        coverage_metadata={
            "dataset": spec.dataset,
            "usage_classification": APPROVED_USAGE_CLASSIFICATION,
            "year": spec.year,
        },
        manifest_status="VERIFIED",
    )


def acquire_sackmann_documents(
    *,
    years: range | tuple[int, ...] | list[int],
    raw_root: Path,
    source_revision: str,
) -> list[SourceDocument]:
    remote_paths = _remote_tree_paths(source_revision)
    return [
        acquire_sackmann_file(
            spec,
            raw_root=raw_root,
            source_revision=source_revision,
            remote_paths=remote_paths,
        )
        for spec in build_dataset_specs(years)
    ]


def build_local_sackmann_documents(
    *,
    years: range | tuple[int, ...] | list[int],
    raw_root: Path,
    source_revision: str,
) -> list[SourceDocument]:
    documents: list[SourceDocument] = []
    for spec in build_dataset_specs(years):
        raw_path = spec.raw_path(raw_root)
        if not raw_path.exists():
            raise FileNotFoundError(f"missing local raw file: {raw_path}")
        if raw_path.stat().st_size <= 0:
            raise ValueError(f"local raw file is empty: {raw_path}")
        documents.append(
            _build_sackmann_document(
                spec,
                raw_path=raw_path,
                source_revision=source_revision,
            )
        )
    return documents


def _json_dump(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _document_summary(document: SourceDocument) -> dict[str, object]:
    dataset = document.coverage_metadata.get("dataset")
    return {
        "dataset": dataset,
        "filename": document.filename,
        "path": str(document.local_path),
        "sha256": document.sha256,
        "size_bytes": document.size_bytes,
        "year": document.season,
    }


def run_dry_runs(
    documents: list[SourceDocument],
    *,
    artifact_dir: Path,
) -> list[dict[str, object]]:
    results: list[dict[str, object]] = []
    for document in documents:
        quality_path = artifact_dir / f"{document.filename}.dry_run.quality.json"
        unmatched_path = artifact_dir / f"{document.filename}.dry_run.unmatched.json"
        result = ingest_sackmann_document(
            None,
            document=document,
            dry_run=True,
            quality_path=quality_path,
            unmatched_path=unmatched_path,
        )
        results.append(
            {
                "dataset": document.coverage_metadata.get("dataset"),
                "filename": document.filename,
                "result": result,
                "year": document.season,
            }
        )
    return results


def _counter_total(results: list[dict[str, object]], counter_name: str) -> int:
    total = 0
    for item in results:
        counters = item["result"]["counters"]
        value = counters.get(counter_name)
        total += 0 if value is None else int(value)
    return total


def _reason_counts(results: list[dict[str, object]]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for item in results:
        for error in item["result"]["errors"]:
            counts[str(error["reason"])] += 1
    return dict(sorted(counts.items()))


def evaluate_dry_run_gate(results: list[dict[str, object]]) -> dict[str, object]:
    unknown_reason_counts = {
        reason: count
        for reason, count in _reason_counts(results).items()
        if reason not in _APPROVED_REJECTION_REASONS
    }
    unsupported_status_counts: Counter[str] = Counter()
    unsupported_surface_rows = 0
    missing_player_id_rows = 0
    missing_tournament_id_rows = 0
    missing_match_num_rows = 0
    unknown_source_columns: dict[str, list[str]] = {}
    for item in results:
        details = item["result"]["details"]
        missing_player_id_rows += int(details["missing_player_id_rows"])
        missing_tournament_id_rows += int(details["missing_tournament_id_rows"])
        missing_match_num_rows += int(details["missing_match_num_rows"])
        unsupported_surface_rows += int(details["unknown_surface_rows"])
        columns = [str(column) for column in details["unknown_source_columns"]]
        if columns:
            unknown_source_columns[str(item["filename"])] = columns
        for entry in details["status_vocabulary"]:
            if not entry["accepted"]:
                raw_value = entry["raw_value"] if entry["raw_value"] is not None else "<NULL>"
                unsupported_status_counts[str(raw_value)] += int(entry["row_count"])

    parser_pass = (
        all(item["result"]["status"] == "DRY_RUN" for item in results)
        and missing_player_id_rows == 0
        and missing_tournament_id_rows == 0
        and missing_match_num_rows == 0
        and unsupported_surface_rows == 0
        and not unknown_reason_counts
    )
    status_pass = not any(reason.startswith("unsupported_") for reason in unknown_reason_counts)

    return {
        "gate_pass": parser_pass and status_pass,
        "missing_player_id_rows": missing_player_id_rows,
        "missing_tournament_id_rows": missing_tournament_id_rows,
        "missing_match_num_rows": missing_match_num_rows,
        "unknown_reason_counts": unknown_reason_counts,
        "unknown_source_columns": unknown_source_columns,
        "unknown_surface_rows": unsupported_surface_rows,
        "unsupported_status_counts": dict(sorted(unsupported_status_counts.items())),
    }


def write_dry_run_summary(
    documents: list[SourceDocument],
    results: list[dict[str, object]],
    *,
    artifact_dir: Path,
    source_revision: str,
) -> dict[str, object]:
    summary = {
        "downloaded_files": [_document_summary(document) for document in documents],
        "dry_runs": [
            {
                "dataset": item["dataset"],
                "filename": item["filename"],
                "year": item["year"],
                **item["result"],
            }
            for item in results
        ],
        "gate": evaluate_dry_run_gate(results),
        "source_revision": source_revision,
    }
    _json_dump(artifact_dir / "smoke_dry_run_summary.json", summary)
    return summary


def _psql(psql_path: str, dsn: str, sql_path: Path) -> None:
    resolved_psql = psql_path if Path(psql_path).exists() else shutil.which(psql_path)
    if resolved_psql is None:
        with psycopg.connect(dsn, autocommit=True) as conn:
            conn.execute(sql_path.read_text(encoding="utf-8"))
        return
    completed = subprocess.run(
        [resolved_psql, "-d", dsn, "-v", "ON_ERROR_STOP=1", "-f", str(sql_path)],
        capture_output=True,
        check=False,
        text=True,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"psql failed for {sql_path}: {completed.stdout}\n{completed.stderr}".strip()
        )


def _migration_entries() -> list[dict[str, str]]:
    payload = json.loads(_MIGRATION_MANIFEST_PATH.read_text(encoding="utf-8"))
    return [dict(entry) for entry in payload["migrations"]]


def reset_database(
    *,
    dsn: str,
    psql_path: str,
    allow_destructive_reset: bool,
) -> None:
    if not allow_destructive_reset:
        raise ValueError("reset_requires_explicit_authorization")
    validate_disposable_history_database_target(dsn)
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("DROP SCHEMA IF EXISTS public CASCADE")
        conn.execute("CREATE SCHEMA public")
    for entry in _migration_entries():
        _psql(psql_path, dsn, _REPO_ROOT / Path(entry["up"]))


def ingest_documents(
    *,
    dsn: str,
    documents: list[SourceDocument],
    artifact_dir: Path,
) -> list[dict[str, object]]:
    results: list[dict[str, object]] = []
    with psycopg.connect(dsn, autocommit=True) as conn:
        for document in documents:
            quality_path = artifact_dir / f"{document.filename}.ingest.quality.json"
            unmatched_path = artifact_dir / f"{document.filename}.ingest.unmatched.json"
            result = ingest_sackmann_document(
                conn,
                document=document,
                dry_run=False,
                quality_path=quality_path,
                unmatched_path=unmatched_path,
            )
            results.append(
                {
                    "dataset": document.coverage_metadata.get("dataset"),
                    "filename": document.filename,
                    "result": result,
                    "year": document.season,
                }
            )
    return results


def _fetch_scalar(conn: psycopg.Connection, sql: str, params: tuple[object, ...] = ()) -> int:
    return int(conn.execute(sql, params).fetchone()[0])


def _table_counts(conn: psycopg.Connection) -> dict[str, int]:
    return {
        label: _fetch_scalar(conn, f"SELECT count(*) FROM {table_name}")
        for label, table_name in sorted(_DB_TABLES.items())
    }


def _year_counts(conn: psycopg.Connection) -> dict[str, int]:
    rows = conn.execute(
        """
        SELECT te.season_year, count(*)
        FROM match_result_observations mro
        JOIN matches m ON m.match_id = mro.match_id
        JOIN tournament_editions te ON te.tournament_edition_id = m.tournament_edition_id
        GROUP BY te.season_year
        ORDER BY te.season_year
        """
    ).fetchall()
    return {str(row[0]): int(row[1]) for row in rows}


def _identity_checks(conn: psycopg.Connection) -> dict[str, int]:
    return {
        "ambiguous_identity_events": _fetch_scalar(
            conn,
            "SELECT count(*) FROM identity_resolution_events WHERE resolution_status = 'AMBIGUOUS'",
        ),
        "duplicate_player_source_ids": _fetch_scalar(
            conn,
            """
            SELECT count(*)
            FROM (
                SELECT source_system, source_player_id
                FROM player_source_ids
                GROUP BY source_system, source_player_id
                HAVING count(DISTINCT player_id) > 1
            ) collisions
            """,
        ),
        "self_matches": _fetch_scalar(
            conn,
            "SELECT count(*) FROM match_versions WHERE player_1_id = player_2_id",
        ),
        "unmatched_identity_events": _fetch_scalar(
            conn,
            "SELECT count(*) FROM identity_resolution_events WHERE resolution_status = 'UNMATCHED'",
        ),
    }


def _match_integrity_checks(conn: psycopg.Connection) -> dict[str, int]:
    return {
        "duplicate_match_source_hashes": _fetch_scalar(
            conn,
            """
            SELECT count(*)
            FROM (
                SELECT source_system, source_record_hash
                FROM match_source_ids
                WHERE source_record_hash IS NOT NULL
                GROUP BY source_system, source_record_hash
                HAVING count(DISTINCT match_id) > 1
            ) duplicates
            """,
        ),
        "duplicate_match_source_ids": _fetch_scalar(
            conn,
            """
            SELECT count(*)
            FROM (
                SELECT source_system, source_match_id
                FROM match_source_ids
                WHERE source_match_id IS NOT NULL
                GROUP BY source_system, source_match_id
                HAVING count(DISTINCT match_id) > 1
            ) duplicates
            """,
        ),
        "orphan_results": _fetch_scalar(
            conn,
            """
            SELECT count(*)
            FROM match_result_observations mro
            LEFT JOIN matches m ON m.match_id = mro.match_id
            WHERE m.match_id IS NULL
            """,
        ),
        "orphan_stats": _fetch_scalar(
            conn,
            """
            SELECT count(*)
            FROM match_stat_observations mso
            LEFT JOIN matches m ON m.match_id = mso.match_id
            WHERE m.match_id IS NULL
            """,
        ),
        "results_without_eligible_version": _fetch_scalar(
            conn,
            """
            SELECT count(*)
            FROM match_result_observations mro
            WHERE NOT EXISTS (
                SELECT 1
                FROM match_versions mv
                WHERE mv.match_id = mro.match_id
                  AND mv.source_observed_at <= mro.as_of
                  AND mv.as_of <= mro.as_of
            )
            """,
        ),
        "winner_side_invalid": _fetch_scalar(
            conn,
            """
            SELECT count(*)
            FROM match_result_observations
            WHERE outcome_status IN ('COMPLETED', 'RETIRED', 'WALKOVER')
              AND winner_side NOT IN (1, 2)
            """,
        ),
    }


def validate_sackmann_boundaries(
    conn: psycopg.Connection,
    documents: list[SourceDocument],
) -> dict[str, int]:
    expected_version_boundaries: dict[str, tuple[datetime, datetime]] = {}
    expected_result_boundaries: dict[str, tuple[datetime, datetime]] = {}
    explicit_match_date_rows = 0
    conservative_tournament_boundary_rows = 0
    for document in documents:
        outcome = parse_sackmann_csv(document)
        for record in outcome.records:
            expected_version_boundaries[record.source_record_hash] = (
                record.match_source_observed_at,
                record.match_as_of,
            )
            expected_result_boundaries[record.source_record_hash] = (
                record.result_source_observed_at,
                record.result_as_of,
            )
            if record.match_source_observed_at == record.result_source_observed_at:
                conservative_tournament_boundary_rows += 1
            else:
                explicit_match_date_rows += 1

    version_rows = conn.execute(
        """
        SELECT msi.source_record_hash, mv.source_observed_at, mv.as_of
        FROM match_source_ids msi
        JOIN match_versions mv ON mv.match_version_id = msi.match_version_id
        WHERE msi.source_system = 'SACKMANN'
          AND msi.source_record_hash IS NOT NULL
        """
    ).fetchall()
    result_rows = conn.execute(
        """
        SELECT msi.source_record_hash, mro.source_observed_at, mro.as_of
        FROM match_source_ids msi
        JOIN match_result_observations mro ON mro.match_id = msi.match_id
        WHERE msi.source_system = 'SACKMANN'
          AND msi.source_record_hash IS NOT NULL
        """
    ).fetchall()

    persisted_version_boundaries: dict[str, set[tuple[datetime, datetime]]] = {}
    persisted_result_boundaries: dict[str, set[tuple[datetime, datetime]]] = {}
    for record_hash, source_observed_at, as_of in version_rows:
        persisted_version_boundaries.setdefault(str(record_hash), set()).add((source_observed_at, as_of))
    for record_hash, source_observed_at, as_of in result_rows:
        persisted_result_boundaries.setdefault(str(record_hash), set()).add((source_observed_at, as_of))

    version_boundary_mismatches = 0
    result_boundary_mismatches = 0
    for record_hash, expected in expected_version_boundaries.items():
        if expected not in persisted_version_boundaries.get(record_hash, set()):
            version_boundary_mismatches += 1
    for record_hash, expected in expected_result_boundaries.items():
        if expected not in persisted_result_boundaries.get(record_hash, set()):
            result_boundary_mismatches += 1

    return {
        "conservative_tournament_boundary_rows": conservative_tournament_boundary_rows,
        "explicit_match_date_rows": explicit_match_date_rows,
        "expected_records": len(expected_version_boundaries),
        "result_boundary_mismatches": result_boundary_mismatches,
        "version_boundary_mismatches": version_boundary_mismatches,
    }


def _extractor_probe(conn: psycopg.Connection, *, cutoff: datetime) -> dict[str, object]:
    observations = load_agent1_history(conn, cutoff=cutoff)
    surface_counts = Counter((item.surface or "UNKNOWN") for item in observations)
    tour_scope_counts = Counter(item.tour_scope for item in observations)
    return {
        "cutoff": cutoff.isoformat(),
        "first_as_of": observations[0].as_of.isoformat() if observations else None,
        "future_excluded_count": sum(
            1
            for item in observations
            if item.source_observed_at > cutoff or item.as_of > cutoff
        ),
        "last_as_of": observations[-1].as_of.isoformat() if observations else None,
        "observation_count": len(observations),
        "surface_counts": dict(sorted(surface_counts.items())),
        "tour_scope_counts": dict(sorted(tour_scope_counts.items())),
    }


def _distribution_summary(conn: psycopg.Connection, *, cutoff: datetime) -> dict[str, dict[str, int]]:
    observations = load_agent1_history(conn, cutoff=cutoff)
    return {
        "status_distribution": dict(
            sorted(Counter(item.outcome_status for item in observations).items())
        ),
        "surface_distribution": dict(
            sorted(Counter((item.surface or "UNKNOWN") for item in observations).items())
        ),
        "tier_distribution": dict(
            sorted(Counter(item.competition_tier for item in observations).items())
        ),
        "tour_distribution": dict(
            sorted(Counter(item.tour_scope for item in observations).items())
        ),
    }


def _qualifying_count(conn: psycopg.Connection, *, cutoff: datetime) -> int:
    observations = load_agent1_history(conn, cutoff=cutoff)
    return sum(1 for item in observations if item.qualifying_flag)


def _stat_coverage(conn: psycopg.Connection) -> list[dict[str, object]]:
    rows = conn.execute(
        """
        WITH stat_context AS (
            SELECT
                te.season_year,
                mv.tour_scope::text AS tour_scope,
                mso.*
            FROM match_stat_observations mso
            JOIN matches m ON m.match_id = mso.match_id
            JOIN tournament_editions te ON te.tournament_edition_id = m.tournament_edition_id
            JOIN LATERAL (
                SELECT mv.*
                FROM match_versions mv
                WHERE mv.match_id = mso.match_id
                  AND mv.source_observed_at <= mso.as_of
                  AND mv.as_of <= mso.as_of
                ORDER BY mv.as_of DESC, mv.source_observed_at DESC, mv.match_version_id DESC
                LIMIT 1
            ) mv ON TRUE
        )
        SELECT
            season_year,
            tour_scope,
            count(*) AS stat_rows,
            count(*) FILTER (WHERE aces IS NOT NULL) AS aces_non_null,
            count(*) FILTER (WHERE double_faults IS NOT NULL) AS double_faults_non_null,
            count(*) FILTER (WHERE service_points_played IS NOT NULL) AS service_points_non_null,
            count(*) FILTER (WHERE first_serves_in IS NOT NULL) AS first_serves_in_non_null,
            count(*) FILTER (WHERE first_serve_points_won IS NOT NULL) AS first_serve_points_won_non_null,
            count(*) FILTER (WHERE second_serve_points_won IS NOT NULL) AS second_serve_points_won_non_null,
            count(*) FILTER (WHERE break_points_saved IS NOT NULL) AS break_points_saved_non_null,
            count(*) FILTER (WHERE break_points_faced IS NOT NULL) AS break_points_faced_non_null
        FROM stat_context
        GROUP BY season_year, tour_scope
        ORDER BY season_year, tour_scope
        """
    ).fetchall()
    coverage: list[dict[str, object]] = []
    for row in rows:
        stat_rows = int(row[2])
        coverage.append(
            {
                "aces_non_null": int(row[3]),
                "break_points_faced_non_null": int(row[10]),
                "break_points_saved_non_null": int(row[9]),
                "double_faults_non_null": int(row[4]),
                "first_serve_points_won_non_null": int(row[7]),
                "first_serves_in_non_null": int(row[6]),
                "season_year": int(row[0]),
                "second_serve_points_won_non_null": int(row[8]),
                "service_points_non_null": int(row[5]),
                "service_points_played_non_null": int(row[5]),
                "stat_rows": stat_rows,
                "tour_scope": str(row[1]),
            }
        )
    return coverage


def summarize_ingest(
    *,
    dsn: str,
    documents: list[SourceDocument],
    ingest_results: list[dict[str, object]],
    cutoff: datetime,
) -> dict[str, object]:
    with psycopg.connect(dsn, autocommit=True) as conn:
        counts = _table_counts(conn)
        identity_checks = _identity_checks(conn)
        match_integrity = _match_integrity_checks(conn)
        pit_checks = validate_sackmann_boundaries(conn, documents)
        extractor_probe = _extractor_probe(conn, cutoff=cutoff)
        distributions = _distribution_summary(conn, cutoff=cutoff)
        summary = {
            "counts": counts,
            "extractor_probe": extractor_probe,
            "identity_checks": identity_checks,
            "ingest_results": [
                {
                    "dataset": item["dataset"],
                    "filename": item["filename"],
                    "result": item["result"],
                    "year": item["year"],
                }
                for item in ingest_results
            ],
            "match_integrity_checks": match_integrity,
            "qualifying_count": _qualifying_count(conn, cutoff=cutoff),
            "source_revision": documents[0].source_revision if documents else None,
            "stat_coverage": _stat_coverage(conn),
            "violations": {
                "duplicate_player_source_ids": identity_checks["duplicate_player_source_ids"],
                "match_as_of_before_source_observed_at": _fetch_scalar(
                    conn,
                    "SELECT count(*) FROM match_versions WHERE as_of < source_observed_at",
                ),
                "orphan_results": match_integrity["orphan_results"],
                "orphan_stats": match_integrity["orphan_stats"],
                "result_as_of_before_source_observed_at": _fetch_scalar(
                    conn,
                    "SELECT count(*) FROM match_result_observations WHERE as_of < source_observed_at",
                ),
                "same_day_boundary_mismatches": (
                    pit_checks["version_boundary_mismatches"] + pit_checks["result_boundary_mismatches"]
                ),
                "self_matches": identity_checks["self_matches"],
            },
            "year_distribution": _year_counts(conn),
            **distributions,
        }
    return summary


def validate_idempotency(
    *,
    dsn: str,
    document: SourceDocument,
    artifact_dir: Path,
) -> dict[str, object]:
    quality_path = artifact_dir / f"{document.filename}.reingest.quality.json"
    unmatched_path = artifact_dir / f"{document.filename}.reingest.unmatched.json"
    with psycopg.connect(dsn, autocommit=True) as conn:
        before = _table_counts(conn)
        result = ingest_sackmann_document(
            conn,
            document=document,
            dry_run=False,
            quality_path=quality_path,
            unmatched_path=unmatched_path,
        )
        after = _table_counts(conn)
    return {
        "counts_after": {
            "ingest_runs": after["ingest_runs"],
            "match_versions": after["match_versions"],
            "matches": after["matches"],
            "results": after["results"],
            "stats": after["stats"],
        },
        "counts_before": {
            "ingest_runs": before["ingest_runs"],
            "match_versions": before["match_versions"],
            "matches": before["matches"],
            "results": before["results"],
            "stats": before["stats"],
        },
        "duplicate_rows": int(result["counters"]["duplicate_rows"]),
        "filename": document.filename,
        "result": result,
    }


def evaluate_smoke_gate(
    dry_run_summary: dict[str, object],
    ingest_summary: dict[str, object],
    idempotency_summary: dict[str, object],
) -> dict[str, object]:
    dry_gate = dry_run_summary["gate"]
    identity_checks = ingest_summary["identity_checks"]
    match_integrity = ingest_summary["match_integrity_checks"]
    violations = ingest_summary["violations"]
    pit_ok = (
        violations["match_as_of_before_source_observed_at"] == 0
        and violations["result_as_of_before_source_observed_at"] == 0
        and violations["same_day_boundary_mismatches"] == 0
    )
    idempotent = (
        idempotency_summary["counts_before"]["matches"] == idempotency_summary["counts_after"]["matches"]
        and idempotency_summary["counts_before"]["match_versions"]
        == idempotency_summary["counts_after"]["match_versions"]
        and idempotency_summary["counts_before"]["results"] == idempotency_summary["counts_after"]["results"]
        and idempotency_summary["counts_before"]["stats"] == idempotency_summary["counts_after"]["stats"]
    )
    passed = (
        dry_gate["gate_pass"]
        and identity_checks["duplicate_player_source_ids"] == 0
        and identity_checks["ambiguous_identity_events"] == 0
        and identity_checks["unmatched_identity_events"] == 0
        and identity_checks["self_matches"] == 0
        and match_integrity["duplicate_match_source_hashes"] == 0
        and match_integrity["duplicate_match_source_ids"] == 0
        and match_integrity["orphan_results"] == 0
        and match_integrity["orphan_stats"] == 0
        and match_integrity["results_without_eligible_version"] == 0
        and match_integrity["winner_side_invalid"] == 0
        and pit_ok
        and idempotent
        and ingest_summary["extractor_probe"]["future_excluded_count"] == 0
    )
    return {
        "extractor": ingest_summary["extractor_probe"]["future_excluded_count"] == 0,
        "idempotency": idempotent,
        "identity": (
            identity_checks["duplicate_player_source_ids"] == 0
            and identity_checks["ambiguous_identity_events"] == 0
            and identity_checks["unmatched_identity_events"] == 0
            and identity_checks["self_matches"] == 0
        ),
        "match_integrity": (
            match_integrity["duplicate_match_source_hashes"] == 0
            and match_integrity["duplicate_match_source_ids"] == 0
            and match_integrity["orphan_results"] == 0
            and match_integrity["orphan_stats"] == 0
            and match_integrity["results_without_eligible_version"] == 0
            and match_integrity["winner_side_invalid"] == 0
        ),
        "parser": dry_gate["gate_pass"],
        "pit": pit_ok,
        "smoke_gate_pass": passed,
        "status": not bool(dry_gate["unknown_reason_counts"]),
    }


def _full_summary_path(artifact_dir: Path, phase: str) -> Path:
    return artifact_dir / f"{phase}_summary.json"


def write_phase_summary(
    *,
    phase: str,
    artifact_dir: Path,
    payload: dict[str, object],
) -> None:
    _json_dump(_full_summary_path(artifact_dir, phase), payload)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Acquire, validate, and locally ingest Sackmann ATP history for Agent 1."
    )
    parser.add_argument("--db-dsn", required=True, help="Disposable local PostgreSQL DSN")
    parser.add_argument("--source-revision", required=True, help="Approved mirror revision to use")
    parser.add_argument(
        "--raw-root",
        default="data/raw/sackmann",
        help="Local raw root for immutable Sackmann CSVs",
    )
    parser.add_argument(
        "--artifact-root",
        default="artifacts/historical_ingest",
        help="Ignored artifact root for manifests and summaries",
    )
    parser.add_argument(
        "--psql-path",
        default="psql",
        help="psql executable used to apply committed migrations",
    )
    parser.add_argument(
        "--reset-disposable-db",
        action="store_true",
        help="Explicitly authorize resetting an approved local disposable history database",
    )
    parser.add_argument(
        "--local-only",
        action="store_true",
        help="Skip remote verification/download and use already-acquired local raw files only",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    raw_root = Path(args.raw_root).resolve()
    artifact_root = Path(args.artifact_root).resolve()
    smoke_artifact_dir = artifact_root / "smoke_2024_2025"
    full_artifact_dir = artifact_root / "full_2016_2025"

    if args.local_only:
        smoke_documents = build_local_sackmann_documents(
            years=(2024, 2025),
            raw_root=raw_root,
            source_revision=args.source_revision,
        )
    else:
        smoke_documents = acquire_sackmann_documents(
            years=(2024, 2025),
            raw_root=raw_root,
            source_revision=args.source_revision,
        )
    write_manifest(smoke_artifact_dir / "acquisition_manifest.json", smoke_documents)
    smoke_dry_runs = run_dry_runs(smoke_documents, artifact_dir=smoke_artifact_dir)
    smoke_dry_run_summary = write_dry_run_summary(
        smoke_documents,
        smoke_dry_runs,
        artifact_dir=smoke_artifact_dir,
        source_revision=args.source_revision,
    )
    if not smoke_dry_run_summary["gate"]["gate_pass"]:
        write_phase_summary(
            phase="smoke_gate",
            artifact_dir=smoke_artifact_dir,
            payload={"result": "SMOKE_FAILED", "summary": smoke_dry_run_summary},
        )
        print(json.dumps({"result": "SMOKE_FAILED", "phase": "dry_run"}, indent=2, sort_keys=True))
        return 1

    if args.reset_disposable_db:
        reset_database(
            dsn=args.db_dsn,
            psql_path=args.psql_path,
            allow_destructive_reset=True,
        )

    smoke_ingest_results = ingest_documents(
        dsn=args.db_dsn,
        documents=smoke_documents,
        artifact_dir=smoke_artifact_dir,
    )
    smoke_ingest_summary = summarize_ingest(
        dsn=args.db_dsn,
        documents=smoke_documents,
        ingest_results=smoke_ingest_results,
        cutoff=_SMOKE_CUTOFF,
    )
    smoke_idempotency = validate_idempotency(
        dsn=args.db_dsn,
        document=smoke_documents[0],
        artifact_dir=smoke_artifact_dir,
    )
    smoke_gate = evaluate_smoke_gate(
        smoke_dry_run_summary,
        smoke_ingest_summary,
        smoke_idempotency,
    )
    smoke_summary_payload = {
        **smoke_ingest_summary,
        "dsn": args.db_dsn,
        "idempotency_after": smoke_idempotency["counts_after"],
        "idempotency_before": smoke_idempotency["counts_before"],
        "reingest_duplicates": smoke_idempotency["duplicate_rows"],
        "smoke_gate": smoke_gate,
    }
    _json_dump(smoke_artifact_dir / "smoke_ingest_summary.json", smoke_summary_payload)
    if not smoke_gate["smoke_gate_pass"]:
        write_phase_summary(
            phase="smoke_gate",
            artifact_dir=smoke_artifact_dir,
            payload={"result": "SMOKE_FAILED", "summary": smoke_summary_payload},
        )
        print(json.dumps({"result": "SMOKE_FAILED", "phase": "ingest"}, indent=2, sort_keys=True))
        return 2

    if args.local_only:
        full_documents = build_local_sackmann_documents(
            years=range(2016, 2026),
            raw_root=raw_root,
            source_revision=args.source_revision,
        )
    else:
        full_documents = acquire_sackmann_documents(
            years=range(2016, 2026),
            raw_root=raw_root,
            source_revision=args.source_revision,
        )
    write_manifest(full_artifact_dir / "acquisition_manifest.json", full_documents)
    expansion_documents = [document for document in full_documents if document.season in range(2016, 2024)]
    expansion_dry_runs = run_dry_runs(expansion_documents, artifact_dir=full_artifact_dir)
    expansion_dry_gate = evaluate_dry_run_gate(expansion_dry_runs)
    expansion_dry_summary = {
        "documents": [_document_summary(document) for document in expansion_documents],
        "dry_runs": [
            {
                "dataset": item["dataset"],
                "filename": item["filename"],
                "year": item["year"],
                **item["result"],
            }
            for item in expansion_dry_runs
        ],
        "gate": expansion_dry_gate,
        "source_revision": args.source_revision,
    }
    _json_dump(full_artifact_dir / "expansion_dry_run_summary.json", expansion_dry_summary)
    if not expansion_dry_gate["gate_pass"]:
        write_phase_summary(
            phase="expansion_gate",
            artifact_dir=full_artifact_dir,
            payload={"result": "EXPANSION_BLOCKED", "summary": expansion_dry_summary},
        )
        print(json.dumps({"result": "EXPANSION_BLOCKED", "phase": "expansion_dry_run"}, indent=2, sort_keys=True))
        return 3

    expansion_ingest_results = ingest_documents(
        dsn=args.db_dsn,
        documents=expansion_documents,
        artifact_dir=full_artifact_dir,
    )
    full_ingest_summary = summarize_ingest(
        dsn=args.db_dsn,
        documents=full_documents,
        ingest_results=smoke_ingest_results + expansion_ingest_results,
        cutoff=_SMOKE_CUTOFF,
    )
    full_summary_payload = {
        **full_ingest_summary,
        "additional_years": list(range(2016, 2024)),
        "expansion_dry_run_gate": expansion_dry_gate,
        "result": "READY_FOR_INDEPENDENT_REVIEW",
    }
    _json_dump(full_artifact_dir / "full_ingest_summary.json", full_summary_payload)
    print(
        json.dumps(
            {
                "result": "READY_FOR_INDEPENDENT_REVIEW",
                "rows_inserted": _counter_total(
                    smoke_ingest_results + expansion_ingest_results,
                    "rows_inserted",
                ),
                "source_revision": args.source_revision,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
