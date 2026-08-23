"""ArangoDB, the only non-Cypher engine in the set.

It is here because it is architecturally the odd one out, and that is the point:
ArangoDB is a document store that reconstructs adjacency through global indexes
rather than storing pointers between neighbours. If native adjacency actually
matters for multi-hop traversal, this is the engine where it should show.

Being non-Cypher means the queries are hand-written in AQL rather than shared,
which is the weakest link in the fairness argument, so it is worth being precise
about what "the same logical query" means here.

  Traversal semantics. Cypher's variable-length pattern uses relationship
  uniqueness (a path cannot reuse an edge, but may revisit a node). ArangoDB's
  traversal defaults to path uniqueness. Left alone, the two return genuinely
  different sets and the counts would not match. The fix is
  OPTIONS {uniqueVertices: 'global', order: 'bfs'}, which makes ArangoDB return
  exactly the set of vertices reachable within k hops, which is what the Cypher
  query's count(DISTINCT b) reduces to. That is semantic alignment, not tuning:
  without it the two engines answer different questions. The runner cross-checks
  the counts across platforms, so if this reasoning were wrong it would show up
  as a mismatch rather than as a fast wrong answer.

  Edge loading. Cypher engines have to look up both endpoints by an indexed
  property to create a relationship. ArangoDB addresses documents by primary key,
  so `_from` and `_to` can be built as strings client-side and no lookup happens
  at all. That is a real advantage for ArangoDB on the ingest phase and it is not
  normalised away, because it is not a trick, it is what the data model buys. It
  is called out next to the ingest numbers.

  Insert path. AQL `FOR doc IN @rows INSERT doc` is used rather than the
  `import_bulk` HTTP endpoint. import_bulk would almost certainly be faster, but
  batch INSERT is the direct analogue of Cypher's UNWIND ... CREATE: same batch
  size, same number of round trips, same server-side loop. Using each engine's
  fastest proprietary loader would answer a different and less comparable
  question. Noted as a caveat since it does understate ArangoDB's real ingest
  ceiling.
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
        """Thread-local client. python-arango wraps a requests Session, which is
        not safe to share across the 40 threads the mixed workload spins up."""
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

        # order: 'bfs' is required for uniqueVertices: 'global'. See the module
        # docstring for why global uniqueness is what makes this match Cypher.
        self.q_khop = {
            k: (
                f"FOR v IN 1..{k} ANY @start {EDGES} "
                f"OPTIONS {{uniqueVertices: 'global', order: 'bfs'}} "
                f"COLLECT WITH COUNT INTO n RETURN n"
            )
            for k in self.workloads.hops
        }

        # Filter on the `key` attribute, not on _key, even though _key holds the
        # node id and would be a free primary-index hit. Using the primary index
        # here would be a genuinely unfair advantage: every other engine is doing
        # a secondary index lookup on a string property, so ArangoDB does too.
        self.q_point = f"FOR a IN {NODES} FILTER a.key == @key LIMIT 1 RETURN a.degree"

        self.q_filtered = (
            f"FOR a IN {NODES} FILTER a.cohort == @cohort AND a.degree >= @minDegree "
            f"COLLECT WITH COUNT INTO n RETURN n"
        )
        self.q_agg_cohorts = f"FOR a IN {NODES} COLLECT c = a.cohort WITH COUNT INTO n RETURN n"

        # COLLECT WITH COUNT rather than LENGTH(coauthor). LENGTH on a collection
        # is an O(1) metadata read and would not be measuring the same thing as a
        # scan on the engines that have to do one.
        self.q_agg_rels = f"FOR e IN {EDGES} COLLECT WITH COUNT INTO n RETURN n"

        self.q_insert_nodes = (
            f"FOR row IN @rows INSERT {{_key: TO_STRING(row.id), id: row.id, key: row.key, "
            f"cohort: row.cohort, degree: row.degree}} INTO {NODES}"
        )
        self.q_insert_edges = (
            f"FOR row IN @rows INSERT {{_from: CONCAT('{NODES}/', TO_STRING(row.src)), "
            f"_to: CONCAT('{NODES}/', TO_STRING(row.dst))}} INTO {EDGES}"
        )

        # One query, two INSERTs, so the mixed workload costs ArangoDB the same
        # single round trip it costs the Cypher engines. Two separate statements
        # would have doubled its network cost and made the throughput comparison
        # meaningless. Validated against 3.12; if a server rejects multiple
        # modifications the adapter falls back and records that it did.
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
        # truncate, not a delete loop. Not measured (it runs before the timer),
        # and a 198k-document AQL REMOVE inside a 256 MB instance would be a
        # test of the transaction size limit rather than a cleanup.
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

        # No index on id is created, and that is deliberate rather than an
        # oversight: the edge loader addresses documents by _key, which the
        # primary index already covers. The Cypher engines need an explicit index
        # on id for the same job. Recorded so the difference is visible.
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
        # AQL traversals start from a document id, and our _key is the numeric
        # node id, so the id is reconstructed from the key rather than looked up.
        # That saves a round trip the Cypher engines do not make either, since
        # they bind the key straight into the pattern.
        # key[1:] not lstrip(prefix): lstrip takes a character set and would
        # happily eat more than the one-character prefix.
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
                # Server refuses two modifications in one query. Latch the
                # fallback so we do not pay for the failed attempt on every
                # subsequent write, and remember it for the results file.
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
        """ArangoDB exposes real stored-size figures, which most engines here do not."""
        try:
            db = self._db()
            out: dict[str, Any] = {"observable": True, "source": "collection figures()"}
            for name in (NODES, EDGES):
                figures = db.collection(name).figures()
                out[name] = {
                    "documents": figures.get("count"),
                    "documents_size_bytes": figures.get("documentsSize"),
                    "indexes": figures.get("figures", {}).get("indexes"),
                }
            if self._write_needs_fallback:
                out["write_path"] = "two AQL statements (server rejected multi-modification query)"
            return out
        except Exception as exc:  # noqa: BLE001
            return {"observable": False, "reason": f"figures() failed: {exc}"}
