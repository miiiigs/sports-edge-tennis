-- Offline calibration dataset for ops/calibration_evaluation.py.
-- Read-only. Reproduces the aligned Model Pick population with PIT A1/A2/A3
-- evidence, tier/surface, terminal result, and match-level Value Bet odds/CLV.
-- Run against production read-only, export the JSON "data" array, then:
--   python ops/calibration_evaluation.py --ledger <exported.json>
WITH aligned_model_candidates AS (
    SELECT mcr.manager_consensus_result_id, mcr.prediction_run_id, mcr.match_id,
        mcr.as_of AS picked_at, mcr.selected_side, mcr.consensus_probability AS aligned_probability,
        'MANAGER_CONSENSUS'::text AS aligned_pick_type
    FROM manager_consensus_results mcr
    JOIN prediction_runs pr ON pr.prediction_run_id = mcr.prediction_run_id
    WHERE mcr.manager_status='CONSENSUS' AND mcr.selected_side IN (1,2) AND mcr.consensus_probability >= 0.600000
      AND pr.prediction_contract_version='predictive_agent_result_v1' AND pr.run_status='COMPLETED'
      AND pr.prediction_finalized_at IS NOT NULL AND pr.experiment_id IS NULL
    UNION ALL
    SELECT mcr.manager_consensus_result_id, mcr.prediction_run_id, mcr.match_id,
        mcr.as_of AS picked_at, a1.selection_side,
        ((a1.win_probability + a2.win_probability)/2.0), 'A1_A2_WIDE_SPREAD'::text
    FROM manager_consensus_results mcr
    JOIN prediction_runs pr ON pr.prediction_run_id = mcr.prediction_run_id
    JOIN predictions a1 ON a1.prediction_run_id=mcr.prediction_run_id AND a1.match_id=mcr.match_id AND a1.agent_code='AGENT_1_SURFACE_ELO'
    JOIN predictions a2 ON a2.prediction_run_id=mcr.prediction_run_id AND a2.match_id=mcr.match_id AND a2.agent_code='AGENT_2_HIERARCHICAL_MARKOV'
    JOIN predictions a3 ON a3.prediction_run_id=mcr.prediction_run_id AND a3.match_id=mcr.match_id AND a3.agent_code='AGENT_3_INTRANSITIVITY'
    WHERE mcr.manager_status='NO_BET' AND mcr.reason_code='AGENT3_ABSTENTION_SPREAD_EXCEEDED'
      AND a1.prediction_status='PREDICTED' AND a2.prediction_status='PREDICTED' AND a3.prediction_status='ABSTAINED'
      AND a1.selection_side IN (1,2) AND a2.selection_side=a1.selection_side
      AND a1.win_probability IS NOT NULL AND a2.win_probability IS NOT NULL
      AND ((a1.win_probability+a2.win_probability)/2.0) >= 0.600000
      AND pr.prediction_contract_version='predictive_agent_result_v1' AND pr.run_status='COMPLETED'
      AND pr.prediction_finalized_at IS NOT NULL AND pr.experiment_id IS NULL
),
aligned_model_picks AS (
    SELECT DISTINCT ON (match_id) manager_consensus_result_id, prediction_run_id, match_id, picked_at, selected_side, aligned_probability, aligned_pick_type
    FROM aligned_model_candidates ORDER BY match_id, picked_at, manager_consensus_result_id, aligned_pick_type
),
day AS (
    SELECT p.*, mv.match_start_at, mv.surface::text AS surface, mv.competition_tier::text AS tier,
        mv.player_1_id, mv.player_2_id, mv.ingest_run_id AS mv_ingest_run_id,
        COALESCE((mv.match_start_at AT TIME ZONE 'Asia/Manila')::date, (p.picked_at AT TIME ZONE 'Asia/Manila')::date) AS manila_date,
        COALESCE((mv.match_start_at AT TIME ZONE 'UTC')::date, (p.picked_at AT TIME ZONE 'UTC')::date) AS utc_date,
        COALESCE((mv.match_start_at AT TIME ZONE 'Europe/London')::date, (p.picked_at AT TIME ZONE 'Europe/London')::date) AS london_date
    FROM aligned_model_picks p
    LEFT JOIN LATERAL (
        SELECT mv0.match_start_at, mv0.surface, mv0.competition_tier, mv0.player_1_id, mv0.player_2_id, mv0.ingest_run_id FROM match_versions mv0
        WHERE mv0.match_id=p.match_id AND mv0.as_of<=p.picked_at AND mv0.source_observed_at<=p.picked_at
        ORDER BY mv0.as_of DESC, mv0.source_observed_at DESC, mv0.match_version_id DESC LIMIT 1
    ) mv ON TRUE
),
wr AS (
    SELECT d.*, r.outcome_status, r.winner_side,
        a1.prediction_status::text AS a1s, a1.selection_side AS a1side, a1.win_probability AS a1p,
        a2.prediction_status::text AS a2s, a2.selection_side AS a2side, a2.win_probability AS a2p,
        a3.prediction_status::text AS a3s, a3.selection_side AS a3side, a3.win_probability AS a3p
    FROM day d
    LEFT JOIN LATERAL (
        SELECT mro.outcome_status::text AS outcome_status, mro.winner_side FROM match_result_observations mro
        WHERE mro.match_id=d.match_id AND mro.outcome_status IN ('COMPLETED','RETIRED','WALKOVER','CANCELLED')
        ORDER BY mro.as_of DESC, mro.source_observed_at DESC, mro.match_result_observation_id DESC LIMIT 1
    ) r ON TRUE
    LEFT JOIN predictions a1 ON a1.prediction_run_id=d.prediction_run_id AND a1.match_id=d.match_id AND a1.agent_code='AGENT_1_SURFACE_ELO'
    LEFT JOIN predictions a2 ON a2.prediction_run_id=d.prediction_run_id AND a2.match_id=d.match_id AND a2.agent_code='AGENT_2_HIERARCHICAL_MARKOV'
    LEFT JOIN predictions a3 ON a3.prediction_run_id=d.prediction_run_id AND a3.match_id=d.match_id AND a3.agent_code='AGENT_3_INTRANSITIVITY'
)
SELECT w.manila_date, w.utc_date, w.london_date, w.match_id,
    t.canonical_name AS tournament, p1.canonical_name AS player_1, p2.canonical_name AS player_2,
    w.tier, w.surface, w.selected_side, w.aligned_probability, w.aligned_pick_type,
    w.a1p AS a1_prob, w.a1side AS a1_side, w.a1s AS a1_status,
    w.a2p AS a2_prob, w.a2side AS a2_side, w.a2s AS a2_status,
    w.a3p AS a3_prob, w.a3side AS a3_side, w.a3s AS a3_status,
    w.picked_at, w.outcome_status, w.winner_side,
    CASE WHEN w.outcome_status='COMPLETED' AND w.winner_side=w.selected_side THEN 'WON'
         WHEN w.outcome_status='COMPLETED' AND w.winner_side IN (1,2) THEN 'LOST'
         WHEN w.outcome_status IN ('RETIRED','WALKOVER','CANCELLED') THEN 'VOID'
         ELSE 'PENDING' END AS result_status,
    dec.decision_id, dec.decision_status, dec.selection_side AS dec_selection_side,
    dec.market_decimal_price, dec.sharp_reference_price, dec.edge_pct, dec.price_source_system,
    dec.price_bookmaker_code, dec.suggested_stake_fraction, dec.full_kelly_fraction, dec.quarter_kelly_fraction,
    dec.staking_rule_version, dec.published_at, dec.min_acceptable_odds, dec.decision_reason_code,
    (dec.decision_id IS NOT NULL AND dec.decision_status='BET' AND dec.selection_side=w.selected_side) AS is_value_bet,
    ds.settlement_status, pp.pnl_units,
    ir.source_system AS fixture_source
FROM wr w
JOIN matches m ON m.match_id=w.match_id
JOIN tournament_editions te ON te.tournament_edition_id=m.tournament_edition_id
JOIN tournaments t ON t.tournament_id=te.tournament_id
LEFT JOIN players p1 ON p1.player_id=w.player_1_id
LEFT JOIN players p2 ON p2.player_id=w.player_2_id
LEFT JOIN LATERAL (
    SELECT d.decision_id, d.decision_status::text AS decision_status, d.selection_side, d.market_decimal_price,
        d.sharp_reference_price, d.edge_pct, d.price_source_system, d.price_bookmaker_code,
        d.suggested_stake_fraction, d.full_kelly_fraction, d.quarter_kelly_fraction, d.staking_rule_version,
        d.published_at, d.min_acceptable_odds, d.reason_code::text AS decision_reason_code
    FROM decisions d
    WHERE d.manager_consensus_result_id = w.manager_consensus_result_id
      AND d.match_id = w.match_id AND d.signal_type='TYPE_A_MODEL_EDGE'
    ORDER BY d.decision_id DESC LIMIT 1
) dec ON TRUE
LEFT JOIN decision_settlements ds ON ds.decision_id = dec.decision_id
LEFT JOIN paper_execution_performance_v1 pp ON pp.decision_id = dec.decision_id
LEFT JOIN ingest_runs ir ON ir.ingest_run_id = w.mv_ingest_run_id
ORDER BY w.manila_date, w.match_id;
