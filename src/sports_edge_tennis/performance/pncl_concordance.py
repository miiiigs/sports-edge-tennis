"""Read-only concordance evidence for API-Tennis bookmaker key ``Pncl``.

This module does not assume that ``Pncl`` means Pinnacle. It compares already-
persisted API-Tennis/Pncl MATCH_WINNER quotes against the nearest already-
reconciled SHARP/PINNACLE quote for the same canonical match and selection.
The result is observational evidence only and never changes bookmaker mapping,
normalization class, market policy, or production decisions.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from statistics import median

import psycopg


@dataclass(frozen=True)
class PnclQuotePair:
    match_id: int
    selection_type: str
    pncl_price: Decimal
    pinnacle_price: Decimal
    pncl_observed_at: datetime
    pinnacle_observed_at: datetime
    gap_seconds: int

    @property
    def absolute_price_difference(self) -> Decimal:
        return abs(self.pncl_price - self.pinnacle_price)


@dataclass(frozen=True)
class PnclPinnacleConcordance:
    mapping_found: bool
    mapping_bookmaker_code: str | None
    mapping_class: str | None
    pncl_quote_count: int
    pncl_match_count: int
    pairs: tuple[PnclQuotePair, ...]

    @property
    def overlap_count(self) -> int:
        return len(self.pairs)

    @property
    def overlap_match_count(self) -> int:
        return len({pair.match_id for pair in self.pairs})

    @property
    def exact_price_count(self) -> int:
        return sum(pair.absolute_price_difference == 0 for pair in self.pairs)

    @property
    def mean_absolute_price_difference(self) -> Decimal | None:
        if not self.pairs:
            return None
        return sum(
            (pair.absolute_price_difference for pair in self.pairs),
            Decimal("0"),
        ) / Decimal(len(self.pairs))

    @property
    def median_absolute_price_difference(self) -> Decimal | None:
        if not self.pairs:
            return None
        return Decimal(str(median([pair.absolute_price_difference for pair in self.pairs])))

    @property
    def max_absolute_price_difference(self) -> Decimal | None:
        if not self.pairs:
            return None
        return max(pair.absolute_price_difference for pair in self.pairs)

    @property
    def max_gap_seconds(self) -> int | None:
        if not self.pairs:
            return None
        return max(pair.gap_seconds for pair in self.pairs)


def load_pncl_pinnacle_concordance(
    conn: psycopg.Connection,
    *,
    as_of: datetime,
    lookback_hours: int = 24,
    max_gap_minutes: int = 15,
    pair_limit: int = 200,
) -> PnclPinnacleConcordance:
    """Compare API-Tennis/Pncl with canonical SHARP/PINNACLE snapshots.

    Pairing is canonical-match and selection exact. For each Pncl quote, the
    nearest Pinnacle quote inside ``max_gap_minutes`` is selected. No provider
    request is made and no database state is mutated.
    """
    if as_of.tzinfo is None:
        raise ValueError("as_of must be timezone-aware")
    if not 1 <= lookback_hours <= 168:
        raise ValueError("lookback_hours must be between 1 and 168")
    if not 1 <= max_gap_minutes <= 60:
        raise ValueError("max_gap_minutes must be between 1 and 60")
    if not 1 <= pair_limit <= 1000:
        raise ValueError("pair_limit must be between 1 and 1000")

    mapping = conn.execute(
        """
        SELECT bookmaker_code, normalization_class::text
        FROM provider_bookmaker_mappings
        WHERE provider_code = 'API_TENNIS'
          AND lower(provider_bookmaker_key) = 'pncl'
          AND effective_from <= %s
        ORDER BY effective_from DESC, provider_bookmaker_mapping_id DESC
        LIMIT 1
        """,
        (as_of,),
    ).fetchone()

    counts = conn.execute(
        """
        SELECT count(*), count(DISTINCT os.match_id)
        FROM odds_snapshots os
        JOIN provider_bookmaker_mappings pbm
          ON pbm.provider_bookmaker_mapping_id = os.provider_bookmaker_mapping_id
        WHERE pbm.provider_code = 'API_TENNIS'
          AND lower(pbm.provider_bookmaker_key) = 'pncl'
          AND os.market_type = 'MATCH_WINNER'
          AND os.selection_type IN ('PLAYER_1', 'PLAYER_2')
          AND os.source_observed_at > %s - (%s * interval '1 hour')
          AND os.source_observed_at <= %s
        """,
        (as_of, lookback_hours, as_of),
    ).fetchone()
    pncl_quote_count = int(counts[0]) if counts is not None else 0
    pncl_match_count = int(counts[1]) if counts is not None else 0

    rows = conn.execute(
        """
        WITH pncl AS (
            SELECT
                os.odds_snapshot_id,
                os.match_id,
                os.selection_type::text AS selection_type,
                os.decimal_price,
                os.source_observed_at
            FROM odds_snapshots os
            JOIN provider_bookmaker_mappings pbm
              ON pbm.provider_bookmaker_mapping_id = os.provider_bookmaker_mapping_id
            WHERE pbm.provider_code = 'API_TENNIS'
              AND lower(pbm.provider_bookmaker_key) = 'pncl'
              AND os.market_type = 'MATCH_WINNER'
              AND os.selection_type IN ('PLAYER_1', 'PLAYER_2')
              AND os.source_observed_at > %s - (%s * interval '1 hour')
              AND os.source_observed_at <= %s
        )
        SELECT
            p.match_id,
            p.selection_type,
            p.decimal_price,
            sharp.decimal_price,
            p.source_observed_at,
            sharp.source_observed_at,
            round(abs(extract(epoch FROM (sharp.source_observed_at - p.source_observed_at))))::bigint
        FROM pncl p
        JOIN LATERAL (
            SELECT s.decimal_price, s.source_observed_at
            FROM odds_snapshots s
            WHERE s.match_id = p.match_id
              AND s.source_system = 'SHARP'
              AND s.bookmaker_code = 'PINNACLE'
              AND s.market_type = 'MATCH_WINNER'
              AND s.selection_type::text = p.selection_type
              AND abs(extract(epoch FROM (s.source_observed_at - p.source_observed_at))) <= %s
            ORDER BY
                abs(extract(epoch FROM (s.source_observed_at - p.source_observed_at))),
                s.source_observed_at DESC,
                s.odds_snapshot_id DESC
            LIMIT 1
        ) sharp ON TRUE
        ORDER BY p.source_observed_at DESC, p.odds_snapshot_id DESC
        LIMIT %s
        """,
        (as_of, lookback_hours, as_of, max_gap_minutes * 60, pair_limit),
    ).fetchall()

    pairs = tuple(
        PnclQuotePair(
            match_id=int(row[0]),
            selection_type=str(row[1]),
            pncl_price=Decimal(row[2]),
            pinnacle_price=Decimal(row[3]),
            pncl_observed_at=row[4],
            pinnacle_observed_at=row[5],
            gap_seconds=int(row[6]),
        )
        for row in rows
    )
    return PnclPinnacleConcordance(
        mapping_found=mapping is not None,
        mapping_bookmaker_code=None if mapping is None else str(mapping[0]),
        mapping_class=None if mapping is None else str(mapping[1]),
        pncl_quote_count=pncl_quote_count,
        pncl_match_count=pncl_match_count,
        pairs=pairs,
    )


def build_pncl_concordance_lines(
    evidence: PnclPinnacleConcordance,
    *,
    sample_limit: int = 5,
) -> tuple[str, ...]:
    """Render bounded secret-free evidence lines; never make an identity verdict."""
    if sample_limit < 0 or sample_limit > 20:
        raise ValueError("sample_limit must be between 0 and 20")

    def fmt(value: Decimal | int | None) -> str:
        return "-" if value is None else str(value)

    lines = [
        "PNCL_PINNACLE_CONCORDANCE "
        f"mapping_found={int(evidence.mapping_found)} "
        f"mapping_bookmaker={evidence.mapping_bookmaker_code or '-'} "
        f"mapping_class={evidence.mapping_class or '-'} "
        f"pncl_quotes={evidence.pncl_quote_count} "
        f"pncl_matches={evidence.pncl_match_count} "
        f"overlaps={evidence.overlap_count} "
        f"overlap_matches={evidence.overlap_match_count} "
        f"exact_prices={evidence.exact_price_count} "
        f"mean_abs_diff={fmt(evidence.mean_absolute_price_difference)} "
        f"median_abs_diff={fmt(evidence.median_absolute_price_difference)} "
        f"max_abs_diff={fmt(evidence.max_absolute_price_difference)} "
        f"max_gap_seconds={fmt(evidence.max_gap_seconds)} "
        "identity_verified=0 classification_changed=0"
    ]
    for pair in evidence.pairs[:sample_limit]:
        lines.append(
            "PNCL_PINNACLE_SAMPLE "
            f"match_id={pair.match_id} selection={pair.selection_type} "
            f"pncl_price={pair.pncl_price} pinnacle_price={pair.pinnacle_price} "
            f"abs_diff={pair.absolute_price_difference} "
            f"gap_seconds={pair.gap_seconds}"
        )
    return tuple(lines)


__all__ = [
    "PnclPinnacleConcordance",
    "PnclQuotePair",
    "build_pncl_concordance_lines",
    "load_pncl_pinnacle_concordance",
]
