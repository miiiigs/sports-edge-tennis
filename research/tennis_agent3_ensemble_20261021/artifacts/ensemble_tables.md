# Ensemble shadow tables

## Decisions

| Metric | Baseline | Candidate |
| --- | ---: | ---: |
| Manager consensus | 308 | 262 |
| Scored matches | 1,938 | 1,938 |

## Gate flips

| Flip | Matches |
| --- | ---: |
| NONE | 2,011 |
| LOST_CONSENSUS | 147 |
| NEW_CONSENSUS | 101 |

## Common scored population

| Metric | Baseline | Candidate | Delta | 95% CI |
| --- | ---: | ---: | ---: | --- |
| brier | 0.237924 | 0.236925 | -0.000999 | n/a |
| log_loss | 0.669553 | 0.665959 | -0.003594 | n/a |
| ece | 0.070500 | 0.051685 | -0.018815 | n/a |
| Brier paired delta | | | -0.000999 | [-0.004550, +0.002350] |
| Log-loss paired delta | | | -0.003594 | [-0.014581, +0.005619] |

## Agent 2 calibration

| Metric | Baseline | Calibrated |
| --- | ---: | ---: |
| Brier | 0.216522 | 0.218012 |
| Log loss | 0.626767 | 0.626117 |
| ECE | 0.071107 | 0.061387 |

## Coverage churn

| Metric | Value |
| --- | ---: |
| New consensus scored / wins / accuracy | 85 / 51 / 60.0000% |
| Lost consensus scored / baseline wins / accuracy | 133 / 103 / 77.4436% |
| Selection changes | 0 |

## Paired common population by surface

| Surface | Common scored | Baseline accuracy | Candidate accuracy | Brier delta | 95% CI | Lost | New |
| --- | ---: | ---: | ---: | ---: | --- | ---: | ---: |
| <missing> | 9 | 77.7778% | 77.7778% | +0.000979 | [-0.012561, +0.012377] | 3 | 6 |
| CLAY | 74 | 59.4595% | 59.4595% | -0.000210 | [-0.004678, +0.004217] | 72 | 51 |
| HARD | 62 | 51.6129% | 51.6129% | -0.002228 | [-0.007893, +0.002781] | 72 | 44 |

## Paired common population by tier

| Tier | Common scored | Baseline accuracy | Candidate accuracy | Brier delta | 95% CI | Lost | New |
| --- | ---: | ---: | ---: | ---: | --- | ---: | ---: |
| ATP_250 | 0 | n/a | n/a | n/a | n/a | 0 | 0 |
| ATP_TOUR | 20 | 75.0000% | 75.0000% | +0.009484 | [+0.004992, +0.014049] | 14 | 11 |
| CHALLENGER | 125 | 54.4000% | 54.4000% | -0.002677 | [-0.006782, +0.001062] | 133 | 90 |
