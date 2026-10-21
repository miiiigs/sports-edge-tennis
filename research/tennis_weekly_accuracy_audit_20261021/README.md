# Tennis Weekly Accuracy Audit - 2026-10-21

Read-only reconciliation of forward-issued Tennis Manager picks for the
2026-10-19 to 2026-10-21 Asia/Manila reporting window, plus a chronological
stability check of the selective filters documented in the model error
forensics research (PR #247).

No production model, Manager rule, database row, dashboard value, or provider
call was changed. The audit never opens a database connection: it consumes a
frozen JSON Lines extract produced by a read-only production session.

## Input

The extractor is the read-only harness from PR #247. It must run where the
production database hostname resolves and always sets
`default_transaction_read_only = on`.

```powershell
python research/tennis_model_error_forensics_20261021/extract_live_picks.py `
  --git-sha (git rev-parse HEAD) > tmp/tennis_forensics_20261021.jsonl
```

The frozen dataset used for this report was extracted at
`2026-10-21T04:06:27Z` with cutoff `2026-10-21T03:30:00Z`:

| Item | Value |
| --- | --- |
| Rows | 973 |
| Manager first evaluations | 346 |
| A1/A2 wide-spread tracking | 553 |
| Value Bets | 67 |
| Veto-blocked | 7 |
| File SHA-256 | `a6b1331d1d051abecae2fcf9fdbd6d37402c73db4b0ea6d9a9bd658d484341e4` |
| Extractor row SHA-256 | `0578947a8e76b06006aeb6044c47769a9dbe828dd9f6f5e0deabd6c3be92a759` |

The dataset is intentionally not committed.

## Run

```powershell
python -m research.tennis_weekly_accuracy_audit_20261021.run_audit `
  --dataset tmp/tennis_forensics_20261021.jsonl `
  --start 2026-10-19 --end 2026-10-21 `
  --timezone Asia/Manila `
  --output-dir research/tennis_weekly_accuracy_audit_20261021/artifacts
```

Outputs:

| File | Contents |
| --- | --- |
| `artifacts/week_reconciliation.json` | populations, settlement bounds, ledgers, subgroups, filter tables |
| `artifacts/tables.md` | human-readable reconciliation, bands, and filter table |

## Definitions

- **Manager Model Pick:** first production Manager `CONSENSUS` evaluation per
  canonical match, confirmed side, probability at or above 60%, finalized
  before the point-in-time match start.
- **Aligned Model Pick:** the dashboard headline population. It is the earliest
  qualifying evaluation per match across Manager `CONSENSUS` and A1/A2
  wide-spread tracking (`AGENT3_ABSTENTION_SPREAD_EXCEEDED`). Tracking rows are
  never Value Bets.
- **Scored:** latest canonical result is `COMPLETED` with `winner_side` 1 or 2.
- **VOID:** `RETIRED`, `WALKOVER`, or `CANCELLED`.
- **CONFLICTED:** result observations disagree.
- **Win rate:** Wins / (Wins + Losses). Pending, void, and conflicted rows are
  reported separately and never enter the denominator.
- **ROI:** flat one-unit stake at the recorded issued price, on settled Value
  Bets only. Guide Odds are never used as issue prices.

## Filters

The filters are predeclared from documented failure patterns, not fitted to
this week:

| Name | Rule |
| --- | --- |
| `R1_MARKET_DISAGREEMENT_20` | Reject model probability more than 20pp above the pre-pick sharp implied probability. |
| `R2_QUALIFICATION_MISMATCH` | Reject tournaments whose canonical name and `qualifying_flag` disagree. |
| `R3_HIGH_CONFIDENCE_NO_SHARP` | Reject picks at or above 80% with no pre-pick sharp snapshot. |
| `R4_COMBINED_PRIMARY` | Reject on R1, R2, or R3. |
| `C1_WIDE_SPREAD_CONTROL` | Reject A1/A2 spread at or above 0.05. Negative control. |
| `C2_THIN_SURFACE_CONTROL` | Reject fewer than two prior surface matches. Negative control. |

`2026-10-13` is the chronological holdout boundary. Because PR #247 examined
data through 2026-10-21, the holdout is a stability check, not a genuine
out-of-sample discovery. Only a forward shadow can confirm improvement.

## Tests

```powershell
python -m pytest tests/test_tennis_weekly_accuracy_audit.py -q
python -m ruff check research/tennis_weekly_accuracy_audit_20261021 tests/test_tennis_weekly_accuracy_audit.py
python -m ruff format --check research/tennis_weekly_accuracy_audit_20261021 tests/test_tennis_weekly_accuracy_audit.py
```

Tests use synthetic rows only and require no database, network, or numpy.
