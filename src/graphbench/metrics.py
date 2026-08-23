"""Timing and percentiles.

p50/p95 nearest-rank, raw samples retained. Reasoning: docs/DECISIONS.md#6.
"""

import math
import statistics
import time
from dataclasses import dataclass, field
from typing import Any


class Timer:
    """perf_counter_ns wrapper. ns because some point lookups here are sub-ms."""

    __slots__ = ("_start", "elapsed_ms")

    def __enter__(self) -> "Timer":
        self._start = time.perf_counter_ns()
        return self

    def __exit__(self, *exc: object) -> None:
        self.elapsed_ms = (time.perf_counter_ns() - self._start) / 1_000_000


@dataclass
class LatencySeries:
    """A set of latency samples in milliseconds, plus what went wrong."""

    samples: list[float] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    timeouts: int = 0

    def add(self, ms: float) -> None:
        self.samples.append(ms)

    def fail(self, exc: BaseException, timed_out: bool = False) -> None:
        # Collected, not raised. "3 of 100 iterations OOMed" is a result.
        if timed_out:
            self.timeouts += 1
        self.errors.append(f"{type(exc).__name__}: {exc}")

    def __len__(self) -> int:
        return len(self.samples)

    def percentile(self, p: float) -> float | None:
        """Nearest-rank percentile. p is a fraction, so 0.95 not 95."""
        if not self.samples:
            return None
        ordered = sorted(self.samples)
        # ceil(p*n) gives the 1-based rank; clamp because ceil(0*n) is 0.
        rank = max(1, math.ceil(p * len(ordered)))
        return ordered[rank - 1]

    @property
    def p50(self) -> float | None:
        return self.percentile(0.50)

    @property
    def p95(self) -> float | None:
        return self.percentile(0.95)

    @property
    def p99(self) -> float | None:
        # Reported but not leaned on: with 100 samples this is one observation.
        return self.percentile(0.99)

    @property
    def mean(self) -> float | None:
        return statistics.fmean(self.samples) if self.samples else None

    @property
    def stdev(self) -> float | None:
        # Shows whether a p50 is stable or the middle of a very wide spread.
        return statistics.stdev(self.samples) if len(self.samples) > 1 else None

    def to_dict(self, include_samples: bool = True) -> dict[str, Any]:
        out: dict[str, Any] = {
            "count": len(self.samples),
            "errors": len(self.errors),
            "timeouts": self.timeouts,
            "p50_ms": _r(self.p50),
            "p95_ms": _r(self.p95),
            "p99_ms": _r(self.p99),
            "mean_ms": _r(self.mean),
            "stdev_ms": _r(self.stdev),
            "min_ms": _r(min(self.samples)) if self.samples else None,
            "max_ms": _r(max(self.samples)) if self.samples else None,
        }
        if self.errors:
            # First few only; 100 copies of one message tells you nothing extra.
            out["error_samples"] = self.errors[:5]
        if include_samples:
            out["samples_ms"] = [_r(s) for s in self.samples]
        return out


@dataclass
class ThroughputResult:
    """Result of a timed concurrent phase, for the mixed workload."""

    concurrency: int
    duration_s: float
    reads: int
    writes: int
    read_latency: LatencySeries
    write_latency: LatencySeries

    @property
    def total_ops(self) -> int:
        return self.reads + self.writes

    @property
    def qps(self) -> float:
        return self.total_ops / self.duration_s if self.duration_s > 0 else 0.0

    def to_dict(self, include_samples: bool = False) -> dict[str, Any]:
        return {
            "concurrency": self.concurrency,
            "duration_s": round(self.duration_s, 2),
            "reads": self.reads,
            "writes": self.writes,
            "total_ops": self.total_ops,
            "qps": round(self.qps, 1),
            # Off by default: 40 clients x 30s is tens of thousands of samples per
            # level and would make the results file unreviewable.
            "read_latency": self.read_latency.to_dict(include_samples),
            "write_latency": self.write_latency.to_dict(include_samples),
        }


def _r(v: float | None) -> float | None:
    """3 decimals: 2 would flatten sub-ms point lookups, more is false precision."""
    return None if v is None else round(v, 3)
