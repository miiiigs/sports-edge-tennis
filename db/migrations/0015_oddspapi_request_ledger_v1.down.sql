-- 0015_oddspapi_request_ledger_v1 (down)
DROP INDEX IF EXISTS idx_oddspapi_event_identity_betradar;
DROP TABLE IF EXISTS oddspapi_event_identity_cache;
DROP INDEX IF EXISTS idx_oddspapi_request_ledger_requested_at;
DROP INDEX IF EXISTS idx_oddspapi_request_ledger_month;
DROP TABLE IF EXISTS oddspapi_request_ledger;
