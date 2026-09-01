from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from sports_edge_tennis.agents.agent3_intransitivity import (
    AGENT_CODE,
    AbstainReason,
    Agent3Config,
    Agent3ValidationError,
    Agent3HistorySnapshot,
    OutcomeObservation,
    PairHistorySnapshot,
    PlayerHistorySnapshot,
    PredictionRequest,
    ResidualObservation,
    combined_score,
    logistic_probability,
    predict,
    residual_for_match,
    shrink,
    summarize_network_bucket,
    summarize_pair_residual_bucket,
    summarize_player_bucket,
    surface_blend_weight,
    temporal_weight,
    weighted_mean,
)


AS_OF = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)


def _observed_at(days_ago: int) -> datetime:
    return AS_OF - timedelta(days=days_ago)


def _outcome(days_ago: int, value: int) -> OutcomeObservation:
    return OutcomeObservation(observed_at=_observed_at(days_ago), value=value)


def _residual(days_ago: int, value: str | Decimal) -> ResidualObservation:
    return ResidualObservation(observed_at=_observed_at(days_ago), residual=Decimal(str(value)))


def _player(
    player_id: int,
    *,
    all_values: list[tuple[int, int]] | None = None,
    surface_values: list[tuple[int, int]] | None = None,
    surface: str = "HARD",
) -> PlayerHistorySnapshot:
    return PlayerHistorySnapshot(
        player_id=player_id,
        all_observations=tuple(_outcome(days_ago, value) for days_ago, value in (all_values or [])),
        surface_observations={
            surface: tuple(
                _outcome(days_ago, value) for days_ago, value in (surface_values or [])
            )
        }
        if surface_values
        else {},
    )


def _pair(
    player_a_id: int,
    player_b_id: int,
    *,
    all_values: list[tuple[int, str | Decimal]] | None = None,
    surface_values: list[tuple[int, str | Decimal]] | None = None,
    surface: str = "HARD",
) -> PairHistorySnapshot:
    low_id, high_id = sorted((player_a_id, player_b_id))
    return PairHistorySnapshot(
        player_low_id=low_id,
        player_high_id=high_id,
        all_observations=tuple(
            _residual(days_ago, value) for days_ago, value in (all_values or [])
        ),
        surface_observations={
            surface: tuple(
                _residual(days_ago, value) for days_ago, value in (surface_values or [])
            )
        }
        if surface_values
        else {},
    )


def _snapshot(
    *,
    players: list[PlayerHistorySnapshot] | None = None,
    pairs: list[PairHistorySnapshot] | None = None,
) -> Agent3HistorySnapshot:
    pair_map = {
        (pair.player_low_id, pair.player_high_id): pair
        for pair in (pairs or [])
    }
    adjacency: dict[int, tuple[int, ...]] = {}
    for pair in pair_map.values():
        adjacency[pair.player_low_id] = tuple(
            sorted({*adjacency.get(pair.player_low_id, ()), pair.player_high_id})
        )
        adjacency[pair.player_high_id] = tuple(
            sorted({*adjacency.get(pair.player_high_id, ()), pair.player_low_id})
        )
    return Agent3HistorySnapshot(
        players={player.player_id: player for player in (players or [])},
        pairs=pair_map,
        adjacency=adjacency,
    )


def _request(
    snapshot: Agent3HistorySnapshot,
    *,
    player_1_id: int = 1,
    player_2_id: int = 2,
    surface: str | None = "HARD",
    pit_state_resolved: bool = True,
    supported_matchup: bool = True,
) -> PredictionRequest:
    return PredictionRequest(
        player_1_id=player_1_id,
        player_2_id=player_2_id,
        surface=surface,
        history_snapshot=snapshot,
        as_of=AS_OF,
        pit_state_resolved=pit_state_resolved,
        supported_matchup=supported_matchup,
    )


def _direct_snapshot(
    *,
    all_values: list[tuple[int, str | Decimal]],
    surface_values: list[tuple[int, str | Decimal]] | None = None,
) -> Agent3HistorySnapshot:
    return _snapshot(pairs=[_pair(1, 2, all_values=all_values, surface_values=surface_values)])


def _network_snapshot(
    *,
    shared_ids: list[int],
    ac_value: str,
    bc_value: str,
    per_edge_count: int = 2,
    include_direct: bool = False,
    direct_value: str = "0.60",
    include_surface: bool = False,
) -> Agent3HistorySnapshot:
    pairs: list[PairHistorySnapshot] = []
    if include_direct:
        pairs.append(
            _pair(
                1,
                2,
                all_values=[(0, direct_value), (0, direct_value), (0, direct_value)],
                surface_values=[(0, direct_value), (0, direct_value), (0, direct_value)]
                if include_surface
                else None,
            )
        )
    for opponent_id in shared_ids:
        pairs.append(
            _pair(
                1,
                opponent_id,
                all_values=[(0, ac_value) for _ in range(per_edge_count)],
                surface_values=[(0, ac_value) for _ in range(per_edge_count)]
                if include_surface
                else None,
            )
        )
        pairs.append(
            _pair(
                2,
                opponent_id,
                all_values=[(0, bc_value) for _ in range(per_edge_count)],
                surface_values=[(0, bc_value) for _ in range(per_edge_count)]
                if include_surface
                else None,
            )
        )
    return _snapshot(pairs=pairs)


def test_default_config_payload_records_locked_identity_and_parameters():
    payload = Agent3Config().to_payload()

    assert payload["half_life_days"] == 730
    assert payload["lookback_days"] == 1460
    assert payload["k_g"] == "8"
    assert payload["k_d"] == "3"
    assert payload["k_n"] == "8"
    assert payload["k_surface"] == "8"
    assert payload["model_version"] == "agent3_intransitivity_v1"
    assert payload["feature_version"] == "agent3_matchup_network_inputs_v1"
    assert payload["config_version"] == "agent3_intransitivity_config_v1"
    assert payload["parameter_schema_version"] == "agent3_intransitivity_params_v1"


def test_locked_config_rejects_tuning_and_identity_overrides():
    with pytest.raises(Agent3ValidationError):
        Agent3Config(k_g=Decimal("9"))
    with pytest.raises(Agent3ValidationError):
        Agent3Config(direct_raw_support=3)
    with pytest.raises(Agent3ValidationError):
        Agent3Config(model_version="changed")


def test_shrink_matches_locked_formula():
    assert shrink(Decimal("0"), Decimal("8")) == Decimal("0")
    assert shrink(Decimal("8"), Decimal("8")) == Decimal("0.5")
    assert shrink(Decimal("3"), Decimal("3")) == Decimal("0.5")


def test_temporal_weight_matches_half_life_contract():
    assert temporal_weight(AS_OF, AS_OF) == Decimal("1")
    assert temporal_weight(_observed_at(730), AS_OF).quantize(Decimal("0.000001")) == Decimal(
        "0.500000"
    )
    assert temporal_weight(_observed_at(1460), AS_OF).quantize(Decimal("0.000001")) == Decimal(
        "0.250000"
    )
    assert temporal_weight(_observed_at(1461), AS_OF) == Decimal("0")


def test_weighted_mean_and_player_baseline_match_hand_calculation():
    mean_value = weighted_mean(
        [
            (Decimal("1"), Decimal("2")),
            (Decimal("-1"), Decimal("1")),
        ]
    )
    snapshot = _snapshot(
        players=[
            _player(
                1,
                all_values=[(0, 1), (0, 1), (0, -1)],
                surface_values=[(0, 1), (0, -1)],
            )
        ]
    )
    summary = summarize_player_bucket(snapshot, 1, surface=None, as_of=AS_OF)

    assert mean_value.quantize(Decimal("0.0000000000000000000000000001")) == Decimal(
        "0.3333333333333333333333333333"
    )
    assert summary.weighted_mean.quantize(Decimal("0.0000000000000000000000000001")) == Decimal(
        "0.3333333333333333333333333333"
    )
    assert summary.baseline.quantize(Decimal("0.0000000000000000000000000001")) == Decimal(
        "0.0909090909090909090909090909"
    )


def test_residual_orientation_is_exactly_antisymmetric():
    forward = residual_for_match(Decimal("0.10"), Decimal("-0.20"), outcome=1)
    reverse = residual_for_match(Decimal("-0.20"), Decimal("0.10"), outcome=-1)

    assert forward == Decimal("0.70")
    assert reverse == -forward


def test_pair_residual_summary_is_exactly_antisymmetric():
    snapshot = _direct_snapshot(all_values=[(0, "0.60"), (0, "0.60"), (0, "0.60")])

    forward = summarize_pair_residual_bucket(snapshot, 1, 2, surface=None, as_of=AS_OF)
    reverse = summarize_pair_residual_bucket(snapshot, 2, 1, surface=None, as_of=AS_OF)

    assert forward.residual_score == Decimal("0.300")
    assert reverse.residual_score == Decimal("-0.300")
    assert reverse.residual_score == -forward.residual_score


def test_network_summary_matches_q_and_antisymmetry_contract():
    snapshot = _network_snapshot(
        shared_ids=[3, 4, 5, 6, 7],
        ac_value="0.60",
        bc_value="-0.60",
    )

    forward = summarize_network_bucket(snapshot, 1, 2, surface=None, as_of=AS_OF)
    reverse = summarize_network_bucket(snapshot, 2, 1, surface=None, as_of=AS_OF)

    assert forward.shared_opponent_count == 5
    assert forward.q_total == Decimal("10")
    assert forward.weighted_mean_gap == Decimal("0.48")
    assert forward.network_score.quantize(Decimal("0.0000000000000000000000000001")) == Decimal(
        "0.2666666666666666666666666667"
    )
    assert reverse.network_score == -forward.network_score


def test_surface_blend_weight_and_combined_score_match_locked_formula():
    eta = surface_blend_weight(Decimal("4"), Decimal("0"))
    score = combined_score(Decimal("0.30"), Decimal("0.20"))

    assert eta.quantize(Decimal("0.0000000000000000000000000001")) == Decimal(
        "0.3333333333333333333333333333"
    )
    assert score == Decimal("0.2700")


def test_logistic_probability_obeys_cap_and_complement():
    positive = logistic_probability(Decimal("0.40"))
    negative = logistic_probability(Decimal("-0.40"))
    capped = logistic_probability(Decimal("100"))

    assert positive.quantize(Decimal("0.000001")) == Decimal("0.584191")
    assert abs((positive + negative) - Decimal("1")) < Decimal("1e-40")
    assert capped == Decimal("0.650000")


def test_direct_h2h_sufficient_support_activates_prediction():
    result = predict(_request(_direct_snapshot(all_values=[(0, "0.60"), (0, "0.60"), (0, "0.60")])))

    assert result.agent_code == AGENT_CODE
    assert result.prediction_status == "PREDICTED"
    assert result.selected_side == 1
    assert result.abstain_reason is None


def test_direct_raw_h2h_insufficient_abstains():
    result = predict(_request(_direct_snapshot(all_values=[(0, "0.60"), (0, "0.60")])))

    assert result.prediction_status == "ABSTAINED"
    assert result.abstain_reason == AbstainReason.INSUFFICIENT_MATCHUP_SUPPORT


def test_direct_effective_h2h_insufficient_despite_raw_support_abstains():
    result = predict(
        _request(
            _direct_snapshot(
                all_values=[(1458, "0.60"), (1459, "0.60"), (1460, "0.60")]
            )
        )
    )

    assert result.prediction_status == "ABSTAINED"
    assert result.abstain_reason == AbstainReason.INSUFFICIENT_MATCHUP_SUPPORT


def test_direct_signal_below_floor_abstains_weak_signal():
    result = predict(_request(_direct_snapshot(all_values=[(0, "0.05"), (0, "0.05"), (0, "0.05")])))

    assert result.prediction_status == "ABSTAINED"
    assert result.abstain_reason == AbstainReason.WEAK_SIGNAL


def test_direct_probability_below_activation_floor_abstains_weak_signal():
    result = predict(_request(_direct_snapshot(all_values=[(0, "0.36"), (0, "0.36"), (0, "0.36")])))

    assert result.prediction_status == "ABSTAINED"
    assert result.abstain_reason == AbstainReason.WEAK_SIGNAL


def test_network_shared_opponents_below_normal_threshold_stays_inactive():
    summary = summarize_network_bucket(
        _network_snapshot(shared_ids=[3, 4, 5, 6], ac_value="0.60", bc_value="-0.60"),
        1,
        2,
        surface=None,
        as_of=AS_OF,
    )

    assert summary.shared_opponent_count == 4
    assert summary.normal_supported is False


def test_network_q_below_normal_threshold_stays_inactive():
    summary = summarize_network_bucket(
        _network_snapshot(shared_ids=[3, 4, 5, 6, 7], ac_value="0.60", bc_value="-0.60", per_edge_count=1),
        1,
        2,
        surface=None,
        as_of=AS_OF,
    )

    assert summary.shared_opponent_count == 5
    assert summary.q_total == Decimal("5")
    assert summary.normal_supported is False


def test_normal_supported_network_contributes_to_direct_score():
    direct_only = predict(
        _request(_direct_snapshot(all_values=[(0, "0.60"), (1, "0.60"), (2, "0.60")]))
    )
    direct_plus_network = predict(
        _request(
            _network_snapshot(
                shared_ids=[3, 4, 5, 6, 7],
                ac_value="0.60",
                bc_value="-0.60",
                include_direct=True,
                direct_value="0.60",
            )
        )
    )

    assert direct_only.prediction_status == "PREDICTED"
    assert direct_plus_network.prediction_status == "PREDICTED"
    assert direct_plus_network.diagnostics.activation_mode == "DIRECT_PLUS_NETWORK"
    assert direct_plus_network.diagnostics.final_score > direct_only.diagnostics.final_score


def test_network_only_shared_opponents_below_stricter_threshold_abstains():
    result = predict(
        _request(_network_snapshot(shared_ids=[3, 4, 5, 6, 7, 8, 9], ac_value="0.60", bc_value="-0.60"))
    )

    assert result.prediction_status == "ABSTAINED"
    assert result.abstain_reason == AbstainReason.INSUFFICIENT_MATCHUP_SUPPORT


def test_network_only_q_below_stricter_threshold_abstains():
    result = predict(
        _request(
            _network_snapshot(
                shared_ids=[3, 4, 5, 6, 7, 8, 9, 10],
                ac_value="0.60",
                bc_value="-0.60",
                per_edge_count=1,
            )
        )
    )

    assert result.prediction_status == "ABSTAINED"
    assert result.abstain_reason == AbstainReason.INSUFFICIENT_MATCHUP_SUPPORT


def test_network_only_abs_n_below_threshold_abstains():
    result = predict(
        _request(
            _network_snapshot(
                shared_ids=[3, 4, 5, 6, 7, 8, 9, 10],
                ac_value="0.20",
                bc_value="-0.20",
            )
        )
    )

    assert result.prediction_status == "ABSTAINED"
    assert result.abstain_reason == AbstainReason.INSUFFICIENT_MATCHUP_SUPPORT


def test_strong_network_only_support_activates_prediction():
    result = predict(
        _request(
            _network_snapshot(
                shared_ids=[3, 4, 5, 6, 7, 8, 9, 10],
                ac_value="0.60",
                bc_value="-0.60",
            )
        )
    )

    assert result.prediction_status == "PREDICTED"
    assert result.diagnostics.activation_mode == "NETWORK_ONLY"
    assert result.selected_side == 1


def test_network_prediction_is_exactly_antisymmetric():
    snapshot = _network_snapshot(
        shared_ids=[3, 4, 5, 6, 7, 8, 9, 10],
        ac_value="0.60",
        bc_value="-0.60",
    )

    forward = predict(_request(snapshot, player_1_id=1, player_2_id=2))
    reverse = predict(_request(snapshot, player_1_id=2, player_2_id=1))

    assert forward.prediction_status == "PREDICTED"
    assert reverse.prediction_status == "PREDICTED"
    assert reverse.player_1_probability == forward.player_2_probability
    assert reverse.player_2_probability == forward.player_1_probability


def test_strong_opposite_direct_and_network_signs_trigger_conflict():
    result = predict(
        _request(
            _network_snapshot(
                shared_ids=[3, 4, 5, 6, 7],
                ac_value="-0.60",
                bc_value="0.60",
                include_direct=True,
                direct_value="0.60",
            )
        )
    )

    assert result.prediction_status == "ABSTAINED"
    assert result.abstain_reason == AbstainReason.CONFLICTING_DIRECT_NETWORK


def test_opposite_signs_below_conflict_floor_do_not_trigger_conflict():
    result = predict(
        _request(
            _network_snapshot(
                shared_ids=[3, 4, 5, 6, 7],
                ac_value="0.46",
                bc_value="0.54",
                include_direct=True,
                direct_value="0.60",
            )
        )
    )

    assert result.prediction_status == "PREDICTED"
    assert result.abstain_reason is None


def test_same_sign_direct_and_network_do_not_trigger_conflict():
    result = predict(
        _request(
            _network_snapshot(
                shared_ids=[3, 4, 5, 6, 7],
                ac_value="0.60",
                bc_value="-0.20",
                include_direct=True,
                direct_value="0.60",
            )
        )
    )

    assert result.prediction_status == "PREDICTED"
    assert result.abstain_reason is None


def test_surface_and_all_bucket_blend_is_exact():
    snapshot = _direct_snapshot(
        all_values=[(0, "0.60"), (0, "0.60"), (0, "0.60")],
        surface_values=[(0, "1.20"), (0, "1.20"), (0, "1.20"), (0, "1.20")],
    )

    result = predict(_request(snapshot))

    assert result.prediction_status == "PREDICTED"
    assert result.diagnostics.eta.quantize(Decimal("0.0000000000000000000000000001")) == Decimal(
        "0.3333333333333333333333333333"
    )
    assert result.diagnostics.S_all == Decimal("0.2100")
    assert result.diagnostics.S_surface == Decimal("0.479999999999999999999999999990")
    assert result.diagnostics.final_score == Decimal(
        "0.29999999999999999999999999999666666666666666666667"
    )


def test_zero_surface_support_falls_back_to_all_bucket_only():
    snapshot = _direct_snapshot(
        all_values=[(0, "0.60"), (0, "0.60"), (0, "0.60")],
        surface_values=None,
    )

    result = predict(_request(snapshot))

    assert result.prediction_status == "PREDICTED"
    assert result.diagnostics.final_score == result.diagnostics.S_all


def test_unknown_target_surface_abstains_first():
    result = predict(
        _request(_snapshot(), surface=None, pit_state_resolved=False, supported_matchup=False)
    )

    assert result.prediction_status == "ABSTAINED"
    assert result.abstain_reason == AbstainReason.UNKNOWN_SURFACE


def test_surface_swap_preserves_player_order_antisymmetry():
    snapshot = _direct_snapshot(
        all_values=[(0, "0.60"), (0, "0.60"), (0, "0.60")],
        surface_values=[(0, "0.90"), (0, "0.90"), (0, "0.90")],
    )

    forward = predict(_request(snapshot, player_1_id=1, player_2_id=2))
    reverse = predict(_request(snapshot, player_1_id=2, player_2_id=1))

    assert reverse.player_1_probability == forward.player_2_probability
    assert reverse.player_2_probability == forward.player_1_probability


def test_abstain_precedence_is_deterministic():
    assert (
        predict(_request(_snapshot(), surface=None, pit_state_resolved=False, supported_matchup=False)).abstain_reason
        == AbstainReason.UNKNOWN_SURFACE
    )
    assert (
        predict(_request(_snapshot(), pit_state_resolved=False, supported_matchup=False)).abstain_reason
        == AbstainReason.PIT_STATE_UNRESOLVED
    )
    assert (
        predict(_request(_snapshot(), pit_state_resolved=True, supported_matchup=False)).abstain_reason
        == AbstainReason.UNSUPPORTED_OR_UNKNOWN
    )
    assert predict(_request(_snapshot())).abstain_reason == AbstainReason.INSUFFICIENT_MATCHUP_SUPPORT
    assert (
        predict(
            _request(
                _network_snapshot(
                    shared_ids=[3, 4, 5, 6, 7],
                    ac_value="-0.60",
                    bc_value="0.60",
                    include_direct=True,
                    direct_value="0.60",
                )
            )
        ).abstain_reason
        == AbstainReason.CONFLICTING_DIRECT_NETWORK
    )
    assert (
        predict(_request(_direct_snapshot(all_values=[(0, "0.05"), (0, "0.05"), (0, "0.05")]))).abstain_reason
        == AbstainReason.WEAK_SIGNAL
    )


@pytest.mark.parametrize(
    "snapshot",
    [
        _direct_snapshot(all_values=[(0, "0.60"), (0, "0.60"), (0, "0.60")]),
        _network_snapshot(
            shared_ids=[3, 4, 5, 6, 7],
            ac_value="0.60",
            bc_value="-0.60",
            include_direct=True,
            direct_value="0.60",
        ),
        _network_snapshot(
            shared_ids=[3, 4, 5, 6, 7, 8, 9, 10],
            ac_value="0.60",
            bc_value="-0.60",
        ),
        _direct_snapshot(
            all_values=[(0, "0.60"), (0, "0.60"), (0, "0.60")],
            surface_values=[(0, "0.90"), (0, "0.90"), (0, "0.90")],
        ),
    ],
)
def test_side_swap_produces_exact_inverse_complement(snapshot: Agent3HistorySnapshot):
    forward = predict(_request(snapshot, player_1_id=1, player_2_id=2))
    reverse = predict(_request(snapshot, player_1_id=2, player_2_id=1))

    assert reverse.prediction_status == forward.prediction_status
    if forward.prediction_status == "ABSTAINED":
        assert reverse.abstain_reason == forward.abstain_reason
        return
    assert reverse.selected_side != forward.selected_side
    assert reverse.selected_probability == forward.selected_probability
    assert reverse.player_1_probability == forward.player_2_probability
    assert reverse.player_2_probability == forward.player_1_probability


def test_repeated_prediction_is_identical():
    snapshot = _network_snapshot(
        shared_ids=[3, 4, 5, 6, 7],
        ac_value="0.60",
        bc_value="-0.60",
        include_direct=True,
        direct_value="0.60",
    )
    request = _request(snapshot)

    assert predict(request) == predict(request)
