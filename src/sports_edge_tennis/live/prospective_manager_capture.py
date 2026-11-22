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
from datetime import UTC,datetime,timedelta
from typing import Callable

import psycopg

from sports_edge_tennis.validation.prospective_manager_shadow_capture import run

POLICY_FROZEN_SOURCE_SHA="177ec8146561c4e06d28e3e490dad38421388035"
OBSERVATION_HORIZON_DAYS=4


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
    for record in report.pop("genuine_future_shadow_records"):
        log("TENNIS_FROZEN_PROSPECTIVE_CANDIDATE "+
            json.dumps(record,sort_keys=True))
    log("TENNIS_FROZEN_PROSPECTIVE_SUMMARY "+
        json.dumps(report,sort_keys=True))
    return report
