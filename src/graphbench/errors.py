"""One place to decide whether an exception means "the engine is out of resources".

There were two copies of this, one in the wipe retry loop and one in the read
workload's abandon check, and they had already drifted: the read side did not match
Memgraph's actual message, `Memory limit exceeded!`, so a Memgraph OOM mid-workload
would keep hammering for all 100 iterations and pollute the percentiles with retry
noise instead of abandoning and reporting.

Matching on message text is unpleasant. The alternative is importing four driver
exception hierarchies into shared code, and these engines report the same condition
under at least that many types:

    Neo4j     neo4j.exceptions.TransientError
              Neo.TransientError.General.OutOfMemoryError: Java heap space
    Neo4j     neo4j.exceptions.GqlError
              51N36: There is not enough memory to perform the current task
    Memgraph  neo4j.exceptions.TransientError
              Memgraph.TransientError.MemgraphError.MemgraphError
              Memory limit exceeded! Current use is 202.86MiB
    ArangoDB  arango.exceptions.AQLQueryExecuteError
              query would use more memory than allowed
    any       OSError / ServiceUnavailable
              Failed to read from defunct connection

All of these strings came from real failures during development, not from
documentation.
"""

# Kept as a module constant so a new engine's phrasing is a one-line addition.
RESOURCE_PHRASES = (
    "outofmemory",
    "out of memory",
    "not enough memory",
    "memory limit",
    "more memory than allowed",
    "heap space",
    "defunct",
)

# Connection loss is treated as a resource problem because in practice it is one:
# every time an engine here ran out of memory it dropped the socket as well.
CONNECTION_PHRASES = (
    "defunct",
    "connection refused",
    "connection reset",
    "failed to read",
    "broken pipe",
    "service unavailable",
)


def _text(exc: BaseException) -> str:
    return f"{type(exc).__name__} {exc}".lower()


def is_resource_error(exc: BaseException) -> bool:
    """Engine out of memory, or a connection it dropped on the way out."""
    text = _text(exc)
    return any(p in text for p in RESOURCE_PHRASES + CONNECTION_PHRASES)


def is_connection_error(exc: BaseException) -> bool:
    text = _text(exc)
    return any(p in text for p in CONNECTION_PHRASES)
