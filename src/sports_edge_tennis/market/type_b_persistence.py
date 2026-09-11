from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

import psycopg

from sports_edge_tennis.market.contracts import SharpReferenceSpec
from sports_edge_tennis.market.pricing import snapshot_lineage_hash
from sports_edge_tennis.market.type_b import TypeBValueSignal


_LEDGER_DDL = """
CREATE TABLE IF NOT EXISTS type_b_scan_runs (
    type_b_scan_run_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    scan_key TEXT NOT NULL UNIQUE CHECK (length(scan_key) = 64),
    horizon_start DATE NOT NULL,
    horizon_end DATE NOT NULL,
    candidate_match_count INTEGER NOT NULL CHECK (candidate_match_count >= 0),
    value_signal_count INTEGER NOT NULL CHECK (value_signal_count >= 0),
    market_policy_version_id BIGINT NOT NULL REFERENCES market_policy_versions(market_policy_version_id),
    sharp_source_system TEXT NOT NULL CHECK (btrim(sharp_source_system) <> ''),
    sharp_bookmaker_code TEXT NOT NULL CHECK (btrim(sharp_bookmaker_code) <> ''),
    as_of TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (horizon_end >= horizon_start)
);

CREATE TABLE IF NOT EXISTS type_b_value_signals (
    type_b_value_signal_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    type_b_scan_run_id BIGINT NOT NULL REFERENCES type_b_scan_runs(type_b_scan_run_id),
    match_id BIGINT NOT NULL REFERENCES matches(match_id),
    market_policy_version_id BIGINT NOT NULL REFERENCES market_policy_versions(market_policy_version_id),
    selection_side SMALLINT NOT NULL CHECK (selection_side IN (1, 2)),
    sharp_fair_probability NUMERIC(18, 12) NOT NULL CHECK (sharp_fair_probability > 0 AND sharp_fair_probability < 1),
    sharp_fair_odds NUMERIC(18, 8) NOT NULL CHECK (sharp_fair_odds > 1),
    sharp_reference_price NUMERIC(18, 8) NOT NULL CHECK (sharp_reference_price > 1),
    offered_price NUMERIC(18, 8) NOT NULL CHECK (offered_price > 1),
    price_source_system TEXT NOT NULL CHECK (btrim(price_source_system) <> ''),
    bookmaker_code TEXT NOT NULL CHECK (btrim(bookmaker_code) <> ''),
    edge_pct NUMERIC(18, 12) NOT NULL,
    threshold NUMERIC(18, 12) NOT NULL CHECK (threshold >= 0),
    min_acceptable_odds NUMERIC(18, 8) NOT NULL CHECK (min_acceptable_odds > 1),
    reference_player_1_snapshot_id BIGINT NOT NULL REFERENCES odds_snapshots(odds_snapshot_id),
    reference_player_2_snapshot_id BIGINT NOT NULL REFERENCES odds_snapshots(odds_snapshot_id),
    offered_snapshot_id BIGINT NOT NULL REFERENCES odds_snapshots(odds_snapshot_id),
    snapshot_lineage_hash TEXT NOT NULL CHECK (length(snapshot_lineage_hash) = 64),
    as_of TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_type_b_value_signal_lineage
        UNIQUE (type_b_scan_run_id, match_id, selection_side, snapshot_lineage_hash)
);

CREATE INDEX IF NOT EXISTS idx_type_b_scan_runs_as_of
    ON type_b_scan_runs (as_of DESC);
CREATE INDEX IF NOT EXISTS idx_type_b_value_signals_match
    ON type_b_value_signals (match_id, as_of DESC);
CREATE INDEX IF NOT EXISTS idx_type_b_value_signals_scan
    ON type_b_value_signals (type_b_scan_run_id);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger WHERE tgname = 'trg_type_b_scan_runs_append_only'
    ) THEN
        CREATE TRIGGER trg_type_b_scan_runs_append_only
        BEFORE UPDATE OR DELETE ON type_b_scan_runs
        FOR EACH ROW EXECUTE FUNCTION raise_append_only_mutation();
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger WHERE tgname = 'trg_type_b_value_signals_append_only'
    ) THEN
        CREATE TRIGGER trg_type_b_value_signals_append_only
        BEFORE UPDATE OR DELETE ON type_b_value_signals
        FOR EACH ROW EXECUTE FUNCTION raise_append_only_mutation();
    END IF;
END;
$$;
"""


@dataclass(frozen=True)
class TypeBLedgerOutcome:
    scan_run_id: int
    inserted_signal_count: int
    persisted_signal_count: int


def ensure_type_b_ledger(conn: psycopg.Connection) -> None:
    """Create the append-only Type B operational ledger if it is not present yet.

    This is intentionally isolated from the legacy `decisions` table because that
    table's V1 BET constraints require Manager/model consensus fields and therefore
    cannot represent an independent Type B price-dispersion signal truthfully.
    """
    conn.execute(_LEDGER_DDL)


def _scan_key(
    *,
    horizon_start: date,
    horizon_end: date,
    candidate_match_ids: tuple[int, ...],
    market_policy_version_id: int,
    sharp_reference: SharpReferenceSpec,
    as_of: datetime,
) -> str:
    import hashlib

    canonical = "|".join(
        (
            horizon_start.isoformat(),
            horizon_end.isoformat(),
            str(market_policy_version_id),
            sharp_reference.source_system,
            sharp_reference.bookmaker_code,
            as_of.isoformat(),
            ",".join(str(match_id) for match_id in sorted(candidate_match_ids)),
        )
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def persist_type_b_scan(
    conn: psycopg.Connection,
    *,
    signals: tuple[TypeBValueSignal, ...],
    candidate_match_ids: tuple[int, ...],
    market_policy_version_id: int,
    horizon_start: date,
    horizon_end: date,
    decision_as_of: datetime,
    sharp_reference: SharpReferenceSpec,
) -> TypeBLedgerOutcome:
    """Append one Type B scan plus all qualifying independent value signals."""
    ensure_type_b_ledger(conn)
    key = _scan_key(
        horizon_start=horizon_start,
        horizon_end=horizon_end,
        candidate_match_ids=candidate_match_ids,
        market_policy_version_id=market_policy_version_id,
        sharp_reference=sharp_reference,
        as_of=decision_as_of,
    )
    row = conn.execute(
        """
        INSERT INTO type_b_scan_runs (
            scan_key,
            horizon_start,
            horizon_end,
            candidate_match_count,
            value_signal_count,
            market_policy_version_id,
            sharp_source_system,
            sharp_bookmaker_code,
            as_of
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (scan_key) DO NOTHING
        RETURNING type_b_scan_run_id
        """,
        (
            key,
            horizon_start,
            horizon_end,
            len(candidate_match_ids),
            len(signals),
            market_policy_version_id,
            sharp_reference.source_system,
            sharp_reference.bookmaker_code,
            decision_as_of,
        ),
    ).fetchone()
    if row is None:
        existing = conn.execute(
            """
            SELECT type_b_scan_run_id, candidate_match_count, value_signal_count
            FROM type_b_scan_runs
            WHERE scan_key = %s
            """,
            (key,),
        ).fetchone()
        if existing is None:
            raise RuntimeError("Type B scan idempotency lookup failed")
        if int(existing[1]) != len(candidate_match_ids) or int(existing[2]) != len(signals):
            raise RuntimeError("Type B scan idempotency mismatch")
        scan_run_id = int(existing[0])
    else:
        scan_run_id = int(row[0])

    inserted = 0
    for signal in signals:
        lineage = snapshot_lineage_hash(
            [
                ("REFERENCE_PLAYER_1", signal.reference_player_1_snapshot_id),
                ("REFERENCE_PLAYER_2", signal.reference_player_2_snapshot_id),
                ("OFFERED_SELECTION", signal.offered_snapshot_id),
            ]
        )
        inserted_row = conn.execute(
            """
            INSERT INTO type_b_value_signals (
                type_b_scan_run_id,
                match_id,
                market_policy_version_id,
                selection_side,
                sharp_fair_probability,
                sharp_fair_odds,
                sharp_reference_price,
                offered_price,
                price_source_system,
                bookmaker_code,
                edge_pct,
                threshold,
                min_acceptable_odds,
                reference_player_1_snapshot_id,
                reference_player_2_snapshot_id,
                offered_snapshot_id,
                snapshot_lineage_hash,
                as_of
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (type_b_scan_run_id, match_id, selection_side, snapshot_lineage_hash)
            DO NOTHING
            RETURNING type_b_value_signal_id
            """,
            (
                scan_run_id,
                signal.match_id,
                market_policy_version_id,
                signal.selection_side,
                signal.sharp_fair_probability,
                signal.sharp_fair_odds,
                signal.sharp_reference_price,
                signal.offered_price,
                signal.price_source_system,
                signal.bookmaker_code,
                signal.edge_pct,
                signal.threshold,
                signal.min_acceptable_odds,
                signal.reference_player_1_snapshot_id,
                signal.reference_player_2_snapshot_id,
                signal.offered_snapshot_id,
                lineage,
                decision_as_of,
            ),
        ).fetchone()
        inserted += int(inserted_row is not None)

    persisted = int(
        conn.execute(
            "SELECT count(*) FROM type_b_value_signals WHERE type_b_scan_run_id = %s",
            (scan_run_id,),
        ).fetchone()[0]
    )
    if persisted != len(signals):
        raise RuntimeError(
            f"Type B persisted signal count mismatch: expected={len(signals)} actual={persisted}"
        )
    return TypeBLedgerOutcome(
        scan_run_id=scan_run_id,
        inserted_signal_count=inserted,
        persisted_signal_count=persisted,
    )


def consecutive_zero_signal_days(
    conn: psycopg.Connection,
    *,
    through_day: date,
    required_days: int = 7,
) -> int:
    """Return required_days only when every consecutive UTC day has scans but no signal."""
    ensure_type_b_ledger(conn)
    rows = conn.execute(
        """
        SELECT (as_of AT TIME ZONE 'UTC')::date AS scan_day,
               MAX(value_signal_count) AS max_signal_count
        FROM type_b_scan_runs
        WHERE (as_of AT TIME ZONE 'UTC')::date BETWEEN %s AND %s
        GROUP BY scan_day
        ORDER BY scan_day DESC
        """,
        (through_day - timedelta(days=required_days - 1), through_day),
    ).fetchall()
    if len(rows) != required_days:
        return 0
    expected_days = [through_day - timedelta(days=offset) for offset in range(required_days)]
    actual_days = [row[0] for row in rows]
    if actual_days != expected_days:
        return 0
    if any(int(row[1]) > 0 for row in rows):
        return 0
    return required_days
