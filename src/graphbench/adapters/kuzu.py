"""Kuzu: embedded, in-process, no server and no network.

Included as a reference floor rather than a competitor, and the results keep it in
its own track for that reason. Two things make it worth having:

It is architecturally the closest thing here to CognoDB's stated shape, one small
isolated store per agent, except taken to the extreme of no server process at all.

And because there is no round trip, the gap between Kuzu and the Bolt engines on
the same query is protocol and network cost rather than graph engine cost. That is
the same thing the RETURN 1 baseline measures, approached from the other end.

Ranking it against the server engines would be dishonest, so the report does not.

Resource capping is weaker than the container track and that is stated: memory is
held to the same 256 MB through buffer_pool_size, but CPU cannot be limited on an
in-process library, so max_num_threads=1 is the closest available analogue to half
a burstable core.

Kuzu is also the one engine here with a fixed schema, so the DDL creates typed node
and relationship tables rather than declaring indexes on a schemaless store.
"""

import shutil
from pathlib import Path
from typing import Any

import kuzu

from graphbench.adapters.base import Adapter

NODE_TABLE = "Author"
REL_TABLE = "Coauthor"
BENCH_TABLE = "Bench"
BENCH_REL = "BenchEdge"


class KuzuAdapter(Adapter):
    engine = "kuzu"
    dialect = "cypher"
    load_method = "embedded kuzu, in-process Cypher UNWIND batches"

    def connect(self) -> None:
        conn = self.platform.connection
        self._path = Path(conn.get("path") or "data/kuzu/bench.kz")
        if not self._path.is_absolute():
            from graphbench import paths as gb_paths

            self._path = gb_paths.ROOT / self._path
        self._buffer_pool_mb = int(conn.get("buffer_pool_mb", 256))
        self._threads = int(conn.get("threads", 1))

        self._open()
        self.build_queries()

    def _open(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._db = kuzu.Database(
            str(self._path),
            buffer_pool_size=self._buffer_pool_mb * 1024 * 1024,
            max_num_threads=self._threads,
        )
        self._conn = kuzu.Connection(self._db)

    def build_queries(self) -> None:
        self.q_noop = "RETURN 1 AS n"
        self.q_khop = {
            k: (
                f"MATCH (a:{NODE_TABLE} {{key: $key}})-[:{REL_TABLE}*1..{k}]-(b:{NODE_TABLE}) "
                f"WHERE b.key <> $key "
                f"RETURN count(DISTINCT b) AS n"
            )
            for k in self.workloads.hops
        }
        self.q_point = f"MATCH (a:{NODE_TABLE} {{key: $key}}) RETURN a.degree AS n"
        self.q_filtered = (
            f"MATCH (a:{NODE_TABLE}) "
            f"WHERE a.cohort = $cohort AND a.degree >= $minDegree "
            f"RETURN count(a) AS n"
        )
        self.q_agg_cohorts = f"MATCH (a:{NODE_TABLE}) RETURN a.cohort AS cohort, count(*) AS n"
        self.q_agg_rels = f"MATCH ()-[r:{REL_TABLE}]->() RETURN count(r) AS n"
        self.q_insert_nodes = (
            f"UNWIND $rows AS row "
            f"CREATE (:{NODE_TABLE} {{id: row.id, key: row.key, "
            f"cohort: row.cohort, degree: row.degree}})"
        )
        self.q_insert_edges = (
            f"UNWIND $rows AS row "
            f"MATCH (a:{NODE_TABLE} {{id: row.src}}), (b:{NODE_TABLE} {{id: row.dst}}) "
            f"CREATE (a)-[:{REL_TABLE}]->(b)"
        )
        self.q_write = (
            f"MATCH (a:{NODE_TABLE} {{key: $key}}) "
            f"CREATE (b:{BENCH_TABLE} {{key: $bkey, tag: $tag}}) "
            f"CREATE (a)-[:{BENCH_REL}]->(b) "
            f"RETURN 1 AS n"
        )
        self.q_write_count = f"MATCH (b:{BENCH_TABLE} {{tag: $tag}}) RETURN count(b) AS n"

    def run(self, query: str, params: dict | None = None) -> list[dict]:
        result = self._conn.execute(query, parameters=params or {})
        rows = []
        columns = result.get_column_names()
        while result.has_next():
            rows.append(dict(zip(columns, result.get_next(), strict=False)))
        return rows

    def close(self) -> None:
        # Drop the connection and database handles so the buffer pool is released
        # and the store file is not left locked for the next platform.
        self._conn = None
        self._db = None

    def server_version(self) -> str:
        return f"Kuzu {kuzu.__version__} (embedded, in-process)"

    def wipe(self) -> None:
        """Delete the store file and reopen.

        Kuzu has no DROP DATABASE, and dropping tables one at a time leaves the
        buffer pool warm, which is the same mistake that made Memgraph's reloads
        fail. Deleting the directory is the in-process equivalent of the container
        rebuild the local track does. Not measured either way.
        """
        self.close()
        if self._path.exists():
            shutil.rmtree(self._path) if self._path.is_dir() else self._path.unlink()
        wal = self._path.with_suffix(self._path.suffix + ".wal")
        if wal.exists():
            wal.unlink()
        self._open()

    def create_indexes(self) -> list[str]:
        """Create the schema.

        Kuzu is the only engine here with a declared schema, so this is CREATE
        TABLE rather than CREATE INDEX. PRIMARY KEY on the string key gives the
        same thing the other engines get from a unique constraint plus an index,
        which is why there is no separate index statement.
        """
        created = []
        statements = [
            (
                "node table with primary key on key",
                f"CREATE NODE TABLE {NODE_TABLE}(id INT64, key STRING, cohort INT64, "
                f"degree INT64, PRIMARY KEY(key))",
            ),
            (
                "relationship table",
                f"CREATE REL TABLE {REL_TABLE}(FROM {NODE_TABLE} TO {NODE_TABLE})",
            ),
            (
                "bench node table (mixed workload)",
                f"CREATE NODE TABLE {BENCH_TABLE}(key STRING, tag STRING, PRIMARY KEY(key))",
            ),
            (
                "bench rel table (mixed workload)",
                f"CREATE REL TABLE {BENCH_REL}(FROM {NODE_TABLE} TO {BENCH_TABLE})",
            ),
        ]
        for description, stmt in statements:
            try:
                self.run(stmt)
                created.append(f"{description}: {stmt}")
            except Exception as exc:  # noqa: BLE001
                if "already exists" in str(exc).lower():
                    created.append(f"{description}: already present")
                else:
                    created.append(f"{description} FAILED: {exc}")

        # No secondary index on id, so the edge loader's MATCH on id is a scan
        # rather than an index probe. Kuzu only indexes the primary key, and
        # making key the primary key was the right call for the point lookup, which
        # is a measured workload, over the edge load, which is one phase.
        created.append("NOTE: only the primary key is indexed, id lookups are scans")
        return created

    def load(self):
        """Schema first, then nodes, then edges.

        This is the one place the shared load order cannot be used: Kuzu cannot
        insert into a table that does not exist, so the schema has to precede the
        node phase rather than sit between the two data phases. The index phase is
        still timed separately, so the numbers stay comparable, but the ordering
        difference is real and recorded here rather than glossed over.
        """
        import time

        from graphbench.adapters.base import LoadPhase, LoadStats

        stats = LoadStats(method=self.load_method, batch_size=self.workloads.batch_size)
        self.wipe()

        t0 = time.perf_counter()
        self.created_indexes = self.create_indexes()
        stats.phases.append(
            LoadPhase("indexes", rows=0, batches=1, seconds=time.perf_counter() - t0)
        )
        stats.phases.append(
            self._timed_phase("nodes", self.graph.iter_nodes, self.insert_node_batch)
        )
        stats.phases.append(
            self._timed_phase("edges", self.graph.iter_edges, self.insert_edge_batch)
        )
        return stats

    def insert_node_batch(self, rows: list[dict]) -> None:
        self.run(self.q_insert_nodes, {"rows": rows})

    def insert_edge_batch(self, rows: list[dict]) -> None:
        self.run(self.q_insert_edges, {"rows": rows})

    def noop(self) -> int:
        return int(self.run(self.q_noop)[0]["n"])

    def k_hop(self, key: str, k: int) -> int:
        return int(self.run(self.q_khop[k], {"key": key})[0]["n"])

    def point_lookup(self, key: str) -> int:
        rows = self.run(self.q_point, {"key": key})
        if not rows:
            raise KeyError(f"{self.platform.id}: no node with key {key!r}")
        return int(rows[0]["n"])

    def filtered_lookup(self, cohort: int, min_degree: int) -> int:
        return int(self.run(self.q_filtered, {"cohort": cohort, "minDegree": min_degree})[0]["n"])

    def aggregate_cohorts(self) -> int:
        return sum(int(r["n"]) for r in self.run(self.q_agg_cohorts))

    def aggregate_rel_count(self) -> int:
        return int(self.run(self.q_agg_rels)[0]["n"])

    def insert_write(self, tag: str, seq: int, attach_to: str) -> int:
        rows = self.run(
            self.q_write, {"key": attach_to, "bkey": f"{tag}-{seq}", "tag": tag}
        )
        return int(rows[0]["n"]) if rows else 0

    def cleanup_writes(self, tag: str) -> int:
        rows = self.run(self.q_write_count, {"tag": tag})
        n = int(rows[0]["n"]) if rows else 0
        if n:
            # DETACH DELETE landed in later Kuzu versions than the pinned one, so
            # relationships go first and then the nodes.
            self.run(f"MATCH (:{NODE_TABLE})-[r:{BENCH_REL}]->(b:{BENCH_TABLE} {{tag: $tag}}) "
                     f"DELETE r", {"tag": tag})
            self.run(f"MATCH (b:{BENCH_TABLE} {{tag: $tag}}) DELETE b", {"tag": tag})
        return n

    def footprint(self) -> dict[str, Any]:
        """Store size on disk, which for an embedded engine is the honest figure.

        Process RSS is not reported because the process is the benchmark client, so
        its memory is mostly Python and the driver, not the graph.
        """
        info: dict[str, Any] = {
            "observable": True,
            "source": "store file size",
            "buffer_pool_mb": self._buffer_pool_mb,
            "max_num_threads": self._threads,
            "note": "in-process, so RSS is the client's and not comparable to a server's",
        }
        if self._path.exists():
            if self._path.is_dir():
                info["store_bytes"] = sum(
                    f.stat().st_size for f in self._path.rglob("*") if f.is_file()
                )
            else:
                info["store_bytes"] = self._path.stat().st_size
        return info
