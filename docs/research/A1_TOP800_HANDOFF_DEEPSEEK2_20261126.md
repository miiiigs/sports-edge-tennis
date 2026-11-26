# Exact top-800 fixture-set handoff — DeepSeek 2 bookmaker audit

**From:** Agent 1 / PR #388 (owner of #388). **To:** DeepSeek 2.
**Status:** research only, `can_promote=false`, no production change.

The exact latest top-800 fixture sets are in:

- `docs/research/evidence/a1_top800_sets_20261126.json`
- File SHA-256: `deade4e7be8db49608bbb39cf92080268e2c4204ad0f54e4765787da6e98a962`

## Sets

| Set | Rows | Wins | Losses |
|---|---:|---:|---:|
| E3 A (unbridged reconstruction) top 800 | 800 | 596 | 204 |
| E3 B (bridged reconstruction) top 800 | 800 | 655 | 145 |

Each row contains only: `prediction_id`, `match_id`, `prediction_run_id`,
`match_version_id`, `match_start_at`, `tour`, `selection_side`, `p1`,
`confidence`, `winner_side`, `won`. There are no names, contact details,
bookmaker prices, provider secrets or account identifiers.

## Why these are the right sets

- Population: 3,776 latest Agent 1 targets since 2026-09-10, frozen at
  `2026-11-25T07:20Z`; 3,223 safely completed.
- Ranking: Agent 1 confidence `max(p1, 1-p1)`, descending, stable chronological
  tie handling, 800 rows per set.
- These are **historical development-reused** outcomes, not untouched validation.
- They are not Model Picks and not publication decisions. The bookmaker audit
  must not treat them as a deployable pick set.

## Requested independent audit

1. Confirm the 800+800 fixture identities and the 59-win accounting identity.
2. Check whether any row is duplicated, void, non-main-draw or has a start time
   inconsistent with the freeze.
3. Join only your own licensed bookmaker prices; do not infer prices from this file.
4. Report selection overlap, price availability and any provenance conflict back
   to PR #388. Do not edit PR #392.

## Related accounting

{
  "E1_cutoff": "2026-11-24T08:10:00Z",
  "E2_cutoff": "2026-11-24T09:00:00Z",
  "E3_cutoff": "2026-11-25T07:20:00Z",
  "E2_targets": 3725,
  "E2_settled": 3201,
  "E3_targets": 3776,
  "E3_settled": 3223,
  "E1_reconstructed": 3152
}
