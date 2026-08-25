"""A hard ceiling on how long one call may take.

`workloads.yaml` promised a per-query ceiling and did not have one. The check was
`if elapsed > timeout_s` *after* the call returned, which enforces nothing when the
call never returns.

It happened. A CognoDB query was accepted and never answered, and the run sat in
`_ssl__SSLSocket_read` -> `read()` on an ESTABLISHED socket for 17 minutes before
being killed by hand. The instance was healthy the whole time; a single request had
stalled. No driver here exposes a socket read timeout, and Bolt transaction timeouts
would need the server to honour them, so the ceiling has to be enforced client-side.

One reused worker thread, not one per call. The Bolt, RESP and HTTP adapters keep
thread-local sessions, so a fresh thread per iteration would build a fresh session
per iteration and measure connection setup rather than the query.

The timer belongs *inside* the submitted callable, so the submit and join overhead
is not attributed to the database.

A timeout leaves the worker thread blocked forever, which cannot be helped: Python
cannot interrupt a thread stuck in a syscall. So the Deadline marks itself poisoned
and the caller discards it. Since one is created per workload, the next workload
gets a fresh thread and therefore a fresh session, which is what you want after a
hang anyway. The orphan is a daemon thread and does not hold up process exit.
"""

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from typing import Any


class QueryTimeout(TimeoutError):
    """A call exceeded the per-query ceiling and was abandoned, not cancelled."""

    def __init__(self, seconds: float, detail: str = ""):
        message = f"exceeded the {seconds}s per-query ceiling"
        if detail:
            message = f"{message} ({detail})"
        super().__init__(message)
        self.seconds = seconds


class Deadline:
    def __init__(self, seconds: float):
        self.seconds = seconds
        self.poisoned = False
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="gb-deadline")

    def run(self, fn: Callable[[], Any]) -> Any:
        if self.poisoned:
            raise QueryTimeout(self.seconds, "worker still blocked from an earlier timeout")

        future = self._pool.submit(fn)
        try:
            return future.result(timeout=self.seconds)
        except FutureTimeout:
            # The query is still running somewhere on the server. We are abandoning
            # it, not cancelling it, and the results say so rather than implying the
            # engine was interrupted.
            self.poisoned = True
            self._pool.shutdown(wait=False, cancel_futures=True)
            raise QueryTimeout(self.seconds) from None

    def close(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)

    def __enter__(self) -> "Deadline":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
