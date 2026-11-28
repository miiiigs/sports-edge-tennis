"""Exact committed historical reconciliation: no network and no model fitting."""
import json
from pathlib import Path

from ops.validation.a1_baseline_reconciliation_20261128 import (
    FULL, RELIABILITY, read_unique, reconcile,
)

EVIDENCE = Path(__file__).resolve().parents[1] / "docs" / "research" / "evidence"


def test_actual_frozen_baseline_join_reproduces_committed_counts():
    actual = reconcile(read_unique(EVIDENCE / FULL),
                       read_unique(EVIDENCE / RELIABILITY))
    expected = json.loads((EVIDENCE / "a1_baseline_reconciliation_20261128.json").read_text())
    assert actual["counts"] == expected["counts"]
    assert actual["points"] == expected["points"]
    for key, comparison in actual["replacement"].items():
        for field, value in comparison.items():
            assert value == expected["replacement"][key][field]
    assert actual["marginal"]["groups"]["all"] == expected["marginal"]["groups"]["all"]
    assert actual["can_promote"] is False
