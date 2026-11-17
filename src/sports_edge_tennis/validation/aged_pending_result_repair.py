"""Two-phase, source-verified, append-only repair for the 17 aged Tennis results.

Default mode is a strict dry-run preview with zero writes.  Apply requires
the exact preview digest, all 17 canonical identities verified, a production
batch lock and one atomic transaction.  No predictions or policy are changed.
Nothing here guesses an outcome for unknown provider status or missing mapping.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import psycopg

from sports_edge_tennis.data.sources.api_tennis_live import (
    _accepted_api_tennis_tournament_ids,
    ingest_api_tennis_source_a_fixtures,
)
from sports_edge_tennis.live.api_tennis_fixture_compat import (
    enrich_api_tennis_fixture_surfaces, normalize_api_tennis_fixture_rounds,
    normalize_api_tennis_upcoming_status,
)
from sports_edge_tennis.validation.aged_pending_provider_audit import _windows, MAX_CALLS
from sports_edge_tennis.live.credentials import require_api_tennis_key
from sports_edge_tennis.live.history_refresh import (
    HistoryRefreshError, _invalidate_model_checkpoint,
    _latest_result_semantic_key, _persist_terminal_results,
    fetch_api_tennis_fixture_window, parse_terminal_result,
)
from sports_edge_tennis.live.pending_result_diagnostics import (
    FLAG_OUTSIDE_RECOVERY_WINDOW, load_pending_result_diagnostics,
)
from sports_edge_tennis.live.production_lock import (
    acquire_production_batch_lock, release_production_batch_lock,
)
from sports_edge_tennis.live.transport import TransportError

EXPECTED_RECOVERABLE = 17


@dataclass(frozen=True)
class Recoverable:
    match_id: int
    event_key: str
    status: str
    winner_side: int | None
    canonical_player_1_id: int
    canonical_player_2_id: int
    canonical_tournament_id: int
    raw: dict[str, Any]


def _text(value: Any) -> str:
    return str(value or "").strip()


def _emit(tag: str, **fields: object) -> None:
    print(tag + " " + json.dumps(fields, sort_keys=True, separators=(",", ":")), flush=True)


def _validate_identity(conn: psycopg.Connection, match_id: int,
                       event_key: str, raw: dict[str, Any]) -> tuple[int,int,int] | None:
    """Proof must already exist before any source writer is invoked."""
    rows = conn.execute(
        """SELECT DISTINCT match_id FROM match_source_ids
           WHERE source_system='API_TENNIS' AND source_match_id=%s""",
        (event_key,),
    ).fetchall()
    if len(rows) != 1 or int(rows[0][0]) != match_id:
        return None
    mv = conn.execute(
        """SELECT mv.player_1_id, mv.player_2_id, te.tournament_id
           FROM match_versions mv
           JOIN matches m ON m.match_id=mv.match_id
           JOIN tournament_editions te ON te.tournament_edition_id=m.tournament_edition_id
           WHERE mv.match_id=%s
           ORDER BY mv.as_of DESC, mv.source_observed_at DESC,
                    mv.match_version_id DESC LIMIT 1""",
        (match_id,),
    ).fetchone()
    if mv is None:
        return None
    p1,p2,tournament_id = map(int,mv)
    provider_player_ids = []
    for field in ("first_player_key", "second_player_key"):
        src_key = _text(raw.get(field))
        if not src_key:
            return None
        ids = conn.execute(
            """SELECT player_id FROM player_source_ids
               WHERE source_system='API_TENNIS' AND source_player_id=%s""",
            (src_key,),
        ).fetchall()
        if len(ids) != 1:
            return None
        provider_player_ids.append(int(ids[0][0]))
    if tuple(provider_player_ids) != (p1,p2):
        return None
    source_tournament = _text(raw.get("tournament_key"))
    if not source_tournament:
        return None
    accepted = _accepted_api_tennis_tournament_ids(conn, source_tournament)
    if tournament_id not in accepted:
        return None
    return p1,p2,tournament_id


def _digest(candidates: list[Recoverable]) -> str:
    data = [
        (c.match_id,c.event_key,c.status,c.winner_side,
         c.canonical_player_1_id,c.canonical_player_2_id,
         c.canonical_tournament_id,
         # Freeze the sporting result and raw identity evidence, not merely
         # winner/status. Any provider change between preview and apply must
         # invalidate the operator-reviewed digest.
         *(_text(c.raw.get(field)) for field in (
             "event_date", "event_time", "event_status", "event_final_result",
             "first_player_key", "second_player_key", "tournament_key",
         )))
        for c in sorted(candidates,key=lambda x:x.match_id)
    ]
    return hashlib.sha256(
        json.dumps(data,sort_keys=True,separators=(",",":")).encode()
    ).hexdigest()


def _candidate(raw: dict[str,Any], *, conn: psycopg.Connection,
               match_id: int, event_key: str) -> tuple[Recoverable | None, str]:
    try:
        terminal=parse_terminal_result(raw)
    except HistoryRefreshError:
        return None,"INVALID_TERMINAL_WINNER"
    if terminal is None:
        return None,"NOT_TERMINAL"
    if terminal.outcome_status not in {"COMPLETED","RETIRED","WALKOVER"}:
        return None,"VOID_OR_CANCELLED_POLICY_REVIEW"
    if terminal.outcome_status == "WALKOVER" and terminal.winner_side is None:
        return None,"WINNERLESS_WALKOVER_REVIEW"
    if _latest_result_semantic_key(conn,match_id) is not None:
        return None,"RESULT_ALREADY_PRESENT"
    proof=_validate_identity(conn,match_id,event_key,raw)
    if proof is None:
        return None,"IDENTITY_NOT_PROVEN"
    p1,p2,tournament_id=proof
    return Recoverable(
        match_id=match_id,event_key=event_key,
        status=terminal.outcome_status,winner_side=terminal.winner_side,
        canonical_player_1_id=p1,canonical_player_2_id=p2,
        canonical_tournament_id=tournament_id,raw=raw
    ),"PROVEN"


def _collect(conn: psycopg.Connection, *, key: str, now: datetime) -> tuple[list[Recoverable],dict[str,int],int]:
    conn.execute("SET default_transaction_read_only=on")
    report=load_pending_result_diagnostics(
        conn,now=now,lookback_days=400,limit=2000,
    )
    old=[
        item for item in report.items
        if FLAG_OUTSIDE_RECOVERY_WINDOW in item.flags
        and item.evidence.latest_match_start_at is not None
    ]
    days={
        item.evidence.latest_match_start_at.date() for item in old
        if item.evidence.api_tennis_event_key
    }
    windows=_windows(days)
    if len(windows)>MAX_CALLS:
        raise ValueError("API request cap exceeded")
    events:dict[str,list[dict[str,Any]]]={}
    for low,high in windows:
        received,rows=fetch_api_tennis_fixture_window(key,low,high,timeout=45)
        for row in rows:
            if isinstance(row,dict) and row.get("event_key"):
                events.setdefault(_text(row["event_key"]),[]).append(row)
        _emit("AGED_REPAIR_PROVIDER_WINDOW",start=low.isoformat(),end=high.isoformat(),
              observed_at=received.isoformat(),rows=len(rows))
    candidates:list[Recoverable]=[]
    reasons:dict[str,int]={}
    for item in old:
        e=item.evidence
        if not e.api_tennis_event_key:
            reason="MISSING_EVENT_LINK"
            candidate=None
        else:
            matches=events.get(e.api_tennis_event_key,[])
            if len(matches)!=1:
                reason="MISSING_EVENT" if not matches else "AMBIGUOUS_EVENT"
                candidate=None
            else:
                candidate,reason=_candidate(
                    matches[0],conn=conn,match_id=e.match_id,
                    event_key=e.api_tennis_event_key,
                )
        reasons[reason]=reasons.get(reason,0)+1
        _emit("AGED_REPAIR_ITEM",match_id=e.match_id,
              provider_event=e.api_tennis_event_key,decision=reason,
              outcome=candidate.status if candidate else None,
              winner_side=candidate.winner_side if candidate else None)
        if candidate is not None:
            candidates.append(candidate)
    return candidates,reasons,len(windows)


def _apply(conn:psycopg.Connection,candidates:list[Recoverable]) -> int:
    if len(candidates)!=EXPECTED_RECOVERABLE:
        raise ValueError("candidate count changed")
    event_keys={c.event_key for c in candidates}
    match_ids={c.match_id for c in candidates}
    if len(event_keys)!=len(candidates) or len(match_ids)!=len(candidates):
        raise ValueError("nonunique canonical candidates")
    with conn.transaction():
        now=datetime.now(UTC)
        prepared=normalize_api_tennis_upcoming_status(
            [c.raw for c in candidates],received_at=now
        )
        prepared=normalize_api_tennis_fixture_rounds(prepared)
        prepared=enrich_api_tennis_fixture_surfaces(conn,prepared,api_key=None)
        # All IDs were verified while read-only. Never allow a writer to invent
        # a canonical match if source identity or provider metadata differs.
        outcome=ingest_api_tennis_source_a_fixtures(
            conn,prepared,received_at=now
        )
        if outcome.rows_rejected or len(outcome.matches)!=len(candidates):
            raise ValueError("fixture ingest did not preserve all candidates")
        got={x.source_match_id:x.match_id for x in outcome.matches}
        if got!={c.event_key:c.match_id for c in candidates}:
            raise ValueError("canonical identity drift on ingestion")
        seen,inserted,unchanged,rejected=_persist_terminal_results(
            conn,fixtures=prepared,ingested_matches=outcome.matches,received_at=now,
        )
        if seen!=EXPECTED_RECOVERABLE or inserted!=EXPECTED_RECOVERABLE or unchanged or rejected:
            raise ValueError("result insertion was incomplete; transaction rolled back")
        _invalidate_model_checkpoint(conn,"AGENT_3_INTRANSITIVITY")
    return len(candidates)


def main(argv:list[str]|None=None)->int:
    parser=argparse.ArgumentParser()
    parser.add_argument("--apply",action="store_true")
    parser.add_argument("--expected-sha",default="")
    args=parser.parse_args(argv)
    dsn=os.environ.get("TENNIS_DATABASE_URL")
    if not dsn:
        _emit("AGED_REPAIR_BLOCKED",reason="MISSING_DATABASE")
        return 2
    if args.apply and len(args.expected_sha)!=64:
        _emit("AGED_REPAIR_BLOCKED",reason="MISSING_PREVIEW_SHA")
        return 2
    key=require_api_tennis_key()
    lock=None
    # For write mode, lock before fetching and validate the current pending
    # cohort while holding the same production owner lock as the hourly runner.
    if args.apply:
        lock=acquire_production_batch_lock(dsn)
        if lock is None:
            _emit("AGED_REPAIR_BLOCKED",reason="PRODUCTION_BATCH_ACTIVE")
            return 2
    try:
        with psycopg.connect(dsn,autocommit=True,connect_timeout=15) as conn:
            now=datetime.now(UTC)
            candidates,reasons,requests_made=_collect(conn,key=key,now=now)
            preview=_digest(candidates)
            _emit("AGED_REPAIR_PREVIEW",snapshot=now.isoformat(),
                  candidate_count=len(candidates),expected_count=EXPECTED_RECOVERABLE,
                  evidence_sha=preview,reason_counts=reasons,
                  requests_made=requests_made,
                  write_mode=args.apply)
            if not args.apply:
                _emit("AGED_REPAIR_DRY_RUN",writes=0)
                return 0
            if preview!=args.expected_sha:
                _emit("AGED_REPAIR_BLOCKED",reason="PREVIEW_SHA_DRIFT",writes=0)
                return 2
            if len(candidates)!=EXPECTED_RECOVERABLE:
                _emit("AGED_REPAIR_BLOCKED",reason="INCOMPLETE_CANONICAL_PROOF",writes=0)
                return 2
            # Connection was read-only for preview. Only after all gates, the
            # authorized apply can switch session to transactional writes.
            conn.execute("SET default_transaction_read_only=off")
            inserted=_apply(conn,candidates)
            _emit("AGED_REPAIR_APPLIED",inserted=inserted,source="API_TENNIS",
                  point_in_time_receive_not_backdated=True)
            return 0
    except (psycopg.Error,TransportError,ValueError) as exc:
        _emit("AGED_REPAIR_FAILED",type=type(exc).__name__,
              no_inferred_results=True)
        return 2
    finally:
        if lock is not None:
            release_production_batch_lock(lock)


if __name__=="__main__":
    raise SystemExit(main())
