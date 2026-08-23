"""FalkorDB. Cypher, but over RESP rather than Bolt, so it needs its own transport.

RESP is a lighter protocol than Bolt, so part of any latency advantage here is
protocol rather than engine, which is what the RETURN 1 baseline is for. The
engine itself evaluates queries as sparse matrix operations (GraphBLAS) instead of
chasing pointers, which is why it is in the comparison.
"""

import threading
from typing import Any

from falkordb import FalkorDB

from graphbench.adapters.cypher import BENCH_LABEL, CypherAdapter

# One graph key inside the Redis keyspace. Named rather than defaulted so wiping
# is a single DELETE of a known key.
GRAPH_KEY = "graphbench"


def _decode_module_version(packed: str) -> str:
    """Redis modules report a packed int, e.g. 42004 for 4.20.4.

    Decoded so a reader can match the results table against an actual release.
    """
    try:
        n = int(packed)
    except (TypeError, ValueError):
        return str(packed)
    return f"{n // 10000}.{(n // 100) % 100}.{n % 100}"


class FalkorDBAdapter(CypherAdapter):
    engine = "falkordb"
    load_method = "FalkorDB client over RESP, GRAPH.QUERY with UNWIND batches"

    def connect(self) -> None:
        conn = self.platform.connection
        self._client_args = {
            "host": conn.get("host", "localhost"),
            "port": int(conn.get("port", 6379)),
        }
        password = conn.get("password") or None
        if password:
            self._client_args["password"] = password

        self._local = threading.local()
        # Touch the connection now so a bad host fails before timing starts.
        self._graph().query("RETURN 1")
        self.build_queries()

    def _graph(self):
        """Thread-local: the redis client is pool-safe but Graph holds per-connection
        query state."""
        graph = getattr(self._local, "graph", None)
        if graph is None:
            db = FalkorDB(**self._client_args)
            graph = db.select_graph(GRAPH_KEY)
            self._local.graph = graph
            self._local.db = db
        return graph

    def run(self, query: str, params: dict | None = None) -> list[dict]:
        result = self._graph().query(query, params=params or None)
        header = [h[1] if isinstance(h, (list, tuple)) else h for h in (result.header or [])]
        names = [h.decode() if isinstance(h, bytes) else str(h) for h in header]
        return [dict(zip(names, row, strict=False)) for row in (result.result_set or [])]

    def close(self) -> None:
        db = getattr(self._local, "db", None)
        if db is not None:
            try:
                db.connection.close()
            except Exception:  # noqa: BLE001, S110
                pass

    def server_version(self) -> str:
        info = self._graph().client.connection.execute_command("INFO", "server")
        if isinstance(info, dict):
            redis_version = info.get("redis_version", "?")
        else:
            redis_version = "?"
        modules = self._graph().client.connection.execute_command("MODULE", "LIST")
        version = "?"
        for module in modules or []:
            # MODULE LIST returns a flat [name, <name>, ver, <ver>] per entry.
            entries = [
                x.decode() if isinstance(x, bytes) else x for x in module
            ] if isinstance(module, (list, tuple)) else []
            if "graph" in [str(e).lower() for e in entries]:
                version = str(entries[entries.index("ver") + 1]) if "ver" in entries else "?"
        return f"FalkorDB module {_decode_module_version(version)} on Redis {redis_version}"

    def wipe(self) -> None:
        # Drops the whole graph key in one call. Not an unfair advantage: wipe runs
        # before the timer starts and is never measured.
        try:
            self._graph().delete()
        except Exception:  # noqa: BLE001
            # Graph key does not exist yet on a first run.
            pass
        # Re-select so the next query recreates the key.
        self._local.graph = None
        self._graph()

    def create_indexes(self) -> list[str]:
        self.index_failures: list[str] = []
        label = self.label
        wanted = [
            ("key", f"CREATE INDEX FOR (a:{label}) ON (a.key)"),
            ("id (edge load path)", f"CREATE INDEX FOR (a:{label}) ON (a.id)"),
            # Multi-property index in one statement, closer to Neo4j's composite
            # than Memgraph's single-property-only.
            (
                "composite (cohort, degree)",
                f"CREATE INDEX FOR (a:{label}) ON (a.cohort, a.degree)",
            ),
            (
                "bench tag (mixed workload cleanup)",
                f"CREATE INDEX FOR (b:{BENCH_LABEL}) ON (b.tag)",
            ),
        ]
        created = []
        for description, stmt in wanted:
            try:
                self.run(stmt)
                created.append(f"{description}: {stmt}")
            except Exception as exc:  # noqa: BLE001
                if "already" in str(exc).lower():
                    created.append(f"{description}: already present")
                else:
                    self.index_failures.append(f"{description} failed: {exc}")

        # No unique constraint: FalkorDB's enforcement is a separate
        # GRAPH.CONSTRAINT command not available on every build. So it does slightly
        # less work than Neo4j on the node phase.
        created.append("NOTE: no unique constraint on key, index only")
        return created + self.index_failures

    def footprint(self) -> dict[str, Any]:
        out: dict[str, Any] = {"observable": True, "source": "INFO memory + GRAPH.MEMORY"}
        try:
            conn = self._graph().client.connection
            info = conn.execute_command("INFO", "memory")
            if isinstance(info, dict):
                out["redis_used_memory_bytes"] = info.get("used_memory")
                out["redis_used_memory_peak_bytes"] = info.get("used_memory_peak")
            usage = conn.execute_command("GRAPH.MEMORY", "USAGE", GRAPH_KEY)
            out["graph_memory"] = usage
        except Exception as exc:  # noqa: BLE001
            return {"observable": False, "reason": f"INFO/GRAPH.MEMORY failed: {exc}"}
        return out
