from __future__ import annotations

"""Validation-only reset: clear the Agent 3 model checkpoint (integrated rebuild
required) and the OddsPapi identity cache (to force a cold odds cycle).

Deletes ONLY those two validation-relevant stores; never touches A1/A2
checkpoints, canonical history, predictions, decisions, or settlement truth.
Sanitized output only.
"""

import json
import os

import psycopg

A3_CHECKPOINT_KEY = "AGENT_3_INTRANSITIVITY"


def main() -> int:
    dsn = os.environ.get("TENNIS_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not dsn:
        raise SystemExit("VALIDATION_RESET_BLOCKED missing_dsn")
    with psycopg.connect(dsn, autocommit=True) as conn:
        deleted_checkpoints = conn.execute(
            "DELETE FROM live_model_state_checkpoints WHERE checkpoint_key = %s",
            (A3_CHECKPOINT_KEY,),
        ).rowcount
        remaining = conn.execute(
            "SELECT checkpoint_key FROM live_model_state_checkpoints ORDER BY checkpoint_key"
        ).fetchall()
        deleted_identity = conn.execute(
            "DELETE FROM oddspapi_event_identity_cache"
        ).rowcount
    print(
        "VALIDATION_RESET "
        + json.dumps(
            {
                "a3_checkpoint_deleted": int(deleted_checkpoints),
                "identity_cache_deleted": int(deleted_identity),
                "remaining_checkpoint_keys": [str(row[0]) for row in remaining],
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
