# Decision Log

This log records irreversible architectural and governance decisions. Entries are append-only.

## D001

Tennis is developed in a dedicated repository and runtime, independent from football.

## D002

Tennis receives a separate database. Not provisioned yet.

## D003

Legacy tennis scaffolds from the football repository (`elo.py`, `simulation.py`) are
reference-only. They must not be imported or used as Agent 1 or Agent 2.

## D004

All final tennis validation must be chronological walk-forward.

## D005

Prediction agents must not consume bookmaker prices.

## D006

Evaluation priority:

```
Calibration
CLV
ROI
```

## D007

Any materially tested model variation must be counted. No silent tuning.

## D008

Tennis data persistence is PostgreSQL-first and uses committed SQL migrations rather than
ORM-generated schema state.

## D009

Canonical player, tournament, edition, and match identities use immutable surrogate keys.
Names and fuzzy matches are never primary keys.

## D010

Player identity resolution fails closed. Ambiguous and unmatched names are stored as
explicit resolution events rather than being silently auto-mapped.

## D011

Match identity, match result observations, and decision settlement are separate layers.
Post-match facts must never be stored on the pre-match fixture identity in a way that hides
look-ahead leakage.

## D012

Odds snapshots are append-only and must retain `source_observed_at`, `as_of`, and
`ingested_at` separately. No price overwrites.

## D013

Ratings, predictions, and manager decisions are immutable historical records carrying
`model_version`, `feature_version`, and `as_of`.

## D014

Type A model-edge signals and Type B price-dispersion signals are distinct decision classes
and must remain separable in storage and reporting.
