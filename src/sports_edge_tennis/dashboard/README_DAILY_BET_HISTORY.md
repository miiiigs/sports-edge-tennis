# Daily Bet History

The daily bet history dashboard is read-only. It queries persisted Type A `BET` decisions and immutable `decision_settlements` rows only. It does not mutate model state, settlement, CLV, provider ingestion, or runner cadence.

Daily grouping uses the UTC date of the original decision (`decisions.as_of`). Profit and ROI use the same one-unit convention as prospective performance reporting.
