"""Snowball sampling, for when the source graph is too big for a free tier.

Why snowball and not random edge/node sampling: docs/DECISIONS.md#3.
"""

import random
from collections.abc import Iterable


def build_adjacency(edges: Iterable[tuple[int, int]]) -> dict[int, set[int]]:
    # Undirected even for a directed source, so the sampler grows along the same
    # edges the traversal workload will later walk.
    adj: dict[int, set[int]] = {}
    for u, v in edges:
        adj.setdefault(u, set()).add(v)
        adj.setdefault(v, set()).add(u)
    return adj


def snowball(adj: dict[int, set[int]], target_edges: int, seed: int) -> set[int]:
    """Grow a node set breadth-first until the induced subgraph has enough edges.

    Deterministic for a given (graph, target, seed): neighbours are visited in
    sorted order and component restarts come from a seeded RNG.
    """
    if target_edges <= 0:
        return set()

    rng = random.Random(seed)
    all_nodes = sorted(adj)
    if not all_nodes:
        return set()

    selected: set[int] = set()
    edges_in = 0
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
            # Component exhausted. Hop to another rather than silently returning a
            # smaller sample than the manifest claims.
            if not enqueue_new_component():
                break
        node = queue[qi]
        qi += 1
        if node in selected:
            continue

        # Edges gained by admitting `node` are exactly its edges back into the
        # selected set. Counting this way keeps the whole thing O(E).
        edges_in += len(adj[node] & selected)
        selected.add(node)
        queue.extend(sorted(n for n in adj[node] if n not in selected))

    return selected


def induced_edges(adj: dict[int, set[int]], nodes: set[int]) -> list[tuple[int, int]]:
    """Edges of the subgraph spanned by `nodes`, one per pair, sorted."""
    out = []
    for u in sorted(nodes):
        for v in sorted(adj[u] & nodes):
            if u < v:  # one edge per pair
                out.append((u, v))
    return out
