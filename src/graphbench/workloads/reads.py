"""Sequential read workloads: baseline, traversals, lookups, aggregations.

All of them: warm up N times discarding results, then measure the next N and keep
every sample. Same shape everywhere, driven from one config. Failures are counted
rather than raised. See docs/DECISIONS.md sections 6, 7 and 9.
"""

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from graphbench.adapters.base import Adapter
from graphbench.config import Workloads
from graphbench.deadline import Deadline, QueryTimeout
from graphbench.errors import is_connection_error, is_resource_error
from graphbench.metrics import LatencySeries, Timer


@dataclass
class ReadResult:
    name: str
    latency: LatencySeries = field(default_factory=LatencySeries)
    # What the query returned, keyed by start node. The runner compares these
    # across platforms: same input, same answer, or the timing means nothing.
    checks: dict[str, int] = field(default_factory=dict)
    expected: int | None = None
    iterations_requested: int = 0
    abandoned: bool = False
    first_call_ms: float | None = None
    # Iterations that only succeeded after reconnecting. Reported because a run
    # with 12 reconnects is a different run from one with none, even if the
    # percentiles look the same.
    reconnects: int = 0

    def to_dict(self, include_samples: bool = True) -> dict[str, Any]:
        out: dict[str, Any] = {
            "name": self.name,
            "iterations_requested": self.iterations_requested,
            "abandoned": self.abandoned,
            "first_call_ms": self.first_call_ms,
            "reconnects": self.reconnects,
            "latency": self.latency.to_dict(include_samples),
        }
        if self.expected is not None:
            out["expected"] = self.expected
            out["matches_expected"] = self.observed_value == self.expected
        if self.checks:
            out["checks"] = self.checks
        return out

    @property
    def observed_value(self) -> int | None:
        """The single value this workload returned, when it returns just one."""
        if len(self.checks) == 1:
            return next(iter(self.checks.values()))
        return None


# A cloud platform 240ms away will lose a connection occasionally, and that is not
# the same event as an engine running out of memory. Beyond this many, the platform
# is genuinely unstable rather than unlucky, and the workload is abandoned.
MAX_RECONNECTS = 25

# Attempts per iteration, and how long to wait before each retry.
#
# One retry with no wait was not enough, and the reason is visible in the data. A
# CognoDB 3-hop query ran 8,975ms and killed the connection; the next five queries
# then failed within 500ms each regardless of start-node degree, before the sixth
# recovered and completed in 17.7s. A dropped connection there is not one bad
# iteration, it is a short cascade, so an immediate single retry lands inside the
# cascade and abandons a workload that would have finished.
#
# Engine-neutral on purpose: every platform gets the same policy, and the retry
# count goes into the results so a row that needed 12 of them is visibly different
# from one that needed none.
RETRY_BACKOFF_S = (0.0, 0.5, 2.0)


def run_read_workload(
    name: str,
    call: Callable[[int], tuple[int, str]],
    workloads: Workloads,
    expected: int | None = None,
    adapter: "Adapter | None" = None,
) -> ReadResult:
    """Warm up, then measure.

    `call(i)` runs iteration i and returns (result_value, check_key). Passing the
    index in lets each workload decide whether it rotates start keys or repeats.
    """
    result = ReadResult(name=name, expected=expected, iterations_requested=workloads.iterations)
    # One per workload: a timeout poisons it, and the next workload then gets a fresh
    # thread and a fresh session, which is what you want after a hang.
    deadline = Deadline(workloads.timeout_s)

    def timed(index: int):
        """Timer inside the worker, so submit/join overhead is not charged to the db."""
        with Timer() as t:
            value, check_key = call(index)
        return value, check_key, t.elapsed_ms

    def should_retry(exc: BaseException, attempt: int) -> bool:
        """Worth another go through a fresh connection, or genuinely over?"""
        if attempt >= len(RETRY_BACKOFF_S) or adapter is None:
            return False
        if not is_connection_error(exc) or _is_memory_error(exc):
            return False
        if result.reconnects >= MAX_RECONNECTS:
            return False
        result.reconnects += 1
        adapter.reset_connection()
        # Wait before the next attempt. Retrying instantly just lands inside the
        # same cascade that caused the first failure.
        time.sleep(RETRY_BACKOFF_S[attempt])
        return True

    # First call is kept separately: it carries plan compilation and index warm-up.
    # Not a true cold start, since the caches are hot from having just written the
    # data. Labelled as such in the results.
    #
    # Warm-up gets the same reconnect tolerance as the measured loop, or one dropped
    # packet here would abandon the workload before measurement even began.
    for i in range(workloads.warmup):
        for attempt in range(1, len(RETRY_BACKOFF_S) + 1):
            try:
                _, _, elapsed_ms = deadline.run(lambda i=i: timed(i))
                if i == 0:
                    result.first_call_ms = round(elapsed_ms, 3)
                break
            except QueryTimeout as exc:
                # fail(timed_out=True) is what counts it; bumping timeouts here as
                # well double-counted.
                result.latency.fail(exc, timed_out=True)
                result.abandoned = True
                deadline.close()
                return result
            except Exception as exc:  # noqa: BLE001
                if should_retry(exc, attempt):
                    continue
                result.latency.fail(exc)
                if _is_fatal(exc):
                    result.abandoned = True
                    deadline.close()
                    return result
                break

    for i in range(workloads.iterations):
        # Two attempts, and only when the first failed for a reason a reconnect
        # could fix. Without this a single lost packet on a long link discards the
        # other 99 iterations, and the missing row reads as "this engine cannot run
        # this query" rather than "the internet hiccuped".
        for attempt in range(1, len(RETRY_BACKOFF_S) + 1):
            try:
                index = workloads.warmup + i
                value, check_key, elapsed_ms = deadline.run(lambda i=index: timed(i))
                result.latency.add(elapsed_ms)
                result.checks.setdefault(check_key, value)
                break
            except QueryTimeout as exc:
                # Abandoned, not cancelled: the query is still running on the server.
                # Recorded as a timeout rather than folded into the percentiles as an
                # ordinary sample.
                result.latency.fail(exc, timed_out=True)
                result.abandoned = True
                deadline.close()
                return result
            except Exception as exc:  # noqa: BLE001
                if should_retry(exc, attempt):
                    continue

                result.latency.fail(exc)
                if _is_fatal(exc):
                    result.abandoned = True
                    deadline.close()
                    return result
                break

    deadline.close()
    return result


def _is_memory_error(exc: BaseException) -> bool:
    """Out of memory specifically, as opposed to a lost connection.

    The distinction matters: a reconnect fixes a lost packet and does nothing at all
    for an engine that has exhausted its heap, where retrying just produces a second
    identical failure.
    """
    return is_resource_error(exc) and not is_connection_error(exc)


def _is_fatal(exc: BaseException) -> bool:
    """Abandon the workload, or count it and continue?

    After an OOM the engine cannot give meaningful timings, so continuing would fill
    the percentiles with retry noise. A connection error only gets here after a
    reconnect has already been tried and failed. Shares its classifier with the wipe
    retry loop, because the two had drifted apart once and the read side was missing
    Memgraph's phrasing.
    """
    return is_resource_error(exc)


def all_read_workloads(adapter: Adapter, workloads: Workloads) -> list[ReadResult]:
    """Run every sequential read workload against one adapter, in a fixed order."""
    keys = adapter.graph.start_keys
    results: list[ReadResult] = []

    if workloads.baseline_query:
        # The floor: driver, protocol, network, no graph work. Everything else is
        # only interpretable relative to this.
        results.append(
            run_read_workload(
                "baseline_noop",
                lambda i: (adapter.noop(), "value"),
                workloads,
                expected=1,
                adapter=adapter,
            )
        )

    results.append(
        run_read_workload(
            "point_lookup",
            lambda i: _point(adapter, keys[i % len(keys)]),
            workloads,
            adapter=adapter,
        )
    )

    for depth in workloads.hops:
        results.append(
            run_read_workload(
                f"traversal_{depth}hop",
                lambda i, d=depth: _khop(adapter, keys[i % len(keys)], d),
                workloads,
                adapter=adapter,
            )
        )

    results.append(
        run_read_workload(
            "filtered_lookup",
            lambda i: (
                adapter.filtered_lookup(workloads.cohort, workloads.min_degree),
                "value",
            ),
            workloads,
            adapter=adapter,
        )
    )

    # The primary aggregation: no engine can answer this from metadata, it has to
    # scan the label. Expected value is the node count, so it is self-checking.
    results.append(
        run_read_workload(
            "aggregation_groupby_cohort",
            lambda i: (adapter.aggregate_cohorts(), "value"),
            workloads,
            expected=adapter.graph.node_count,
            adapter=adapter,
        )
    )

    # Secondary, and labelled as such: Neo4j serves count(r) for a single type from
    # its count store in O(1), so this partly measures "is there a counter".
    results.append(
        run_read_workload(
            "aggregation_rel_count",
            lambda i: (adapter.aggregate_rel_count(), "value"),
            workloads,
            expected=adapter.graph.edge_count,
            adapter=adapter,
        )
    )

    return results


def _point(adapter: Adapter, key: str) -> tuple[int, str]:
    return adapter.point_lookup(key), key


def _khop(adapter: Adapter, key: str, depth: int) -> tuple[int, str]:
    return adapter.k_hop(key, depth), key
