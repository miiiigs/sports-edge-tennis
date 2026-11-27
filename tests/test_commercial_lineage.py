from sports_edge_tennis.validation.commercial_lineage import (
    REQUIRED_COMPONENTS, RIGHTS, audit_lineage, recovered_source_census,
)


def test_transitive_restricted_dependency_blocks():
    source = {"id": "s", "kind": "source", "parents": [], "sha256": "a"*64, "source_version": "v1"}
    model = {"id": "m", "kind": "artifact", "parents": ["s"], "sha256": "b"*64}
    roots = {k: ["m"] for k in REQUIRED_COMPONENTS}
    register = {"s": {"status": "RESTRICTED", "rights": list(RIGHTS), "evidence_ref": "contract", "source_version": "v1"}}
    assert not audit_lineage([source, model], roots, register)["manifest_gate_passed"]
    register["s"]["status"] = "COMMERCIAL_CONFIRMED"
    report = audit_lineage([source, model], roots, register)
    assert report["manifest_gate_passed"]
    assert not report["promotion_authorized"]
    register["s"]["source_version"] = "v2"
    assert not audit_lineage([source, model], roots, register)["manifest_gate_passed"]


def test_missing_roots_and_cycle_fail_closed():
    node = {"id": "a", "kind": "artifact", "parents": ["a", "missing"], "sha256": "a"*64}
    report = audit_lineage([node], {"a1_results": ["a"]}, {})
    assert not report["manifest_gate_passed"]
    assert {"cycle", "missing_node", "missing_component_lineage"} <= {b["reason"] for b in report["blockers"]}


def test_census_distinguishes_result_version_and_identity():
    report = recovered_source_census([
        {"match_id": 1, "result_source": "SACKMANN", "version_source": "API_TENNIS",
         "original_players": [1, 2], "bridged_players": [3, 4]},
        {"match_id": 2, "original_players": [1, 2], "bridged_players": [1, 2]},
    ])
    assert (report["matches"], report["changed_matches"], report["changed_links"]) == (2, 1, 2)
    assert any(g["identity_source"] == "UNKNOWN" and g["metric"] == "changed_links" and g["n"] == 2 for g in report["groups"])
