BEGIN;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM decisions) THEN
        RAISE EXCEPTION
            '0008_market_value_v1.up.sql requires decisions to be empty before adding Market lineage columns.';
    END IF;
END;
$$;

ALTER TYPE decision_reason_code
    ADD VALUE IF NOT EXISTS 'NOT_MANAGER_ELIGIBLE';
ALTER TYPE decision_reason_code
    ADD VALUE IF NOT EXISTS 'MISSING_MARKET';
ALTER TYPE decision_reason_code
    ADD VALUE IF NOT EXISTS 'INCOMPLETE_MARKET';
ALTER TYPE decision_reason_code
    ADD VALUE IF NOT EXISTS 'STALE_MARKET';
ALTER TYPE decision_reason_code
    ADD VALUE IF NOT EXISTS 'MARKET_SUSPENDED';
ALTER TYPE decision_reason_code
    ADD VALUE IF NOT EXISTS 'CROSSED_MARKET';
ALTER TYPE decision_reason_code
    ADD VALUE IF NOT EXISTS 'INVALID_PRICE';
ALTER TYPE decision_reason_code
    ADD VALUE IF NOT EXISTS 'NO_MODEL_EDGE_VS_SHARP';
ALTER TYPE decision_reason_code
    ADD VALUE IF NOT EXISTS 'EDGE_BELOW_THRESHOLD';
ALTER TYPE decision_reason_code
    ADD VALUE IF NOT EXISTS 'UNSUPPORTED_TIER';
ALTER TYPE decision_reason_code
    ADD VALUE IF NOT EXISTS 'MISSING_SHARP_REFERENCE';

CREATE TYPE market_snapshot_role AS ENUM (
    'REFERENCE_PLAYER_1',
    'REFERENCE_PLAYER_2',
    'OFFERED_SELECTION'
);

CREATE TABLE market_policy_versions (
    market_policy_version_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    policy_key TEXT NOT NULL,
    policy_version TEXT NOT NULL,
    atp_main_min_edge_pct NUMERIC(8, 6) NOT NULL,
    challenger_min_edge_pct NUMERIC(8, 6) NOT NULL,
    long_price_threshold NUMERIC(12, 6) NOT NULL,
    long_price_min_edge_pct NUMERIC(8, 6) NOT NULL,
    default_devig_method TEXT NOT NULL,
    sharp_reference_policy_code TEXT NOT NULL,
    odds_max_staleness_minutes INTEGER NOT NULL,
    type_b_enabled BOOLEAN NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_market_policy_versions_identity
        UNIQUE (policy_key, policy_version),
    CONSTRAINT chk_market_policy_versions_atp_main_min_edge
        CHECK (atp_main_min_edge_pct >= 0),
    CONSTRAINT chk_market_policy_versions_challenger_min_edge
        CHECK (challenger_min_edge_pct >= 0),
    CONSTRAINT chk_market_policy_versions_long_price_threshold
        CHECK (long_price_threshold > 1),
    CONSTRAINT chk_market_policy_versions_long_price_min_edge
        CHECK (long_price_min_edge_pct >= 0),
    CONSTRAINT chk_market_policy_versions_default_devig_method
        CHECK (default_devig_method IN ('POWER', 'SHIN')),
    CONSTRAINT chk_market_policy_versions_odds_staleness
        CHECK (odds_max_staleness_minutes >= 0),
    CONSTRAINT chk_market_policy_versions_type_b_enabled
        CHECK (type_b_enabled = FALSE)
);

INSERT INTO market_policy_versions (
    policy_key,
    policy_version,
    atp_main_min_edge_pct,
    challenger_min_edge_pct,
    long_price_threshold,
    long_price_min_edge_pct,
    default_devig_method,
    sharp_reference_policy_code,
    odds_max_staleness_minutes,
    type_b_enabled
)
VALUES (
    'tennis_market_value',
    'v1',
    0.050000,
    0.080000,
    4.000000,
    0.080000,
    'POWER',
    'PINNACLE_SHARP',
    15,
    FALSE
);

ALTER TABLE decisions
    ALTER COLUMN decision_policy_version_id DROP NOT NULL;

ALTER TABLE decisions
    ADD COLUMN manager_consensus_result_id BIGINT,
    ADD COLUMN market_policy_version_id BIGINT,
    ADD COLUMN snapshot_lineage_hash TEXT,
    ADD CONSTRAINT fk_decisions_manager_consensus_result
        FOREIGN KEY (manager_consensus_result_id)
        REFERENCES manager_consensus_results(manager_consensus_result_id),
    ADD CONSTRAINT fk_decisions_market_policy_version
        FOREIGN KEY (market_policy_version_id)
        REFERENCES market_policy_versions(market_policy_version_id),
    ADD CONSTRAINT chk_decisions_market_links_coherent
        CHECK (
            manager_consensus_result_id IS NULL
            OR (
                market_policy_version_id IS NOT NULL
                AND snapshot_lineage_hash IS NOT NULL
            )
        ),
    ADD CONSTRAINT uq_decisions_market_idempotency
        UNIQUE (manager_consensus_result_id, market_policy_version_id, as_of, snapshot_lineage_hash);

CREATE TABLE decision_odds_snapshots (
    decision_id BIGINT NOT NULL REFERENCES decisions(decision_id),
    odds_snapshot_id BIGINT NOT NULL REFERENCES odds_snapshots(odds_snapshot_id),
    snapshot_role market_snapshot_role NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_decision_odds_snapshots_role
        UNIQUE (decision_id, snapshot_role)
);

CREATE INDEX idx_decision_odds_snapshots_odds
    ON decision_odds_snapshots (odds_snapshot_id);

CREATE OR REPLACE FUNCTION guard_market_decision_insert()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
    parent_manager_status TEXT;
    parent_run_id BIGINT;
    parent_match_id BIGINT;
BEGIN
    IF NEW.manager_consensus_result_id IS NULL THEN
        RETURN NEW;
    END IF;

    SELECT manager_status::text, prediction_run_id, match_id
    INTO parent_manager_status, parent_run_id, parent_match_id
    FROM manager_consensus_results
    WHERE manager_consensus_result_id = NEW.manager_consensus_result_id
    FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION
            'market decision requires an existing manager_consensus_result parent (manager_consensus_result_id=%).',
            NEW.manager_consensus_result_id;
    END IF;

    IF parent_run_id <> NEW.prediction_run_id OR parent_match_id <> NEW.match_id THEN
        RAISE EXCEPTION
            'market decision prediction_run_id/match_id must match its manager_consensus_result parent.';
    END IF;

    IF NEW.decision_status = 'BET' AND parent_manager_status <> 'CONSENSUS' THEN
        RAISE EXCEPTION
            'a BET market decision requires a CONSENSUS manager_consensus_result parent.';
    END IF;

    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_market_decisions_eligibility_guard
BEFORE INSERT ON decisions
FOR EACH ROW
EXECUTE FUNCTION guard_market_decision_insert();

CREATE TRIGGER trg_market_policy_versions_append_only_mutation
BEFORE UPDATE OR DELETE ON market_policy_versions
FOR EACH ROW
EXECUTE FUNCTION raise_append_only_mutation();

CREATE TRIGGER trg_decision_odds_snapshots_append_only_mutation
BEFORE UPDATE OR DELETE ON decision_odds_snapshots
FOR EACH ROW
EXECUTE FUNCTION raise_append_only_mutation();

COMMIT;
