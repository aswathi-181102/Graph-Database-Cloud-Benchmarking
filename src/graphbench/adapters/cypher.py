"""The Cypher half of the workload, shared by CognoDB, Neo4j, Memgraph, FalkorDB.

Four of the five engines run byte-identical query text, so a latency difference
between them cannot be me writing a better query for one. Only DDL is
engine-specific, since that is where the dialects actually diverge.
"""

from graphbench.adapters.base import Adapter

# Label used only by the mixed workload's writes, so they can be found and
# deleted without touching the loaded graph.
BENCH_LABEL = "Bench"
BENCH_REL = "BENCH_EDGE"


class CypherAdapter(Adapter):
    """Implements every operation in Cypher. Subclasses supply transport and DDL."""

    dialect = "cypher"

    def build_queries(self) -> None:
        """Precompute query strings. f-string formatting inside a timed loop would
        be a measurable fraction of a sub-ms point lookup."""
        label, rel = self.label, self.rel

        self.q_noop = "RETURN 1 AS n"

        # Undirected pattern: co-authorship has no direction even though it is
        # stored one way round. 1..k neighbourhood, not exact depth, so the counts
        # match AQL's traversal. See docs/DECISIONS.md#4.
        self.q_khop = {
            k: (
                f"MATCH (a:{label} {{key: $key}})-[:{rel}*1..{k}]-(b:{label}) "
                f"WHERE b.key <> $key "
                f"RETURN count(DISTINCT b) AS n"
            )
            for k in self.workloads.hops
        }

        # Returns a scalar, not the node: `RETURN a` would make this a
        # serialisation benchmark.
        self.q_point = f"MATCH (a:{label} {{key: $key}}) RETURN a.degree AS n"

        # Two predicates on purpose, so engines with a composite index can use it
        # and engines without one have to filter.
        self.q_filtered = (
            f"MATCH (a:{label}) "
            f"WHERE a.cohort = $cohort AND a.degree >= $minDegree "
            f"RETURN count(a) AS n"
        )

        self.q_agg_cohorts = f"MATCH (a:{label}) RETURN a.cohort AS cohort, count(*) AS n"
        self.q_agg_rels = f"MATCH ()-[r:{rel}]->() RETURN count(r) AS n"

        # CREATE, not MERGE: the data is already deduped, so MERGE would only add
        # an index probe per row for a guarantee we already have.
        self.q_insert_nodes = (
            f"UNWIND $rows AS row "
            f"CREATE (:{label} {{id: row.id, key: row.key, "
            f"cohort: row.cohort, degree: row.degree}})"
        )

        # Needs the id index built in the previous phase. Matching on id rather
        # than key avoids building 400k strings client-side.
        self.q_insert_edges = (
            f"UNWIND $rows AS row "
            f"MATCH (a:{label} {{id: row.src}}), (b:{label} {{id: row.dst}}) "
            f"CREATE (a)-[:{rel}]->(b)"
        )

        # Index lookup + node create + relationship create, which is roughly what
        # appending to an agent's context graph looks like.
        self.q_write = (
            f"MATCH (a:{label} {{key: $key}}) "
            f"CREATE (b:{BENCH_LABEL} {{key: $bkey, tag: $tag}}) "
            f"CREATE (a)-[:{BENCH_REL}]->(b) "
            f"RETURN 1 AS n"
        )
        self.q_write_count = f"MATCH (b:{BENCH_LABEL} {{tag: $tag}}) RETURN count(b) AS n"
        self.q_write_delete = f"MATCH (b:{BENCH_LABEL} {{tag: $tag}}) DETACH DELETE b"

        # Batched: one `MATCH (n) DETACH DELETE n` over 198k nodes builds a
        # transaction far bigger than 256 MB and dies everywhere.
        self.q_wipe_batch = "MATCH (n) WITH n LIMIT $limit DETACH DELETE n RETURN count(n) AS n"

    # Subclasses implement the transport. Returning a list of dicts keeps the
    # operations below free of driver types.
    def run(self, query: str, params: dict | None = None) -> list[dict]:
        raise NotImplementedError

    # ------------------------------------------------------------ read ops ---
    def noop(self) -> int:
        return int(self.run(self.q_noop)[0]["n"])

    def k_hop(self, key: str, k: int) -> int:
        return int(self.run(self.q_khop[k], {"key": key})[0]["n"])

    def point_lookup(self, key: str) -> int:
        rows = self.run(self.q_point, {"key": key})
        # Missing key means the start-node list and the graph disagree: a data bug,
        # not a slow query.
        if not rows:
            raise KeyError(f"{self.platform.id}: no node with key {key!r}")
        return int(rows[0]["n"])

    def filtered_lookup(self, cohort: int, min_degree: int) -> int:
        rows = self.run(self.q_filtered, {"cohort": cohort, "minDegree": min_degree})
        return int(rows[0]["n"])

    def aggregate_cohorts(self) -> int:
        return sum(int(r["n"]) for r in self.run(self.q_agg_cohorts))

    def aggregate_rel_count(self) -> int:
        return int(self.run(self.q_agg_rels)[0]["n"])

    # -------------------------------------------------------------- writes ---
    def insert_write(self, tag: str, seq: int, attach_to: str) -> int:
        params = {"key": attach_to, "bkey": f"{tag}-{seq}", "tag": tag}
        rows = self.run(self.q_write, params)
        return int(rows[0]["n"]) if rows else 0

    def cleanup_writes(self, tag: str) -> int:
        # Count then delete: portable Cypher cannot report what DETACH DELETE
        # removed, and the count is how we confirm the graph was restored.
        rows = self.run(self.q_write_count, {"tag": tag})
        n = int(rows[0]["n"]) if rows else 0
        if n:
            self.run(self.q_write_delete, {"tag": tag})
        return n

    # ---------------------------------------------------------------- misc ---
    def insert_node_batch(self, rows: list[dict]) -> None:
        self.run(self.q_insert_nodes, {"rows": rows})

    def insert_edge_batch(self, rows: list[dict]) -> None:
        self.run(self.q_insert_edges, {"rows": rows})

    # Started at 10,000 and Neo4j died: OutOfMemoryError, Java heap space, then a
    # dropped connection. 10k nodes means detaching 100k+ relationships in one
    # transaction against a 96 MB heap. Not a measured phase, so a small batch
    # costs only wall clock.
    WIPE_BATCH = 2_000
    WIPE_BATCH_FLOOR = 100

    def wipe(self, batch: int | None = None) -> None:
        batch = batch or self.WIPE_BATCH
        self.wipe_batch_used = batch

        while True:
            try:
                rows = self.run(self.q_wipe_batch, {"limit": batch})
            except Exception as exc:  # noqa: BLE001
                if not _is_resource_error(exc) or batch <= self.WIPE_BATCH_FLOOR:
                    raise
                # The OOM kills the connection too, so the session has to go first
                # or every retry fails on a defunct socket.
                self.reset_connection()
                batch = max(self.WIPE_BATCH_FLOOR, batch // 2)
                self.wipe_batch_used = batch
                continue

            if not rows or int(rows[0]["n"]) == 0:
                return

    def reset_connection(self) -> None:
        """Drop cached connection state. Overridden by transports that cache it."""
        return


def _is_resource_error(exc: BaseException) -> bool:
    """Engine out of memory, rather than a real bug?

    Matching message text is ugly, but these engines report the same condition
    under at least four different exception types.
    """
    text = f"{type(exc).__name__} {exc}".lower()
    needles = (
        "outofmemory",
        "out of memory",
        "not enough memory",
        "heap space",
        "memory limit",
        "defunct",
        "connection",
    )
    return any(n in text for n in needles)
