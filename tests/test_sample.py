from graphbench.datasets import sample


def ring(n: int) -> list[tuple[int, int]]:
    """Cycle graph, every node degree 2. Predictable induced edge counts."""
    return [(i, (i + 1) % n) for i in range(n)]


def two_components() -> list[tuple[int, int]]:
    # 0-1-2 triangle and 10-11-12 triangle, nothing between them
    return [(0, 1), (1, 2), (0, 2), (10, 11), (11, 12), (10, 12)]


def test_adjacency_is_symmetric_for_directed_input():
    adj = sample.build_adjacency([(1, 2), (2, 3)])
    assert adj[2] == {1, 3}
    assert adj[1] == {2}


def test_snowball_reaches_the_edge_target():
    adj = sample.build_adjacency(ring(200))
    nodes = sample.snowball(adj, target_edges=50, seed=1)
    edges = sample.induced_edges(adj, nodes)
    assert len(edges) >= 50


def test_snowball_is_deterministic_for_a_given_seed():
    adj = sample.build_adjacency(ring(200))
    a = sample.snowball(adj, target_edges=40, seed=99)
    b = sample.snowball(adj, target_edges=40, seed=99)
    assert a == b


def test_snowball_crosses_disconnected_components():
    # Asking for 5 edges cannot be satisfied by one 3-edge triangle, so the
    # sampler has to hop. This is the case that silently returned short before.
    adj = sample.build_adjacency(two_components())
    nodes = sample.snowball(adj, target_edges=5, seed=3)
    assert len(sample.induced_edges(adj, nodes)) >= 5


def test_snowball_stops_when_the_graph_runs_out():
    adj = sample.build_adjacency(two_components())
    nodes = sample.snowball(adj, target_edges=10_000, seed=3)
    # 6 edges total, so it should hand back everything rather than loop forever
    assert nodes == {0, 1, 2, 10, 11, 12}


def test_snowball_with_no_target_is_empty():
    adj = sample.build_adjacency(ring(10))
    assert sample.snowball(adj, target_edges=0, seed=1) == set()


def test_induced_edges_are_deduped_and_sorted():
    adj = sample.build_adjacency(two_components())
    edges = sample.induced_edges(adj, {0, 1, 2})
    assert edges == [(0, 1), (0, 2), (1, 2)]
