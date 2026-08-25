"""The per-query ceiling, which used to be a comment rather than a mechanism."""

import threading
import time

import pytest

from graphbench.deadline import Deadline, QueryTimeout


def test_a_fast_call_returns_its_value():
    with Deadline(5) as d:
        assert d.run(lambda: 42) == 42


def test_a_hung_call_raises_rather_than_blocking_forever():
    """The actual failure: a query accepted and never answered, with no socket read
    timeout anywhere in the stack."""
    started = threading.Event()

    def hang():
        started.set()
        time.sleep(30)

    with Deadline(0.2) as d:
        began = time.perf_counter()
        with pytest.raises(QueryTimeout, match="per-query ceiling"):
            d.run(hang)
        assert time.perf_counter() - began < 5
    assert started.is_set()


def test_a_timeout_poisons_the_deadline():
    # The worker is stuck in a syscall and python cannot interrupt it, so the whole
    # object is discarded rather than pretending the next call is safe.
    with Deadline(0.1) as d:
        with pytest.raises(QueryTimeout):
            d.run(lambda: time.sleep(10))
        assert d.poisoned
        with pytest.raises(QueryTimeout, match="still blocked"):
            d.run(lambda: 1)


def test_exceptions_propagate_unchanged():
    with Deadline(5) as d:
        with pytest.raises(ValueError, match="engine said no"):
            d.run(lambda: (_ for _ in ()).throw(ValueError("engine said no")))


def test_the_worker_thread_is_reused_across_calls():
    """One thread, so thread-local sessions survive between iterations. A fresh
    thread per call would build a fresh session per call and measure connection
    setup instead of the query."""
    seen = set()
    with Deadline(5) as d:
        for _ in range(5):
            seen.add(d.run(threading.get_ident))
    assert len(seen) == 1


def test_the_worker_is_not_the_calling_thread():
    with Deadline(5) as d:
        assert d.run(threading.get_ident) != threading.get_ident()
