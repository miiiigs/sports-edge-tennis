-- A3/A4 research-only pre-issuance Guide Odds sidecar.
-- Read-only. Shares the FIRST-ISSUED original-pick CTE with
-- ops/calibration_dataset.sql. Do not change decision/market tables.
-- Only the same OddsPapi raw provider observation, bookmaker and
-- source_snapshot_id for both MATCH_WINNER sides can qualify.
-- Only market data source-observed AND ingested before the pick may qualify.
-- A missing, restricted, stale or unmapped bookmaker market returns NO ROW.
-- Export as {"quotes": [...rows...]}. Never fill missing odds with estimates.
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
paired_guide_quotes AS (
    SELECT p.match_id, p.selected_side,
           s.decimal_price::text AS selected_decimal,
           o.decimal_price::text AS opposite_decimal,
           'ODDSPAPI_TENNIS'::text AS source,
           s.bookmaker_code, s.source_snapshot_id,
           GREATEST(s.source_observed_at, o.source_observed_at) AS source_observed_at,
           GREATEST(s.ingested_at, o.ingested_at) AS ingested_at,
           'OPEN'::text AS market_status,
           s.as_of AS quote_as_of, s.odds_snapshot_id
    FROM aligned_model_picks p
    JOIN odds_snapshots s ON s.match_id=p.match_id
      AND s.market_type='MATCH_WINNER'
      AND s.selection_type=(
          CASE WHEN p.selected_side=1 THEN 'PLAYER_1' ELSE 'PLAYER_2' END
      )::market_selection_type
      AND s.market_status='OPEN' AND s.odds_origin='LIVE_PROVIDER'
      AND s.source_snapshot_id IS NOT NULL
    JOIN odds_snapshots o ON o.match_id=s.match_id
      AND o.source_system=s.source_system
      AND o.bookmaker_code=s.bookmaker_code
      AND o.source_snapshot_id=s.source_snapshot_id
      AND o.as_of=s.as_of
      AND o.market_type='MATCH_WINNER'
      AND o.market_status='OPEN'
      AND o.odds_origin='LIVE_PROVIDER'
      AND o.selection_type=(
          CASE WHEN p.selected_side=1 THEN 'PLAYER_2' ELSE 'PLAYER_1' END
      )::market_selection_type
    JOIN raw_odds_observations raw
      ON raw.raw_odds_observation_id=s.raw_odds_observation_id
      AND raw.provider_code='ODDSPAPI'
    WHERE o.raw_odds_observation_id=s.raw_odds_observation_id
      AND s.as_of <= p.picked_at AND o.as_of <= p.picked_at
      AND s.source_observed_at <= p.picked_at
      AND o.source_observed_at <= p.picked_at
      AND s.ingested_at <= p.picked_at AND o.ingested_at <= p.picked_at
      AND s.source_observed_at >= p.picked_at - INTERVAL '6 hours'
      AND o.source_observed_at >= p.picked_at - INTERVAL '6 hours'
      AND s.decimal_price > 1 AND o.decimal_price > 1
)
SELECT DISTINCT ON (match_id)
    match_id, selected_side, selected_decimal, opposite_decimal,
    source, bookmaker_code, source_snapshot_id, source_observed_at,
    ingested_at, market_status
FROM paired_guide_quotes
ORDER BY match_id, source_observed_at DESC, quote_as_of DESC, odds_snapshot_id DESC;
