# Agent 3 evidence tables

## Coverage

| Metric | Value |
| --- | ---: |
| A3 evaluation rows | 31,121 |
| Unique matches evaluated | 2,259 |
| Unique matches predicted | 10 |
| Unique-match coverage | 0.4427% |

## Diagnostics gate decomposition

| Gate/cohort | Evaluations |
| --- | ---: |
| direct_raw_zero | 5,670 |
| direct_raw_one | 1,638 |
| direct_raw_ge2_effective_lt_2_5 | 1,227 |
| direct_gate_pass | 48 |
| network_normal_support | 48 |
| direct_plus_network | 48 |
| direct_only | 0 |
| network_eligible_zero | 4,890 |
| network_eligible_1_4 | 2,802 |
| network_eligible_5_7 | 577 |
| network_eligible_ge8 | 314 |
| network_only_gate_pass | 170 |
| network_only_gate_pass_but_not_predicted | 153 |

## Insufficient-support cohorts

| Cohort | Evaluations |
| --- | ---: |
| direct_raw=0 | 5,156 |
| direct_raw>=2_effective<2.5 | 1,227 |
| direct_raw=1 | 1,638 |

## Agent contribution

| Metric | Value |
| --- | ---: |
| Unique A3 predicted matches | 10 |
| Scored A3 predicted matches | 9 |
| A3 wins | 7 |
| A3 accuracy | 77.7778% |
| A3 Brier | 0.229893 |
| A3 log loss | 0.652834 |
| A3 side = A1 side (3-active rows) | 8/8 |
| 3-active Manager rows | 8 |
| 3-active rows passing 0.08 spread | 0 |

## Two-agent counterfactual on 3-active rows

| A3-abstention spread gate | Scored picks | Wins | Accuracy |
| --- | ---: | ---: | ---: |
| 0.06 | 1 | 1 | 100.0000% |
| 0.08 | 3 | 3 | 100.0000% |
