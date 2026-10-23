# Connected-source ranking gap — 2026-10-23

Supplement to `SOURCE_AUDIT_20261023.md`. **No provider access keys inspected or invoked; this is documentation/repository evidence, not a live entitlement audit.**

## API-Tennis (current primary source)
Public provider documentation: https://api-tennis.com/documentation

- `method=get_standings&event_type=ATP|WTA` returns tennis standings for tournaments covered by a current subscription, with `place`, `player_key`, `league` and points. The documented parameter list includes `method`, `APIkey` and `event_type`, **not a historical week or as-of timestamp**. Accordingly, an API call made today cannot establish a historical pre-pick official ranking in September/October 2026 without a separately time-stamped archived payload.
- `method=get_players` includes `player_key` and profile-season `rank`, but season-level ranks are not equivalent to a weekly ATP/WTA singles ranking known before each match.
- Existing source code under `src/sports_edge_tennis/data/sources/api_tennis_live.py` and `src/sports_edge_tennis/live/transport.py` handles fixture `first_player_key`/`second_player_key` mapping; **this is not evidence of independently audited ATP/WTA official ranking-ID crosswalk**.
- **Verdict:** reasonable future provider inquiry: ask whether plan includes immutable `get_standings` historical snapshots with original retrieval timestamps, player keys, weeks, full positions through 601+, retention rights and stable change logs. In absence of that, historical archive remains blocked.

## Sportradar Tennis v3 (existing benchmark source)
Public docs: https://developer.sportradar.com/tennis/reference/rankings and https://developer.sportradar.com/tennis/docs/tennis-ig-leaderboards-rankings-standings

- Rankings are described as a weekly feed with current `year`, `week`, `rank`, `sr:competitor` identity and optional `generated_at` in responses. The retrieval guide shows 500 players per tour; that truncates 501–600 and 601+ exposure. Documentation reviewed here does not establish a historical rankings-by-date endpoint.
- A full historical response from a previously captured and integrity-pinned local archive could be eligible after independent review of its original collection timestamp and provider contract. New responses cannot be passed off as past observations.
- **Verdict:** potential **future live-shadow collection** if licensed; not a retrospective solution on its own.

## Repository snapshot evidence
- The reviewed PR #257 initial source contract and migration `0001_tennis_data_foundation.up.sql` distinguish historical Elo `rating_snapshots` from official ATP/WTA rankings. Repo main's inspected `research/` path returned not found; PR #257 started with `research/tennis_pit_rank_evidence_20261022/` alone.
- No rank artifact, immutable licensed historical source export, original point-in-time release proof or 866-row frozen data export was included in the inspected files. This **does not prove** no one has such data outside GitHub; it means this lane cannot verify it here.
- The original prediction source export remains a separate authorized SELECT-only operator step. In the absence of schema-validated SQL and a protected production connection, do not issue a database query, modify settlement tables or create a new cohort with altered definitions.

## Operator checklist
1. Ask API-Tennis or existing licensed ranking provider for ATP/WTA weekly **historical** complete singles lists and contractual storage/research permission.
2. Ask for proof of **release time**, not merely a dated historical value; provider delivery timestamps can help only if they reflect original historical capture.
3. Provide canonical player ID crosswalk for each API-Tennis `player_key`, Sportradar URN or tour player ID, with one-to-one audited identity.
4. Produce source archive snapshot checksums and the original 866-row frozen first-production cohort checksum, row counts, original model versions and statuses.
5. Run offline enrichment only after previous four pass; report uncovered and conflicting rows explicitly.
