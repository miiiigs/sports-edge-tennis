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
