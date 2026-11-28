"""Offline commercial lineage audit. Never grants rights or accesses providers."""
from collections import Counter
from hashlib import sha256
import json

STATUSES = frozenset({
    "COMMERCIAL_CONFIRMED", "RESEARCH_ONLY", "RESTRICTED",
    "CONTRACT_DEPENDENT", "UNRESOLVED", "UNKNOWN_PROVENANCE",
})
RIGHTS = frozenset({"training", "derived_forecasts", "retention", "publication"})
REQUIRED_COMPONENTS = frozenset({
    "a1_results", "a1_identities", "overall_ratings", "surface_ratings",
    "a2_stats", "a3_data", "calibration", "manager_training",
    "manager_evaluation", "saved_models", "precomputed_ratings",
    "feature_snapshots", "cached_history", "frozen_manifests",
})


def audit_lineage(nodes, roots, rights_register):
    """Audit explicit dependency graph; source evidence must be externally reviewed.

    Nodes: id, kind (source/artifact), parents, sha256, optional source_version.
    Register: source node id -> status, rights, evidence_ref, source_version.
    Roots: mapping of required component to one or more node ids.
    Does not authenticate evidence or prove completeness of supplied manifests.
    """
    index = {}
    for node in nodes:
        key = node["id"]
        if not isinstance(key, str) or not key or key in index:
            raise ValueError("duplicate or invalid node id")
        index[key] = node
    blockers = set()
    visited = set()
    sources = set()

    def visit(key, active):
        if key in active:
            blockers.add((key, "cycle"))
            return
        if key in visited:
            return
        node = index.get(key)
        if node is None:
            blockers.add((key, "missing_node"))
            return
        digest = node.get("sha256")
        if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            blockers.add((key, "missing_or_invalid_digest"))
        parents = node.get("parents")
        if not isinstance(parents, list) or any(not isinstance(p, str) or not p for p in parents):
            blockers.add((key, "invalid_parents"))
            parents = []
        if node.get("kind") == "source":
            sources.add(key)
            record = rights_register.get(key, {})
            status = record.get("status", "UNKNOWN_PROVENANCE")
            if status not in STATUSES or status != "COMMERCIAL_CONFIRMED":
                blockers.add((key, "rights_not_confirmed"))
            if not RIGHTS.issubset(record.get("rights") or []):
                blockers.add((key, "required_rights_missing"))
            if not record.get("evidence_ref") or not node.get("source_version") or record.get("source_version") != node.get("source_version"):
                blockers.add((key, "version_bound_evidence_missing"))
        elif node.get("kind") != "artifact" or not parents:
            blockers.add((key, "untraced_artifact"))
        for parent in parents:
            visit(parent, active | {key})
        visited.add(key)

    for component in sorted(REQUIRED_COMPONENTS | set(roots)):
        ids = roots.get(component)
        if not isinstance(ids, list) or not ids or any(not isinstance(x, str) or not x for x in ids):
            blockers.add((component, "missing_component_lineage"))
            continue
        for key in ids:
            visit(key, set())
    return {"manifest_gate_passed": not blockers, "promotion_authorized": False,
            "sources": sorted(sources), "visited_nodes": sorted(visited),
            "blockers": [{"id": k, "reason": r} for k, r in sorted(blockers)],
            "requires_independent_completeness_and_rights_review": True}


def recovered_source_census(rows):
    """Consume one selected PIT match per row, not arbitrary latest source rows.

    Include result_source, version_source, original_players, bridged_players;
    keep result/version source combinations distinct; identity source is separate.
    Missing source labels remain UNKNOWN, never inferred from Agent 2.
    """
    counts = Counter()
    seen = set()
    links = set()
    link_sources = {}
    changed_matches = 0
    for row in rows:
        key = row["match_id"]
        if key in seen:
            raise ValueError("duplicate match")
        seen.add(key)
        before, after = row["original_players"], row["bridged_players"]
        if len(before) != 2 or len(after) != 2 or None in (*before, *after) or len(set(before)) != 2 or len(set(after)) != 2:
            raise ValueError("invalid player pair")
        source = tuple(row.get(k) or "UNKNOWN" for k in ("result_source", "version_source", "identity_source"))
        changed = before != after
        counts[(*source, "all_matches")] += 1
        if changed:
            changed_matches += 1
            counts[(*source, "changed_matches")] += 1
        for old, new in zip(before, after):
            if old != new:
                link = (key, new)
                links.add(link)
                link_sources[link] = source
    for source in link_sources.values():
        counts[(*source, "changed_links")] += 1
    return {"matches": len(seen), "changed_matches": changed_matches,
            "changed_links": len(links),
            "groups": [{"result_source": r, "version_source": v, "identity_source": i,
                        "metric": m, "n": n} for (r, v, i, m), n in sorted(counts.items())]}


def manifest_digest(manifest):
    return sha256(json.dumps(manifest, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
