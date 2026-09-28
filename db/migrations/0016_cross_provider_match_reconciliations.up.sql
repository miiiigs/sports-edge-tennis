BEGIN;

CREATE TABLE cross_provider_match_reconciliations (
    reconciliation_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    canonical_target_match_id BIGINT NOT NULL REFERENCES matches(match_id),
    evidence_match_id BIGINT NOT NULL REFERENCES matches(match_id),
    target_source_system TEXT NOT NULL CHECK (btrim(target_source_system) <> ''),
    target_source_match_id TEXT NOT NULL CHECK (btrim(target_source_match_id) <> ''),
    evidence_source_system TEXT NOT NULL CHECK (btrim(evidence_source_system) <> ''),
    evidence_source_match_id TEXT NOT NULL CHECK (btrim(evidence_source_match_id) <> ''),
    evidence_result_observation_id BIGINT NOT NULL REFERENCES match_result_observations(match_result_observation_id),
    classification TEXT NOT NULL CHECK (
        classification IN ('EXACT_CROSS_PROVIDER_MATCH', 'PLAYER_ORDER_REVERSED_EXACT')
    ),
    player_order_mapping TEXT NOT NULL CHECK (player_order_mapping IN ('DIRECT', 'REVERSED')),
    reconciliation_policy_version TEXT NOT NULL CHECK (btrim(reconciliation_policy_version) <> ''),
    reviewed_at TIMESTAMPTZ NOT NULL,
    evidence_payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_cross_provider_match_reconciliations_identity
        UNIQUE (
            canonical_target_match_id,
            evidence_result_observation_id,
            reconciliation_policy_version
        ),
    CONSTRAINT chk_cross_provider_match_reconciliations_not_self
        CHECK (canonical_target_match_id <> evidence_match_id)
);

CREATE INDEX idx_cross_provider_match_reconciliations_target
    ON cross_provider_match_reconciliations (canonical_target_match_id);

CREATE INDEX idx_cross_provider_match_reconciliations_evidence
    ON cross_provider_match_reconciliations (evidence_match_id);

CREATE TRIGGER trg_cross_provider_match_reconciliations_append_only_mutation
BEFORE UPDATE OR DELETE ON cross_provider_match_reconciliations
FOR EACH ROW
EXECUTE FUNCTION raise_append_only_mutation();

COMMIT;
