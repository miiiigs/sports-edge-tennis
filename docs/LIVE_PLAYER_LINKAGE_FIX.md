# Live API-Tennis player linkage

The live API-Tennis fixture adapter can contain stable provider player IDs that were not present in the historical Tennis corpus. Early live ingestion therefore created new canonical player rows even when the same player already existed historically.

For prediction input only, live target resolution now bridges a live-only player row to an existing historical player ID when all of the following hold:

1. the live row has no point-in-time historical result evidence itself;
2. exactly one other player matches the exact normalized Tennis-data alias or exact canonical name; and
3. that candidate has point-in-time historical result evidence before the prediction cutoff.

Zero or multiple historical candidates fail closed to the original live player ID, allowing downstream agents to abstain normally. Match/source lineage, API-Tennis source IDs, persisted match versions, provider reconciliation, and dashboard identity remain unchanged.
