from __future__ import annotations

import os
import time
from collections.abc import Mapping, Sequence

import psycopg

from sports_edge_tennis.live.credentials import load_secret
from sports_edge_tennis.live.sportradar import fetch_sport_event_summary
from sports_edge_tennis.live.sportradar_reconciliation import resolve_existing_player


MATCH_IDS = (130890, 130892, 130893, 130894, 130895, 131031)
SOURCE_SYSTEM = "SPORTRADAR"


def _mapping(value):
    return value if isinstance(value, Mapping) else {}


def _list(value):
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    return [item for item in value if isinstance(item, Mapping)]


def _latest_match(conn, match_id: int):
    return conn.execute(
        """
        SELECT
            mv.match_version_id,
            mv.player_1_id,
            p1.canonical_name,
            mv.player_2_id,
            p2.canonical_name,
            mv.match_start_at,
            mv.match_status::text
        FROM match_versions mv
        JOIN players p1 ON p1.player_id = mv.player_1_id
        JOIN players p2 ON p2.player_id = mv.player_2_id
        WHERE mv.match_id = %s
        ORDER BY mv.as_of DESC, mv.source_observed_at DESC, mv.match_version_id DESC
        LIMIT 1
        """,
        (match_id,),
    ).fetchone()


def _event_id(conn, match_id: int):
    rows = conn.execute(
        """
        SELECT source_match_id
        FROM match_source_ids
        WHERE match_id = %s
          AND source_system = 'SPORTRADAR'
        ORDER BY match_source_id
        """,
        (match_id,),
    ).fetchall()
    return tuple(str(row[0]) for row in rows)


def _candidate_evidence(conn, candidate_ids: tuple[int, ...]):
    if not candidate_ids:
        return ()
    rows = conn.execute(
        """
        SELECT
            p.player_id,
            p.canonical_name,
            COALESCE(array_agg(DISTINCT psi.source_system || ':' || psi.source_player_id)
                FILTER (WHERE psi.player_source_id IS NOT NULL), '{}') AS source_ids,
            COALESCE(array_agg(DISTINCT pa.normalized_alias)
                FILTER (WHERE pa.player_alias_id IS NOT NULL), '{}') AS aliases
        FROM players p
        LEFT JOIN player_source_ids psi ON psi.player_id = p.player_id
        LEFT JOIN player_aliases pa ON pa.player_id = p.player_id
        WHERE p.player_id = ANY(%s)
        GROUP BY p.player_id, p.canonical_name
        ORDER BY p.player_id
        """,
        (list(candidate_ids),),
    ).fetchall()
    return tuple(
        {
            "player_id": int(row[0]),
            "name": str(row[1]),
            "source_ids": tuple(sorted(str(v) for v in (row[2] or ()))),
            "aliases": tuple(sorted(str(v) for v in (row[3] or ()))),
        }
        for row in rows
    )


def _bridge_evidence(conn, provider_player_id: str):
    rows = conn.execute(
        """
        SELECT
            live_player_id,
            action,
            historical_player_id,
            evidence_provider_name,
            evidence_status
        FROM model_identity_bridge_events
        WHERE evidence_source_system = 'SPORTRADAR'
          AND evidence_provider_player_id = %s
        ORDER BY evidence_as_of DESC, model_identity_bridge_event_id DESC
        LIMIT 5
        """,
        (provider_player_id,),
    ).fetchall()
    return tuple(
        {
            "live_player_id": int(row[0]),
            "action": str(row[1]),
            "historical_player_id": int(row[2]) if row[2] is not None else None,
            "provider_name": str(row[3]) if row[3] is not None else None,
            "status": str(row[4]) if row[4] is not None else None,
        }
        for row in rows
    )


def _classification(resolution, evidence):
    if resolution.status == "SOURCE_ID_RESOLVED":
        return "EXACT_RESOLVABLE"
    if resolution.status == "UNIQUE_NAME_RESOLVED":
        return "EXACT_RESOLVABLE"
    if resolution.status == "SOURCE_ID_CONFLICT":
        return "DUPLICATE_CANONICAL_IDENTITY"
    if resolution.status == "AMBIGUOUS_PLAYER":
        source_sets = [
            tuple(v for v in row["source_ids"] if v.startswith("API_TENNIS:") or v.startswith("SACKMANN:"))
            for row in evidence
        ]
        return (
            "DUPLICATE_CANONICAL_IDENTITY"
            if sum(bool(x) for x in source_sets) > 1
            else "TRUE_AMBIGUOUS"
        )
    if resolution.status == "NEW_PLAYER":
        return "MISSING_SOURCE_BRIDGE"
    return "INSUFFICIENT_EVIDENCE"


def main() -> int:
    dsn = os.environ.get("TENNIS_DATABASE_URL") or os.environ.get("DATABASE_URL")
    api_key = load_secret("SPORTRADAR_TENNIS_API_KEY")
    if not dsn:
        print("IDENTITY_AUDIT_BLOCKED missing_database", flush=True)
        return 2
    if not api_key:
        print("IDENTITY_AUDIT_BLOCKED missing_sportradar_key", flush=True)
        return 2

    access = os.environ.get("SPORTRADAR_TENNIS_ACCESS_LEVEL", "production")
    language = os.environ.get("SPORTRADAR_TENNIS_LANGUAGE", "en")
    conn = psycopg.connect(
        dsn,
        autocommit=True,
        options="-c default_transaction_read_only=on",
    )
    try:
        read_only = conn.execute("SHOW default_transaction_read_only").fetchone()[0]
        print(
            f"IDENTITY_AUDIT_START matches={len(MATCH_IDS)} transaction_default_read_only={read_only} writes=0",
            flush=True,
        )
        root_counter: dict[str, int] = {}

        for index, match_id in enumerate(MATCH_IDS):
            latest = _latest_match(conn, match_id)
            event_ids = _event_id(conn, match_id)
            if latest is None:
                print(f"IDENTITY_AUDIT_MATCH match_id={match_id} status=NO_CANONICAL_MATCH", flush=True)
                continue
            if len(event_ids) != 1:
                print(
                    f"IDENTITY_AUDIT_MATCH match_id={match_id} status=EVENT_SOURCE_ID_COUNT count={len(event_ids)}",
                    flush=True,
                )
                continue

            if index:
                time.sleep(0.25)
            try:
                _, payload = fetch_sport_event_summary(
                    api_key,
                    event_ids[0],
                    access_level=access,
                    language=language,
                )
            except Exception as exc:
                print(
                    f"IDENTITY_AUDIT_MATCH match_id={match_id} event_id={event_ids[0]} status=PROVIDER_ERROR kind={type(exc).__name__}",
                    flush=True,
                )
                continue

            event = _mapping(payload.get("sport_event"))
            competitors = _list(event.get("competitors"))
            by_side = {
                str(c.get("qualifier") or "").strip().casefold(): c
                for c in competitors
            }
            provider_rows = []
            for side in ("home", "away"):
                competitor = by_side.get(side)
                if not competitor:
                    provider_rows.append({"side": side, "status": "MISSING"})
                    continue
                provider_id = str(competitor.get("id") or "").strip()
                provider_name = str(competitor.get("name") or "").strip()
                resolution = resolve_existing_player(
                    conn,
                    provider_player_id=provider_id,
                    provider_name=provider_name,
                )
                evidence = _candidate_evidence(conn, resolution.candidate_player_ids)
                bridges = _bridge_evidence(conn, provider_id)
                classification = _classification(resolution, evidence)
                root_counter[classification] = root_counter.get(classification, 0) + 1
                provider_rows.append(
                    {
                        "side": side,
                        "provider_id": provider_id,
                        "provider_name": provider_name,
                        "resolver_status": resolution.status,
                        "resolved_player_id": resolution.player_id,
                        "candidate_ids": resolution.candidate_player_ids,
                        "classification": classification,
                        "candidate_evidence": evidence,
                        "bridge_evidence": bridges,
                    }
                )

            print(
                "IDENTITY_AUDIT_MATCH "
                f"match_id={match_id} event_id={event_ids[0]} "
                f"latest_version={latest[0]} canonical_p1={latest[1]}:{latest[2]!r} "
                f"canonical_p2={latest[3]}:{latest[4]!r} start={latest[5]} status={latest[6]}",
                flush=True,
            )
            for row in provider_rows:
                print(
                    "IDENTITY_AUDIT_PLAYER "
                    f"match_id={match_id} side={row.get('side')} "
                    f"provider_id={row.get('provider_id','-')} provider_name={row.get('provider_name','-')!r} "
                    f"resolver={row.get('resolver_status','-')} resolved={row.get('resolved_player_id')} "
                    f"candidates={row.get('candidate_ids',())} classification={row.get('classification','-')}",
                    flush=True,
                )
                for ev in row.get("candidate_evidence", ()):
                    print(
                        "IDENTITY_AUDIT_CANDIDATE "
                        f"match_id={match_id} provider_id={row.get('provider_id')} "
                        f"player_id={ev['player_id']} name={ev['name']!r} "
                        f"sources={ev['source_ids']} aliases={ev['aliases']}",
                        flush=True,
                    )
                for bridge in row.get("bridge_evidence", ()):
                    print(
                        "IDENTITY_AUDIT_BRIDGE "
                        f"match_id={match_id} provider_id={row.get('provider_id')} "
                        f"live={bridge['live_player_id']} action={bridge['action']} "
                        f"historical={bridge['historical_player_id']} status={bridge['status']}",
                        flush=True,
                    )

        print(
            "IDENTITY_AUDIT_SUMMARY "
            + " ".join(f"{key}={value}" for key, value in sorted(root_counter.items()))
            + " writes=0",
            flush=True,
        )
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
