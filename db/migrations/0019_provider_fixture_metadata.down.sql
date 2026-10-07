BEGIN;

DROP TRIGGER IF EXISTS trg_provider_fixture_metadata_append_only_mutation
    ON provider_fixture_metadata;

DROP TABLE IF EXISTS provider_fixture_metadata;

COMMIT;
