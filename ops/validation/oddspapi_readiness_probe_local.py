"""Run the capped OddsPapi readiness probe from a workstation checkout.

    railway run --service tennis-live-runner -- python ops/validation/oddspapi_readiness_probe_local.py
    railway run --service tennis-live-runner -- python ops/validation/oddspapi_readiness_probe_local.py --execute

`railway run` supplies ODDSPAPI_API_KEY, but the runner's TENNIS_DATABASE_URL is a
private `*.railway.internal` host that a workstation cannot reach. This wrapper asks
the Railway CLI for the Postgres service's TCP-proxy credentials, builds the DSN in
memory (never printed, never written to disk) and runs this checkout's
`sports_edge_tennis.live.oddspapi_probe` with the 5-request hard cap. Without
`--execute` no OddsPapi request is made.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote

POSTGRES_SERVICE = "Postgres-Sdqz"
REPO_SRC = Path(__file__).resolve().parents[2] / "src"


def _public_dsn() -> str:
    railway = shutil.which("railway")
    if railway is None:
        raise SystemExit("ODDSPAPI_PROBE_BLOCKED reason=RAILWAY_CLI_NOT_FOUND")
    out = subprocess.run(
        [railway, "variables", "--service", POSTGRES_SERVICE, "--json"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    env = json.loads(out)
    try:
        return "postgresql://{}:{}@{}:{}/{}".format(
            quote(env["PGUSER"], safe=""),
            quote(env["PGPASSWORD"], safe=""),
            env["RAILWAY_TCP_PROXY_DOMAIN"],
            env["RAILWAY_TCP_PROXY_PORT"],
            env["PGDATABASE"],
        )
    except KeyError as exc:
        raise SystemExit(f"ODDSPAPI_PROBE_BLOCKED reason=MISSING_DB_VARIABLE:{exc.args[0]}")


def main(argv: list[str]) -> int:
    if "--dsn" in argv or any(arg.startswith("--dsn=") for arg in argv):
        raise SystemExit("ODDSPAPI_PROBE_BLOCKED reason=DSN_IS_RESOLVED_BY_WRAPPER")
    os.environ["TENNIS_DATABASE_URL"] = _public_dsn()
    sys.path.insert(0, str(REPO_SRC))
    from sports_edge_tennis.live import oddspapi_probe

    print(
        "ODDSPAPI_PROBE_START "
        f"execute={'--execute' in argv} "
        f"api_key_present={bool(os.environ.get('ODDSPAPI_API_KEY'))} "
        f"module={oddspapi_probe.__file__}",
        flush=True,
    )
    return oddspapi_probe.main(["--max-requests", str(oddspapi_probe.HARD_MAX_REQUESTS), *argv])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
