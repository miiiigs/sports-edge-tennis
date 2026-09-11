# Agent 3 API-Tennis History Refresh

Use the standalone history refresh when recent completed API-Tennis results are missing from the historical model warehouse.

## Bounded backfill

```bash
python -m sports_edge_tennis.live.history_refresh \
  --start-date 2026-01-01 \
  --end-date 2026-09-09 \
  --chunk-days 31
```

Operational constraints:

- maximum provider chunk is 31 days
- maximum requested window is 370 days
- one API-Tennis fixture request is made per chunk, with no retries or polling
- only the existing men-only ATP/Challenger fixture adapter determines accepted scope
- result observations are append-only and keep their actual receive time as `source_observed_at` and `as_of`
- semantically unchanged results are not appended again
- provider corrections append a new result observation
- Agent 3 uses match occurrence time for temporal decay only after the result is PIT-visible
- live-only API-Tennis player identities bridge to historical model identities only when resolution is unique and PIT-supported
- backfill surface enrichment is database-only to avoid `get_draw` request fan-out
- new result truth invalidates only Agent 3's rebuildable model checkpoint

After a backfill, rerun the Agent 3 history recency audit and confirm recent 2026 observations are visible before evaluating any Agent 3 support-policy changes.
