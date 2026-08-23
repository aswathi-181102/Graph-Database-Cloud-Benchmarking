"""Snowball sampling, used when the source graph is too big for a free tier.

Sampling strategy is the highest-risk decision in this whole harness, because a
bad sample changes what the benchmark is even measuring. Three options were on
the table:

  Random edge sampling. Easiest to write. Rejected: it shreds local structure.
  Keep 1% of edges and you get a sparse graph of mostly degree-1 nodes, so 2-hop
  and 3-hop collapse towards the same tiny result set and the traversal metric
  stops discriminating between engines.

  Random node sampling with induced edges. Better, but on a heavy-tailed graph
  the induced subgraph is still mostly disconnected fragments unless the sample
  fraction is large.

  Breadth-first snowball, which is what this does. Keeps one dense connected
  region intact, so k-hop expansion still grows the way it does in the real
  graph. The known bias is that it over-represents high-degree nodes (they get
  reached early and often), which is a real caveat and is stated in the README
  rather than glossed over.

Only used for soc-Pokec. The default dataset is small enough to load whole,
which is itself the best mitigation: no sampling, no sampling bias.
"""

import random
from collections.abc import Iterable


def build_adjacency(edges: Iterable[tuple[int, int]]) -> dict[int, set[int]]:
    # Undirected adjacency even when the source is directed, because the
    # traversal workload uses an undirected pattern and the sampler should grow
    # along the same edges the benchmark will later walk.
    adj: dict[int, set[int]] = {}
    for u, v in edges:
        adj.setdefault(u, set()).add(v)
        adj.setdefault(v, set()).add(u)
    return adj


def snowball(adj: dict[int, set[int]], target_edges: int, seed: int) -> set[int]:
    """Grow a node set breadth-first until the induced subgraph has enough edges.

    Deterministic for a given (graph, target, seed): neighbours are visited in
    sorted order, and the restart node after exhausting a component is drawn
    from a seeded RNG. Determinism is not a nicety here, it is the difference
    between "platform B was slower" and "platform B got a different graph".
    """
    if target_edges <= 0:
        return set()

    rng = random.Random(seed)
    all_nodes = sorted(adj)
    if not all_nodes:
        return set()

    selected: set[int] = set()
    edges_in = 0
    # Plain list plus an index instead of collections.deque: we never pop from
    # the front, we only walk forward, and keeping the full list makes the
    # traversal order inspectable when a sample looks wrong.
    queue: list[int] = []
    qi = 0

    def enqueue_new_component() -> bool:
        remaining = [n for n in all_nodes if n not in selected]
        if not remaining:
            return False
        queue.append(rng.choice(remaining))
        return True

    enqueue_new_component()

    while edges_in < target_edges:
        if qi >= len(queue):
            # Component exhausted before hitting the target. Hop to another one
            # rather than returning short: a sample that silently comes back at
            # half the requested size would quietly change the dataset size
            # without changing the manifest's target_edges.
            if not enqueue_new_component():
                break
        node = queue[qi]
        qi += 1
        if node in selected:
            continue

        # Counting incrementally is what keeps this O(E). The edges gained by
        # admitting `node` are exactly its edges back into the already-selected
        # set, so there is never a need to recompute the induced subgraph.
        edges_in += len(adj[node] & selected)
        selected.add(node)
        queue.extend(sorted(n for n in adj[node] if n not in selected))

    return selected


def induced_edges(adj: dict[int, set[int]], nodes: set[int]) -> list[tuple[int, int]]:
    """Edges of the subgraph spanned by `nodes`, one per pair, sorted."""
    out = []
    for u in sorted(nodes):
        for v in sorted(adj[u] & nodes):
            # u < v keeps one edge per pair. Without it every edge appears twice
            # and the relationship count doubles.
            if u < v:
                out.append((u, v))
    return out
