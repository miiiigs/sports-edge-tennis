# Independent official fixture census: Nov 25 execution and blockers

Research only; runner baseline ad65894f553d550d647341bf601e1ef92851b95a.
PR359 already merged at e9fa00c2b8db12cbd2edd39bea0547aea4a1c7b7. Extend its licensed adapter, not a new ingestion path. No modifications to PR385–390, live services, DB or provider accounts.

## Rights and actual access (2026-11-25)
- ATP https://www.atptour.com/en/terms-and-conditions sections 3 and 7: prior written permission required for commercial use or systematic retrieval.
- TDI https://results.tennisdata.com/en/terms-and-conditions sections 5–6: commercial licence required; public site prohibits automated mining and model validation. No fixture collection from this website.
- ProTennisLive https://api.protennislive.com/feeds/swagger/index.html describes API; neither documentation nor visibility demonstrates permission to call it.
- Sportradar https://developer.sportradar.com/tennis/docs/tennis-ig-data-coverage-tiers describes official ATP/Ch coverage; https://www.atptour.com/en/news/sportradar-atp-partnership-december-2023 confirms commercial rights partnership. https://sportradar.com/official-atp-addendum/ is evidence that contract matters, not evidence of our entitlement. Existing historical Sportradar rows do not prove current licence, retention or completeness.
- API-Tennis https://api-tennis.com/documentation documents fixture fields and Challenger Men Singles; https://api-tennis.com/terms-of-use is public subscription terms. Existing internal API-Tennis fixtures are not an independent denominator; exact current subscription rights and official provenance not verified.
- https://developer.sportradar.com/tennis/docs/tennis-ig-monitoring-data-changes and /tennis-ig-schedules describe created/updated/removed and competitor merge feeds. A removal is not a verified void; absence from an incomplete snapshot is unknown; first observation is not necessarily first publication.
- No licence or permitted independent complete fixture sample established in this session. Zero new provider calls, zero spend. Official denominators and W/L/P/V unavailable, not zero. Request an owner-approved written evaluation entitlement and complete ATP/CH men's main-draw singles export, including stable IDs, competition/season/round/draw, both participant IDs, original announced start and first-published UTC, source version/time, current status, updates/removals and completeness declaration by date/tour. Exclude qualifiers, doubles, other competitions; AO/United Cup only after explicit approval.

## Actual archived internal cohort regrouping, not an official census
Previous original database evidence PR347/349: Oct15–Nov20 UTC 1,898 stored, 1,688 PIT-visible, 1,649 scheduled, 453 qualifying, 1,196 main-draw proxy already targeted, 975 A1+A2, 7 A3, 213 first-run Manager consensus, 544 aligned picks, 517 safe completed results. 544/1196=45.48% conditional retention only. No independent denominator. The old audit's 1,196 already-targeted rows cannot expose unobserved official fixtures.

Read-only regrouping of the original candidate JSON log messages in PR390 at f75e13919d28bad01c86b4a6788ff096b7749b5a (not modifying that branch). Source files docs/research/forward_captures/railway_forward_20261124_full_day.json (Git blob 296146630f2f38d6b4cde10cf41a07deacea25da) and railway_forward_20261125_0000_0400Z.json (Git blob 7dc15a5a69e267b822042c614b431f19c21bdd94). First file 51 candidate, 50 summary, 24 heartbeat, 24 success records; second 6 candidate, 6 summary, 4 heartbeat, 4 success. Log retrieved after capture; independent contemporaneous receipt not established. Group by scheduled_start_at_utc date, not issue date, and tour; filter message prefix TENNIS_FROZEN_PROSPECTIVE_CANDIDATE, parse embedded JSON, count distinct canonical_match_id and original_agent_statuses/original_manager_status. No deduced published picks: each explicitly not_a_published_model_pick, no draw-stage field, no settlement.

| Scheduled UTC day | Tour | unique targets | A1 | A2 | A3 | first Manager consensus |
|---|---|---:|---:|---:|---:|---:|
| 2026-11-24 | ATP_MAIN | 1 | 1 | 0 | 0 | 0 |
| 2026-11-24 | CHALLENGER | 16 | 16 | 10 | 0 | 1 |
| 2026-11-25 | CHALLENGER | 29 | 29 | 17 | 0 | 5 |
| 2026-11-26 | CHALLENGER | 11 | 11 | 9 | 0 | 2 |
| total | | 57 | 57 | 36 | 0 | 8 |

Manager reasons: TWO_OR_MORE_ABSTENTIONS 21, AGENT3_ABSTENTION_SPREAD_EXCEEDED 17, DIRECTIONAL_DISAGREEMENT 11, NONE 8. IDs by date/tour:
Nov24 ATP: 134351.
Nov24 CH: 134321–134329,134331,134339–134344.
Nov25 CH: 134332–134338,134345–134349,134352–134362,134371–134376.
Nov26 CH: 134363–134370,134377–134379.
Coverage, player mapping, original feature completeness, externally published picks and actual outcomes cannot be established from these files.

## Implementation and next gate
Existing licensed_fixture_census now emits daily_waterfall with UTC scheduled-day/tour breakdown and per-reference rows, including unknown source publication, missing exact crosswalk, no PIT version, no target, missing Manager decision, abstention and aligned-pick state. Full-day provider completeness explicitly required, unknown first publication blocks knowable denominator, duplicate canonical IDs fail closed, schedule mismatch no longer silently accepted within a day. Nonempty authorized input still required; zero rows for an independently declared complete day within a nonempty window only. Original and current schedule reconciliation helper distinguishes explicit removals, reschedules, absent complete vs incomplete snapshots, and newly observed records; does not infer cancellation or mutate original forecasts. This is offline logic, not live change-feed integration.
Player identity/feature qualification, run-specific reasons, publication receipt, W/L/P/V, external event crosswalk creation, tournament-local dates, and provider change-feed acquisition remain unverified/not implemented here. Existing source_matching queries use report-time exact crosswalk and historical PIT version; mapping itself is not proven available before issue. No authorized DSN or export established. Full production-row execution remains blocked. Use existing read-only CLI and authorized manifest; no provider calls or production writes. Next action: authorized sample and restricted read-only local execution, complete daily source-to-original-run/settlement join and document each mismatch. Do not mark Trello complete.
