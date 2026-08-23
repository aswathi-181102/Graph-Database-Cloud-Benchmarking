"""Adapter factory.

Imports are lazy and per engine on purpose. Kuzu and python-arango are only
needed if you actually benchmark those platforms, and someone reproducing just
the CognoDB vs Neo4j comparison should not be blocked by an unrelated wheel
failing to build.
"""

from graphbench.adapters.base import Adapter, LoadPhase, LoadStats
from graphbench.config import Platform, Workloads
from graphbench.datasets import PreparedGraph

__all__ = ["Adapter", "LoadPhase", "LoadStats", "build", "supported_engines"]

# engine name in platforms.yaml -> (module, class)
_ENGINES = {
    "cognodb": ("graphbench.adapters.bolt", "CognoDBAdapter"),
    "neo4j": ("graphbench.adapters.bolt", "Neo4jAdapter"),
    "memgraph": ("graphbench.adapters.bolt", "MemgraphAdapter"),
    "falkordb": ("graphbench.adapters.falkor", "FalkorDBAdapter"),
    "arangodb": ("graphbench.adapters.arango", "ArangoAdapter"),
    "kuzu": ("graphbench.adapters.kuzu", "KuzuAdapter"),
}


def supported_engines() -> list[str]:
    return sorted(_ENGINES)


def build(platform: Platform, graph: PreparedGraph, workloads: Workloads) -> Adapter:
    try:
        module_name, class_name = _ENGINES[platform.engine]
    except KeyError:
        raise KeyError(
            f"no adapter for engine {platform.engine!r}, have: {supported_engines()}"
        ) from None

    module = __import__(module_name, fromlist=[class_name])
    return getattr(module, class_name)(platform, graph, workloads)
