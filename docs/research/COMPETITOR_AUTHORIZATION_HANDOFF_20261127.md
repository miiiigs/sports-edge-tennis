# Competitor authorization and evidence handoff — 27 November 2026

Research only. No purchase, production activation, authorized records or new observations. Existing PR #393 head 1d61e58291897eb81328a83256a50687276570de; related #388 786b01f6, #389 9575ea35, #394 b065e22b. Previous PostgreSQL CI run 45814093508 succeeded. All drafts below are text artifacts only; no verified company mailbox, no sender approval. Personal mailbox checked for correspondence with the three supplied addresses: none returned. No email sent, delivered or answered through this workstream.

## Enquiries (drafted, not approved, not sent)

### Pronocast — contactpronocast@gmail.com
Subject: Permission request — internal ATP/Challenger forecasting benchmark

Hello Pronocast team,

We are developing Brazzy365, a commercial tennis forecasting research platform. We would like your written permission for an internal, non-redistributed scientific comparison of men's ATP Tour and ATP Challenger singles, separating main draw and qualifying. We would not scrape or circumvent your access controls.

Could you provide an authorized historical export and/or a prospective delivery option, including stable fixture and player IDs, tournament/tier/draw, scheduled start and actual start where available, original forecast publication time (UTC), later revisions with their own times, match-winner pick, both numerical win probabilities where available, model version, selection flags, and odds source/time if supplied? Please distinguish scheduled start, export generation time and first publication time. We would also appreciate exact confidence-band counts, correct/settled counts and coverage against all eligible fixtures, including missing forecasts, separately by tour and draw.

Please confirm permitted internal commercial benchmarking, capture frequency/method, private retention of raw or normalized records and integrity digests, reproducibility and aggregate reporting to internal stakeholders, any restrictions, expiry, deletion requirements and commercial terms. No redistribution or purchase is implied.

Your About page invites requests for alternative data cuts. We have reviewed your terms, particularly the restriction on systematic extraction, and request a provider-approved export or explicit written exception instead. Could you also reconcile the 26 November 2026 headline of 81.6% for the >=70% cohort with the displayed bands (1,378 at 73.9% and 1,199 at 89.7%, weighted 81.25%)? Please supply exact unrounded counts, cohort boundaries, exclusions, denominators, and whether the headline and bands use the same snapshot. Please distinguish odds-assisted predictions from market-independent estimates and historical test results from originally published forecasts.

Kind regards,
Brazzy365 Research Team

### Tennis Foresight — contact@tennisforesight.com
Subject: Permission request — JSON archive and pre-match publication evidence

Hello Tennis Foresight team,

We are developing Brazzy365, a commercial tennis forecasting research platform. We would like your written permission for an internal, non-redistributed scientific comparison of men's ATP Tour and ATP Challenger singles, separating main draw and qualifying. We would not scrape or circumvent your access controls.

Could you provide an authorized historical export and/or a prospective delivery option, including stable fixture and player IDs, tournament/tier/draw, scheduled start and actual start where available, original forecast publication time (UTC), later revisions with their own times, match-winner pick, both numerical win probabilities where available, model version, selection flags, and odds source/time if supplied? Please distinguish scheduled start, export generation time and first publication time. We would also appreciate exact confidence-band counts, correct/settled counts and coverage against all eligible fixtures, including missing forecasts, separately by tour and draw.

Please confirm permitted internal commercial benchmarking, capture frequency/method, private retention of raw or normalized records and integrity digests, reproducibility and aggregate reporting to internal stakeholders, any restrictions, expiry, deletion requirements and commercial terms. No redistribution or purchase is implied.

We are particularly interested in the JSON archive linked from your match pages. Does it contain original pre-match estimates, their first publication timestamps and revision history, or only match results/current or reconstructed estimates? Can you supply a schema and a provider-approved sample, and separate live forecasts from walk-forward backtests? Please clarify whether the estimates incorporate market odds and whether closing market prices are separate from the forecasts.

Kind regards,
Brazzy365 Research Team

### TennisPredictions.ai — support@tennispredictions.ai
Subject: Written commercial research permission — match-level Excel archive

Hello TennisPredictions.ai team,

We are developing Brazzy365, a commercial tennis forecasting research platform. We would like your written permission for an internal, non-redistributed scientific comparison of men's ATP Tour and ATP Challenger singles, separating main draw and qualifying. We would not scrape or circumvent your access controls.

Could you provide an authorized historical export and/or a prospective delivery option, including stable fixture and player IDs, tournament/tier/draw, scheduled start and actual start where available, original forecast publication time (UTC), later revisions with their own times, match-winner pick, both numerical win probabilities where available, model version, selection flags, and odds source/time if supplied? Please distinguish scheduled start, export generation time and first publication time. We would also appreciate exact confidence-band counts, correct/settled counts and coverage against all eligible fixtures, including missing forecasts, separately by tour and draw.

Please confirm permitted internal commercial benchmarking, capture frequency/method, private retention of raw or normalized records and integrity digests, reproducibility and aggregate reporting to internal stakeholders, any restrictions, expiry, deletion requirements and commercial terms. No redistribution or purchase is implied.

We are requesting the match-level Excel archive described on your Progress page rather than its daily aggregate CSV. We understand your terms limit use to personal purposes without prior written permission. Could you clarify whether the workbook date/time is scheduled start or original publication, whether confidence is a trust score rather than a calibrated win probability, and whether actual win probabilities and first-issued Tip of the Day selection flags are available? Please confirm grading rules for retirements, walkovers and cancellations, and whether revisions are retained.

Kind regards,
Brazzy365 Research Team

## Source rights and semantics audit (27 Nov 2026)

- https://www.pronocast.com/en/about permits quoting aggregate figures with attribution and invites data-cut requests. https://www.pronocast.com/en/terms (28 Oct 2026), section 5 prohibits systematic extraction. No commercial capture grant. No export schema inspected. Provider publication claim is not independent evidence.
- https://tennisforesight.com/terms and /track-record rendered the homepage rather than the requested content. Match pages link to a full JSON archive (example https://tennisforesight.com/match/12175778-fritz-vs-fils-basel), but archive rights, field semantics and original publication times remain unverified. Do not download.
- https://tennispredictions.ai/terms-and-conditions (29 Jul 2026), sections 5–6 require prior written commercial permission. https://tennispredictions.ai/progress describes daily aggregate CSV and a yearly-tab match-level Excel with time, odds, pick, confidence and outcome. Time semantics and probability semantics unknown; workbook not downloaded. Treat trust as nonprobability. Publicly downloadable does not mean commercially licensed.
- No fallback provider with verified written grant established; public aggregate research remains permissible context, not match-level acquisition.

## Hypothesis register (provider claims, not independent findings)
All studies cover Jan 2022–22 Sep 2026; original scripts and match-level provenance unavailable; draw splits and retirement treatment need confirmation.

| ID / source | Population and provider finding | Brazzy365 independent reproduction / confounders |
|---|---|---|
| H1 https://www.pronocast.com/en/blog/new-players-tennis-elo-overrated | 95,100 singles ATP/WTA and men's/women's Challenger; 8,237 with <20 archive matches. 665 newcomer favourites: 41.1% vs 63.1% expected, 5,735 newcomer underdogs: experienced favourite 85.6% vs 78.5%. Same-sample experienced calibration; count/tour splits exploratory. | #388: distinguish identity failure, actual cold start, archive left truncation, return from absence and feeder-tour history. Freeze 0–4/5–9/10–19/20+ cohorts, PIT support and earlier calibration; cluster uncertainty. Do not apply blanket penalty. |
| H2 https://www.pronocast.com/en/blog/tennis-challenger-vs-atp-favourites | 86,865 ATP/WTA/Challenger singles, both players >=20 prior matches, no ITF targets; 19,552 ATP vs 42,047 men's Challenger, Elo favourite 64.5% vs 64.0%; adjusted gap +0.2 pp, 95% interval -0.6 to +1.0. Odds available for 44,737 overall. | Compare tier calibration on identical PIT probabilities; split main/qualifying, surface, support and odds availability, avoid equating no evidence of gap with equality. No duplication of #388. |
| H3 https://www.pronocast.com/en/blog/tennis-season-start-end-favourites | 38,071 ATP/WTA only, both >=20; January +0.1 pp residual, June -2.5 pp, December 346. Month correction prespecified; June grass and year splits exploratory; Oct–Dec only 2022–25. | #388: predeclare month/rest/season interactions, control surface and tournament mix, validate Challenger separately. No generic January penalty; rest days distinct from month. |
| H4 https://www.pronocast.com/en/blog/tennis-surface-change-players | 36,164 ATP/WTA matches, known surface, both >=20; 72,328 player observations, 7,780 switches. Global Elo residual -3.6 pp (95% -4.7 to -2.6), surface Elo -0.3 (-1.3 to +0.8). Previous match can include ITF/Challenger. | #388: PIT previous surface from licensed histories, cluster by match/player/tournament, surface support and missing lower-tier histories; direction/sequence splits post hoc. Existing surface Elo may already account for effect; avoid duplicate experiment. |
| H5 https://www.pronocast.com/en/blog/best-of-five-tennis-favourites | 33,794 ATP/WTA, 2,405 BO5; men Slam 70.8%, women Slam 70.5%; adjusted difference +0.6 pp (95% -2.0 to +3.1); later-round +7.0 pp exploratory (581 men's matches). Incomplete draws and odds subset 16,469. | #389: existing format-aware Markov remains authoritative; predeclare round/surface/seeding and opponent quality, distinguish Slam from format and sex, test BO5 separately; no universal additive uplift. |

Freeze a licensed PIT population with archive completeness, chronological result visibility, independent settlement and exact versioned predictions before evaluating; report clustered intervals and multiplicity. Cross-sex controls and same-sample calibration do not establish causal effects. No new forecasting experiments launched.

## #394 handoff and scoring
ConfiguredCompetitorCollector accepts normalized bytes, an expected transport SHA and permitted evidence bytes, with collector-side grant and census registry. Rejects historical origin, unknown publication timestamp semantics, missing retention rights and digest mismatch. Source transformation fidelity and rights authenticity remain externally verified requirements. Local clock is NOT independent proof; no live network route, shared deployment, or schedule configured. Existing local receipt API is unchanged for legacy synthetic research. Added draw-stratified reports and paired cluster intervals (tournament/month, >=4 clusters) by baseline market class, not a significance or promotion gate. Existing equivalent-volume selection is descriptive, not a frozen live selection claim. Official result/census input remains externally asserted; odds metrics unavailable until authorized, timestamped odds fields exist. Historical licensed imports need a separate nonprospective pipeline.

## Evidence ledger
Three repository drafts; approvals 0; sent 0; delivered 0; responses 0; verified commercial grants 0; authorized historical rows 0; genuine prospective observations 0; settled matched fixtures 0. Accuracy, Brier, log loss and coverage undefined without eligible census and observations. No superiority claim.
