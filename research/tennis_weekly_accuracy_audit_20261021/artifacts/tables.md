# Weekly Tennis Manager Accuracy Audit

- Window: 2026-10-19 to 2026-10-21 (Asia/Manila)
- Minimum probability: 0.6
- Dataset: `a6b1331d1d051abecae2fcf9fdbd6d37402c73db4b0ea6d9a9bd658d484341e4` (973 rows)
- Extract cutoff: 2026-10-21T03:30:00+00:00

## Population reconciliation

| Population | Picks | Scored | W | L | Pending | Void | Accuracy | 95% CI | Mean p | Gap | Brier | Log loss |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | ---: | ---: | ---: | ---: |
| Manager >=60% (official) | 20 | 17 | 9 | 8 | 3 | 0 | 52.9% | 31.0%-73.8% | 69.7% | -16.7pp | 0.2799 | 0.7734 |
| Manager all probabilities | 37 | 31 | 16 | 15 | 5 | 1 | 51.6% | 34.8%-68.0% | 63.2% | -11.6pp | 0.2653 | 0.7350 |
| Aligned headline | 88 | 63 | 38 | 25 | 20 | 5 | 60.3% | 48.0%-71.5% | 70.6% | -10.3pp | 0.2378 | 0.6681 |
|   aligned Manager subset | 19 | 16 | 8 | 8 | 3 | 0 | 50.0% | 28.0%-72.0% | 70.1% | -20.1pp | 0.2887 | 0.7925 |
|   aligned tracking subset | 69 | 47 | 30 | 17 | 17 | 5 | 63.8% | 49.5%-76.0% | 70.8% | -7.0pp | 0.2204 | 0.6257 |

## Probability bands (Manager >=60%, full history)

| Probability band | Scored | W | L | Accuracy | Mean p | Gap | Brier |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 0.60-0.70 | 98 | 67 | 31 | 68.4% | 64.2% | +4.2pp | 0.2220 |
| 0.70-0.80 | 58 | 46 | 12 | 79.3% | 74.4% | +4.9pp | 0.1644 |
| 0.80-0.90 | 41 | 28 | 13 | 68.3% | 84.3% | -16.0pp | 0.2416 |
| 0.90-1.01 | 15 | 12 | 3 | 80.0% | 93.7% | -13.7pp | 0.1707 |

## Predeclared selective filters

| Filter | Full keep (n / acc) | Full delta | Holdout keep (n / acc) | Holdout delta | Coverage (holdout) | Week keep (n / acc) | Week rejected (n / acc) |
| --- | --- | ---: | --- | ---: | ---: | --- | --- |
| R1_MARKET_DISAGREEMENT_20 | 188 / 76.6% | +4.4pp | 43 / 76.7% | +8.1pp | 84.3% | 12 / 66.7% | 5 / 20.0% |
| R2_QUALIFICATION_MISMATCH | 127 / 79.5% | +7.4pp | 34 / 73.5% | +4.9pp | 66.7% | 10 / 60.0% | 7 / 42.9% |
| R3_HIGH_CONFIDENCE_NO_SHARP | 186 / 73.7% | +1.5pp | 46 / 67.4% | -1.2pp | 90.2% | 17 / 52.9% | 0 / - |
| R4_COMBINED_PRIMARY | 98 / 85.7% | +13.5pp | 24 / 83.3% | +14.7pp | 47.1% | 6 / 83.3% | 11 / 36.4% |
| C1_WIDE_SPREAD_CONTROL | 170 / 71.2% | -1.0pp | 41 / 63.4% | -5.2pp | 80.4% | 15 / 53.3% | 2 / 50.0% |
| C2_THIN_SURFACE_CONTROL | 126 / 71.4% | -0.7pp | 44 / 70.5% | +1.8pp | 86.3% | 13 / 61.5% | 4 / 25.0% |

## Settlement bounds for the reporting window

- Official Manager picks: 9 wins / 17 scored (52.9%); best case with all 3 pending wins: 60.0%.
- Aligned headline: 38 wins / 63 scored (60.3%); best case with all 20 pending wins: 69.9%.

Accuracy uses Wins / (Wins + Losses). Pending, void, and conflicted rows are
reported separately and never enter the denominator.
