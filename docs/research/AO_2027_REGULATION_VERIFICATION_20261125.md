# Australian Open 2027 regulation verification — 25 November 2026

**Branch / PR:** `workstream/ao-2027-format-readiness-20261125` (PR #389)
**Mode:** public-document verification only. No provider calls, purchases,
database writes or deployment.
**Status:** PARTIALLY VERIFIED — the general Grand Slam format is confirmed, but
the 2027 edition-specific rulebook was not located. Release remains blocked on
owner confirmation.

## Sources checked

| Source | What it establishes | Result |
|---|---|---|
| Australian Open official site, 2027 tournament header | Tournament dates **10–31 January 2027** | Retrieved 2026-11-25 |
| [AO guide to scoring](https://ausopen.com/articles/news/guide-scoring-tennis) | Men's singles at the AO and all majors is best-of-five; women's/qualifying/junior are best-of-three; from AO 2019 the final set is a 10-point tiebreak at 6–6; all Slams uniform from Roland Garros 2022 | Retrieved 2026-11-25 |
| [ITF / Grand Slam 2026 Rulebook](https://content.wimbledon.com/is/content/AELTC/aeltc/wimbledon/live-site/player/PDFs/Grand_Slam_Rulebook_2026.pdf) | The current published Grand Slam rulebook is the **2026** edition | Located by search; PDF not parsed in this session |
| [ATP 2027 calendar](https://www.atptour.com/-/media/files/calendar-pdfs/2026/2027-atp-tour-calendar-14-january-2026-updated.pdf) | January events: Brisbane/Hong Kong (w/c 4 Jan), Adelaide/Auckland (w/c 11 Jan), AO (from 17/18 Jan); United Cup 1 Jan | Previously recorded; not re-parsed |
| Search for "Grand Slam Rulebook 2027" | No 2027 edition-specific Grand Slam rulebook or AO 2027 regulations document was returned | **Not found** |

## What is verified

1. **Men's main-draw format is best-of-five.** The AO's own scoring guide states
   that a men's singles match at the Australian Open, and all majors, is
   best-of-five; the winner is first to three sets.
2. **The deciding set uses a 10-point tiebreak at 6–6.** The AO introduced this
   for all final sets in 2019, and all Grand Slams adopted the uniform format
   from Roland Garros 2022. The code's
   `BO5_DECIDER_TB10_AT_6_6` rule matches the published format.
3. **Qualifying is best-of-three.** The same AO guide lists qualifying among the
   best-of-three events. The candidate parser classifies Q1–Q3 as best-of-three
   and leaves the Agent 2 scoring resolver unsupported for 2027 qualifying.
4. **The January calendar is consistent with the discovery rehearsal.** The AO
   is a 128-player main draw beginning in the week of 17/18 January 2027, with
   qualifying in the preceding week.

## What is not verified

- The **2027 edition-specific Grand Slam rulebook** and any AO 2027 tournament
  regulations were not located. The 2026 rulebook is the latest published
  edition found. Carrying the 2026 format forward is a reasonable candidate, but
  it is not the same as confirming the 2027 edition.
  Rechecked 26 November 2026: a public search still returned only the 2026 Grand
  Slam Rule Book and the 2026 ATP Official Rulebook; no 2027 edition was found.
- Any 2027 change to draw size, qualifying format, seedings, byes or the
  deciding-set rule is unknown and must not be assumed.
- The exact official 2027 main-draw entry list and draw are not available before
  the tournament.

## Consequence for the release

The code change is correctly scoped and fails closed, but it must not be
released as an approved 2027 rule on the strength of the general AO page alone.
The release gate remains:

1. locate and freeze the official 2027 edition-specific regulations (or an
   authorised owner decision that the 2026 rules carry forward unchanged);
2. record the exact source, retrieval date and scope;
3. re-run the format/qualifying/United Cup tests against that decision;
4. obtain owner approval.

No 2027 forecast, historical correction or production scope expansion follows
from this verification.
