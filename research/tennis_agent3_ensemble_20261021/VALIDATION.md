# Validation evidence

- **Validated code head:** `eda73cedd1aba4916e7a44bc0c397113a845be3f`
- **Base:** PR #248 head `a88dcd457cac271d1119c091d002eea4c4f49e9d`
- **Date:** 2026-10-21
- **Working tree:** clean at validation time

This validation commit adds only this evidence file. The code and artifacts
validated are exactly the parent commit above.

## Focused tests

```text
python -m pytest tests/test_tennis_agent3_ensemble.py -q
...............                                                          [100%]
15 passed in 1.14s
```

## Full repository suite

```text
python -m pytest -q
1281 passed, 700 skipped in 64.56s (0:01:04)
```

The skipped tests are pre-existing environment-dependent tests; no new skips
were introduced.

## Compilation

```text
python -m py_compile research/tennis_agent3_ensemble_20261021/*.py
COMPILE_OK
```

## Research artifacts regenerated at this head

```text
python -m research.tennis_agent3_ensemble_20261021.analysis \
  --extract-dir tmp/lane4/extract \
  --artifacts-dir research/tennis_agent3_ensemble_20261021/artifacts

python -m research.tennis_agent3_ensemble_20261021.ensemble_shadow \
  --extract-dir tmp/lane4/extract \
  --artifacts-dir research/tennis_agent3_ensemble_20261021/artifacts
```

Frozen extraction counts: 31,121 A3 evaluation rows, 8,583 diagnostics,
2,259 unique Manager rows. Raw JSONL files are not committed; see
`artifacts/extraction_manifest.json` for counts and SHA-256 hashes.

## Safety checks

- `SET default_transaction_read_only = on` in the extractor.
- No production prediction, Manager, policy, odds, stake or settlement writes.
- No Manager threshold, A1/A2 weight, betting logic or risk change.
- No Lane 1/Lane 2 branch change.
- The shadow decision test mutates the outcome and asserts all decision fields
  are unchanged.
- Forward collection is append-only and idempotent on
  `(match_id, prediction_run_id)`.
