#!/usr/bin/env bash
set -euo pipefail

python -m pip install .
export PYTHONPATH=/app/src

exec python -m sports_edge_tennis.live.api_tennis_stats_backfill \
  --start-date 2026-08-18 \
  --end-date 2026-09-17 \
  --chunk-days 1 \
  --apply \
  --regime-code A2_API_TENNIS_HISTORY_20260818_20260917_V1
