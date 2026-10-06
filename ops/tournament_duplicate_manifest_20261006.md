# Tournament duplicate manifest (dry-run evidence) — 2026-10-06

Read-only audit of API-Tennis-created `ATP_MAIN` canonical tournaments versus
existing canonical (SACKMANN) identities. **No historical merge is applied.**
This manifest is review evidence only. Production reconciliation requires
separate authorization.

Provider at audit time: `API_TENNIS`, scope `ATP_MAIN`. Match counts are the
number of `matches` linked to the duplicate tournament.

## Proven duplicates (deterministic: identical normalized identity, same scope, existing canonical tier)

| duplicate_id | duplicate_name | dup_tier | canonical_id | canonical_name | canonical_tier | matches |
|---|---|---|---|---|---|---|
| 855 | Shanghai | ATP_TOUR | 60 | Shanghai Masters | MASTERS_1000 | 36 |
| 806 | Cincinnati | ATP_TOUR | 51 | Cincinnati Masters | MASTERS_1000 | 133 |
| 717 | Madrid | ATP_TOUR | 29 | Madrid Masters | MASTERS_1000 | 134 |
| 688 | Miami | ATP_TOUR | 20 | Miami Masters | MASTERS_1000 | 133 |
| 700 | Monte Carlo | ATP_TOUR | 23 | Monte Carlo Masters | MASTERS_1000 | 79 |
| 729 | Rome | ATP_TOUR | 30 | Rome Masters | MASTERS_1000 | 132 |
| 640 | Australian Open | ATP_TOUR | 6 | Australian Open | GRAND_SLAM | 243 |
| 768 | Wimbledon | ATP_TOUR | 39 | Wimbledon | GRAND_SLAM | 242 |

These are the identities currently encoded in the reviewed alias table
(`data/tournament_identity.py`). Prospective creation is prevented by
`HistoricalRepository.get_or_create_tournament`.

## Ambiguous candidates (NOT auto-merged; fail closed)

| duplicate_id | duplicate_name | note |
|---|---|---|
| 625 | US Open | Two canonical rows: 53 "US Open" and 324 "Us Open" (GRAND_SLAM). Multiple candidates → not encoded. |
| 829 | Wimbledon Men Singles | Likely a sub-edition of canonical 39 "Wimbledon"; tier already GRAND_SLAM. Needs explicit evidence. |
| 659 | ATP Davis Cup - World Group I | Namesake, distinct competition from 657 "World Group"; do NOT merge. |
| 658 | ATP Davis Cup - World Group II | Namesake, distinct competition; do NOT merge. |
| 796 | Montreal | Canonical name differs (Canada/Toronto series); not prefix-provable in this audit. |
| 677 | ATP Indian Wells | Canonical name differs ("Indian Wells Masters" vs "ATP Indian Wells"); needs explicit evidence. |

## Shanghai (Problem B) detail

- Duplicate: `tournament_id=855` "Shanghai", scope `ATP_MAIN`, tier `ATP_TOUR`,
  created `2026-10-05 00:01:30Z`, 36 matches.
- Canonical: `tournament_id=60` "Shanghai Masters", scope `ATP_MAIN`, tier
  `MASTERS_1000`, created `2026-08-31`.
- Failure: provider name "Shanghai" ≠ canonical name "Shanghai Masters", and the
  provider default tier `ATP_TOUR` ≠ canonical `MASTERS_1000`; no alias/normalized
  fallback existed → new tournament row created.
- Consequence: `Agent 2` scoring-rule resolver sees alias `SHANGHAI` (absent from
  `ORDINARY_ATP_TOURNAMENT_ALIASES_V1`) and tier `ATP_TOUR` (not in
  `{MASTERS_1000, ATP_500, ATP_250}`) → `UNSUPPORTED_OR_UNKNOWN`.
- Affected A2 abstention rows (production, all clean): SF 185 + F 60 = **245**.

## Scoring-rule impact

- Duplicate identity → `ATP_TOUR` → scoring rule unresolved → A2 `ABSTAINED`
  (`UNSUPPORTED_OR_UNKNOWN`). No A1/A2/A3 mathematics change is involved.

## Surface (Problem A) note

The 41 unknown-surface aligned picks are Challenger events where API-Tennis
omitted surface. Deterministic prior-season canonical surface exists (e.g.
`Mouilleron-Le-Captif CH` / `Columbus CH` = HARD, 2016–2025). The new
cross-source canonical prior-surface fallback in
`live/api_tennis_fixture_compat.py` recovers it prospectively at ingestion.

## Non-actions

- No historical tournament merge applied.
- No historical prediction rewritten.
- No production mutation.
