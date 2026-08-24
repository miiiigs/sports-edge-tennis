# Legacy Reference

## Status

```
REFERENCE ONLY
NOT SPEC COMPLIANT
DO NOT IMPORT
DO NOT USE AS AGENT 1
DO NOT USE AS AGENT 2
```

These files existed inside the football runtime and are legacy scaffolds only. They were
NOT copied into this tennis repository.

## Provenance

| Field | Value |
| --- | --- |
| Football repository | `sports-edge-ai` |
| Football source commit | `f379088d31888b29688a3905f5f07c3dbac30e60` |

| File | Path | Source blob SHA |
| --- | --- | --- |
| Legacy Elo scaffold | `code/src/sports_ai/agents/tennis/elo.py` | `03bfcb1a64bae8233da292ce65d91f1702b3b05e` |
| Legacy simulator scaffold | `code/src/sports_ai/agents/tennis/simulation.py` | `e94b1de4eda0ab9ffd4badf4572e3aa1f83ae2df` |

## Documented deficiencies

### Legacy Elo scaffold

- constant K around 24
- no required four-rating architecture
- no proper surface architecture
- no canonical validation
- no canonical blending

### Legacy simulator scaffold

- Monte Carlo
- best-of-three only
- no exact point-to-match recursion
- no best-of-five
- incomplete historical final-set rules
- no proper SPW/RPW model

The implementation is deliberately not reproduced here.
