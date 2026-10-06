BEGIN;

-- Reviewed operational correction layer for provider tournament identities.
--
-- The append-only `tournament_source_ids` table records what a provider
-- historically mapped to. When that mapping is later proven wrong (an early
-- API-Tennis display name created a duplicate canonical tournament), we do not
-- mutate provenance. Instead we record an explicit, reviewed correction that
-- canonical resolution consults FIRST.
CREATE TABLE tournament_identity_corrections (
    correction_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_system TEXT NOT NULL CHECK (btrim(source_system) <> ''),
    source_tournament_id TEXT NOT NULL CHECK (btrim(source_tournament_id) <> ''),
    canonical_tournament_id BIGINT NOT NULL REFERENCES tournaments(tournament_id),
    reason TEXT NOT NULL CHECK (btrim(reason) <> ''),
    evidence_ref TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_tournament_identity_corrections_provider
        UNIQUE (source_system, source_tournament_id)
);

CREATE INDEX idx_tournament_identity_corrections_canonical
    ON tournament_identity_corrections (canonical_tournament_id);

CREATE TRIGGER trg_tournament_identity_corrections_append_only_mutation
BEFORE UPDATE OR DELETE ON tournament_identity_corrections
FOR EACH ROW
EXECUTE FUNCTION raise_append_only_mutation();

-- Seed ONLY the reviewed Master-level corrections approved by this lane. The
-- insert is a no-op on an empty schema (no matching canonical tournament) and a
-- deterministic one-candidate match on production. Grand Slam corrections
-- (Australian Open, Wimbledon) are intentionally deferred pending a separate
-- BO3/BO5 and scope audit.
INSERT INTO tournament_identity_corrections (
    source_system, source_tournament_id, canonical_tournament_id, reason, evidence_ref
)
SELECT v.source_system, v.source_tournament_id, t.tournament_id, v.reason, v.evidence_ref
FROM (
    VALUES
        ('API_TENNIS', '1667', 'Shanghai Masters', 'PRE_ALIAS_DUPLICATE_ATP_TOUR', 'MANIFEST_20261006'),
        ('API_TENNIS', '1210', 'Cincinnati Masters', 'PRE_ALIAS_DUPLICATE_ATP_TOUR', 'MANIFEST_20261006'),
        ('SPORTRADAR', 'sr:competition:2983', 'Cincinnati Masters', 'PRE_ALIAS_DUPLICATE_ATP_TOUR', 'MANIFEST_20261006'),
        ('API_TENNIS', '2004', 'Madrid Masters', 'PRE_ALIAS_DUPLICATE_ATP_TOUR', 'MANIFEST_20261006'),
        ('API_TENNIS', '1928', 'Miami Masters', 'PRE_ALIAS_DUPLICATE_ATP_TOUR', 'MANIFEST_20261006'),
        ('API_TENNIS', '1970', 'Monte Carlo Masters', 'PRE_ALIAS_DUPLICATE_ATP_TOUR', 'MANIFEST_20261006'),
        ('API_TENNIS', '2011', 'Rome Masters', 'PRE_ALIAS_DUPLICATE_ATP_TOUR', 'MANIFEST_20261006')
) AS v(source_system, source_tournament_id, canonical_name, reason, evidence_ref)
JOIN tournaments t
  ON t.canonical_name = v.canonical_name
 AND t.tour_scope = 'ATP_MAIN'
WHERE (
    SELECT count(*) FROM tournaments t2
    WHERE t2.canonical_name = v.canonical_name AND t2.tour_scope = 'ATP_MAIN'
) = 1;

COMMIT;
