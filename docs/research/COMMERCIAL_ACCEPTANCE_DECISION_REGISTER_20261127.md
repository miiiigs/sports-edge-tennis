# Commercial acceptance decision register — 2026-11-27
PR #351; research only; can_promote=false.

## Measured evidence versus missing evidence
Reconciled #351 27de6df54330c10ab56905fb16cbde8f6b4bd0a1, #392 f234a26cb57f268333b64a84e6d3a34412fee83f, #394 9343a756db496fc0730e773a79ece9c782d91854. #351 was already ancestral to #394. Central census and tests copied as exact existing blobs, not rewritten. Frozen ledger blob 02d4b62fad541ee236c78a85a130b3dd97967e47: 1,207,849 ASCII bytes, SHA-256 cbbb0056f82939ccdfde12e8a41de95ac9f2b8e17ab1da23feaf7591f2cdcfa9, 16,104 unique matches, 48 late_mapping=1, 16,056=0. Columns match_id, history_as_of, visible_from, late_mapping only. History timestamps 2026-09-13T01:38:49.119194Z to 2026-11-25T06:04:25.665193Z. E3 cutoff 2026-11-25T07:20:00Z; earlier integrity census 134,093 common histories, 16,105 changes, 28,852 recovered links. Later extract cutoff not precisely recorded; prior explanation is a hypothesis, exact discrepant ID unproven. Do not treat max history timestamp as extraction cutoff.

No authorized database execution path in this session: no DATABASE_URL or PGSERVICE, no psql or Railway CLI; Railway connector lists project and services, no existing fork, no direct production SQL tool. No credentials requested or printed, no new service/fork, no provider calls. Supplier counts, source versions, actual missing ingestion references and cutoff mismatches are NOT MEASURED. The new aggregate CLI runs the central census and candidate identity-reference inventory in one read-only repeatable-read transaction. Null supplier fields stay null; mismatches stay out of supplier counts; results and versions separate. Identity inventory covers original participant IDs, conservative visible source IDs, aliases and resolved events only; exact consumed aliases, bridge events, model-side identities, full history and rating artifacts still need a version-bound manifest. Timestamp equality alone is not exact row identity. E3 run is a reconstruction of the later ledger, not its original extract.

Operator: from this branch checkout, with psycopg and package installed and a securely injected authorized read-only DATABASE_URL, run:
```bash
python ops/validation/a1_commercial_source_census_20261127.py \
  --ledger docs/research/evidence/a1_history_attribution_matches_20261127.csv \
  --output /secure/a1_commercial_census_e3.json --authorized-read-only
```
Use existing central CLI if row-level output needed, keep private:
```bash
python ops/validation/a1_selected_source_census_20261127.py \
  --ledger docs/research/evidence/a1_history_attribution_matches_20261127.csv \
  --cutoff 2026-11-25T07:20:00+00:00 \
  --output /secure/a1_selected_rows_e3.json --authorized-read-only
```
Existing DB operator must also export full 134,093 common history IDs at E3 using the original bridge policy, diff against later ledger by ID, and preserve selected row IDs, source payload hashes, model identity source IDs/aliases/events and full rating state dependency closure. The changed-only ledger is not the full A1 corpus. Do not commit restricted raw records; review permitted aggregates only.

## Supplier enquiry states
Existing six tailored drafts in COMMERCIAL_MODEL_LINEAGE_AND_ENQUIRIES_20261127.md remain PREPARED; no approvals, sends, verified responses or supplier quotes. Official routes rechecked 27 November:
- Sackmann: https://www.tennisabstract.com/blog/tennis-abstract-contact/ ; public MCP repo https://github.com/JeffSackmann/tennis_MatchChartingProject has noncommercial licensing; verify original ATP release and negotiate written historic/derived rights. Do not assume MCP license proves exact ATP corpus license version.
- API-Tennis: https://api-tennis.com/contact ; https://api-tennis.com/terms-of-use ; individual accounts, public terms do not explicitly establish full training/retention/derived publication rights. Confirm current plan with account owner.
- OddsPapi: https://oddspapi.io/en/contact ; https://oddspapi.io/en/legal/terms ; no standalone resale/repackaging/redistribution; explicit model/audit/artifact rights remain unresolved.
- Sportradar: https://developer.sportradar.com/tennis/docs/tennis-ig-historical-data and https://developer.sportradar.com/tennis/docs/tennis-ig-data-coverage-tiers ; sales contact route and coverage matrix; separate archive quote and ongoing tiers.
- Infront: https://www.infront.sport/contact ; ITF official data rights from 2025 per https://www.world.tennis/en/news-and-media/articles/infront-to-become-itf-official-data-partner/ and extension https://www.world.tennis/en/news-and-media/articles/infront-and-itf-extend-global-partnership/.
- LSports: https://www.lsports.eu/contact ; official distributor starting 2025 per https://www.lsports.eu/news/lsports-to-become-itfs-official-data-distributor. Separate pre-2025 archive ownership from newer chain; no ITF picks. No grant implied by official status.

## Account and purchase decisions
#392 measurement 2026-11-27T03:37Z: API_TENNIS 22,515 raw, 101,172 normalized quotes, 1,327 priced matches; ODDSPAPI 11,352 raw, 22,500 quotes, 522 matches; zero original-time comparisons and zero dual-provider normalized overlap. Existing usage is historical persisted usage, not current billing quota. API-Tennis entitlement-only; current plan, remaining quota, competition access unknown. OddsPapi Pinnacle prices present, exact current quotas/contract unknown. No duplicated requests; #392 owns any future approved probe. Owner to supply redacted account plan/usage/competition coverage and invoices or approved one-shot quota-safe probe. Do not claim usage as billable requests.

A2 prior evidence 120,689 essential-complete matches: 115,678 Sackmann, 4,325 API-Tennis, 686 Sportradar; 5,011 non-Sackmann not automatically cleared. These are not A1 supplier counts or guaranteed replaceable backfill requirements. Candidate A audit existing dependencies; B/D share A1 result/identity/rating blockers; C inherits B plus A2/A3/Manager dependency closure. Market/competitor comparisons require separate data rights. Rights confirmed for commercial promotion: none in reviewed evidence. Replacement required if no grant for actual consumed restricted inputs; exact backfill dates/volumes require full source census and matched coverage, not assumed. Do not reuse uncleared ratings or fitted artifacts. Brian must approve authorized sender/scope, rights/legal review, data access, sample thresholds, itemized quotes and any historical backfill or recurring expense. Approximately GBP1,000/month conditional consideration is not purchasing authorization. No actual quotations received. No commercial training, spend, deployment or production change.
