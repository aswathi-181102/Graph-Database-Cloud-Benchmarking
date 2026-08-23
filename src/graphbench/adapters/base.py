"""The contract every engine implements.

The whole fairness argument of this benchmark rests on this file. There is one
definition of each workload and each engine only supplies its own dialect of it,
so it is structurally impossible for one platform to be running a subtly
different query than another. If the interface had been "each adapter has a
run_benchmark method" the engines would have drifted apart within a day.

Two design decisions worth defending:

Every read operation returns an int, not rows. Partly because materialising rows
would measure Python object construction, but mostly because it makes the
results checkable: 2-hop from a given start key must return the same count on
all five platforms. The runner compares them and flags disagreement. Without
that, a query that is accidentally cheaper on one engine (say, because a
direction was dropped) would look like a performance win.

load() is a template method, not abstract. The sequence is wipe, load nodes,
create indexes, load edges, and it is identical everywhere. Only the batch
insert and the DDL are engine specific. Letting each adapter own the sequence
would have allowed a platform to, for example, build indexes after the edges and
post a better ingest number.
"""

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, ClassVar

from graphbench.config import Platform, Workloads
from graphbench.datasets import PreparedGraph


@dataclass
class LoadPhase:
    name: str  # nodes | indexes | edges
    rows: int
    batches: int
    seconds: float
    errors: list[str] = field(default_factory=list)

    def rows_per_second(self) -> float | None:
        if self.seconds <= 0 or self.rows == 0:
            return None
        return self.rows / self.seconds


@dataclass
class LoadStats:
    """Ingest result, broken into phases rather than one wall-clock number.

    The assignment asks for nodes/second, relationships/second and total wall
    clock. Splitting them out is what makes the total honest: index build time is
    real time spent loading, so it belongs in the total, but folding it into the
    node rate would flatter engines that build indexes lazily.
    """

    method: str
    batch_size: int
    phases: list[LoadPhase] = field(default_factory=list)

    def phase(self, name: str) -> LoadPhase | None:
        return next((p for p in self.phases if p.name == name), None)

    @property
    def total_seconds(self) -> float:
        return sum(p.seconds for p in self.phases)

    @property
    def nodes_per_second(self) -> float | None:
        p = self.phase("nodes")
        return p.rows_per_second() if p else None

    @property
    def rels_per_second(self) -> float | None:
        p = self.phase("edges")
        return p.rows_per_second() if p else None

    @property
    def errors(self) -> list[str]:
        return [e for p in self.phases for e in p.errors]

    def to_dict(self) -> dict[str, Any]:
        return {
            "method": self.method,
            "batch_size": self.batch_size,
            "total_seconds": round(self.total_seconds, 3),
            "nodes_per_second": _round(self.nodes_per_second),
            "rels_per_second": _round(self.rels_per_second),
            "phases": [
                {
                    "name": p.name,
                    "rows": p.rows,
                    "batches": p.batches,
                    "seconds": round(p.seconds, 3),
                    "rows_per_second": _round(p.rows_per_second()),
                    "errors": p.errors,
                }
                for p in self.phases
            ],
        }


def _round(v: float | None) -> float | None:
    return None if v is None else round(v, 1)


class Adapter(ABC):
    """One graph engine, driven through one fixed set of operations."""

    # Set by subclasses. load_method is reported verbatim in the README because
    # the assignment asks how the data got in, and "driver batching" versus "bulk
    # importer" is a big enough difference to change the ingest number by an
    # order of magnitude.
    engine: ClassVar[str] = ""
    dialect: ClassVar[str] = ""
    load_method: ClassVar[str] = ""

    def __init__(self, platform: Platform, graph: PreparedGraph, workloads: Workloads):
        self.platform = platform
        self.graph = graph
        self.workloads = workloads
        self.label = graph.node_label
        self.rel = graph.rel_type

    # ----------------------------------------------------------- lifecycle --
    @abstractmethod
    def connect(self) -> None: ...

    @abstractmethod
    def close(self) -> None: ...

    @abstractmethod
    def server_version(self) -> str:
        """Reported in results so a rerun can be compared against the same build."""

    @abstractmethod
    def wipe(self) -> None:
        """Leave the database empty. Called before every load."""

    @abstractmethod
    def create_indexes(self) -> list[str]:
        """Create the index set and return human-readable descriptions of it.

        Descriptions go straight into the README, because "which properties are
        indexed on each platform" is a required deliverable and the engines
        genuinely differ in what they support. An engine that cannot do a
        composite index is at a real disadvantage on the filtered lookup, and
        that should be visible rather than smoothed over.
        """

    # -------------------------------------------------------------- ingest --
    @abstractmethod
    def insert_node_batch(self, rows: list[dict]) -> None: ...

    @abstractmethod
    def insert_edge_batch(self, rows: list[dict]) -> None: ...

    def load(self) -> LoadStats:
        """wipe -> nodes -> indexes -> edges, timed per phase.

        Indexes are built between the two data phases on purpose. Before the
        nodes, a unique constraint would slow every node insert and the node rate
        would partly be measuring index maintenance. After the edges, the edge
        phase would have to find endpoints by full scan and would take minutes on
        every engine. Between them is both the fastest and the most realistic
        order, and more importantly it is the same order everywhere.
        """
        stats = LoadStats(method=self.load_method, batch_size=self.workloads.batch_size)
        self.wipe()

        stats.phases.append(
            self._timed_phase("nodes", self.graph.iter_nodes, self.insert_node_batch)
        )

        t0 = time.perf_counter()
        index_errors: list[str] = []
        try:
            self.created_indexes = self.create_indexes()
        except Exception as exc:  # noqa: BLE001 - recorded, not swallowed
            index_errors.append(f"{type(exc).__name__}: {exc}")
            self.created_indexes = []
        stats.phases.append(
            LoadPhase("indexes", rows=0, batches=1, seconds=time.perf_counter() - t0,
                      errors=index_errors)
        )

        stats.phases.append(
            self._timed_phase("edges", self.graph.iter_edges, self.insert_edge_batch)
        )
        return stats

    def _timed_phase(self, name, batches_fn, insert_fn) -> LoadPhase:
        rows = batches = 0
        errors: list[str] = []
        t0 = time.perf_counter()
        for batch in batches_fn(self.workloads.batch_size):
            try:
                insert_fn(batch)
                rows += len(batch)
            except Exception as exc:  # noqa: BLE001
                # Keep going and count it. A partial load is a legitimate result
                # at 256 MB and is far more informative than a traceback: it tells
                # us how many rows in the engine gave up.
                errors.append(f"batch {batches}: {type(exc).__name__}: {exc}")
            batches += 1
        return LoadPhase(name, rows=rows, batches=batches,
                         seconds=time.perf_counter() - t0, errors=errors)

    # --------------------------------------------------------- read ops -----
    @abstractmethod
    def noop(self) -> int:
        """Cheapest possible round trip. Isolates driver + protocol + network."""

    @abstractmethod
    def k_hop(self, key: str, k: int) -> int:
        """Distinct nodes within 1..k hops of `key`, excluding `key` itself."""

    @abstractmethod
    def point_lookup(self, key: str) -> int:
        """Return the node's degree, i.e. one indexed equality lookup."""

    @abstractmethod
    def filtered_lookup(self, cohort: int, min_degree: int) -> int:
        """Count nodes matching an equality plus a range predicate."""

    @abstractmethod
    def aggregate_cohorts(self) -> int:
        """Group-by cohort, returning the summed count.

        Returns the sum rather than the groups so it has a known correct answer:
        it must equal the node count on every platform.
        """

    @abstractmethod
    def aggregate_rel_count(self) -> int:
        """Count relationships by type. Must equal the dataset edge count."""

    # ------------------------------------------------------------- writes ---
    @abstractmethod
    def insert_write(self, tag: str, seq: int, attach_to: str) -> int:
        """One write for the mixed workload: a new node plus an edge to `attach_to`."""

    @abstractmethod
    def cleanup_writes(self, tag: str) -> int:
        """Remove everything insert_write created for `tag`. Returns rows removed."""

    # ---------------------------------------------------------- footprint ---
    def footprint(self) -> dict[str, Any]:
        """Whatever the platform exposes about its own resource use.

        Default is "nothing observable", which is the honest answer for a managed
        service that does not expose it. Overridden where the engine has a real
        introspection command. Never estimated.
        """
        return {"observable": False, "reason": "engine exposes no store/memory introspection"}
