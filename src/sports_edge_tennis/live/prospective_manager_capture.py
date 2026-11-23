"""Optional, nonblocking first-issued Manager research observer for live batches.

Uses the EXISTING persisted production predictions, not another prediction
engine or odds provider. A new read-only DB connection avoids changing the
session's transaction mode for market settlement. Disabled unless explicitly
enabled; failures never stop live publication or settlement. Original
unfiltered Manager outcomes are logged alongside independently frozen
research B/C/blend decisions before fixture start.

This creates timestamped Railway logs, not a new persistent database table.
Operator must preserve logs. No historical rerun can recreate publication.
"""
from __future__ import annotations

import json
import os
from datetime import UTC,datetime,timedelta
from typing import Callable

import psycopg

from sports_edge_tennis.validation.prospective_manager_shadow_capture import VERSION, run

POLICY_FROZEN_SOURCE_SHA="177ec8146561c4e06d28e3e490dad38421388035"
OBSERVATION_HORIZON_DAYS=4


def capture_heartbeat(*, enabled: bool, attempted: int, successful: int,
                      records: int, failed: int) -> str:
    """Emit a no-secrets, no-query observer liveness proof for each successful batch.

    A missing prospective summary is otherwise ambiguous: the flag may be off,
    no production prediction run may have occurred, or the observer may have
    failed. Counts describe logger attempts, NOT unique official eligible
    matches, persisted archives, or settled predictions.
    """
    values = (attempted, successful, records, failed)
    if any(not isinstance(value, int) or value < 0 for value in values):
        raise ValueError("observer counters must be nonnegative integers")
    if successful + failed != attempted:
        raise ValueError("observer attempts must reconcile")
    if not enabled and any(values):
        raise ValueError("disabled observer cannot have capture attempts")
    if not enabled:
        status = "DISABLED"
    elif attempted == 0:
        status = "NO_PRODUCTION_RUNS"
    elif failed:
        status = "PARTIAL_FAILURE" if successful else "ALL_ATTEMPTS_FAILED"
    elif records == 0:
        status = "NO_FUTURE_FIRST_ISSUES"
    else:
        status = "CAPTURED"
    return "TENNIS_FROZEN_PROSPECTIVE_HEARTBEAT " + json.dumps(
        {
            "enabled": enabled,
            "attempted": attempted,
            "successful": successful,
            "failed": failed,
            "records_logged": records,
            "status": status,
            "persistent_storage_verified": False,
            "published_model_picks_changed": False,
        },
        sort_keys=True,
    )


def observe_first_issued_runs(*,dsn:str,run_ids:list[int]|tuple[int,...],
                              enabled:bool,log:Callable[[str],None],
                              capture_at:datetime|None=None,
                              connector:Callable[...,object]=psycopg.connect
                              )->dict|None:
    if not enabled:
        return None
    seen={int(i) for i in run_ids if i is not None}
    if not seen or any(i<=0 for i in seen):
        return None
    now=(capture_at or datetime.now(UTC))
    if now.tzinfo is None:
        raise ValueError("capture clock must be timezone-aware")
    now=now.astimezone(UTC)
    start=now+timedelta(minutes=30)
    end=now+timedelta(days=OBSERVATION_HORIZON_DAYS)
    with connector(dsn,autocommit=True,connect_timeout=10) as read_only:
        # run() sets read-only on THIS SEPARATE session and cannot poison
        # any of the live batch's write-capable database connections.
        report=run(read_only,captured_at=now,start=start,end=end,
                   source_sha=POLICY_FROZEN_SOURCE_SHA,
                   only_run_ids=seen)
    # Original records themselves remain untouched; report metadata records
    # the runtime source revision without mistaking it for every underlying
    # agent model config or for verified historical feature publication clocks.
    report["observer_record_schema_version"]=VERSION
    report["railway_deployed_commit"]=(
        os.environ.get("RAILWAY_GIT_COMMIT_SHA") or None
    )
    report["per_agent_original_model_version_verified"]=False
    report["external_append_only_archive_verified"]=False
    for record in report.pop("genuine_future_shadow_records"):
        log("TENNIS_FROZEN_PROSPECTIVE_CANDIDATE "+
            json.dumps(record,sort_keys=True))
    log("TENNIS_FROZEN_PROSPECTIVE_SUMMARY "+
        json.dumps(report,sort_keys=True))
    return report
