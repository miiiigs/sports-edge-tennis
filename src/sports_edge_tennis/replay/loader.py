"""Read-only Postgres loader for the official replay rerun path.

Every query is read-only and applies the universal point-in-time rule

    source_observed_at <= cutoff AND as_of <= cutoff

with ``ingested_at`` deliberately excluded. The loader builds
:class:`~sports_edge_tennis.replay.contracts.ReplayRunInput` objects for the pure
engine; it never writes production decision tables, checkpoint caches, or any
replay rows.

The loader deliberately reuses the same PIT data-access modules that the live
system uses (``runner.targets``, ``data.agent*_history``, ``market.persistence``,
``veto.service``) so that replay inputs are not a second, drifting source of
truth.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Sequence

import psycopg

from sports_edge_tennis.agents.agent1_surface_elo import (
    DEFAULT_CONFIG as AGENT1_DEFAULT_CONFIG,
    replay_history as replay_agent1_history,
)
from sports_edge_tennis.agents.agent2_hierarchical_markov import (
    DEFAULT_CONFIG as AGENT2_DEFAULT_CONFIG,
)
from sports_edge_tennis.agents.agent3_intransitivity import (
    DEFAULT_CONFIG as AGENT3_DEFAULT_CONFIG,
)
from sports_edge_tennis.data.agent1_history import load_agent1_history
from sports_edge_tennis.data.agent2_history import (
    build_history_snapshot as build_agent2_snapshot,
    load_agent2_history,
)
from sports_edge_tennis.data.agent3_history import load_agent3_history
from sports_edge_tennis.manager.contracts import ManagerPolicyV1
from sports_edge_tennis.market.contracts import (
    MARKET_POLICY_KEY,
    MARKET_POLICY_VERSION,
    MarketPolicyV1,
    OddsSnapshotV1,
    SharpReferenceSpec,
)
from sports_edge_tennis.market.persistence import (
    load_competition_tier,
    load_market_policy_version,
    load_match_winner_odds,
)
from sports_edge_tennis.market.staking import STAKING_RULE_VERSION
from sports_edge_tennis.replay.contracts import (
    PitInputProof,
    ReplayActualResult,
    ReplayDecisionTime,
    ReplayMatchInput,
    ReplayRunInput,
)
from sports_edge_tennis.replay.serde import payload_sha256
from sports_edge_tennis.runner.agent3_live_snapshot import (
    build_live_agent3_history_snapshot,
)
from sports_edge_tennis.runner.contracts import ResolvedTargetV1, TargetResolutionError
from sports_edge_tennis.runner.targets import (
    _has_non_api_source_identity,
    resolve_target,
)
from sports_edge_tennis.veto.contracts import VetoContext
from sports_edge_tennis.veto.service import (
    VETO_POLICY_VERSION,
    VetoEvidenceError,
    _load_previous_match,
    _load_sharp_price_at,
    _load_target_context,
)

PINNACLE_SHARP = SharpReferenceSpec(source_system="SHARP", bookmaker_code="PINNACLE")


class ReplayLoadError(RuntimeError):
    """Raised when replay inputs cannot be loaded safely (fail closed)."""


@dataclass(frozen=True)
class ReplayDecisionTimePolicy:
    """Derive the two PIT cutoffs for a match from its scheduled start time."""

    prediction_lead: timedelta = timedelta(hours=6)
    market_lead: timedelta = timedelta(hours=1)

    def resolve(self, match_start_at: datetime | None) -> ReplayDecisionTime | None:
        if match_start_at is None:
            return None
        if match_start_at.tzinfo is None:
            raise ReplayLoadError("match_start_at must be timezone-aware")
        prediction_as_of = match_start_at - self.prediction_lead
        market_as_of = match_start_at - self.market_lead
        if market_as_of < prediction_as_of:
            raise ReplayLoadError("market cutoff must not precede prediction cutoff")
        return ReplayDecisionTime(
            prediction_as_of=prediction_as_of,
            market_as_of=market_as_of,
            source="MATCH_START_MINUS_LEAD",
        )


@dataclass(frozen=True)
class ReplayRunSpec:
    """One replayed evaluation pass with explicit, caller-controlled cutoffs."""

    run_id: str
    run_sequence: int
    prediction_as_of: datetime
    market_as_of: datetime
    match_ids: tuple[int, ...]


@dataclass(frozen=True)
class ReplayLoadRequest:
    run_specs: tuple[ReplayRunSpec, ...]
    result_as_of: datetime
    manager_policy_version: str = "v1"
    market_policy_version: str = MARKET_POLICY_VERSION
    sharp_reference: SharpReferenceSpec = PINNACLE_SHARP
    decision_time_policy: ReplayDecisionTimePolicy = ReplayDecisionTimePolicy()


@dataclass(frozen=True)
class LoadedReplayInputs:
    runs: tuple[ReplayRunInput, ...]
    input_snapshot_ids: tuple[str, ...]


def load_manager_policy(conn: psycopg.Connection, *, policy_version: str) -> ManagerPolicyV1:
    row = conn.execute(
        """
        SELECT
            manager_policy_version_id,
            policy_key,
            policy_version,
            three_agent_max_probability_spread,
            agent3_abstention_max_probability_spread,
            min_active_agent_probability,
            allow_agent3_abstention
        FROM manager_policy_versions
        WHERE policy_key = 'tennis_manager_consensus'
          AND policy_version = %s
        """,
        (policy_version,),
    ).fetchone()
    if row is None:
        raise ReplayLoadError(
            f"manager policy version {policy_version!r} is not present; replay is read-only"
        )
    return ManagerPolicyV1(*row)


def load_market_policy(conn: psycopg.Connection, *, policy_version: str) -> MarketPolicyV1:
    row = conn.execute(
        """
        SELECT market_policy_version_id
        FROM market_policy_versions
        WHERE policy_key = %s AND policy_version = %s
        """,
        (MARKET_POLICY_KEY, policy_version),
    ).fetchone()
    if row is None:
        raise ReplayLoadError(
            f"market policy version {policy_version!r} is not present; replay is read-only"
        )
    return load_market_policy_version(conn, market_policy_version_id=int(row[0]))


def _load_match_start_at(
    conn: psycopg.Connection,
    *,
    match_id: int,
    as_of: datetime,
) -> datetime | None:
    row = conn.execute(
        """
        SELECT match_start_at
        FROM match_versions
        WHERE match_id = %s
          AND source_observed_at <= %s
          AND as_of <= %s
        ORDER BY as_of DESC, source_observed_at DESC, match_version_id DESC
        LIMIT 1
        """,
        (match_id, as_of, as_of),
    ).fetchone()
    return row[0] if row is not None else None


def _load_actual_result(
    conn: psycopg.Connection,
    *,
    match_id: int,
    result_as_of: datetime,
) -> ReplayActualResult | None:
    row = conn.execute(
        """
        SELECT
            match_result_observation_id,
            outcome_status::text,
            winner_side,
            source_observed_at,
            as_of
        FROM match_result_observations
        WHERE match_id = %s
          AND source_observed_at <= %s
          AND as_of <= %s
        ORDER BY as_of DESC, source_observed_at DESC, match_result_observation_id DESC
        LIMIT 1
        """,
        (match_id, result_as_of, result_as_of),
    ).fetchone()
    if row is None:
        return None
    visible_at = max(row[3], row[4])
    return ReplayActualResult(
        match_id=match_id,
        outcome_status=str(row[1]),
        winner_side=int(row[2]) if row[2] is not None else None,
        result_observation_id=int(row[0]),
        result_visible_at=visible_at,
    )


def _identity_status(
    conn: psycopg.Connection,
    *,
    target: ResolvedTargetV1,
    match_version_id: int,
) -> str:
    row = conn.execute(
        "SELECT player_1_id, player_2_id FROM match_versions WHERE match_version_id = %s",
        (match_version_id,),
    ).fetchone()
    if row is None:
        return "LIVE_UNRESOLVED"
    live_ids = (int(row[0]), int(row[1]))
    model_ids = (target.player_1_id, target.player_2_id)
    if model_ids != live_ids:
        return "BRIDGED"
    if _has_non_api_source_identity(conn, live_ids[0]) and _has_non_api_source_identity(
        conn, live_ids[1]
    ):
        return "HISTORICAL"
    return "LIVE_UNRESOLVED"


def _odds_proof(
    *,
    match_id: int,
    cutoff: datetime,
    odds_snapshots: Sequence[OddsSnapshotV1],
) -> PitInputProof:
    observed = [snapshot.source_observed_at for snapshot in odds_snapshots if snapshot.source_observed_at]
    valid = [snapshot.as_of for snapshot in odds_snapshots]
    snapshot_hash = payload_sha256(
        [
            {
                "odds_snapshot_id": snapshot.odds_snapshot_id,
                "source_system": snapshot.source_system,
                "bookmaker_code": snapshot.bookmaker_code,
                "source_observed_at": snapshot.source_observed_at,
                "as_of": snapshot.as_of,
            }
            for snapshot in odds_snapshots
        ]
    )
    return PitInputProof(
        kind="ODDS",
        cutoff=cutoff,
        max_source_observed_at=max(observed) if observed else None,
        max_as_of=max(valid) if valid else None,
        evidence_count=len(odds_snapshots),
        snapshot_sha256=snapshot_hash,
        pit_complete=True,
    )


def _build_veto_context(
    conn: psycopg.Connection,
    *,
    match_id: int,
    selected_side: int,
    current_sharp_reference_price,
    prediction_as_of: datetime,
    market_as_of: datetime,
    sharp_reference: SharpReferenceSpec,
) -> VetoContext | None:
    try:
        p1, p2, target_start, current_status, fixture_observed_at = _load_target_context(
            conn, match_id=match_id, as_of=market_as_of
        )
    except VetoEvidenceError:
        return None

    previous = tuple(
        evidence
        for evidence in (
            _load_previous_match(
                conn,
                player_id=p1,
                player_side_in_target=1,
                target_match_id=match_id,
                target_start_at=target_start,
                as_of=market_as_of,
            ),
            _load_previous_match(
                conn,
                player_id=p2,
                player_side_in_target=2,
                target_match_id=match_id,
                target_start_at=target_start,
                as_of=market_as_of,
            ),
        )
        if evidence is not None
    )

    baseline = _load_sharp_price_at(
        conn,
        match_id=match_id,
        selected_side=selected_side,
        cutoff=prediction_as_of,
        sharp_reference=sharp_reference,
    )
    sharp_move = None
    if (
        baseline is not None
        and current_sharp_reference_price is not None
        and baseline > 0
    ):
        sharp_move = (current_sharp_reference_price / baseline) - 1

    return VetoContext(
        match_id=match_id,
        prediction_run_id=0,
        selected_side=selected_side,
        decision_as_of=market_as_of,
        target_start_at=target_start,
        current_match_status=current_status,
        fixture_source_observed_at=fixture_observed_at,
        previous_matches=previous,
        sharp_move_against_pct=sharp_move,
    )


def _make_veto_factory(
    conn: psycopg.Connection,
    *,
    prediction_as_of: datetime,
    market_as_of: datetime,
    sharp_reference: SharpReferenceSpec,
):
    def factory(match: ReplayMatchInput, decision) -> VetoContext | None:
        if decision.selected_side not in (1, 2):
            return None
        return _build_veto_context(
            conn,
            match_id=match.target.match_id,
            selected_side=decision.selected_side,
            current_sharp_reference_price=decision.sharp_reference_price,
            prediction_as_of=prediction_as_of,
            market_as_of=market_as_of,
            sharp_reference=sharp_reference,
        )

    return factory


def _load_match_input(
    conn: psycopg.Connection,
    *,
    match_id: int,
    prediction_as_of: datetime,
    market_as_of: datetime,
    result_as_of: datetime,
    sharp_reference: SharpReferenceSpec,
) -> ReplayMatchInput:
    try:
        target = resolve_target(conn, match_id=match_id, as_of=prediction_as_of)
    except TargetResolutionError as exc:
        raise ReplayLoadError(str(exc)) from exc

    surface_status = "RESOLVED" if target.surface is not None else "AMBIGUOUS_HISTORY"
    identity_status = _identity_status(
        conn, target=target, match_version_id=target.match_version_id
    )

    competition_tier = load_competition_tier(
        conn, match_id=match_id, decision_as_of=market_as_of
    )
    odds_snapshots = tuple(
        load_match_winner_odds(conn, match_id=match_id, decision_as_of=market_as_of)
    )
    proof = _odds_proof(
        match_id=match_id, cutoff=market_as_of, odds_snapshots=odds_snapshots
    )

    return ReplayMatchInput(
        target=target,
        competition_tier=competition_tier,
        odds_snapshots=odds_snapshots,
        odds_proof=proof,
        veto_context=None,
        actual_result=_load_actual_result(
            conn, match_id=match_id, result_as_of=result_as_of
        ),
        provider_event_ids=(),
        identity_status=identity_status,
        surface_status=surface_status,
    )


def _snapshot_cutoff(observations, attribute: str) -> datetime | None:
    values = [getattr(observation, attribute) for observation in observations]
    values = [value for value in values if value is not None]
    return max(values) if values else None


def load_replay_inputs(
    conn: psycopg.Connection,
    request: ReplayLoadRequest,
) -> LoadedReplayInputs:
    """Load all PIT-correct inputs for the requested replay runs."""

    manager_policy = load_manager_policy(
        conn, policy_version=request.manager_policy_version
    )
    market_policy = load_market_policy(
        conn, policy_version=request.market_policy_version
    )

    runs: list[ReplayRunInput] = []
    snapshot_ids: list[str] = []

    for spec in request.run_specs:
        agent1_observations = load_agent1_history(conn, cutoff=spec.prediction_as_of)
        agent1_replay = replay_agent1_history(
            agent1_observations, spec.prediction_as_of, AGENT1_DEFAULT_CONFIG
        )

        agent2_observations = load_agent2_history(conn, cutoff=spec.prediction_as_of)
        agent2_snapshot = build_agent2_snapshot(
            agent2_observations, cutoff=spec.prediction_as_of
        )

        agent3_days = (AGENT3_DEFAULT_CONFIG.lookback_days * 2) + 2
        agent3_observations = load_agent3_history(
            conn,
            cutoff=spec.prediction_as_of,
            date_start=(spec.prediction_as_of - timedelta(days=agent3_days)).date(),
        )
        agent3_snapshot = build_live_agent3_history_snapshot(
            agent3_observations, cutoff=spec.prediction_as_of
        )

        a1_id = payload_sha256(
            {
                "kind": "AGENT_1",
                "cutoff": spec.prediction_as_of,
                "count": len(agent1_observations),
                "max_observed": _snapshot_cutoff(agent1_observations, "source_observed_at"),
            }
        )
        a2_id = payload_sha256(
            {
                "kind": "AGENT_2",
                "cutoff": spec.prediction_as_of,
                "count": len(agent2_observations),
                "max_visible": _snapshot_cutoff(agent2_observations, "result_as_of"),
            }
        )
        a3_id = payload_sha256(
            {
                "kind": "AGENT_3",
                "cutoff": spec.prediction_as_of,
                "count": len(agent3_observations),
                "max_visible": _snapshot_cutoff(agent3_observations, "result_as_of"),
            }
        )
        snapshot_ids.extend((a1_id, a2_id, a3_id))

        matches: list[ReplayMatchInput] = []
        for match_id in spec.match_ids:
            matches.append(
                _load_match_input(
                    conn,
                    match_id=match_id,
                    prediction_as_of=spec.prediction_as_of,
                    market_as_of=spec.market_as_of,
                    result_as_of=request.result_as_of,
                    sharp_reference=request.sharp_reference,
                )
            )

        runs.append(
            ReplayRunInput(
                run_id=spec.run_id,
                run_sequence=spec.run_sequence,
                decision_time=ReplayDecisionTime(
                    prediction_as_of=spec.prediction_as_of,
                    market_as_of=spec.market_as_of,
                    source="CALLER_SPEC",
                ),
                manager_policy=manager_policy,
                market_policy=market_policy,
                sharp_reference=request.sharp_reference,
                matches=tuple(matches),
                agent1_replay=agent1_replay,
                agent2_snapshot=agent2_snapshot,
                agent3_snapshot=agent3_snapshot,
                agent1_snapshot_sha256=a1_id,
                agent2_snapshot_sha256=a2_id,
                agent3_snapshot_sha256=a3_id,
                veto_context_factory=_make_veto_factory(
                    conn,
                    prediction_as_of=spec.prediction_as_of,
                    market_as_of=spec.market_as_of,
                    sharp_reference=request.sharp_reference,
                ),
            )
        )

    return LoadedReplayInputs(runs=tuple(runs), input_snapshot_ids=tuple(snapshot_ids))


def derive_run_specs_from_match_days(
    conn: psycopg.Connection,
    *,
    match_ids: tuple[int, ...],
    policy: ReplayDecisionTimePolicy = ReplayDecisionTimePolicy(),
    window_as_of: datetime | None = None,
) -> tuple[ReplayRunSpec, ...]:
    """Plan one run per UTC match day using match-start-minus-lead cutoffs.

    A match without a PIT-visible ``match_start_at`` cannot establish a safe
    historical decision time and is omitted; the caller treats it as
    ``DATA_NOT_AVAILABLE_PIT``.
    """

    window_as_of = window_as_of or datetime.now(timezone.utc)

    by_day: dict[date, list[int]] = {}
    for match_id in match_ids:
        match_start_at = _load_match_start_at(conn, match_id=match_id, as_of=window_as_of)
        if match_start_at is None:
            continue
        by_day.setdefault(match_start_at.astimezone(timezone.utc).date(), []).append(match_id)

    specs: list[ReplayRunSpec] = []
    for sequence, day in enumerate(sorted(by_day), start=1):
        day_matches = sorted(by_day[day])
        day_start = datetime.combine(day, time(0, 0), tzinfo=timezone.utc)
        # A single coherent hourly-style pass per slate day, at the median lead.
        prediction_as_of = day_start + timedelta(hours=12) - policy.prediction_lead
        market_as_of = day_start + timedelta(hours=12) - policy.market_lead
        specs.append(
            ReplayRunSpec(
                run_id=f"replay-{day.isoformat()}",
                run_sequence=sequence,
                prediction_as_of=prediction_as_of,
                market_as_of=market_as_of,
                match_ids=tuple(day_matches),
            )
        )
    return tuple(specs)


__all__ = [
    "PINNACLE_SHARP",
    "ReplayLoadError",
    "ReplayDecisionTimePolicy",
    "ReplayRunSpec",
    "ReplayLoadRequest",
    "LoadedReplayInputs",
    "load_manager_policy",
    "load_market_policy",
    "load_replay_inputs",
    "derive_run_specs_from_match_days",
    "VETO_POLICY_VERSION",
    "STAKING_RULE_VERSION",
    "AGENT1_DEFAULT_CONFIG",
    "AGENT2_DEFAULT_CONFIG",
    "AGENT3_DEFAULT_CONFIG",
]
