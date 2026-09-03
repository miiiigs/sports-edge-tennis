BEGIN;

CREATE TYPE manager_consensus_status AS ENUM ('CONSENSUS', 'NO_BET');

CREATE TYPE manager_reason_code AS ENUM (
    'NONE',
    'DIRECTIONAL_DISAGREEMENT',
    'THREE_AGENT_SPREAD_EXCEEDED',
    'AGENT3_ABSTENTION_SPREAD_EXCEEDED',
    'ACTIVE_PROBABILITY_BELOW_MINIMUM',
    'UNSUPPORTED_ABSTENTION_CONFIGURATION',
    'TWO_OR_MORE_ABSTENTIONS'
);

CREATE TABLE manager_policy_versions (
    manager_policy_version_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    policy_key TEXT NOT NULL,
    policy_version TEXT NOT NULL,
    consensus_mode TEXT NOT NULL,
    allow_agent3_abstention BOOLEAN NOT NULL,
    three_agent_max_probability_spread NUMERIC(8, 6) NOT NULL,
    agent3_abstention_max_probability_spread NUMERIC(8, 6) NOT NULL,
    min_active_agent_probability NUMERIC(8, 6) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_manager_policy_versions_identity
        UNIQUE (policy_key, policy_version),
    CONSTRAINT chk_manager_policy_versions_consensus_mode
        CHECK (consensus_mode = 'UNANIMOUS_ACTIVE_AGENTS'),
    CONSTRAINT chk_manager_policy_versions_allow_agent3_abstention
        CHECK (allow_agent3_abstention = TRUE),
    CONSTRAINT chk_manager_policy_versions_three_agent_spread
        CHECK (
            three_agent_max_probability_spread >= 0
            AND three_agent_max_probability_spread <= 1
        ),
    CONSTRAINT chk_manager_policy_versions_agent3_abstention_spread
        CHECK (
            agent3_abstention_max_probability_spread >= 0
            AND agent3_abstention_max_probability_spread <= 1
        ),
    CONSTRAINT chk_manager_policy_versions_min_active_probability
        CHECK (min_active_agent_probability >= 0 AND min_active_agent_probability <= 1)
);

INSERT INTO manager_policy_versions (
    policy_key,
    policy_version,
    consensus_mode,
    allow_agent3_abstention,
    three_agent_max_probability_spread,
    agent3_abstention_max_probability_spread,
    min_active_agent_probability
)
VALUES (
    'tennis_manager_consensus',
    'v1',
    'UNANIMOUS_ACTIVE_AGENTS',
    TRUE,
    0.080000,
    0.060000,
    0.500000
);

CREATE TABLE manager_consensus_results (
    manager_consensus_result_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    prediction_run_id BIGINT NOT NULL,
    match_id BIGINT NOT NULL,
    manager_policy_version_id BIGINT NOT NULL,
    manager_status manager_consensus_status NOT NULL,
    selected_side SMALLINT,
    consensus_probability NUMERIC(51, 50),
    active_agent_count SMALLINT NOT NULL,
    abstaining_agent_count SMALLINT NOT NULL,
    probability_spread NUMERIC(51, 50),
    reason_code manager_reason_code NOT NULL,
    as_of TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_manager_consensus_results_run_match
        UNIQUE (prediction_run_id, match_id),
    CONSTRAINT fk_manager_consensus_results_target
        FOREIGN KEY (prediction_run_id, match_id)
        REFERENCES prediction_run_targets(prediction_run_id, match_id),
    CONSTRAINT fk_manager_consensus_results_run_as_of
        FOREIGN KEY (prediction_run_id, as_of)
        REFERENCES prediction_runs(prediction_run_id, as_of),
    CONSTRAINT fk_manager_consensus_results_policy
        FOREIGN KEY (manager_policy_version_id)
        REFERENCES manager_policy_versions(manager_policy_version_id),
    CONSTRAINT chk_manager_consensus_results_status_reason
        CHECK (
            (manager_status = 'CONSENSUS' AND reason_code = 'NONE')
            OR (manager_status = 'NO_BET' AND reason_code <> 'NONE')
        ),
    CONSTRAINT chk_manager_consensus_results_consensus_fields
        CHECK (
            (manager_status = 'CONSENSUS'
                AND selected_side IS NOT NULL
                AND consensus_probability IS NOT NULL
                AND active_agent_count >= 2)
            OR (manager_status = 'NO_BET'
                AND selected_side IS NULL
                AND consensus_probability IS NULL)
        ),
    CONSTRAINT chk_manager_consensus_results_agent_counts
        CHECK (active_agent_count + abstaining_agent_count = 3),
    CONSTRAINT chk_manager_consensus_results_selected_side
        CHECK (selected_side IS NULL OR selected_side IN (1, 2)),
    CONSTRAINT chk_manager_consensus_results_probability_range
        CHECK (
            consensus_probability IS NULL
            OR (consensus_probability >= 0 AND consensus_probability <= 1)
        ),
    CONSTRAINT chk_manager_consensus_results_spread_nullability
        CHECK (
            (active_agent_count >= 2 AND probability_spread IS NOT NULL)
            OR (active_agent_count < 2 AND probability_spread IS NULL)
        ),
    CONSTRAINT chk_manager_consensus_results_spread_range
        CHECK (
            probability_spread IS NULL
            OR (probability_spread >= 0 AND probability_spread <= 1)
        )
);

CREATE INDEX idx_manager_consensus_results_match
    ON manager_consensus_results (match_id);

CREATE INDEX idx_manager_consensus_results_status
    ON manager_consensus_results (manager_status);

CREATE OR REPLACE FUNCTION guard_manager_consensus_result_insert()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
    parent_contract_version TEXT;
    parent_run_status TEXT;
    parent_prediction_finalized_at TIMESTAMPTZ;
BEGIN
    SELECT prediction_contract_version, run_status, prediction_finalized_at
    INTO parent_contract_version, parent_run_status, parent_prediction_finalized_at
    FROM prediction_runs
    WHERE prediction_run_id = NEW.prediction_run_id
    FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION
            'manager_consensus_results insert requires an existing prediction_runs parent row (prediction_run_id=%).',
            NEW.prediction_run_id;
    END IF;

    IF parent_contract_version <> 'predictive_agent_result_v1' THEN
        RAISE EXCEPTION
            'manager_consensus_results requires prediction_contract_version = predictive_agent_result_v1.';
    END IF;

    IF parent_run_status <> 'COMPLETED' THEN
        RAISE EXCEPTION
            'manager_consensus_results requires a COMPLETED prediction run.';
    END IF;

    IF parent_prediction_finalized_at IS NULL THEN
        RAISE EXCEPTION
            'manager_consensus_results requires a finalized prediction run.';
    END IF;

    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_manager_consensus_results_eligibility_guard
BEFORE INSERT ON manager_consensus_results
FOR EACH ROW
EXECUTE FUNCTION guard_manager_consensus_result_insert();

CREATE TRIGGER trg_manager_consensus_results_append_only_mutation
BEFORE UPDATE OR DELETE ON manager_consensus_results
FOR EACH ROW
EXECUTE FUNCTION raise_append_only_mutation();

CREATE TRIGGER trg_manager_policy_versions_append_only_mutation
BEFORE UPDATE OR DELETE ON manager_policy_versions
FOR EACH ROW
EXECUTE FUNCTION raise_append_only_mutation();

COMMIT;
