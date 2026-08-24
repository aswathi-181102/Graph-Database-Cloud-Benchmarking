"""Tests that the query text is what the methodology says it is.

The transports need live engines and are exercised by real runs. The query
construction does not, and it is the part where a silent regression would be worst:
changing `*1..k` to `*k..k`, or dropping the undirected pattern, would still return
plausible numbers while measuring a different thing entirely. These lock the
definitions down.
"""

import pytest

from graphbench.adapters import arango as arango_mod
from graphbench.adapters.cypher import BENCH_LABEL, CypherAdapter


class StubCypher(CypherAdapter):
    """CypherAdapter with the transport replaced by a scripted dict."""

    engine = "stub"
    load_method = "stub"

    def __init__(self, platform, graph, workloads, rows=None):
        super().__init__(platform, graph, workloads)
        self.executed: list[tuple[str, dict]] = []
        self.rows = rows if rows is not None else [{"n": 7}]
        self.build_queries()

    def connect(self):
        pass

    def close(self):
        pass

    def server_version(self):
        return "stub"

    def create_indexes(self):
        return []

    def run(self, query, params=None):
        self.executed.append((query, params or {}))
        return self.rows


@pytest.fixture
def stub(platform, graph, workloads):
    return StubCypher(platform, graph, workloads)


# ------------------------------------------------------------ query text ------


def test_khop_uses_a_variable_length_neighbourhood_not_exact_depth(stub, workloads):
    for k in workloads.hops:
        q = stub.q_khop[k]
        assert f"*1..{k}" in q, "must be the 1..k neighbourhood, see DECISIONS.md#4"
        if k > 1:
            # at k=1 the two forms are the same query, so there is nothing to assert
            assert f"*{k}..{k}" not in q


def test_khop_pattern_is_undirected(stub):
    for q in stub.q_khop.values():
        assert "]-(" in q
        assert "]->(" not in q, "a co-authorship edge has no direction"


def test_khop_excludes_the_start_node(stub):
    for q in stub.q_khop.values():
        assert "b.key <> $key" in q


def test_khop_counts_distinct_nodes(stub):
    for q in stub.q_khop.values():
        assert "count(DISTINCT b)" in q


def test_point_lookup_returns_a_scalar_not_the_node(stub):
    # RETURN a would make this a serialisation benchmark
    assert "RETURN a.degree" in stub.q_point
    assert "RETURN a " not in stub.q_point


def test_filtered_lookup_has_both_predicates(stub):
    assert "a.cohort = $cohort" in stub.q_filtered
    assert "a.degree >= $minDegree" in stub.q_filtered


def test_node_insert_uses_create_not_merge(stub):
    # the data is already deduped, so MERGE would only add an index probe per row
    assert "CREATE (" in stub.q_insert_nodes
    assert "MERGE" not in stub.q_insert_nodes


def test_edge_insert_matches_on_id(stub):
    assert "{id: row.src}" in stub.q_insert_edges
    assert "{id: row.dst}" in stub.q_insert_edges


def test_labels_come_from_the_dataset_not_a_constant(platform, graph, workloads):
    stub = StubCypher(platform, graph, workloads)
    assert f":{graph.node_label} " in stub.q_point or f":{graph.node_label} {{" in stub.q_point
    assert graph.rel_type in stub.q_insert_edges


def test_bench_writes_are_isolated_under_their_own_label(stub):
    assert BENCH_LABEL in stub.q_write
    assert BENCH_LABEL in stub.q_write_delete
    # cleanup targets the tag, so a crashed run can be cleaned up by tag
    assert "$tag" in stub.q_write_delete


def test_wipe_is_batched(stub):
    assert "LIMIT $limit" in stub.q_wipe_batch


# ----------------------------------------------------------- op behaviour -----


def test_ops_bind_their_parameters(stub, workloads):
    stub.k_hop("a3", workloads.hops[0])
    query, params = stub.executed[-1]
    assert params == {"key": "a3"}

    stub.filtered_lookup(7, 10)
    _, params = stub.executed[-1]
    assert params == {"cohort": 7, "minDegree": 10}


def test_missing_key_is_a_data_bug_not_a_slow_query(platform, graph, workloads):
    stub = StubCypher(platform, graph, workloads, rows=[])
    with pytest.raises(KeyError, match="a999"):
        stub.point_lookup("a999")


def test_group_by_sums_across_the_returned_groups(platform, graph, workloads):
    stub = StubCypher(platform, graph, workloads, rows=[{"n": 3}, {"n": 4}, {"n": 5}])
    assert stub.aggregate_cohorts() == 12


def test_cleanup_counts_before_deleting(platform, graph, workloads):
    stub = StubCypher(platform, graph, workloads, rows=[{"n": 4}])
    assert stub.cleanup_writes("tag-1") == 4
    assert len(stub.executed) == 2  # count, then delete


def test_cleanup_skips_the_delete_when_there_is_nothing_to_remove(
    platform, graph, workloads
):
    stub = StubCypher(platform, graph, workloads, rows=[{"n": 0}])
    assert stub.cleanup_writes("tag-1") == 0
    assert len(stub.executed) == 1


def test_wipe_stops_when_the_batch_comes_back_empty(platform, graph, workloads):
    class Draining(StubCypher):
        def __init__(self, *a, **k):
            self.remaining = 3
            super().__init__(*a, **k)

        def run(self, query, params=None):
            self.executed.append((query, params or {}))
            if "DETACH DELETE" in query and self.remaining:
                self.remaining -= 1
                return [{"n": 2000}]
            return [{"n": 0}]

    stub = Draining(platform, graph, workloads)
    stub.wipe()
    assert stub.remaining == 0


def test_wipe_halves_its_batch_on_a_resource_error(platform, graph, workloads):
    """Neo4j died on a 10,000-node delete batch. The retry has to shrink, and it has
    to reset the connection first because the OOM takes the socket with it."""

    class Fragile(StubCypher):
        def __init__(self, *a, **k):
            self.failures = 2
            self.resets = 0
            super().__init__(*a, **k)

        def run(self, query, params=None):
            if "DETACH DELETE" in query and self.failures:
                self.failures -= 1
                raise RuntimeError("Memory limit exceeded! Current use is 202.86MiB")
            return [{"n": 0}]

        def reset_connection(self):
            self.resets += 1

    stub = Fragile(platform, graph, workloads)
    stub.wipe()
    assert stub.resets == 2
    assert stub.wipe_batch_used == stub.WIPE_BATCH // 4


def test_wipe_gives_up_at_the_floor(platform, graph, workloads):
    class Hopeless(StubCypher):
        def run(self, query, params=None):
            if "DETACH DELETE" in query:
                raise RuntimeError("Memory limit exceeded!")
            return [{"n": 0}]

        def reset_connection(self):
            pass

    stub = Hopeless(platform, graph, workloads)
    with pytest.raises(RuntimeError, match="Memory limit"):
        stub.wipe(batch=stub.WIPE_BATCH_FLOOR)


def test_a_real_bug_still_propagates(platform, graph, workloads):
    class Broken(StubCypher):
        def run(self, query, params=None):
            if "DETACH DELETE" in query:
                raise SyntaxError("Invalid input 'DETACH'")
            return [{"n": 0}]

    with pytest.raises(SyntaxError):
        Broken(platform, graph, workloads).wipe()


# ---------------------------------------------------------------- arango ------


class StubArango(arango_mod.ArangoAdapter):
    def __init__(self, platform, graph, workloads, rows=None):
        super(arango_mod.ArangoAdapter, self).__init__(platform, graph, workloads)
        self.executed = []
        self.rows = rows if rows is not None else [7]
        self.workloads = workloads
        self._write_needs_fallback = False
        self._build_queries()

    def run(self, query, bind=None):
        self.executed.append((query, bind or {}))
        return self.rows


@pytest.fixture
def stub_arango(platform, graph, workloads):
    return StubArango(platform, graph, workloads)


def test_aql_traversal_uses_global_vertex_uniqueness(stub_arango, workloads):
    """Without this, AQL uses path uniqueness and answers a different question than
    Cypher's relationship uniqueness. It is semantic alignment, not tuning."""
    for k in workloads.hops:
        q = stub_arango.q_khop[k]
        assert "uniqueVertices: 'global'" in q
        assert "order: 'bfs'" in q, "global uniqueness requires bfs ordering"
        assert f"1..{k}" in q
        assert "ANY" in q, "must be undirected, matching the Cypher pattern"


def test_aql_point_lookup_uses_the_secondary_index_not_the_primary_key(stub_arango):
    # _key would be a free primary-index hit; every other engine does a secondary
    # index lookup on a string, so ArangoDB does too
    assert "a.key == @key" in stub_arango.q_point
    assert "a._key" not in stub_arango.q_point


def test_aql_relationship_count_scans_rather_than_reading_metadata(stub_arango):
    assert "COLLECT WITH COUNT" in stub_arango.q_agg_rels
    assert "LENGTH(" not in stub_arango.q_agg_rels


def test_aql_edge_insert_builds_document_ids_client_side(stub_arango):
    assert "CONCAT(" in stub_arango.q_insert_edges
    assert "_from" in stub_arango.q_insert_edges
    assert "_to" in stub_arango.q_insert_edges


def test_aql_write_is_a_single_round_trip(stub_arango):
    # two INSERTs in one query, so the mixed workload costs the same round trip the
    # Cypher engines pay
    assert stub_arango.q_write.count("INSERT") == 2


def test_arango_start_id_is_rebuilt_from_the_key(stub_arango, workloads):
    stub_arango.k_hop("a1250", workloads.hops[0])
    _, bind = stub_arango.executed[-1]
    assert bind["start"] == f"{arango_mod.NODES}/1250"


def test_arango_falls_back_when_multi_modification_is_refused(platform, graph, workloads):
    class Refusing(StubArango):
        def run(self, query, bind=None):
            self.executed.append((query, bind or {}))
            if query is self.q_write:
                raise RuntimeError("multiple data-modification operations not allowed")
            return ["authors/1"]

    stub = Refusing(platform, graph, workloads)
    assert stub.insert_write("t", 1, "a1") == 1
    assert stub._write_needs_fallback is True

    # and it does not retry the failing form on the next write
    before = len(stub.executed)
    stub.insert_write("t", 2, "a1")
    assert all(q is not stub.q_write for q, _ in stub.executed[before:])


def test_arango_does_not_swallow_an_unrelated_write_error(platform, graph, workloads):
    class Broken(StubArango):
        def run(self, query, bind=None):
            raise RuntimeError("unique constraint violated")

    with pytest.raises(RuntimeError, match="unique constraint"):
        Broken(platform, graph, workloads).insert_write("t", 1, "a1")
