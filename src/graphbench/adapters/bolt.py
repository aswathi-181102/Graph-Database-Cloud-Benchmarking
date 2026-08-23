"""Bolt transport plus the three engines that speak it: CognoDB, Neo4j, Memgraph.

CognoDB advertises Bolt 5.0-5.4 and official-driver compatibility, so all three
share one transport, which makes the CognoDB/Neo4j comparison as close to
apples-to-apples as this study gets: same driver, same query text, same client.
"""

import threading
from typing import Any

from neo4j import GraphDatabase

from graphbench.adapters.cypher import BENCH_LABEL, CypherAdapter


class BoltAdapter(CypherAdapter):
    """Shared Bolt plumbing. Engine differences live in the subclasses."""

    def connect(self) -> None:
        conn = self.platform.connection
        user = conn.get("user") or ""
        password = conn.get("password") or ""

        # Memgraph ships with auth off and rejects a credential tuple; CognoDB and
        # Aura require one.
        auth = (user, password) if user else None

        # Sized past the largest concurrency level, so the mixed workload never
        # measures queueing in my client instead of work in the database.
        pool = max(self.workloads.concurrency) + 4

        self._driver = GraphDatabase.driver(
            conn["uri"],
            auth=auth,
            max_connection_pool_size=pool,
            connection_acquisition_timeout=60.0,
            connection_timeout=30.0,
            keep_alive=True,
        )
        # Only Neo4j has multiple databases; passing database= elsewhere is a
        # protocol error.
        self._database = conn.get("database") or None

        self._local = threading.local()
        self._sessions: list[Any] = []
        self._sessions_lock = threading.Lock()

        # Fail before any timing starts: a connection error mid-load would corrupt
        # the ingest number.
        self._driver.verify_connectivity()
        self.build_queries()

    def _session(self):
        # Thread-local: the driver is thread-safe, a Session is not, and the mixed
        # workload runs 40 clients.
        session = getattr(self._local, "session", None)
        if session is None:
            session = (
                self._driver.session(database=self._database)
                if self._database
                else self._driver.session()
            )
            self._local.session = session
            with self._sessions_lock:
                self._sessions.append(session)
        return session

    def run(self, query: str, params: dict | None = None) -> list[dict]:
        # Autocommit: consuming the result is what commits it, so the list
        # comprehension is load-bearing.
        result = self._session().run(query, params or {})
        return [dict(record) for record in result]

    def reset_connection(self) -> None:
        """Drop this thread's session so the next call reconnects.

        Neo4j drops the connection when the heap blows, and the cached session then
        fails every retry with "Failed to read from defunct connection".
        """
        session = getattr(self._local, "session", None)
        if session is not None:
            try:
                session.close()
            except Exception:  # noqa: BLE001, S110
                pass
            with self._sessions_lock:
                if session in self._sessions:
                    self._sessions.remove(session)
        self._local.session = None

    def close(self) -> None:
        with self._sessions_lock:
            for session in self._sessions:
                try:
                    session.close()
                except Exception:  # noqa: BLE001, S110
                    pass  # dead sessions are expected after an engine OOMs
            self._sessions.clear()
        self._driver.close()

    def _try_variants(self, description: str, variants: list[str]) -> str | None:
        """Run the first DDL variant that works and report which one it was.

        Index/constraint syntax is not portable and the engines disagree in ways
        that are not always documented, so try and record rather than assume.
        """
        errors = []
        for stmt in variants:
            try:
                self.run(stmt)
                return f"{description}: {stmt}"
            except Exception as exc:  # noqa: BLE001
                msg = str(exc).lower()
                if "already exist" in msg or "equivalent" in msg:
                    return f"{description}: already present"
                errors.append(f"{type(exc).__name__}: {exc}")
        self.index_failures.append(
            f"{description} -> none of {len(variants)} forms worked: {errors}"
        )
        return None


class Neo4jAdapter(BoltAdapter):
    engine = "neo4j"
    load_method = "official Neo4j Bolt driver, UNWIND batches"

    def server_version(self) -> str:
        rows = self.run(
            "CALL dbms.components() YIELD name, versions, edition "
            "RETURN name + ' ' + versions[0] + ' (' + edition + ')' AS v"
        )
        return str(rows[0]["v"])

    def create_indexes(self) -> list[str]:
        self.index_failures: list[str] = []
        label = self.label
        created = [
            self._try_variants(
                "unique key",
                [
                    f"CREATE CONSTRAINT {label.lower()}_key IF NOT EXISTS "
                    f"FOR (a:{label}) REQUIRE a.key IS UNIQUE"
                ],
            ),
            self._try_variants(
                "id (edge load path)",
                [f"CREATE INDEX {label.lower()}_id IF NOT EXISTS FOR (a:{label}) ON (a.id)"],
            ),
            # Neo4j has composite indexes, Memgraph does not. Shows up in the
            # filtered lookup.
            self._try_variants(
                "composite (cohort, degree)",
                [
                    f"CREATE INDEX {label.lower()}_cohort_degree IF NOT EXISTS "
                    f"FOR (a:{label}) ON (a.cohort, a.degree)"
                ],
            ),
            self._try_variants(
                "bench tag (mixed workload cleanup)",
                [f"CREATE INDEX bench_tag IF NOT EXISTS FOR (b:{BENCH_LABEL}) ON (b.tag)"],
            ),
        ]

        # Neo4j builds indexes asynchronously; without this the cost leaks into
        # the edge phase. Every other engine here builds synchronously.
        self.run("CALL db.awaitIndexes(300)")
        return [c for c in created if c] + self.index_failures

    def footprint(self) -> dict[str, Any]:
        out: dict[str, Any] = {"observable": True, "source": "dbms.queryJmx"}
        try:
            rows = self.run(
                "CALL dbms.queryJmx('java.lang:type=Memory') YIELD attributes "
                "RETURN attributes.HeapMemoryUsage.value.properties.used AS heap_used, "
                "attributes.NonHeapMemoryUsage.value.properties.used AS nonheap_used"
            )
            out["jvm_heap_used_bytes"] = rows[0]["heap_used"]
            out["jvm_nonheap_used_bytes"] = rows[0]["nonheap_used"]
        except Exception as exc:  # noqa: BLE001
            # Aura restricts dbms.* procedures, so this legitimately fails there.
            out = {"observable": False, "reason": f"dbms.queryJmx unavailable: {exc}"}
        return out


class MemgraphAdapter(BoltAdapter):
    engine = "memgraph"
    load_method = "official Neo4j Bolt driver, UNWIND batches"

    def server_version(self) -> str:
        rows = self.run("SHOW VERSION")
        return f"Memgraph {list(rows[0].values())[0]}"

    def create_indexes(self) -> list[str]:
        self.index_failures: list[str] = []
        label = self.label
        created = [
            self._try_variants(
                "unique key",
                [f"CREATE CONSTRAINT ON (a:{label}) ASSERT a.key IS UNIQUE"],
            ),
            self._try_variants("key", [f"CREATE INDEX ON :{label}(key)"]),
            self._try_variants("id (edge load path)", [f"CREATE INDEX ON :{label}(id)"]),
            # No composite label-property index, so degree gets filtered in the
            # engine. A real capability gap, reported in the results table.
            self._try_variants(
                "cohort (no composite support)", [f"CREATE INDEX ON :{label}(cohort)"]
            ),
            self._try_variants(
                "bench tag (mixed workload cleanup)",
                [f"CREATE INDEX ON :{BENCH_LABEL}(tag)"],
            ),
        ]
        return [c for c in created if c] + self.index_failures

    def footprint(self) -> dict[str, Any]:
        try:
            rows = self.run("SHOW STORAGE INFO")
        except Exception as exc:  # noqa: BLE001
            return {"observable": False, "reason": f"SHOW STORAGE INFO failed: {exc}"}

        # Older builds return (name, value) pairs, newer ones a single wide row.
        info: dict[str, Any] = {"observable": True, "source": "SHOW STORAGE INFO"}
        if rows and len(rows[0]) == 2:
            keys = list(rows[0])
            for row in rows:
                info[str(row[keys[0]])] = row[keys[1]]
        else:
            for row in rows:
                info.update({str(k): v for k, v in row.items()})
        return info


class CognoDBAdapter(BoltAdapter):
    """CognoDB Cloud.

    Inherits everything except DDL. CognoDB documents Cypher and driver
    compatibility but not which index syntax it implements, so this tries the
    Neo4j 5 form, then Neo4j 4, then Memgraph, and records which one was accepted.
    Version and footprint are probed the same way and report "not observable"
    rather than a guess.
    """

    engine = "cognodb"
    load_method = "official Neo4j Bolt driver, UNWIND batches"

    def server_version(self) -> str:
        for query in (
            "CALL dbms.components() YIELD name, versions RETURN name + ' ' + versions[0] AS v",
            "SHOW VERSION",
        ):
            try:
                rows = self.run(query)
                if rows:
                    return str(list(rows[0].values())[0])
            except Exception:  # noqa: BLE001, S110
                continue
        return "unknown (no version procedure answered)"

    def create_indexes(self) -> list[str]:
        self.index_failures: list[str] = []
        label = self.label
        created = [
            self._try_variants(
                "unique key",
                [
                    f"CREATE CONSTRAINT {label.lower()}_key IF NOT EXISTS "
                    f"FOR (a:{label}) REQUIRE a.key IS UNIQUE",
                    f"CREATE CONSTRAINT ON (a:{label}) ASSERT a.key IS UNIQUE",
                ],
            ),
            self._try_variants(
                "id (edge load path)",
                [
                    f"CREATE INDEX {label.lower()}_id IF NOT EXISTS FOR (a:{label}) ON (a.id)",
                    f"CREATE INDEX ON :{label}(id)",
                ],
            ),
            self._try_variants(
                "cohort / composite",
                [
                    f"CREATE INDEX {label.lower()}_cohort_degree IF NOT EXISTS "
                    f"FOR (a:{label}) ON (a.cohort, a.degree)",
                    f"CREATE INDEX {label.lower()}_cohort IF NOT EXISTS "
                    f"FOR (a:{label}) ON (a.cohort)",
                    f"CREATE INDEX ON :{label}(cohort)",
                ],
            ),
            self._try_variants(
                "bench tag (mixed workload cleanup)",
                [
                    f"CREATE INDEX bench_tag IF NOT EXISTS FOR (b:{BENCH_LABEL}) ON (b.tag)",
                    f"CREATE INDEX ON :{BENCH_LABEL}(tag)",
                ],
            ),
        ]

        # Harmless if unsupported, needed if CognoDB builds indexes like Neo4j.
        try:
            self.run("CALL db.awaitIndexes(300)")
        except Exception:  # noqa: BLE001, S110
            pass
        return [c for c in created if c] + self.index_failures

    def footprint(self) -> dict[str, Any]:
        for query, source in (
            ("SHOW STORAGE INFO", "SHOW STORAGE INFO"),
            ("CALL dbms.listConfig('server.memory') YIELD name, value RETURN name, value",
             "dbms.listConfig"),
        ):
            try:
                rows = self.run(query)
            except Exception:  # noqa: BLE001
                continue
            if rows:
                info: dict[str, Any] = {"observable": True, "source": source}
                for row in rows:
                    values = list(row.values())
                    if len(values) == 2:
                        info[str(values[0])] = values[1]
                    else:
                        info.update({str(k): v for k, v in row.items()})
                return info
        return {
            "observable": False,
            "reason": "no storage or config introspection procedure answered",
        }
