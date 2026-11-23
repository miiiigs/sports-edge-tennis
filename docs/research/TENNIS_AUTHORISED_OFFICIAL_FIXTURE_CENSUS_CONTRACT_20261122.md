# Independent ATP / Challenger match census: authorised-source acceptance gate
Research-only, 22 November 2026.

## What the system does, and does NOT do

Brian correctly identified that 1,898 stored fixtures, 1,196 PIT-visible scheduled fixtures, and 1,683 original first-run target records are **different populations**. None is a proof of all official matches we could have discovered. This module takes a **separately authorised and declared complete** reference export and traces each independently numbered fixture to its earliest provable internal discovery.

No official reference export is currently licensed/provided under this PR. Therefore NO complete official match universe, daily missed count, 40–50% true publication coverage, or historical backfilled official prediction is claimed. The module contains no web client, scraper, provider API calls, vendor credentials or data purchases.

ATP/Tennis Data Innovations' published terms (https://results.tennisdata.com/en/terms-and-conditions) specifically restrict commercial use and automated extraction/AI use without appropriate authorization. The public ProTennisLive Swagger page does NOT itself grant permission to scrape, commercially ingest, or train models. Obtain written authorization or a licensed equivalent before passing a reference export.

## Only accepted reference sample

A provider or rights holder must furnish a complete, permitted CSV with: \`source_match_id,tour_scope,draw_stage,match_type,scheduled_start_utc,first_published_utc\`.

- \`tour_scope\` is \`ATP_MAIN\` or \`CHALLENGER\` and \`match_type\` is \`SINGLES\`.
- \`draw_stage\` is \`MAIN\` or \`QUALIFYING\`; the current main-draw census **excludes qualifying** while reporting how many the source contained. Scope changes require an explicit research contract, not silent reclassification.
- \`first_published_utc\` is **original source announcement time**, not last modified, copied on 22 November, or match start time. If unknown, leave empty; it is excluded from a verified “knowable 30 minutes before start” denominator.
- The original scheduled start and all feature/observation clocks require explicit UTC timestamps.
- Provider IDs must uniquely identify matches. No player-name fuzzy matching, unsafe date proximity or plausible-looking invented IDs.

Manifest JSON next to the CSV must contain a valid SHA256 of its contents, exact source_system as it appears in internal \`match_source_ids\`, export_version, licence_reference, \`owner_approved_commercial_validation=true\`, truthful \`provider_declares_complete_window\`, UTC report as_of and window start/end. A sample export must **not** falsely declare the full reference period complete.

Illustrative manifest only (NOT a valid licensed export or a claim of 2026 source completeness):

\`\`\`json
{
  "source_system": "LICENSED_PROVIDER_SOURCE_KEY",
  "export_version": "sample-v1",
  "licence_reference": "PROVIDER_WRITTEN_PERMISSION_REFERENCE",
  "owner_approved_commercial_validation": true,
  "provider_declares_complete_window": false,
  "requested_window_start_utc": "2026-11-15T00:00:00Z",
  "requested_window_end_utc": "2026-11-22T00:00:00Z",
  "report_as_of_utc": "2026-11-22T03:30:00Z",
  "file_sha256": "REPLACE_WITH_ACTUAL_64_CHAR_SHA256"
}
\`\`\`

## Strict quality stages

1. Authorised source export contains in-scope MAIN singles fixture; original source announcement preceded scheduled start by at least 30 minutes.
2. Provider's unique match ID maps exactly to ONE canonical internal match. Missing crosswalk means **unknown identity**, NOT automatically a missed fixture (it may exist under another provider ID).
3. Canonical match has a match_version whose as_of, source_observed_at and database ingestion were all known by the original 30-minute deadline. If ID maps but earliest usable version arrived later, this is demonstrable **discovery timing failure**.
4. An original completed production run targeted the match before the deadline. Each Agent's status is counted separately. A3 abstention is not a failed pick.
5. Original Manager consensus status. This is NOT bookmaker market approval.
6. Aligned dashboard Model Picks are separately counted: strict Manager consensus versus A1/A2 wide-spread tracking, and only those issued before the deadline.
7. Result settlement is not provided by the reference fixture CSV, so do not infer a WIN/LOSS or label an unresolved/void as lost. A separately proven settlement source is required for Stage 7.

The report distinguishes source-announced-late, unknown original announcement time, no exact provider mapping, ambiguous identity, scheduled-time mismatch, demonstrably late internal discovery, available per-agent predictions and final aligned Model Pick. It reports a conditional **exact-ID lower bound** when the provider asserts complete scope but does not claim total discovery rate verified: unmatched official IDs can still map to internal records by another provider identifier.

## Invocation

\`\`\`bash
python -m sports_edge_tennis.validation.licensed_fixture_census \
  --licensed-csv /secure/authorised.csv \
  --manifest-json /secure/source-manifest.json
\`\`\`

\`TENNIS_DATABASE_URL\` must point to a restricted read-only database; the script independently sets \`default_transaction_read_only=on\`. Do not include real player personal information in logs. This runs only when a permitted source export is actually supplied. Until then the independent official source is a documented licensing/data blocker, not a completed integration.

Tests: \`tests/test_licensed_fixture_census.py\`. No live service changes, vendor requests or expenditures.
