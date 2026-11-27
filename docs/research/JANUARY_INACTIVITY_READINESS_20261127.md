# January and inactivity readiness — operational metadata, no universal penalty

**Date:** 27 November 2026
**Branch / PR:** `workstream/ao-2027-format-readiness-20261125` (PR #389)
**Coordinated with:** PR #388 (PIT-safe Agent 1 identity bridge and January
feasibility), PR #392 (off-season evidence foundation)
**Status:** classifier and tests implemented. No model probability changed.
**Mode:** no provider call, no database write, no production change.
**Code:** `src/sports_edge_tennis/validation/activity_context.py`
**Tests:** `tests/test_activity_context.py` (12 tests)

## 1. The evidence against a blanket January penalty

From the published month-by-month study of 38,071 ATP/WTA matches
(January 2022 – September 2026):

| Month | Matches | Favourite wins | Gap at equal probability |
|---|---:|---:|---:|
| January | 4,263 | 64.9% | **+0.1 point** |
| February | 3,938 | 65.6% | +1.2 |
| June | 4,142 | 62.5% | **−2.5 points** |
| October | 2,773 | 65.1% | 0.0 |
| November | 731 | 66.5% | +1.4 |
| December | 346 | 65.0% | −1.4 |

January is level with the rest of the year. The only month that survives the
multiple-test correction is June, and it is entirely a grass effect (−3.5 points
on June grass, +0.8 on June clay). The only disappointing January is 2022
(−5.1 points); from 2023 to 2026 every January is above expectation.

Separation matters because a different, real effect exists: returning after a
month without playing has been reported at roughly **ten points below** what Elo
predicts, while simply stringing matches together is barely measurable. That is
an *individual inactivity* effect, not a calendar effect.

Conclusion: a universal January penalty would be a fabricated forecast change.
An individual absence is a real candidate signal, but it must be identified from
the player's own history, not from the month.

## 2. What the classifier distinguishes

`classify_activity` returns a set of flags plus one primary context:

| Context | Meaning |
|---|---|
| `NORMAL_SEASONAL_INTERRUPTION` | a January return whose last match was in the tour's prior season and inside the normal off-season window |
| `EXTENDED_INDIVIDUAL_ABSENCE` | more than 90 days inactive and not explained by the normal off-season |
| `SHORT_RETURN_FROM_ABSENCE` | 30–90 days inactive |
| `MISSING_LOWER_LEVEL_HISTORY` | fewer than 20 tour-level matches and lower-level history is present or unknown |
| `WITHDRAWAL` | provider status `WITHDRAWN` |
| `RETIREMENT` | provider status `RETIRED` |
| `SURFACE_CHANGE` | current surface differs from the previous surface |
| `INSUFFICIENT_ACTIVITY_EVIDENCE` | no last-match date or no usable history |

Multiple flags may coexist (for example, a surface change on a player with only
four matches on that surface).

## 3. Thresholds are configurable and explicit

| Threshold | Default | Purpose |
|---|---:|---|
| `short_absence_days` | 30 | start of a short return |
| `extended_absence_days` | 90 | individual absence |
| `normal_offseason_max_days` | 120 | upper bound of a normal seasonal break |
| `season_opening_days` | 21 | January competition-opening window |
| `min_tour_level_matches` | 20 | thin tour-level history |
| `min_surface_matches` | 10 | thin surface history |
| `offseason_last_match_month` | 10 | October or later is the prior season |

These are **operational labels**, not estimated effect sizes. They are
configurable so an owner can change them without a code change, and they are
validated (`short < extended <= normal off-season`).

## 4. Metadata contract

The context is JSONB-safe and can travel in an existing diagnostics payload:

```json
{
  "activity_context": {
    "player_id": 1234,
    "flags": ["NORMAL_SEASONAL_INTERRUPTION"],
    "primary": "NORMAL_SEASONAL_INTERRUPTION",
    "days_inactive": 46,
    "is_january": true,
    "is_competition_opening": true,
    "forecast_adjustment": 0.0,
    "january_universal_penalty_applied": false,
    "adjustment_policy": "NONE_METADATA_ONLY"
  },
  "forecast_probability_changed_by_activity": false
}
```

`attach_activity_metadata` never mutates the probability. A future model change
must be proposed, fitted and validated separately.

## 5. Tests

| Test | Proves |
|---|---|
| normal January return | 46-day break after 20 November is seasonal, not extended |
| extended January absence | a June absence returning in January is extended, not seasonal |
| mid-season extended absence | the classifier does not require January |
| short return | 30–90 days is its own context |
| withdrawal / retirement | distinct terminal statuses |
| missing lower-level history | flagged independently of inactivity |
| surface change | flagged, and can coexist with thin surface history |
| unknown evidence | becomes `INSUFFICIENT_ACTIVITY_EVIDENCE`, not a penalty |
| January vs July | both carry exactly zero adjustment |
| diagnostics attachment | the probability string is unchanged |
| thresholds | invalid configurations fail closed |
| observations | future dates, negative counts and unknown statuses fail closed |

## 6. Coordination with PR #388 and PR #392

- **PR #388** owns the PIT-safe identity bridge and the historical January
  feasibility evidence. The activity classifier needs the same player identity
  that the bridge resolves; it does not create a parallel identity path.
- **PR #392** owns the off-season evidence foundation. Its normal-season
  interruption model is the evidence source for the `NORMAL_SEASONAL_INTERRUPTION`
  boundary; this branch only labels the context.
- No shared file is modified by this branch. The classifier is additive and can
  be imported by either workstream.

## 7. What is not claimed

- No player-level historical inactivity effect was estimated in this session.
  That requires the licensed historical corpus and read-only database access
  described in the BO5 methodology.
- The thresholds are not calibrated probabilities.
- No withdrawal, retirement or replacement event is created. They are labels on
  provider-observed status.
- No January forecast penalty, surface penalty or absence penalty is applied.

## 8. Decision

**Operational metadata: accepted. Forecast adjustment: none.** The platform can
now distinguish a normal January return, an extended individual absence, missing
lower-level history, a withdrawal, a retirement and a surface change without
smuggling a calendar prior into the probability.
