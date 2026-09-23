from __future__ import annotations

"""Lightweight progress heartbeat for long read-only replay calls.

Emitting a periodic line while a long model replay runs makes rehearsal jobs
observable without changing any model behaviour. The heartbeat runs on a daemon
thread and never mutates shared state.
"""

import contextlib
import threading
import time
from collections.abc import Iterator


@contextlib.contextmanager
def heartbeat(label: str, *, interval_seconds: float = 30.0) -> Iterator[None]:
    stop = threading.Event()
    started = time.monotonic()

    def _beat() -> None:
        while not stop.wait(interval_seconds):
            print(
                f"PROGRESS {label} elapsed_seconds={time.monotonic() - started:.0f}",
                flush=True,
            )

    thread = threading.Thread(target=_beat, daemon=True)
    thread.start()
    print(f"PROGRESS {label} started", flush=True)
    try:
        yield
    finally:
        stop.set()
        thread.join(timeout=1.0)
        print(
            f"PROGRESS {label} finished_seconds={time.monotonic() - started:.0f}",
            flush=True,
        )


__all__ = ["heartbeat"]
