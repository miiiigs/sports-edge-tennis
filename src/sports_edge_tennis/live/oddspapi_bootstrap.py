from __future__ import annotations

from datetime import UTC, datetime

import psycopg


PROVIDER_CODE = "ODDSPAPI"
MAPPINGS = (
    ("pinnacle+30", "PINNACLE", "SHARP"),
    ("1xbet", "1XBET", "OFFERED"),
    ("unibet", "UNIBET", "OFFERED"),
    ("bet365", "BET365", "OFFERED"),
)
MAPPING_VERSION = 1


class OddspapiBookmakerMappingConflictError(RuntimeError):
    pass


def ensure_oddspapi_bookmaker_mappings(
    conn: psycopg.Connection,
    *,
    clock: callable | None = None,
) -> tuple[int, ...]:
    clock = clock or (lambda: datetime.now(UTC))
    ids: list[int] = []
    for provider_key, bookmaker_code, normalization_class in MAPPINGS:
        row = conn.execute(
            """
            SELECT provider_bookmaker_mapping_id, bookmaker_code, normalization_class::text
            FROM provider_bookmaker_mappings
            WHERE provider_code = %s AND provider_bookmaker_key = %s
            ORDER BY effective_from DESC, provider_bookmaker_mapping_id DESC
            LIMIT 1
            """,
            (PROVIDER_CODE, provider_key),
        ).fetchone()
        if row is not None:
            if str(row[1]) != bookmaker_code or str(row[2]) != normalization_class:
                raise OddspapiBookmakerMappingConflictError(
                    f"OddsPapi mapping conflict for {provider_key}: {row[1]}/{row[2]}"
                )
            ids.append(int(row[0]))
            continue
        mapping_id = conn.execute(
            """
            INSERT INTO provider_bookmaker_mappings (
                provider_code, provider_bookmaker_key, mapping_version,
                bookmaker_code, normalization_class, effective_from
            ) VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING provider_bookmaker_mapping_id
            """,
            (
                PROVIDER_CODE,
                provider_key,
                MAPPING_VERSION,
                bookmaker_code,
                normalization_class,
                clock(),
            ),
        ).fetchone()[0]
        ids.append(int(mapping_id))
    return tuple(ids)


__all__ = ["MAPPINGS", "PROVIDER_CODE", "ensure_oddspapi_bookmaker_mappings"]
