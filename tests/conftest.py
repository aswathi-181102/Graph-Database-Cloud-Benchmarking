"""Shared fixtures, including a fake engine.

The adapters are exercised against real databases, which is the right way to test
them, but it leaves the runner and the workload loops untested because they need a
live engine to drive. A fake adapter closes that: it implements the same contract in
a dict, so the parts of the harness that decide what to run, what to time, when to
abandon a workload and what to write out can all be tested without Docker.

It can also be told to fail in specific ways, which is the only practical way to
test the failure handling. Waiting for a real 256 MB engine to OOM on cue is not a
test.
"""

import csv
from dataclasses import dataclass, field

import pytest

from graphbench.adapters.base import Adapter
from graphbench.config import Platform, Tier, Workloads
from graphbench.datasets.loader import PreparedGraph


@pytest.fixture
def workloads():
    """Small but structurally identical to the shipped config."""
    return Workloads(
        batch_size=2,
        iterations=100,
        warmup=2,
        baseline_query=True,
        hops=(1, 2),
        timeout_s=30,
        concurrency=(1, 2),
        duration_s=1,
        read_ratio=0.9,
        cleanup_writes=True,
        cohort=7,
        min_degree=10,
    )


@pytest.fixture
def graph(tmp_path):
    """A five-node line graph on disk, so iter_nodes/iter_edges are real reads."""
    directory = tmp_path / "tiny"
    directory.mkdir()

    nodes = [
        {"id": i, "key": f"a{i}", "cohort": i % 4, "degree": 1 if i in (0, 4) else 2}
        for i in range(5)
    ]
    with (directory / "nodes.csv").open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=["id", "key", "cohort", "degree"])
        writer.writeheader()
        writer.writerows(nodes)

    with (directory / "edges.csv").open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=["src", "dst"])
        writer.writeheader()
        writer.writerows({"src": i, "dst": i + 1} for i in range(4))

    manifest = {
        "dataset": "tiny",
        "nodes": 5,
        "edges": 4,
        "node_label": "Author",
        "rel_type": "COAUTHOR",
        "cohorts": 4,
        "raw_sha256": "0" * 64,
        "source_url": "file:///tiny",
    }
    return PreparedGraph(directory, manifest, [f"a{i}" for i in range(5)])


@pytest.fixture
def platform():
    return Platform(
        id="fake-local",
        display="Fake (capped)",
        engine="fake",
        adapter="fake",
        track="local",
        tier=Tier(name="test", vcpu=0.5, ram_mb=256, disk_gb=1),
    )


@dataclass
class FakeAdapter(Adapter):
    """In-memory engine implementing the real contract.

    Counts calls and can be told to raise, so the harness's timing, verification and
    failure paths are testable without a database.
    """

    # Set these to make it misbehave.
    fail_on: dict[str, Exception] = field(default_factory=dict)
    slow_ops: dict[str, float] = field(default_factory=dict)
    fail_node_batches: set = field(default_factory=set)
    fail_edge_batches: set = field(default_factory=set)

    engine = "fake"
    dialect = "cypher"
    load_method = "fake in-memory"

    def __init__(self, platform, graph, workloads, **kwargs):
        super().__init__(platform, graph, workloads)
        self.fail_on = kwargs.get("fail_on", {})
        self.slow_ops = kwargs.get("slow_ops", {})
        self.fail_node_batches = kwargs.get("fail_node_batches", set())
        self.fail_edge_batches = kwargs.get("fail_edge_batches", set())
        self.calls: dict[str, int] = {}
        self.nodes: dict[str, dict] = {}
        self.edges: list[tuple[int, int]] = []
        self.writes: dict[str, list[str]] = {}
        self.closed = False
        self.wiped = 0
        self._node_batch = 0
        self._edge_batch = 0

    def _record(self, op: str) -> None:
        self.calls[op] = self.calls.get(op, 0) + 1
        if op in self.slow_ops:
            import time

            time.sleep(self.slow_ops[op])
        if op in self.fail_on:
            raise self.fail_on[op]

    def connect(self) -> None:
        self._record("connect")

    def close(self) -> None:
        self.closed = True

    def server_version(self) -> str:
        self._record("server_version")
        return "Fake 1.0"

    def wipe(self) -> None:
        self._record("wipe")
        self.nodes.clear()
        self.edges.clear()
        self.wiped += 1

    def create_indexes(self) -> list[str]:
        self._record("create_indexes")
        return ["fake index on key"]

    def insert_node_batch(self, rows: list[dict]) -> None:
        batch = self._node_batch
        self._node_batch += 1
        if batch in self.fail_node_batches:
            raise RuntimeError(f"fake node batch {batch} refused")
        for row in rows:
            self.nodes[row["key"]] = row

    def insert_edge_batch(self, rows: list[dict]) -> None:
        batch = self._edge_batch
        self._edge_batch += 1
        if batch in self.fail_edge_batches:
            raise RuntimeError(f"fake edge batch {batch} refused")
        self.edges.extend((row["src"], row["dst"]) for row in rows)

    def noop(self) -> int:
        self._record("noop")
        return 1

    def k_hop(self, key: str, k: int) -> int:
        self._record(f"k_hop{k}")
        # Line graph: within k hops of a node there are at most k on each side.
        index = int(key[1:])
        reach = {
            n
            for n in range(len(self.nodes) or 5)
            if n != index and abs(n - index) <= k
        }
        return len(reach)

    def point_lookup(self, key: str) -> int:
        self._record("point_lookup")
        if key not in self.nodes:
            raise KeyError(key)
        return int(self.nodes[key]["degree"])

    def filtered_lookup(self, cohort: int, min_degree: int) -> int:
        self._record("filtered_lookup")
        return sum(
            1
            for n in self.nodes.values()
            if n["cohort"] == cohort and n["degree"] >= min_degree
        )

    def aggregate_cohorts(self) -> int:
        self._record("aggregate_cohorts")
        return len(self.nodes)

    def aggregate_rel_count(self) -> int:
        self._record("aggregate_rel_count")
        return len(self.edges)

    def insert_write(self, tag: str, seq: int, attach_to: str) -> int:
        self._record("insert_write")
        self.writes.setdefault(tag, []).append(f"{tag}-{seq}")
        return 1

    def cleanup_writes(self, tag: str) -> int:
        self._record("cleanup_writes")
        return len(self.writes.pop(tag, []))

    def footprint(self) -> dict:
        return {"observable": True, "source": "fake"}


@pytest.fixture
def adapter_factory(platform, graph, workloads):
    def make(**kwargs):
        return FakeAdapter(platform, graph, workloads, **kwargs)

    return make


@pytest.fixture
def adapter(adapter_factory):
    a = adapter_factory()
    a.connect()
    return a
