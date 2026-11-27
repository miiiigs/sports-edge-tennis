"""Database-backed work-product receipts for research rehearsals.

This is the producer side of the independent monitoring adapter. It does not
trust a process exit code, a log line or a caller-supplied count. Each stage is
verified by SQL against the persisted rows, and a receipt is only written when
every required stage has matching expected/persisted evidence.

Seven stages:

1. fixture discovery (provider ingest rows -> persisted match versions);
2. forecast execution (run targets -> three-agent on-time predictions);
3. bookmaker acquisition (BET decisions -> persisted prices/snapshots);
4. independent archive acknowledgement (originals -> delivered archive events);
5. official settlement (visible results -> non-pending settlements);
6. bookmaker settlement (only under explicitly approved rules, never inferred);
7. prospective candidate execution (predeclared expectations -> originals).

Missing support tables are ``UNVERIFIED``, never zero. A zero-fixture run is only
successful when discovery and reconciliation are explicitly acknowledged. The
publisher writes the same ``TENNIS_MONITORING_RECEIPT_V1`` that
``ops/validation/monitoring_cycle.py`` reads, so the monitor can only signal
success after the receipt exists.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Mapping, Sequence

import psycopg

from sports_edge_tennis.ops.monitoring import (
    REQUIRED_STAGES,
    STAGE_BOOKMAKER_ACQUISITION,
    STAGE_BOOKMAKER_SETTLEMENT,
    STAGE_FIXTURE_DISCOVERY,
    STAGE_FORECAST_GENERATION,
    STAGE_INDEPENDENT_ARCHIVAL,
    STAGE_OFFICIAL_SETTLEMENT,
    STAGE_PROSPECTIVE_CANDIDATE,
    MonitoringReceipt,
    RunRecord,
    StageResult,
    write_receipt,
)

VERSION = "TENNIS_WORK_PRODUCT_RECEIPT_PUBLISHER_20261127_V1"

STATUS_VERIFIED = "VERIFIED"
STATUS_PARTIAL = "PARTIAL"
STATUS_UNVERIFIED = "UNVERIFIED"
STATUS_EMPTY_ACKNOWLEDGED = "EMPTY_ACKNOWLEDGED"


@dataclass(frozen=True)
class ReceiptScope:
    """Explicit scope for one receipt. UTC and timezone-aware throughout."""

    slot: datetime
    window_start: datetime
    window_end: datetime
    prediction_run_id: int | None = None
    experiment_id: str | None = None
    tour_scopes: tuple[str, ...] = ("ATP_MAIN", "CHALLENGER")
    include_qualifying: bool = False
    cutoff: datetime | None = None
    require_archive: bool = True
    require_prospective_candidate: bool = True
    approved_bookmaker_rules: Mapping[tuple[str, str], str] = field(
        default_factory=dict
    )
    archive_manifest_path: str | None = None
    candidate_ledger_path: str | None = None
    acknowledge_empty_discovery: bool = False

    def __post_init__(self) -> None:
        for name in ("slot", "window_start", "window_end"):
            value = getattr(self, name)
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError(f"{name} must be timezone-aware")
        if self.window_end < self.window_start:
            raise ValueError("window_end must not precede window_start")
        if self.slot < self.window_start or self.slot > self.window_end:
            raise ValueError("slot must be inside the window")
        if self.cutoff is not None and (
            self.cutoff.tzinfo is None or self.cutoff.utcoffset() is None
        ):
            raise ValueError("cutoff must be timezone-aware")
        allowed = {"ATP_MAIN", "CHALLENGER"}
        if not set(self.tour_scopes) <= allowed or not self.tour_scopes:
            raise ValueError("unsupported tour scope")
        if self.prediction_run_id is not None and self.prediction_run_id <= 0:
            raise ValueError("prediction_run_id must be positive")


@dataclass(frozen=True)
class StageCheck:
    stage: str
    status: str
    expected_rows: int | None
    persisted_rows: int | None
    verification: str
    detail: str = ""
    acknowledges_empty: bool = False
    extra: Mapping[str, object] = field(default_factory=dict)

    @property
    def durable(self) -> bool:
        if self.status == STATUS_VERIFIED:
            return True
        if self.status == STATUS_EMPTY_ACKNOWLEDGED:
            return self.acknowledges_empty
        return False

    def to_stage_result(self, *, started_at: datetime, finished_at: datetime) -> StageResult:
        return StageResult(
            stage=self.stage,
            state="COMPLETED" if self.durable else "FAILED",
            expected_rows=self.expected_rows,
            persisted_rows=self.persisted_rows,
            durable_verified=self.durable,
            verification=self.verification,
            started_at=started_at,
            finished_at=finished_at,
            detail=self.detail or None,
            acknowledge_empty=self.acknowledges_empty,
        )

    def to_payload(self) -> dict[str, object]:
        return {
            "stage": self.stage,
            "status": self.status,
            "expected_rows": self.expected_rows,
            "persisted_rows": self.persisted_rows,
            "verification": self.verification,
            "durable": self.durable,
            "acknowledges_empty": self.acknowledges_empty,
            "detail": self.detail,
            "extra": dict(self.extra),
        }


def _table_exists(conn: psycopg.Connection, name: str) -> bool:
    row = conn.execute("SELECT to_regclass(%s)", (f"public.{name}",)).fetchone()
    return bool(row and row[0] is not None)


def _scalar(conn: psycopg.Connection, sql: str, params: Mapping[str, object]):
    row = conn.execute(sql, params).fetchone()
    return None if row is None else row


class WorkProductReceiptPublisher:
    def __init__(self, conn: psycopg.Connection, *, clock=None):
        if not conn.autocommit:
            raise ValueError("publisher requires an autocommit connection")
        self._conn = conn
        self._clock = clock or (lambda: datetime.now(UTC))

    def collect(self, scope: ReceiptScope) -> dict[str, object]:
        started = self._now()
        checks: list[StageCheck] = [
            self._discovery(scope),
            self._forecast(scope),
            self._bookmaker_acquisition(scope),
            self._archive(scope),
            self._official_settlement(scope),
            self._bookmaker_settlement(scope),
            self._prospective_candidate(scope),
        ]
        finished = self._now()
        by_name = {check.stage: check for check in checks}
        discovery = by_name[STAGE_FIXTURE_DISCOVERY]
        if discovery.status == STATUS_EMPTY_ACKNOWLEDGED:
            checks = [
                StageCheck(
                    check.stage,
                    STATUS_EMPTY_ACKNOWLEDGED,
                    check.expected_rows or 0,
                    check.persisted_rows or 0,
                    check.verification,
                    "NO_ELIGIBLE_FIXTURES",
                    acknowledges_empty=True,
                    extra=check.extra,
                )
                if check.expected_rows in (0, None)
                else check
                for check in checks
            ]
            by_name = {check.stage: check for check in checks}
            discovery = by_name[STAGE_FIXTURE_DISCOVERY]
        forecast = by_name[STAGE_FORECAST_GENERATION]
        eligible = forecast.expected_rows
        discovered = discovery.persisted_rows
        reconciled = discovery.persisted_rows
        reconciliation_completed = (
            discovery.status in (STATUS_VERIFIED, STATUS_EMPTY_ACKNOWLEDGED)
            and discovery.durable
        )
        missing = [check.stage for check in checks if not check.durable]
        record = RunRecord(
            run_id=f"receipt-{scope.slot.strftime('%Y%m%dT%H%M%SZ')}",
            slot=scope.slot,
            started_at=started,
            finished_at=finished,
            stages=tuple(
                check.to_stage_result(started_at=started, finished_at=finished)
                for check in checks
            ),
            eligible_fixture_count=eligible,
            discovered_fixture_count=discovered,
            reconciled_fixture_count=reconciled,
            reconciliation_completed=reconciliation_completed,
        )
        return {
            "version": VERSION,
            "scope": self._scope_payload(scope),
            "checks": [check.to_payload() for check in checks],
            "record": record,
            "receipt": MonitoringReceipt(
                slot=scope.slot,
                record=record,
                published_at=finished,
                publisher="sports-edge-tennis-work-product-receipt-publisher",
            ),
            "missing_stages": tuple(missing),
            "success": not missing,
        }

    def publish(
        self,
        scope: ReceiptScope,
        *,
        receipt_path: str | None = None,
    ) -> dict[str, object]:
        collected = self.collect(scope)
        if receipt_path:
            write_receipt(receipt_path, collected["receipt"])
            with open(receipt_path, "rb") as handle:
                digest = hashlib.sha256(handle.read()).hexdigest()
        else:
            digest = hashlib.sha256(
                json.dumps(
                    collected["receipt"].to_payload(), sort_keys=True
                ).encode()
            ).hexdigest()
        return {
            "receipt_path": receipt_path,
            "receipt_sha256": digest,
            "success": collected["success"],
            "missing_stages": collected["missing_stages"],
            "checks": collected["checks"],
            "record": collected["record"].to_payload(),
            "record_object": collected["record"],
        }

    # -- stages ---------------------------------------------------------

    def _discovery(self, scope: ReceiptScope) -> StageCheck:
        sql = """
        SELECT
          (SELECT COALESCE(SUM(ir.rows_inserted + ir.rows_updated), 0)
             FROM ingest_runs ir
            WHERE ir.source_system = 'API_TENNIS'
              AND ir.source_dataset = 'api_tennis_scheduled_fixtures'
              AND ir.started_at >= %(start)s AND ir.started_at <= %(end)s) AS expected,
          (SELECT COUNT(*)
             FROM match_versions mv
             JOIN ingest_runs ir ON ir.ingest_run_id = mv.ingest_run_id
            WHERE ir.source_system = 'API_TENNIS'
              AND ir.source_dataset = 'api_tennis_scheduled_fixtures'
              AND ir.started_at >= %(start)s AND ir.started_at <= %(end)s
              AND mv.match_start_at >= %(start)s AND mv.match_start_at <= %(end)s
              AND mv.tour_scope::text = ANY(%(tours)s::text[])
              AND (%(include_qualifying)s OR mv.qualifying_flag = FALSE)) AS persisted,
          (SELECT COUNT(*) FROM ingest_runs ir
            WHERE ir.source_system = 'API_TENNIS'
              AND ir.source_dataset = 'api_tennis_scheduled_fixtures'
              AND ir.started_at >= %(start)s AND ir.started_at <= %(end)s
              AND ir.status = 'PARTIAL') AS partial_runs,
          (SELECT COUNT(*) FROM ingest_runs ir
            WHERE ir.source_system = 'API_TENNIS'
              AND ir.source_dataset = 'api_tennis_scheduled_fixtures'
              AND ir.started_at >= %(start)s AND ir.started_at <= %(end)s) AS run_count,
          (SELECT COALESCE(SUM(ir.rows_rejected), 0) FROM ingest_runs ir
            WHERE ir.source_system = 'API_TENNIS'
              AND ir.source_dataset = 'api_tennis_scheduled_fixtures'
              AND ir.started_at >= %(start)s AND ir.started_at <= %(end)s) AS rejected
        """
        row = _scalar(
            self._conn,
            sql,
            {
                "start": scope.window_start,
                "end": scope.window_end,
                "tours": list(scope.tour_scopes),
                "include_qualifying": scope.include_qualifying,
            },
        )
        expected, persisted, partial_runs, run_count, rejected = row
        if partial_runs:
            return StageCheck(
                STAGE_FIXTURE_DISCOVERY,
                STATUS_PARTIAL,
                int(expected),
                int(persisted),
                "INGEST_RUN_ROWS",
                "PARTIAL_INGEST_RUNS",
                extra={"partial_runs": partial_runs, "rejected": rejected},
            )
        if run_count == 0:
            return StageCheck(
                STAGE_FIXTURE_DISCOVERY,
                STATUS_UNVERIFIED,
                0,
                0,
                "INGEST_RUN_ROWS",
                "NO_DISCOVERY_RUN_IN_WINDOW",
            )
        if int(expected) == 0:
            if scope.acknowledge_empty_discovery:
                return StageCheck(
                    STAGE_FIXTURE_DISCOVERY,
                    STATUS_EMPTY_ACKNOWLEDGED,
                    0,
                    0,
                    "INGEST_RUN_ROWS",
                    "EMPTY_DISCOVERY_EXPLICITLY_ACKNOWLEDGED",
                    acknowledges_empty=True,
                    extra={"run_count": run_count, "rejected": rejected},
                )
            return StageCheck(
                STAGE_FIXTURE_DISCOVERY,
                STATUS_UNVERIFIED,
                0,
                0,
                "INGEST_RUN_ROWS",
                "ZERO_DISCOVERY_ROWS_WITHOUT_ACKNOWLEDGEMENT",
                extra={"run_count": run_count, "rejected": rejected},
            )
        if int(expected) != int(persisted):
            return StageCheck(
                STAGE_FIXTURE_DISCOVERY,
                STATUS_PARTIAL,
                int(expected),
                int(persisted),
                "INGEST_RUN_ROWS",
                "DISCOVERY_ROW_COUNT_MISMATCH",
                extra={"rejected": rejected, "run_count": run_count},
            )
        return StageCheck(
            STAGE_FIXTURE_DISCOVERY,
            STATUS_VERIFIED,
            int(expected),
            int(persisted),
            "INGEST_RUN_ROWS",
            extra={"run_count": run_count, "rejected": rejected},
        )

    def _resolve_run(self, scope: ReceiptScope) -> int | None:
        if scope.prediction_run_id is not None:
            return scope.prediction_run_id
        row = self._conn.execute(
            """
            SELECT prediction_run_id FROM prediction_runs
            WHERE run_status='COMPLETED' AND experiment_id IS NULL
              AND prediction_finalized_at >= %(start)s
              AND prediction_finalized_at <= %(end)s
            ORDER BY prediction_finalized_at DESC, prediction_run_id DESC
            LIMIT 1
            """,
            {"start": scope.window_start, "end": scope.window_end},
        ).fetchone()
        return None if row is None else int(row[0])

    def _forecast(self, scope: ReceiptScope) -> StageCheck:
        run_id = self._resolve_run(scope)
        if run_id is None:
            return StageCheck(
                STAGE_FORECAST_GENERATION,
                STATUS_UNVERIFIED,
                None,
                None,
                "PREDICTION_RUN_ROWS",
                "NO_COMPLETED_PREDICTION_RUN_IN_WINDOW",
            )
        experiment = scope.experiment_id
        sql = """
        WITH run AS (
          SELECT prediction_run_id, run_status, as_of, prediction_finalized_at,
                 experiment_id
          FROM prediction_runs WHERE prediction_run_id = %(run_id)s
        ), targets AS (
          SELECT t.match_id, mv.match_start_at
          FROM prediction_run_targets t
          JOIN run r ON TRUE
          JOIN LATERAL (
            SELECT mv.match_start_at
            FROM match_versions mv
            WHERE mv.match_id = t.match_id AND mv.as_of <= r.as_of
            ORDER BY mv.as_of DESC, mv.source_observed_at DESC,
                     mv.match_version_id DESC
            LIMIT 1
          ) mv ON TRUE
          WHERE t.prediction_run_id = r.prediction_run_id
        )
        SELECT
          (SELECT COUNT(*) FROM targets) AS expected,
          (SELECT COUNT(*) FROM (
             SELECT t.match_id
             FROM targets t
             JOIN predictions p
               ON p.prediction_run_id = %(run_id)s
              AND p.match_id = t.match_id
             WHERE p.prediction_locked_at
                   <= t.match_start_at - interval '30 minutes'
               AND (%(experiment)s::text IS NULL
                    OR p.experiment_id IS NOT DISTINCT FROM %(experiment)s::text)
             GROUP BY t.match_id
             HAVING COUNT(DISTINCT p.agent_code) = 3
          ) verified) AS persisted,
          (SELECT run_status FROM run) AS run_status,
          (SELECT prediction_finalized_at FROM run) AS finalized_at,
          (SELECT experiment_id FROM run) AS run_experiment
        """
        row = _scalar(
            self._conn,
            sql,
            {"run_id": run_id, "experiment": experiment},
        )
        expected, persisted, run_status, finalized_at, run_experiment = row
        if run_status != "COMPLETED" or finalized_at is None:
            return StageCheck(
                STAGE_FORECAST_GENERATION,
                STATUS_PARTIAL,
                int(expected),
                int(persisted),
                "PREDICTION_RUN_ROWS",
                f"RUN_STATUS_{run_status}",
                extra={"run_id": run_id},
            )
        if experiment is None and run_experiment is not None:
            return StageCheck(
                STAGE_FORECAST_GENERATION,
                STATUS_UNVERIFIED,
                int(expected),
                int(persisted),
                "PREDICTION_RUN_ROWS",
                "EXPERIMENT_RUN_REQUIRES_EXPLICIT_SCOPE",
                extra={"run_id": run_id},
            )
        if int(expected) != int(persisted):
            return StageCheck(
                STAGE_FORECAST_GENERATION,
                STATUS_PARTIAL,
                int(expected),
                int(persisted),
                "PREDICTION_RUN_ROWS",
                "MISSING_OR_LATE_AGENT_PREDICTIONS",
                extra={"run_id": run_id},
            )
        if int(expected) == 0:
            return StageCheck(
                STAGE_FORECAST_GENERATION,
                STATUS_UNVERIFIED,
                0,
                0,
                "PREDICTION_RUN_ROWS",
                "EMPTY_RUN_TARGETS_UNVERIFIED",
                extra={"run_id": run_id},
            )
        return StageCheck(
            STAGE_FORECAST_GENERATION,
            STATUS_VERIFIED,
            int(expected),
            int(persisted),
            "PREDICTION_RUN_ROWS",
            extra={"run_id": run_id},
        )

    def _bookmaker_acquisition(self, scope: ReceiptScope) -> StageCheck:
        run_id = self._resolve_run(scope)
        if run_id is None:
            return StageCheck(
                STAGE_BOOKMAKER_ACQUISITION,
                STATUS_UNVERIFIED,
                None,
                None,
                "DECISION_ODDS_ROWS",
                "NO_COMPLETED_PREDICTION_RUN_IN_WINDOW",
            )
        row = _scalar(
            self._conn,
            """
            SELECT
              COUNT(*) AS expected,
              COUNT(*) FILTER (
                WHERE d.market_decimal_price IS NOT NULL
                  AND EXISTS (
                    SELECT 1 FROM decision_odds_snapshots dos
                    WHERE dos.decision_id = d.decision_id
                  )
              ) AS persisted
            FROM decisions d
            WHERE d.prediction_run_id = %(run_id)s
              AND d.decision_status = 'BET'
            """,
            {"run_id": run_id},
        )
        expected, persisted = int(row[0]), int(row[1])
        if expected == 0:
            return StageCheck(
                STAGE_BOOKMAKER_ACQUISITION,
                STATUS_EMPTY_ACKNOWLEDGED,
                0,
                0,
                "DECISION_ODDS_ROWS",
                "NO_BET_DECISIONS_REQUIRE_NO_PRICE",
                acknowledges_empty=True,
                extra={"run_id": run_id},
            )
        if expected != persisted:
            return StageCheck(
                STAGE_BOOKMAKER_ACQUISITION,
                STATUS_PARTIAL,
                expected,
                persisted,
                "DECISION_ODDS_ROWS",
                "BET_DECISION_WITHOUT_PERSISTED_PRICE",
                extra={"run_id": run_id},
            )
        return StageCheck(
            STAGE_BOOKMAKER_ACQUISITION,
            STATUS_VERIFIED,
            expected,
            persisted,
            "DECISION_ODDS_ROWS",
            extra={"run_id": run_id},
        )

    def _archive(self, scope: ReceiptScope) -> StageCheck:
        if not scope.require_archive:
            return StageCheck(
                STAGE_INDEPENDENT_ARCHIVAL,
                STATUS_EMPTY_ACKNOWLEDGED,
                0,
                0,
                "SCOPE_EXCLUDED",
                "ARCHIVE_NOT_REQUIRED_BY_SCOPE",
                acknowledges_empty=False,
            )
        if not scope.experiment_id:
            return StageCheck(
                STAGE_INDEPENDENT_ARCHIVAL,
                STATUS_UNVERIFIED,
                None,
                None,
                "ARCHIVE_LEDGER",
                "NO_EXPERIMENT_SCOPE_FOR_ARCHIVE",
            )
        if _table_exists(self._conn, "forward_originals") and _table_exists(
            self._conn, "forward_delivery_events"
        ):
            row = _scalar(
                self._conn,
                """
                SELECT
                  (SELECT COUNT(*) FROM forward_originals
                    WHERE experiment_version = %(experiment)s) AS expected,
                  (SELECT COUNT(*)
                     FROM forward_originals o
                     JOIN LATERAL (
                       SELECT state FROM forward_delivery_events d
                       WHERE d.experiment_version = o.experiment_version
                         AND d.match_id = o.match_id AND d.channel = 'ARCHIVE'
                       ORDER BY d.event_id DESC LIMIT 1
                     ) last ON TRUE
                     WHERE last.state IN ('DELIVERED','RECONCILED')) AS persisted
                """,
                {"experiment": scope.experiment_id},
            )
            return self._ledger_check(
                STAGE_INDEPENDENT_ARCHIVAL,
                int(row[0]),
                int(row[1]),
                "FORWARD_DELIVERY_EVENTS",
                "ARCHIVE_NOT_ACKNOWLEDGED",
            )
        if scope.archive_manifest_path:
            return self._manifest_check(
                STAGE_INDEPENDENT_ARCHIVAL,
                scope.archive_manifest_path,
                "ARCHIVE_ACK_MANIFEST",
            )
        return StageCheck(
            STAGE_INDEPENDENT_ARCHIVAL,
            STATUS_UNVERIFIED,
            None,
            None,
            "ARCHIVE_LEDGER",
            "ARCHIVE_LEDGER_OR_MANIFEST_UNAVAILABLE",
        )

    def _official_settlement(self, scope: ReceiptScope) -> StageCheck:
        run_id = self._resolve_run(scope)
        if run_id is None:
            return StageCheck(
                STAGE_OFFICIAL_SETTLEMENT,
                STATUS_UNVERIFIED,
                None,
                None,
                "DECISION_SETTLEMENTS",
                "NO_COMPLETED_PREDICTION_RUN_IN_WINDOW",
            )
        cutoff = scope.cutoff or scope.window_end
        row = _scalar(
            self._conn,
            """
            WITH bet AS (
              SELECT d.decision_id, d.match_id
              FROM decisions d
              WHERE d.prediction_run_id = %(run_id)s
                AND d.decision_status = 'BET'
            ), visible AS (
              SELECT b.decision_id, r.match_result_observation_id,
                     ROW_NUMBER() OVER (
                       PARTITION BY b.decision_id
                       ORDER BY r.as_of DESC, r.source_observed_at DESC,
                                r.match_result_observation_id DESC
                     ) AS rn
              FROM bet b
              JOIN match_result_observations r ON r.match_id = b.match_id
              WHERE r.ingested_at <= %(cutoff)s AND r.as_of <= %(cutoff)s
            )
            SELECT COUNT(*) AS expected,
                   COUNT(*) FILTER (
                     WHERE ds.settlement_status IN
                       ('WON','LOST','VOID','PUSH','CANCELLED')
                       AND ds.settled_at IS NOT NULL
                   ) AS persisted,
                   COUNT(*) FILTER (
                     WHERE ds.match_result_observation_id
                       IS DISTINCT FROM v.match_result_observation_id
                   ) AS stale
            FROM visible v
            LEFT JOIN decision_settlements ds ON ds.decision_id = v.decision_id
            WHERE v.rn = 1
            """,
            {"run_id": run_id, "cutoff": cutoff},
        )
        expected, persisted, stale = int(row[0]), int(row[1]), int(row[2])
        if stale:
            return StageCheck(
                STAGE_OFFICIAL_SETTLEMENT,
                STATUS_PARTIAL,
                expected,
                persisted,
                "DECISION_SETTLEMENTS",
                "SETTLEMENT_BOUND_TO_STALE_RESULT_OBSERVATION",
                extra={"stale": stale, "run_id": run_id},
            )
        if expected == 0:
            return StageCheck(
                STAGE_OFFICIAL_SETTLEMENT,
                STATUS_EMPTY_ACKNOWLEDGED,
                0,
                0,
                "DECISION_SETTLEMENTS",
                "NO_VISIBLE_RESULT_FOR_BET_DECISIONS",
                acknowledges_empty=True,
                extra={"run_id": run_id},
            )
        if expected != persisted:
            return StageCheck(
                STAGE_OFFICIAL_SETTLEMENT,
                STATUS_PARTIAL,
                expected,
                persisted,
                "DECISION_SETTLEMENTS",
                "VISIBLE_RESULT_NOT_SETTLED",
                extra={"run_id": run_id},
            )
        return StageCheck(
            STAGE_OFFICIAL_SETTLEMENT,
            STATUS_VERIFIED,
            expected,
            persisted,
            "DECISION_SETTLEMENTS",
            extra={"run_id": run_id},
        )

    def _bookmaker_settlement(self, scope: ReceiptScope) -> StageCheck:
        if not scope.approved_bookmaker_rules:
            return StageCheck(
                STAGE_BOOKMAKER_SETTLEMENT,
                STATUS_EMPTY_ACKNOWLEDGED,
                0,
                0,
                "NO_APPROVED_BOOKMAKER_RULES",
                "NO_BOOKMAKER_GRADING_IS_INFERRED",
                acknowledges_empty=True,
            )
        if not scope.experiment_id:
            return StageCheck(
                STAGE_BOOKMAKER_SETTLEMENT,
                STATUS_UNVERIFIED,
                None,
                None,
                "FORWARD_SETTLEMENT_EVENTS",
                "NO_EXPERIMENT_SCOPE_FOR_BOOKMAKER_SETTLEMENT",
            )
        if not _table_exists(self._conn, "forward_settlement_events"):
            return StageCheck(
                STAGE_BOOKMAKER_SETTLEMENT,
                STATUS_UNVERIFIED,
                None,
                None,
                "FORWARD_SETTLEMENT_EVENTS",
                "BOOKMAKER_SETTLEMENT_LEDGER_UNAVAILABLE",
            )
        row = _scalar(
            self._conn,
            """
            SELECT
              (SELECT COUNT(*) FROM forward_originals
                WHERE experiment_version = %(experiment)s) AS expected,
              (SELECT COUNT(*) FROM forward_originals o
                WHERE o.experiment_version = %(experiment)s
                  AND EXISTS (
                    SELECT 1 FROM forward_settlement_events s
                    WHERE s.experiment_version = o.experiment_version
                      AND s.match_id = o.match_id
                      AND s.pathway = 'BOOKMAKER'
                  )) AS persisted
            """,
            {"experiment": scope.experiment_id},
        )
        return self._ledger_check(
            STAGE_BOOKMAKER_SETTLEMENT,
            int(row[0]),
            int(row[1]),
            "FORWARD_SETTLEMENT_EVENTS",
            "BOOKMAKER_SETTLEMENT_MISSING",
        )

    def _prospective_candidate(self, scope: ReceiptScope) -> StageCheck:
        if not scope.require_prospective_candidate:
            return StageCheck(
                STAGE_PROSPECTIVE_CANDIDATE,
                STATUS_EMPTY_ACKNOWLEDGED,
                0,
                0,
                "SCOPE_EXCLUDED",
                "PROSPECTIVE_CANDIDATE_NOT_REQUIRED_BY_SCOPE",
                acknowledges_empty=False,
            )
        if not scope.experiment_id:
            return StageCheck(
                STAGE_PROSPECTIVE_CANDIDATE,
                STATUS_UNVERIFIED,
                None,
                None,
                "FORWARD_FIXTURE_EXPECTATIONS",
                "NO_EXPERIMENT_SCOPE_FOR_PROSPECTIVE_CANDIDATE",
            )
        if _table_exists(self._conn, "forward_fixture_expectations") and _table_exists(
            self._conn, "forward_originals"
        ):
            row = _scalar(
                self._conn,
                """
                SELECT
                  (SELECT COUNT(*) FROM forward_fixture_expectations
                    WHERE experiment_version = %(experiment)s) AS expected,
                  (SELECT COUNT(*) FROM forward_fixture_expectations e
                    JOIN forward_originals o
                      ON o.experiment_version = e.experiment_version
                     AND o.match_id = e.match_id
                    WHERE e.experiment_version = %(experiment)s) AS persisted
                """,
                {"experiment": scope.experiment_id},
            )
            return self._ledger_check(
                STAGE_PROSPECTIVE_CANDIDATE,
                int(row[0]),
                int(row[1]),
                "FORWARD_FIXTURE_EXPECTATIONS",
                "PREDECLARED_CANDIDATE_NOT_EXECUTED",
            )
        if scope.candidate_ledger_path:
            return self._manifest_check(
                STAGE_PROSPECTIVE_CANDIDATE,
                scope.candidate_ledger_path,
                "CANDIDATE_LEDGER_MANIFEST",
            )
        return StageCheck(
            STAGE_PROSPECTIVE_CANDIDATE,
            STATUS_UNVERIFIED,
            None,
            None,
            "FORWARD_FIXTURE_EXPECTATIONS",
            "CANDIDATE_LEDGER_OR_MANIFEST_UNAVAILABLE",
        )

    # -- helpers --------------------------------------------------------

    def _ledger_check(
        self,
        stage: str,
        expected: int,
        persisted: int,
        verification: str,
        failure_detail: str,
    ) -> StageCheck:
        if expected == 0:
            return StageCheck(
                stage,
                STATUS_UNVERIFIED,
                0,
                0,
                verification,
                "EMPTY_LEDGER_WITHOUT_EXPLICIT_ACKNOWLEDGEMENT",
            )
        if expected != persisted:
            return StageCheck(
                stage,
                STATUS_PARTIAL,
                expected,
                persisted,
                verification,
                failure_detail,
            )
        return StageCheck(
            stage,
            STATUS_VERIFIED,
            expected,
            persisted,
            verification,
        )

    def _manifest_check(
        self, stage: str, path: str, verification: str
    ) -> StageCheck:
        with open(path, "rb") as handle:
            raw = handle.read()
        digest = hashlib.sha256(raw).hexdigest()
        payload = json.loads(raw)
        entries = payload.get("acknowledgements") or payload.get("candidates")
        if not isinstance(entries, list) or not entries:
            return StageCheck(
                stage,
                STATUS_UNVERIFIED,
                0,
                0,
                verification,
                "EMPTY_EXTERNAL_MANIFEST",
                extra={"manifest_sha256": digest},
            )
        verified = [
            entry
            for entry in entries
            if isinstance(entry, Mapping) and entry.get("verified") is True
        ]
        if len(verified) != len(entries):
            return StageCheck(
                stage,
                STATUS_PARTIAL,
                len(entries),
                len(verified),
                verification,
                "UNVERIFIED_EXTERNAL_MANIFEST_ENTRY",
                extra={"manifest_sha256": digest},
            )
        return StageCheck(
            stage,
            STATUS_VERIFIED,
            len(entries),
            len(verified),
            verification,
            extra={"manifest_sha256": digest},
        )

    def _scope_payload(self, scope: ReceiptScope) -> dict[str, object]:
        return {
            "slot": scope.slot.isoformat(),
            "window_start": scope.window_start.isoformat(),
            "window_end": scope.window_end.isoformat(),
            "prediction_run_id": scope.prediction_run_id,
            "experiment_id": scope.experiment_id,
            "tour_scopes": list(scope.tour_scopes),
            "include_qualifying": scope.include_qualifying,
            "cutoff": scope.cutoff.isoformat() if scope.cutoff else None,
            "require_archive": scope.require_archive,
            "require_prospective_candidate": scope.require_prospective_candidate,
            "approved_bookmaker_rules": sorted(
                [list(key), value]
                for key, value in scope.approved_bookmaker_rules.items()
            ),
        }

    def _now(self) -> datetime:
        return self._clock()


def required_stage_names() -> tuple[str, ...]:
    return REQUIRED_STAGES
