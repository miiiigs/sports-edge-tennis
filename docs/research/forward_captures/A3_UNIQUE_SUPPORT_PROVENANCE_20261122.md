# First-unique Agent 3 support census — Sunday 22 November 2026

- **Source code:** PR #367, pinned research-only head `e7538cebcd4ba5bc1f0a31795137a3c239784c3e`.
- **Real read-only Railway deployment:** `5aff5bd6-75bc-473f-8b27-c7802fbb361f`.
- **Log evidence recorded:** 2026-11-22T15:36:43.581023238Z (23:36:43 Manila).
- **Original export:** `a3_first_unique_support_20261122.json` in this directory; original JSON emitted from the Railway deployment (not recreated from match outcomes).
- **First-unique match denominator:** 2,127, not the 20,868 repeated A3 evaluation events nor the 1,683 original first-run Manager research population.
- **Status:** A3 predicted 8 unique matches, abstained on 2,119. 846 had raw shared opponents but zero eligible links; 528 were below normal common opponent/Q gate; 355 unknown surfaces; 152 structurally appear supported but still abstained; 158 no raw shared opponents; 70 zero player history; 10 remaining PIT/identity exceptions.
- **Structural counterfactual candidates:** 1,336 have >=3 raw shared opponents and >=3 graph-degree per player with original abstention. This count is only a *research lead* and cannot indicate additional actual predictions or improved winner accuracy; it does not reproduce every A3 opponent-specific historical weight and does not establish raw feature source timing.
- **Sample:** 100 original abstentions, sorted by raw shared opponent support; SHA256 `403e984710a99b016234df6d2fad154e1a3cd761cd697ea296ca71c418b0a693`.

Next scientific step: run the pure A3 soft-edge ESS / surface-weighted PR #361 against genuinely source-verified, fixed prospective snapshots, then compare separate ATP/Challenger accuracy, calibration and Manager outcomes at equivalent selection coverage. Retain original A3. Do not forcibly remove abstentions or change production on the strength of this read-only graph support count.
