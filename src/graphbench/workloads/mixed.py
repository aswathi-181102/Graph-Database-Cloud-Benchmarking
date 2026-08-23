"""Concurrent read/write throughput, swept across client counts.

A sweep rather than one number, because the shape is the point: an engine that wins
at one client and collapses at forty is a different product from one that stays
flat. 40 clients against 0.5 vCPU is deliberate overload.

Mix, read composition and seeding rationale: docs/DECISIONS.md#8.
"""

import random
import threading
import time
from dataclasses import dataclass

from graphbench.adapters.base import Adapter
from graphbench.config import Workloads
from graphbench.metrics import LatencySeries, ThroughputResult, Timer

# Split of the read share between point lookup and 2-hop. Not per-platform.
POINT_SHARE = 0.5


@dataclass
class MixedOutcome:
    results: list[ThroughputResult]
    writes_cleaned: int
    errors: list[str]


def run_mixed(adapter: Adapter, workloads: Workloads, tag: str) -> MixedOutcome:
    results: list[ThroughputResult] = []
    errors: list[str] = []

    for level in workloads.concurrency:
        try:
            results.append(_run_level(adapter, workloads, tag, level))
        except Exception as exc:  # noqa: BLE001
            # An engine dying at 40 clients is a finding, and it should not cost us
            # the 1 and 10 client numbers already collected.
            errors.append(f"concurrency {level}: {type(exc).__name__}: {exc}")

    cleaned = 0
    if workloads.cleanup_writes:
        try:
            cleaned = adapter.cleanup_writes(tag)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"cleanup: {type(exc).__name__}: {exc}")
    return MixedOutcome(results=results, writes_cleaned=cleaned, errors=errors)


def _run_level(adapter: Adapter, workloads: Workloads, tag: str, clients: int) -> ThroughputResult:
    keys = adapter.graph.start_keys
    read_latency = LatencySeries()
    write_latency = LatencySeries()
    counters = {"reads": 0, "writes": 0}

    # Merge only, never in the hot loop: a shared lock there would serialise the
    # clients and the throughput number would describe my mutex.
    merge_lock = threading.Lock()

    # Everyone starts applying load at the same instant. Without this the first
    # thread gets an uncontended database and the last gets a saturated one.
    start_gate = threading.Barrier(clients + 1)
    deadline = threading.Event()

    def worker(worker_id: int) -> None:
        rng = random.Random(_WORKER_SEED + worker_id)
        local_reads: list[float] = []
        local_writes: list[float] = []
        local_errors: list[str] = []
        reads = writes = 0
        seq = 0

        start_gate.wait()
        while not deadline.is_set():
            key = keys[(worker_id * 31 + seq) % len(keys)]
            seq += 1
            is_read = rng.random() < workloads.read_ratio
            try:
                if is_read:
                    with Timer() as t:
                        if rng.random() < POINT_SHARE:
                            adapter.point_lookup(key)
                        else:
                            adapter.k_hop(key, 2)
                    local_reads.append(t.elapsed_ms)
                    reads += 1
                else:
                    with Timer() as t:
                        adapter.insert_write(tag, worker_id * 1_000_000 + seq, key)
                    local_writes.append(t.elapsed_ms)
                    writes += 1
            except Exception as exc:  # noqa: BLE001
                local_errors.append(f"{type(exc).__name__}: {exc}")
                if len(local_errors) > 50:
                    break  # not coming back; stop hammering it

        with merge_lock:
            read_latency.samples.extend(local_reads)
            write_latency.samples.extend(local_writes)
            read_latency.errors.extend(local_errors[:5])
            counters["reads"] += reads
            counters["writes"] += writes

    threads = [threading.Thread(target=worker, args=(i,), daemon=True) for i in range(clients)]
    for t in threads:
        t.start()

    start_gate.wait()
    began = time.perf_counter()
    # Sleep, not poll: during the window the client should do nothing but wait on
    # sockets.
    time.sleep(workloads.duration_s)
    deadline.set()
    for t in threads:
        # A worker blocked on a slow query still needs to hand back its samples.
        t.join(timeout=workloads.timeout_s + 30)
    elapsed = time.perf_counter() - began

    return ThroughputResult(
        concurrency=clients,
        duration_s=elapsed,
        reads=counters["reads"],
        writes=counters["writes"],
        read_latency=read_latency,
        write_latency=write_latency,
    )


# Fixed so the read/write interleaving is identical across platforms and reruns.
_WORKER_SEED = 4517
