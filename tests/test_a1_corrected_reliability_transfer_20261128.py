import json
from ops.validation.a1_corrected_reliability_transfer_20261128 import ROOT, evaluate, load


def test_reproduce_corrected_no_fit_transfer():
    expected=json.loads((ROOT/"a1_corrected_reliability_transfer_20261128.json").read_text())
    actual=evaluate(load())
    for key in actual:
        assert actual[key]==expected[key]
    assert expected["can_promote"] is False
