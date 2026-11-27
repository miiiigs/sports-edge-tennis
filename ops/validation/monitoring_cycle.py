"""External monitoring cycle for the tennis pipeline.

Run this from a scheduler that is independent of ``tennis-live-runner`` (for
example a separate Railway cron service or an uptime service). It never calls a
provider and never writes the production database.

The pipeline publishes a durable completion receipt only after it has verified
the persisted rows for every required stage. This command reads that receipt and
signals:

* success (heartbeat) when the receipt proves durable completion;
* failure when the receipt is missing, late, crashed, partial or unverified;
* success again on recovery, which closes the incident.

Exit codes: 0 healthy, 1 job failure, 2 monitoring service unavailable.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime, timedelta

from sports_edge_tennis.ops.monitoring import (
    HealthchecksClient,
    HealthchecksConfig,
    load_receipt,
    load_state,
    run_monitoring_cycle,
    write_state,
)


def _parse_slot(value: str) -> datetime:
    moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError("--slot must be timezone-aware (use Z or an offset)")
    return moment


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--slot", required=True, help="expected execution slot (UTC)")
    parser.add_argument(
        "--receipt",
        help="durable completion receipt written by the pipeline for this slot",
    )
    parser.add_argument(
        "--stream",
        default="LIVE_RUNNER",
        help="environment stream suffix, for example LIVE_RUNNER",
    )
    parser.add_argument(
        "--state-path",
        help="durable JSON state path for duplicate/recovery suppression",
    )
    parser.add_argument("--run-timeout-minutes", type=int, default=45)
    parser.add_argument("--as-of", help="evaluation clock; defaults to now (UTC)")
    parser.add_argument("--out-json")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="evaluate and advance local state without sending a heartbeat",
    )
    parser.add_argument(
        "--print-config",
        action="store_true",
        help="print the credential-free configuration and exit",
    )
    args = parser.parse_args(argv)

    slot = _parse_slot(args.slot)
    now = (
        _parse_slot(args.as_of)
        if args.as_of
        else datetime.now(UTC)
    )
    if now < slot:
        parser.error("--as-of must not precede the slot")

    config = HealthchecksConfig.from_environment(
        stream=args.stream, environ=os.environ
    )
    if args.print_config:
        print(json.dumps(config.redacted(), indent=2, sort_keys=True))
        return 0

    record = None
    receipt_error = None
    if args.receipt:
        if os.path.exists(args.receipt):
            try:
                receipt = load_receipt(args.receipt)
            except (OSError, ValueError, KeyError) as exc:
                receipt_error = f"INVALID_RECEIPT:{exc}"
            else:
                if receipt.slot != slot:
                    receipt_error = "RECEIPT_SLOT_MISMATCH"
                else:
                    record = receipt.record
        else:
            receipt_error = "RECEIPT_MISSING"

    if receipt_error and record is None:
        # Fail closed: absent or unreadable durable evidence is not success.
        from sports_edge_tennis.ops.monitoring import RunRecord

        record = RunRecord(
            run_id=f"unverified-{slot.isoformat()}",
            slot=slot,
            started_at=slot,
            finished_at=None,
            error=receipt_error,
            crashed=True,
        )

    state = load_state(args.state_path)
    result = run_monitoring_cycle(
        slot=slot,
        record=record,
        now=now,
        state=state,
        client=None if args.dry_run else HealthchecksClient(config),
        run_timeout=timedelta(minutes=args.run_timeout_minutes),
        dry_run=args.dry_run,
    )
    write_state(args.state_path, result.state)
    payload = {
        "stream": args.stream,
        "dry_run": args.dry_run,
        "monitoring": config.redacted(),
        "receipt_error": receipt_error,
        **result.to_payload(),
    }
    text = json.dumps(payload, indent=2, sort_keys=True)
    print(text)
    if args.out_json:
        with open(args.out_json, "w", encoding="utf-8") as handle:
            handle.write(text + "\n")
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
