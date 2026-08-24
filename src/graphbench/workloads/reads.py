"""Sequential read workloads: baseline, traversals, lookups, aggregations.

All of them: warm up N times discarding results, then measure the next N and keep
every sample. Same shape everywhere, driven from one config. Failures are counted
rather than raised. See docs/DECISIONS.md sections 6, 7 and 9.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from graphbench.adapters.base import Adapter
from graphbench.config import Workloads
from graphbench.errors import is_resource_error
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

    def to_dict(self, include_samples: bool = True) -> dict[str, Any]:
        out: dict[str, Any] = {
            "name": self.name,
            "iterations_requested": self.iterations_requested,
            "abandoned": self.abandoned,
            "first_call_ms": self.first_call_ms,
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


def run_read_workload(
    name: str,
    call: Callable[[int], tuple[int, str]],
    workloads: Workloads,
    expected: int | None = None,
) -> ReadResult:
    """Warm up, then measure.

    `call(i)` runs iteration i and returns (result_value, check_key). Passing the
    index in lets each workload decide whether it rotates start keys or repeats.
    """
    result = ReadResult(name=name, expected=expected, iterations_requested=workloads.iterations)

    # First call is kept separately: it carries plan compilation and index warm-up.
    # Not a true cold start, since the caches are hot from having just written the
    # data. Labelled as such in the results.
    for i in range(workloads.warmup):
        try:
            with Timer() as t:
                call(i)
            if i == 0:
                result.first_call_ms = round(t.elapsed_ms, 3)
        except Exception as exc:  # noqa: BLE001
            result.latency.fail(exc)
            if _is_fatal(exc):
                result.abandoned = True
                return result

    for i in range(workloads.iterations):
        try:
            with Timer() as t:
                value, check_key = call(workloads.warmup + i)
            result.latency.add(t.elapsed_ms)
            result.checks.setdefault(check_key, value)

            if t.elapsed_ms / 1000.0 > workloads.timeout_s:
                # Not cancelled (no portable client-side cancel here), but no further
                # iterations are issued and it is recorded as a timeout rather than
                # folded into the percentiles.
                result.latency.timeouts += 1
                result.abandoned = True
                return result
        except Exception as exc:  # noqa: BLE001
            result.latency.fail(exc)
            if _is_fatal(exc):
                result.abandoned = True
                return result

    return result


def _is_fatal(exc: BaseException) -> bool:
    """Abandon the workload, or count it and continue?

    After an OOM or a dead connection the engine cannot give meaningful timings, so
    continuing would fill the percentiles with retry noise. Shares its classifier
    with the wipe retry loop, because the two had already drifted apart once and the
    read side was missing Memgraph's phrasing.
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
            )
        )

    results.append(
        run_read_workload(
            "point_lookup",
            lambda i: _point(adapter, keys[i % len(keys)]),
            workloads,
        )
    )

    for depth in workloads.hops:
        results.append(
            run_read_workload(
                f"traversal_{depth}hop",
                lambda i, d=depth: _khop(adapter, keys[i % len(keys)], d),
                workloads,
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
        )
    )

    return results


def _point(adapter: Adapter, key: str) -> tuple[int, str]:
    return adapter.point_lookup(key), key


def _khop(adapter: Adapter, key: str, depth: int) -> tuple[int, str]:
    return adapter.k_hop(key, depth), key
