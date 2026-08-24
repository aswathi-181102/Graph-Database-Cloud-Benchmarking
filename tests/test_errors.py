"""The classifier that decides whether to abandon a workload.

Every string here is a real message seen from a real engine during development, not
one invented for the test. That matters: the reason this module exists is that a
hand-written needle list missed Memgraph's actual phrasing, so a Memgraph OOM was
being treated as a transient blip and retried 100 times.
"""

import pytest

from graphbench import errors

REAL_RESOURCE_MESSAGES = [
    # Neo4j, 96 MB heap, deleting 10,000 nodes in one transaction
    "TransientError: {code: Neo.TransientError.General.OutOfMemoryError} "
    "{message: Java heap space}",
    # Neo4j again, same cause, different code path
    "GqlError: 51N36: There is not enough memory to perform the current task.",
    # Memgraph, 200 MiB limit, edge load. The one the old list missed.
    "TransientError: {code: Memgraph.TransientError.MemgraphError.MemgraphError} "
    "{message: Memory limit exceeded! Current use is 202.86MiB, while the maximum "
    "allowed size for allocation is set to 200.00MiB.}",
    # Memgraph, the allocation-attempt variant
    "Memory limit exceeded! Attempting to allocate a chunk of 388.00KiB which would "
    "put the current use to 203.24MiB",
    # what the driver says after an engine dies mid-query
    "OSError: Failed to read from defunct connection IPv4Address(('localhost', 7687))",
]

NORMAL_MESSAGES = [
    "SyntaxError: Invalid input 'MATCH'",
    "KeyError: 'a999'",
    "ValueError: bad parameter",
    "ClientError: Constraint validation failed",
]


@pytest.mark.parametrize("message", REAL_RESOURCE_MESSAGES)
def test_real_engine_resource_failures_are_recognised(message):
    assert errors.is_resource_error(RuntimeError(message))


@pytest.mark.parametrize("message", NORMAL_MESSAGES)
def test_ordinary_errors_are_not_treated_as_resource_failures(message):
    assert not errors.is_resource_error(ValueError(message))


def test_memgraph_phrasing_specifically():
    # Regression guard for the bug that produced this module: "Memory limit
    # exceeded" contains none of "outofmemory", "out of memory", "heap space".
    exc = RuntimeError("Memory limit exceeded! Current use is 202.86MiB")
    assert errors.is_resource_error(exc)


def test_exception_type_name_is_also_matched():
    # Some drivers put the useful word in the class rather than the message.
    class OutOfMemoryError(Exception):
        pass

    assert errors.is_resource_error(OutOfMemoryError("no detail"))


def test_connection_errors_are_a_narrower_category():
    dead = OSError("Failed to read from defunct connection")
    oom = RuntimeError("Memory limit exceeded!")
    assert errors.is_connection_error(dead)
    assert not errors.is_connection_error(oom)
    # but both mean stop, because an OOM usually takes the socket with it
    assert errors.is_resource_error(dead)
    assert errors.is_resource_error(oom)


def test_matching_is_case_insensitive():
    assert errors.is_resource_error(RuntimeError("JAVA HEAP SPACE"))
    assert errors.is_resource_error(RuntimeError("memory LIMIT exceeded"))
