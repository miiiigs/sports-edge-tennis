"""Pure research-only Agent3 1/3/4-year history-window comparison.

Wang & Drekic motivated comparing historical support windows, but our live A3
already uses FOUR years. This module filters an ALREADY FROZEN, genuinely
pre-match A3 snapshot at its original prediction cutoff and reruns the locked
production mathematics unchanged. It does not relax support gates or provide
a new neural/Markov model; all variants are tagged experimental.

IMPORTANT: this is not a history-reconstruction engine. Input snapshot must
have been frozen before the fixture start with immutable source provenance.
Never pass retrospective backfilled features or a snapshot built after result.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Any

from sports_edge_tennis.agents.agent3_intransitivity import (
    DEFAULT_CONFIG,
    Agent3HistorySnapshot,
    PairHistorySnapshot,
    PlayerHistorySnapshot,
    PredictionRequest,
    PredictionResult,
    predict,
)

VERSION="A3_FROZEN_PIT_HISTORY_WINDOWS_SHADOW_V1"
WINDOW_DAYS=(365,1095,1460)


def _utc(moment:datetime)->datetime:
    if moment.tzinfo is None:
        raise ValueError("snapshot cutoff and observation timestamps must be timezone aware")
    return moment.astimezone(UTC)


def _in_window(observation:Any, *,cutoff:datetime,days:int)->bool:
    when=_utc(observation.observed_at)
    return cutoff-timedelta(days=days)<=when<cutoff



def _validate_original_snapshot(snapshot:Agent3HistorySnapshot, cutoff:datetime)->None:
    """Require past-only observations before touching production math."""
    def check(observations)->None:
        for observation in observations:
            if _utc(observation.observed_at)>=cutoff:
                raise ValueError("snapshot includes future or cutoff-time observation")
    for player in snapshot.players.values():
        check(player.all_observations)
        for records in player.surface_observations.values():
            check(records)
    for pair in snapshot.pairs.values():
        check(pair.all_observations)
        for records in pair.surface_observations.values():
            check(records)

def filter_snapshot(snapshot:Agent3HistorySnapshot, *,cutoff:datetime,
                    days:int)->Agent3HistorySnapshot:
    """Returns a new snapshot. Does NOT mutate original production state."""
    cutoff=_utc(cutoff)
    if days not in WINDOW_DAYS:
        raise ValueError("only registered 365,1095,1460 day windows are allowed")
    players={}
    for pid,history in snapshot.players.items():
        all_obs=tuple(x for x in history.all_observations
                      if _in_window(x,cutoff=cutoff,days=days))
        per_surface={
            surface:retained
            for surface, observations in history.surface_observations.items()
            if (retained:=tuple(x for x in observations
                                if _in_window(x,cutoff=cutoff,days=days)))
        }
        if all_obs:
            players[pid]=PlayerHistorySnapshot(
                player_id=pid,all_observations=all_obs,
                surface_observations=per_surface,
            )
    pairs={}
    neighbors={}
    for (low,high),history in snapshot.pairs.items():
        all_obs=tuple(x for x in history.all_observations
                      if _in_window(x,cutoff=cutoff,days=days))
        if not all_obs:
            continue
        per_surface={
            surface:retained
            for surface,observations in history.surface_observations.items()
            if (retained:=tuple(x for x in observations
                                if _in_window(x,cutoff=cutoff,days=days)))
        }
        pairs[(low,high)]=PairHistorySnapshot(
            player_low_id=low,player_high_id=high,
            all_observations=all_obs,surface_observations=per_surface,
        )
        neighbors.setdefault(low,set()).add(high)
        neighbors.setdefault(high,set()).add(low)
    return Agent3HistorySnapshot(
        players=players,pairs=pairs,
        adjacency={pid:tuple(sorted(others))
                   for pid,others in sorted(neighbors.items())},
    )


def _result_payload(result:PredictionResult)->dict[str,Any]:
    return {
        "status":result.prediction_status,
        "selected_side":result.selected_side,
        "p1":str(result.player_1_probability)
             if result.player_1_probability is not None else None,
        "p2":str(result.player_2_probability)
             if result.player_2_probability is not None else None,
        "abstain":result.abstain_reason.value if result.abstain_reason else None,
        "matched_effective_direct":
            str(result.diagnostics.direct_effective_count_all),
        "matched_shared_opponents":result.diagnostics.network_shared_opponents_all,
        "matched_q":str(result.diagnostics.network_q_all),
        "model_version":result.model_version,
    }


def compare_locked_windows(request:PredictionRequest, *,issue_at:datetime,
                           match_start_at:datetime,
                           generated_at:datetime|None=None)->dict[str,Any]:
    """Shadow calculation only if original data as-of and issue times are safe.

    The requested fixture must still be in the future relative to any actual
    live shadow generation. For original historical replay experiments this
    cannot be used to invent a historical issue time.
    """
    issue_at,match_start_at=map(_utc,(issue_at,match_start_at))
    cutoff=_utc(request.as_of)
    generated_at=_utc(generated_at) if generated_at else datetime.now(UTC)
    if issue_at<cutoff or generated_at>issue_at:
        raise ValueError("issue must follow frozen history cutoff and actual computation")
    if match_start_at-issue_at<timedelta(minutes=30):
        raise ValueError("shadow generated or issued too late for original fixture")
    if not request.pit_state_resolved or not request.supported_matchup:
        raise ValueError("unresolved original pre-match state")
    # The caller must additionally prove source-level ingestion before the
    # cutoff. The snapshot itself does not contain source ingestion metadata.
    _validate_original_snapshot(request.history_snapshot,cutoff)
    baseline=predict(request,DEFAULT_CONFIG)
    variants={}
    for days in WINDOW_DAYS:
        reduced=filter_snapshot(request.history_snapshot,cutoff=cutoff,days=days)
        shadow_request=PredictionRequest(
            player_1_id=request.player_1_id,
            player_2_id=request.player_2_id,
            surface=request.surface,
            history_snapshot=reduced,
            as_of=cutoff,
            pit_state_resolved=request.pit_state_resolved,
            supported_matchup=request.supported_matchup,
        )
        variants[str(days)]=_result_payload(predict(shadow_request,DEFAULT_CONFIG))
    base=_result_payload(baseline)
    signature=sha256((
        f"{VERSION}|{request.player_1_id}|{request.player_2_id}|"
        f"{cutoff.isoformat()}|{generated_at.isoformat()}|"
        f"{match_start_at.isoformat()}|{sorted(variants.items())}"
    ).encode()).hexdigest()
    return {
        "version":VERSION,"snapshot_cutoff_utc":cutoff.isoformat(),
        "generated_at_utc":generated_at.isoformat(),
        "issue_at_utc":issue_at.isoformat(),
        "scheduled_start_utc":match_start_at.isoformat(),
        "data_signature_sha256":signature,
        "production_lookback_days":DEFAULT_CONFIG.lookback_days,
        "production_half_life_days":DEFAULT_CONFIG.half_life_days,
        "original_baseline":base,
        "experimental_1y_3y_4y":variants,
        "four_year_parity_with_baseline":variants["1460"]==base,
        "no_live_prediction_written":True,
        "no_new_data_or_odds_calls":True,
        "experimental_forecasts_not_model_picks":True,
        "not_outcome_validated":True,
        "not_independent_full_slate_coverage":True,
        "caveat":(
            "All three use the SAME locked Agent3 parameters and different "
            "history-window evidence. Only a verifiably PIT-frozen snapshot "
            "can produce honest pre-match shadow outputs; no future outcomes "
            "or retrospective recomputation may be represented as originally issued."
        ),
    }
