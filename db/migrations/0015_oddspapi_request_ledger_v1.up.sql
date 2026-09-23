-- 0015_oddspapi_request_ledger_v1
-- Durable OddsPapi request-usage ledger and event-identity cache.
-- The ledger records every billable HTTP attempt (including retries) so the
-- monthly allowance can be enforced without a provider usage endpoint. It never
-- stores credentials, authorization headers, or secret query contents.

CREATE TABLE IF NOT EXISTS oddspapi_request_ledger (
    oddspapi_request_ledger_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    requested_at TIMESTAMPTZ NOT NULL,
    billing_month TEXT NOT NULL,
    endpoint TEXT NOT NULL,
    bookmaker TEXT,
    tournament_chunk_size INTEGER,
    request_purpose TEXT NOT NULL,
    classification TEXT NOT NULL,
    attempt_status TEXT NOT NULL DEFAULT 'RESERVED',
    http_status INTEGER,
    retry_sequence INTEGER NOT NULL DEFAULT 0,
    correlation_id TEXT,
    error_kind TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    CHECK (btrim(endpoint) <> ''),
    CHECK (request_purpose IN ('QUALIFICATION', 'DISCOVERY', 'DIAGNOSTIC')),
    CHECK (classification IN ('PRODUCTION', 'SHADOW', 'DIAGNOSTIC')),
    CHECK (attempt_status IN ('RESERVED', 'SUCCESS', 'FAILURE')),
    CHECK (retry_sequence >= 0)
);

CREATE INDEX IF NOT EXISTS idx_oddspapi_request_ledger_month
    ON oddspapi_request_ledger (billing_month);

CREATE INDEX IF NOT EXISTS idx_oddspapi_request_ledger_requested_at
    ON oddspapi_request_ledger (requested_at);

CREATE TABLE IF NOT EXISTS oddspapi_event_identity_cache (
    canonical_match_id BIGINT PRIMARY KEY REFERENCES matches(match_id),
    sportradar_event_id TEXT,
    oddspapi_event_id TEXT,
    betradar_id TEXT,
    oddspapi_tournament_id INTEGER,
    player_orientation TEXT NOT NULL,
    resolution_status TEXT NOT NULL,
    scheduled_start TIMESTAMPTZ,
    resolved_at TIMESTAMPTZ NOT NULL,
    last_verified_at TIMESTAMPTZ NOT NULL,
    CHECK (player_orientation IN ('DIRECT', 'REVERSED', 'UNKNOWN')),
    CHECK (resolution_status IN ('EXACT', 'CONFLICT'))
);

CREATE INDEX IF NOT EXISTS idx_oddspapi_event_identity_betradar
    ON oddspapi_event_identity_cache (betradar_id);
