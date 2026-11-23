# Brazzy365 Manager A/B/C original-run audit | 21 Nov 2026

Research only. No live Manager, model, odds, publication or DB changes.

## Reproduction
- Implementation PR #348 merged SHA 9b73aa135f9546b185ace3d0d24a42e6821ac298. PostgreSQL Foundation CI #44865090089 SUCCESS.
- Railway pinned isolated read-only deployment 3887b14c-52c7-4049-806c-e1e0220989e2 at code revision 6e8f3a8d529bcdf3dd61f5c9de59d5adf28ca62b.
- Source module: src/sports_edge_tennis/validation/manager_abc_first_issue_ablation.py.
- Data window [2026-10-15T00:00:00Z,2026-11-21T00:00:00Z), report cutoff 2026-11-21T03:30:00Z.
- Dataset SHA256 aef52c56c90cfe338884a277212720b8c5dded24974a5d93e1774ff559b03adb.
- Run with python -m sports_edge_tennis.validation.manager_abc_first_issue_ablation --window-start-utc 2026-10-15T00:00:00Z --window-end-utc 2026-11-21T00:00:00Z --as-of-utc 2026-11-21T03:30:00Z and the existing read-only production DSN.

## Scope and agent participation
- 1,692 earliest completed original nonexperimental run targets. Nine missing or too-late predictions excluded, leaving 1,683 matches with original run issued 30+ minutes before match start.
- 339 ATP and 1,344 Challenger. Across 1,683, 149 unresolved/void/noncompleted outcomes ungraded; 1,534 safe settled.
- A3 predicted only 7/1,683 = 0.42%, abstained 1,676/1,683 = 99.58%. No A3 results missing. ATP active 4/339; Challenger 3/1,344.
- First-run original Manager 295 CONSENSUS. Among 1,388 nonconsensus cases: AGENT3_ABSTENTION_SPREAD_EXCEEDED 598, DIRECTIONAL_DISAGREEMENT 384, TWO_OR_MORE_ABSTENTIONS 401, THREE_AGENT_SPREAD_EXCEEDED 4, UNSUPPORTED_ABSTENTION_CONFIGURATION 1.

## Research-only policies
A: Three active, unanimous same side, mean winner confidence >=60%.
B: Two or more agreeing, each agreeing agent confidence >=60%, agreeing confidences within 20 percentage points. These guards are exploratory and not validated.
C: Average raw oriented player-1 probabilities with >=2 active agents; publish predicted side if >=60% raw mean confidence. NOT calibrated.
Original Manager: actual first-run recorded CONSENSUS, no bookmaker/market gate.
D validation-weighted and E chronological stacking NOT FITTED. No valid three-agent OOS training support is established yet.

## Results (different selected subsets, not paired)

| Policy | Issued out of 1,683 | Settled W-L | Ungraded | Accuracy | Wilson 95% | Brier | Log loss |
|---|---:|---:|---:|---:|---:|---:|---:|
| A 3/3 | 4 (0.24%) | 4-0 | 0 | 100% (n=4 only) | 51.0-100% | 0.125752 | 0.436145 |
| B guarded 2+ | 413 (24.54%) | 259-117 | 37 | 68.88% | 64.03-73.35% | 0.212530 | 0.617584 |
| C raw average | 775 (46.05%) | 465-237 | 73 | 66.24% | 62.66-69.64% | 0.222562 | 0.638255 |
| Existing Manager | 295 (17.53%) | 168-99 | 28 | 62.92% | 56.98-68.49% | 0.228139 | 0.647356 |

Issued fractions are conditional on first-run targeted fixtures, not ALL eligible ATP and Challenger fixtures. Different rules select different matches. A 4-for-4 100% score is statistically meaningless. No statistical or causal claim of superiority is justified.

## ATP vs Challenger breakdown

| Level | A3 active | Existing Manager | Research B | Research C |
|---|---:|---|---|---|
| ATP (339 first-issued) | 4 | 20W-13L (60.61%); 39 issued | 26W-10L (72.22%); 43 issued | 67W-24L (73.63%); 101 issued |
| Challenger (1,344 first-issued) | 3 | 148W-86L (63.25%); 256 issued | 233W-107L (68.53%); 370 issued | 398W-213L (65.14%); 674 issued |

ATP n=36 (B), n=91 (C) settled, far too small for a tour-specific policy switch. Some selected subgroups differ in match mix, so raw win-rate comparisons overstate evidence.

## Interpretation and decisions
1. Agent 3 is almost entirely abstaining in real original pre-match evidence; strict 3/3 is a severe coverage bottleneck. That does NOT prove rejected matches would be winners.
2. The comparison is **descriptive shadow research, not forward validation**. Even a higher B selected-group win rate must be checked on identical matches at equivalent real eligible-slate coverage and available odds.
3. The 1,683 first-run targets do not quantify all eligible fixtures; P0 PR #347 separately audits PIT-visible fixture denominator and legacy historical cutoff leakage. Neither should be substituted for official complete fixture availability.
4. Next: same-match A/B/C vs original Manager, agent disagreement attribution, holdout-frozen calibrated ensemble and ablation. Manager D/E require genuine chronological out-of-sample agent forecasts and sufficient A3 support.
5. Preserve live Model Picks and keep market/value decisions separate. No new costs or production changes.

Related PRs: #343, #345, #347, #348; Trello https://trello.com/c/jvlNgP4E.
