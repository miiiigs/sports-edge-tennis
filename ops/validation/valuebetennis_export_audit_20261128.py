"""Aggregate-only inspection of local Valuebetennis season exports.

Does not train, import to production, certify licensing or infer historical
visibility from the advertised UTC match date. Raw rows never leave this process.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path

REQUIRED = frozenset(("match_id", "date", "tournoi", "tournoi_id", "categorie",
    "genre", "surface", "tour", "joueur1", "joueur1_id", "joueur2", "joueur2_id",
    "cote1_ouverture", "cote2_ouverture", "cote1_cloture", "cote2_cloture",
    "vainqueur_id", "score", "duree_min"))
CATEGORY = {"itf": "ITF", "challenger": "CHALLENGER", "main tour": "ATP_MAIN",
            "masters": "ATP_MAIN", "grand slam": "ATP_MAIN"}


def positive_id(value):
    value = value.strip()
    return value if value.isascii() and value.isdecimal() and int(value) > 0 else None


def timestamp(value):
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return "INVALID", None
    # Midnight may be genuine, but is not proof of exact competitive start.
    if len(value.strip()) == 10:
        return "DATE_ONLY", parsed
    if parsed.tzinfo is None:
        # Supplier states UTC; distinguish assumed from encoded offset.
        return "TIME_WITH_DOCUMENTED_UTC", parsed.replace(tzinfo=timezone.utc)
    return "OFFSET_ENCODED", parsed.astimezone(timezone.utc)


def audit(paths):
    counts = Counter()
    groups = defaultdict(Counter)
    seen = {}
    conflicting_ids = set()
    duplicated = set()
    players = defaultdict(set)
    files = []
    for path in paths:
        path = Path(path)
        raw = path.read_bytes()
        # Official page documents the field names, not a guaranteed delimiter.
        with path.open(encoding="utf-8-sig", newline="") as handle:
            dialect = csv.Sniffer().sniff(handle.read(16384), delimiters=",;\t")
            handle.seek(0)
            reader = csv.DictReader(handle, dialect=dialect)
            if reader.fieldnames is None or not REQUIRED.issubset(reader.fieldnames):
                raise ValueError("unsupported export columns")
            file_count = 0
            for row in reader:
                if None in row or any(row.get(k) is None for k in REQUIRED):
                    raise ValueError("malformed export row")
                file_count += 1
                counts["raw_rows"] += 1
                mid = positive_id(row["match_id"])
                if mid is None:
                    counts["invalid_match_id"] += 1
                    continue
                signature = tuple(row[k].strip() for k in sorted(REQUIRED))
                if mid in seen:
                    counts["duplicate_rows"] += 1
                    duplicated.add(mid)
                    if seen[mid][0] != signature:
                        conflicting_ids.add(mid)
                    continue
                gender = row["genre"].strip().lower()
                cat = row["categorie"].strip().lower()
                group = CATEGORY.get(cat, "UNKNOWN_CATEGORY") if gender == "atp" else (
                    "WOMEN" if gender == "wta" else "UNKNOWN_GENDER")
                p1, p2, winner = (positive_id(row[k]) for k in
                                   ("joueur1_id", "joueur2_id", "vainqueur_id"))
                date_class, dt = timestamp(row["date"])
                flags = set()
                if p1 is None or p2 is None or p1 == p2:
                    flags.add("INVALID_PLAYER_PAIR")
                if winner is None or winner not in (p1, p2):
                    flags.add("INVALID_WINNER")
                if date_class == "INVALID":
                    flags.add("INVALID_DATE")
                if not row["surface"].strip():
                    flags.add("MISSING_SURFACE")
                if not row["score"].strip():
                    flags.add("MISSING_SCORE")
                if positive_id(row["tournoi_id"]) is None:
                    flags.add("INVALID_TOURNAMENT_ID")
                seen[mid] = (signature, group, flags, date_class, dt,
                             p1,p2,row["joueur1"].strip(),row["joueur2"].strip())
            files.append({"file": path.name, "sha256": sha256(raw).hexdigest(),
                          "bytes": len(raw), "raw_rows": file_count})
    for mid, (_, group, flags, date_class, dt, p1,p2,n1,n2) in seen.items():
        c = groups[group]
        c["unique_matches"] += 1
        c["date_"+date_class] += 1
        if dt is not None:
            c["year_"+str(dt.year)] += 1
            if dt.hour == dt.minute == dt.second == dt.microsecond == 0:
                c["midnight_timestamp"] += 1
        if mid in duplicated:
            c["duplicated_match_ids"] += 1
        if mid in conflicting_ids:
            flags.add("CONFLICTING_DUPLICATE")
        for flag in flags:
            c[flag] += 1
        if p1 and n1:
            players[p1].add(n1.casefold())
        if p2 and n2:
            players[p2].add(n2.casefold())
        if group in ("ATP_MAIN","CHALLENGER","ITF") and not flags:
            c["structurally_complete_rows"] += 1
    return {"classification": "LOCAL_STRUCTURAL_AUDIT_ONLY",
            "files": files, "counts": dict(counts),
            "groups": {k:dict(v) for k,v in sorted(groups.items())},
            "unique_match_ids": len(seen),
            "conflicting_duplicate_ids": len(conflicting_ids),
            "unique_player_ids_with_names": len(players),
            "player_ids_with_multiple_normalized_names": sum(len(x)>1 for x in players.values()),
            "cross_provider_identity_verified": False,
            "actual_start_precision_verified": False,
            "original_ingestion_times_available_in_published_schema": False,
            "commercial_source_chain_verified": False,
            "usable_for_commercial_training": False, "can_promote": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("files", nargs="+", type=Path)
    args = parser.parse_args()
    print(json.dumps(audit(args.files), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
