import json
from pathlib import Path
import pytest
from ops.validation.reliability_offset_synthetic_20261128 import experiment, population

EVIDENCE=Path(__file__).resolve().parents[1]/"docs/research/evidence/reliability_offset_synthetic_20261128.json"


def test_fixed_offset_synthetic_reproduction():
    expected=json.loads(EVIDENCE.read_text())
    actual=experiment()
    for regime,arms in actual.items():
        for arm,values in arms.items():
            for key,value in values.items():
                if value is None:
                    assert expected["results"][regime][arm][key] is None
                else:
                    assert value==pytest.approx(expected["results"][regime][arm][key],abs=1e-10)
    assert expected["can_promote"] is False


def test_chronological_split_has_no_duplicate_ids():
    rows=population(True)
    assert len({r["id"] for r in rows})==6000
    assert rows[3599]["id"]<rows[3600]["id"]<rows[4800]["id"]
