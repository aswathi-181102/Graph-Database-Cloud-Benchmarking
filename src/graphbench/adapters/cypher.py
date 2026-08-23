"""The Cypher half of the workload, shared by CognoDB, Neo4j, Memgraph, FalkorDB.

Four of the five engines speak Cypher, so four of the five run byte-identical
query text. That is the strongest fairness guarantee in the suite and it is worth
being explicit about what it buys: any latency difference between CognoDB, Neo4j,
Memgraph and FalkorDB cannot be attributed to me writing a better query for one
of them, because there is only one query.

The exception is DDL. Index and constraint syntax is where the Cypher dialects
genuinely diverge, so that stays in the engine-specific adapters and each one
reports what it actually managed to create.

Query strings are built once at connect time, not per call. At the latencies
being measured (sub-millisecond point lookups on some engines) f-string
formatting inside the timed loop would be a measurable fraction of the result.
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
        """Precompute every query string. Call at the end of connect()."""
        label, rel = self.label, self.rel

        self.q_noop = "RETURN 1 AS n"

        # k-hop neighbourhood, undirected pattern (no arrow) because a
        # co-authorship edge has no direction in reality even though it is stored
        # one way round. Cypher's variable-length expansion uses relationship
        # uniqueness, so a path cannot reuse an edge; the b.key <> $key guard
        # then removes the start node itself, which is the only way it could
        # reappear.
        self.q_khop = {
            k: (
                f"MATCH (a:{label} {{key: $key}})-[:{rel}*1..{k}]-(b:{label}) "
                f"WHERE b.key <> $key "
                f"RETURN count(DISTINCT b) AS n"
            )
            for k in self.workloads.hops
        }

        # Point lookup: one indexed equality, returning a scalar property. Not
        # `RETURN a`, which would make the engines ship a whole node and turn this
        # into a serialisation benchmark.
        self.q_point = f"MATCH (a:{label} {{key: $key}}) RETURN a.degree AS n"

        # Equality plus range. Deliberately two predicates so an engine with a
        # composite index can use it and an engine without one has to filter,
        # which is a difference worth exposing rather than hiding.
        self.q_filtered = (
            f"MATCH (a:{label}) "
            f"WHERE a.cohort = $cohort AND a.degree >= $minDegree "
            f"RETURN count(a) AS n"
        )

        self.q_agg_cohorts = f"MATCH (a:{label}) RETURN a.cohort AS cohort, count(*) AS n"
        self.q_agg_rels = f"MATCH ()-[r:{rel}]->() RETURN count(r) AS n"

        # CREATE, not MERGE. The dataset is already deduplicated, so MERGE would
        # add an index probe per row for a uniqueness guarantee we already have,
        # and it would penalise engines with slower index lookups on a phase
        # that is supposed to be measuring write throughput.
        self.q_insert_nodes = (
            f"UNWIND $rows AS row "
            f"CREATE (:{label} {{id: row.id, key: row.key, "
            f"cohort: row.cohort, degree: row.degree}})"
        )

        # Edges do need lookups, hence the index on id created before this phase.
        # Matching on id rather than key because id is the natural join column in
        # the source data and avoids building 400k strings client-side.
        self.q_insert_edges = (
            f"UNWIND $rows AS row "
            f"MATCH (a:{label} {{id: row.src}}), (b:{label} {{id: row.dst}}) "
            f"CREATE (a)-[:{rel}]->(b)"
        )

        # Mixed-workload write: attaches a new node to an existing one, so it
        # exercises an index lookup, a node create and a relationship create,
        # which is what an append to an agent's context graph actually looks like.
        self.q_write = (
            f"MATCH (a:{label} {{key: $key}}) "
            f"CREATE (b:{BENCH_LABEL} {{key: $bkey, tag: $tag}}) "
            f"CREATE (a)-[:{BENCH_REL}]->(b) "
            f"RETURN 1 AS n"
        )
        self.q_write_count = f"MATCH (b:{BENCH_LABEL} {{tag: $tag}}) RETURN count(b) AS n"
        self.q_write_delete = f"MATCH (b:{BENCH_LABEL} {{tag: $tag}}) DETACH DELETE b"

        # Deleting in capped batches. A single `MATCH (n) DETACH DELETE n` over
        # 198k nodes builds one transaction far larger than 256 MB and dies on
        # every engine here. The LIMIT loop is also portable, which the
        # engine-specific bulk delete calls are not.
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
        # A missing key means the start-node list and the loaded graph disagree,
        # which is a data bug, not a slow query. Fail loudly.
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
        # Count first, then delete: DETACH DELETE cannot report what it removed
        # in portable Cypher, and knowing the number is how we confirm the graph
        # was actually returned to its pre-mixed-workload state.
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

    # Wipe batch sizing. Started at 10,000 and Neo4j died on it:
    #
    #   Neo.TransientError.General.OutOfMemoryError: Java heap space
    #
    # then dropped the connection. Deleting 10,000 nodes means detaching every
    # relationship attached to them, and in this graph that is over 100k
    # relationships in one transaction against a 96 MB heap. 2,000 holds, and the
    # loop halves down to a floor if an engine still cannot take it.
    #
    # Worth being clear that this is not a measured phase, so a smaller batch
    # costs nothing but wall clock. It is only in here at all because a wipe that
    # kills the database ruins the run that follows it.
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
                # An OOM usually kills the connection too, so the session has to
                # go before the retry or every subsequent query fails on a
                # defunct socket.
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
    """Is this the engine running out of memory rather than a real bug?

    Matching on message text is unpleasant, but the alternative is importing each
    driver's exception hierarchy into shared code, and these engines report the
    same condition under at least four different exception types.
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
