"""ArangoDB, the only non-Cypher engine in the set.

It is here because it is the architectural odd one out: a document store that
rebuilds adjacency from global indexes rather than storing pointers between
neighbours. If native adjacency matters for multi-hop traversal, this is where it
should show.

Non-Cypher means the queries are hand written, which is the weakest link in the
fairness argument, so the three places it differs are documented at the point of
difference below and in docs/DECISIONS.md sections 4 and 5.
"""

import threading
from typing import Any

from arango import ArangoClient

from graphbench.adapters.base import Adapter

NODES = "authors"
EDGES = "coauthor"
BENCH_NODES = "bench"
BENCH_EDGES = "bench_edge"


class ArangoAdapter(Adapter):
    engine = "arangodb"
    dialect = "aql"
    load_method = "python-arango, AQL batch INSERT (not import_bulk, see module docstring)"

    def connect(self) -> None:
        conn = self.platform.connection
        self._url = conn["url"]
        self._dbname = conn.get("database") or "_system"
        self._user = conn.get("user") or "root"
        self._password = conn.get("password") or ""
        self._local = threading.local()

        db = self._db()
        self._ensure_collections(db)
        self._build_queries()

    def _db(self):
        """Thread-local: python-arango wraps a requests Session, not safe to share
        across 40 threads."""
        db = getattr(self._local, "db", None)
        if db is None:
            client = ArangoClient(hosts=self._url, request_timeout=self.workloads.timeout_s + 30)
            db = client.db(self._dbname, username=self._user, password=self._password)
            self._local.client = client
            self._local.db = db
        return db

    def _ensure_collections(self, db) -> None:
        for name, is_edge in (
            (NODES, False),
            (EDGES, True),
            (BENCH_NODES, False),
            (BENCH_EDGES, True),
        ):
            if not db.has_collection(name):
                db.create_collection(name, edge=is_edge)

    def _build_queries(self) -> None:
        self.q_noop = "RETURN 1"

        # uniqueVertices: 'global' (which requires order: 'bfs') is what makes this
        # return the same set Cypher's count(DISTINCT b) does. Semantic alignment,
        # not tuning: without it the two engines answer different questions.
        self.q_khop = {
            k: (
                f"FOR v IN 1..{k} ANY @start {EDGES} "
                f"OPTIONS {{uniqueVertices: 'global', order: 'bfs'}} "
                f"COLLECT WITH COUNT INTO n RETURN n"
            )
            for k in self.workloads.hops
        }

        # Filters `key`, not _key. _key would be a free primary-index hit, and
        # every other engine is doing a secondary index lookup on a string.
        self.q_point = f"FOR a IN {NODES} FILTER a.key == @key LIMIT 1 RETURN a.degree"

        self.q_filtered = (
            f"FOR a IN {NODES} FILTER a.cohort == @cohort AND a.degree >= @minDegree "
            f"COLLECT WITH COUNT INTO n RETURN n"
        )
        self.q_agg_cohorts = f"FOR a IN {NODES} COLLECT c = a.cohort WITH COUNT INTO n RETURN n"

        # Not LENGTH(coauthor): that is an O(1) metadata read, not a scan.
        self.q_agg_rels = f"FOR e IN {EDGES} COLLECT WITH COUNT INTO n RETURN n"

        self.q_insert_nodes = (
            f"FOR row IN @rows INSERT {{_key: TO_STRING(row.id), id: row.id, key: row.key, "
            f"cohort: row.cohort, degree: row.degree}} INTO {NODES}"
        )
        self.q_insert_edges = (
            f"FOR row IN @rows INSERT {{_from: CONCAT('{NODES}/', TO_STRING(row.src)), "
            f"_to: CONCAT('{NODES}/', TO_STRING(row.dst))}} INTO {EDGES}"
        )

        # One query, two INSERTs, so this costs the same single round trip the
        # Cypher engines pay. Two statements would double its network cost and wreck
        # the throughput comparison. Falls back and records it if a server refuses.
        self.q_write = (
            f"LET target = FIRST(FOR a IN {NODES} FILTER a.key == @key LIMIT 1 RETURN a._id) "
            f"LET node = FIRST(INSERT {{_key: @bkey, tag: @tag}} INTO {BENCH_NODES} RETURN NEW) "
            f"INSERT {{_from: target, _to: node._id, tag: @tag}} INTO {BENCH_EDGES} "
            f"RETURN 1"
        )
        self.q_write_fallback_node = (
            f"INSERT {{_key: @bkey, tag: @tag}} INTO {BENCH_NODES} RETURN NEW._id"
        )
        self.q_write_fallback_edge = (
            f"LET target = FIRST(FOR a IN {NODES} FILTER a.key == @key LIMIT 1 RETURN a._id) "
            f"INSERT {{_from: target, _to: @nodeId, tag: @tag}} INTO {BENCH_EDGES} RETURN 1"
        )
        self._write_needs_fallback = False

        self.q_cleanup_edges = (
            f"FOR e IN {BENCH_EDGES} FILTER e.tag == @tag REMOVE e IN {BENCH_EDGES} "
            f"COLLECT WITH COUNT INTO n RETURN n"
        )
        self.q_cleanup_nodes = (
            f"FOR b IN {BENCH_NODES} FILTER b.tag == @tag REMOVE b IN {BENCH_NODES} "
            f"COLLECT WITH COUNT INTO n RETURN n"
        )
        self.q_count_writes = (
            f"FOR b IN {BENCH_NODES} FILTER b.tag == @tag COLLECT WITH COUNT INTO n RETURN n"
        )

    def run(self, query: str, bind: dict | None = None) -> list:
        cursor = self._db().aql.execute(query, bind_vars=bind or {})
        return list(cursor)

    def close(self) -> None:
        client = getattr(self._local, "client", None)
        if client is not None:
            client.close()

    def server_version(self) -> str:
        db = self._db()
        return f"ArangoDB {db.version()}"

    def wipe(self) -> None:
        # truncate, not an AQL REMOVE loop: 198k documents in one transaction inside
        # 256 MB tests the transaction size limit, not the cleanup. Never measured.
        db = self._db()
        for name in (EDGES, NODES, BENCH_EDGES, BENCH_NODES):
            if db.has_collection(name):
                db.collection(name).truncate()

    def create_indexes(self) -> list[str]:
        db = self._db()
        created: list[str] = []
        failures: list[str] = []

        wanted = [
            (NODES, {"fields": ["key"], "unique": True}, "unique key (persistent)"),
            (NODES, {"fields": ["cohort", "degree"], "unique": False},
             "composite (cohort, degree) (persistent)"),
            (BENCH_NODES, {"fields": ["tag"], "unique": False}, "bench tag (persistent)"),
            (BENCH_EDGES, {"fields": ["tag"], "unique": False}, "bench edge tag (persistent)"),
        ]
        for collection, spec, description in wanted:
            try:
                db.collection(collection).add_index({"type": "persistent", **spec})
                created.append(f"{description} on {collection}")
            except Exception as exc:  # noqa: BLE001
                failures.append(f"{description} on {collection} failed: {exc}")

        # No id index on purpose: the edge loader addresses documents by _key, which
        # the primary index covers. The Cypher engines need one for the same job.
        created.append("NOTE: no secondary index on id, edge load uses _key via the primary index")
        return created + failures

    def insert_node_batch(self, rows: list[dict]) -> None:
        self.run(self.q_insert_nodes, {"rows": rows})

    def insert_edge_batch(self, rows: list[dict]) -> None:
        self.run(self.q_insert_edges, {"rows": rows})

    # --------------------------------------------------------------- reads ---
    def noop(self) -> int:
        return int(self.run(self.q_noop)[0])

    def k_hop(self, key: str, k: int) -> int:
        # Traversals start from a document id, so it is rebuilt from the key rather
        # than looked up. No round trip either way, same as Cypher binding the key
        # into the pattern. key[1:] not lstrip(): lstrip takes a character set.
        start = f"{NODES}/{key[1:]}"
        rows = self.run(self.q_khop[k], {"start": start})
        return int(rows[0]) if rows else 0

    def point_lookup(self, key: str) -> int:
        rows = self.run(self.q_point, {"key": key})
        if not rows:
            raise KeyError(f"{self.platform.id}: no document with key {key!r}")
        return int(rows[0])

    def filtered_lookup(self, cohort: int, min_degree: int) -> int:
        rows = self.run(self.q_filtered, {"cohort": cohort, "minDegree": min_degree})
        return int(rows[0]) if rows else 0

    def aggregate_cohorts(self) -> int:
        return sum(int(n) for n in self.run(self.q_agg_cohorts))

    def aggregate_rel_count(self) -> int:
        rows = self.run(self.q_agg_rels)
        return int(rows[0]) if rows else 0

    # -------------------------------------------------------------- writes ---
    def insert_write(self, tag: str, seq: int, attach_to: str) -> int:
        params = {"key": attach_to, "bkey": f"{tag}-{seq}", "tag": tag}
        if not self._write_needs_fallback:
            try:
                rows = self.run(self.q_write, params)
                return int(rows[0]) if rows else 0
            except Exception as exc:  # noqa: BLE001
                if "multiple" not in str(exc).lower() and "modification" not in str(exc).lower():
                    raise
                # Latch it, so we do not pay for the failed attempt on every write.
                self._write_needs_fallback = True

        node_id = self.run(self.q_write_fallback_node, params)[0]
        self.run(
            self.q_write_fallback_edge,
            {"key": attach_to, "nodeId": node_id, "tag": tag},
        )
        return 1

    def cleanup_writes(self, tag: str) -> int:
        rows = self.run(self.q_count_writes, {"tag": tag})
        n = int(rows[0]) if rows else 0
        if n:
            self.run(self.q_cleanup_edges, {"tag": tag})
            self.run(self.q_cleanup_nodes, {"tag": tag})
        return n

    def footprint(self) -> dict[str, Any]:
        """One of the few engines here that reports real stored-size figures."""
        try:
            db = self._db()
            # statistics(), not figures(): python-arango 8.x renamed it, and the
            # first run of this reported "not observable" purely because of that.
            out: dict[str, Any] = {"observable": True, "source": "collection statistics()"}
            for name in (NODES, EDGES):
                stats = db.collection(name).statistics()
                figures = stats.get("figures", stats)
                out[name] = {
                    "documents": stats.get("count"),
                    "documents_size_bytes": figures.get("documentsSize"),
                    "indexes": figures.get("indexes"),
                }
            if self._write_needs_fallback:
                out["write_path"] = "two AQL statements (server rejected multi-modification query)"
            return out
        except Exception as exc:  # noqa: BLE001
            return {"observable": False, "reason": f"statistics() failed: {exc}"}
