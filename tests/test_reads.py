from dataclasses import replace

from graphbench.workloads import reads


def run(name, call, workloads, **kwargs):
    return reads.run_read_workload(name, call, workloads, **kwargs)


def test_measures_exactly_the_requested_iterations(workloads):
    calls = []
    result = run("x", lambda i: (calls.append(i) or 1, "value"), workloads)
    assert len(result.latency) == workloads.iterations
    # warmup runs too, but is not measured
    assert len(calls) == workloads.iterations + workloads.warmup


def test_first_warmup_call_is_kept_separately(workloads):
    result = run("x", lambda i: (1, "value"), workloads)
    assert result.first_call_ms is not None
    # and it is not one of the measured samples
    assert len(result.latency) == workloads.iterations


def test_no_warmup_means_no_first_call_figure(workloads):
    result = run("x", lambda i: (1, "value"), replace(workloads, warmup=0))
    assert result.first_call_ms is None


def test_returned_values_are_captured_for_cross_checking(workloads):
    result = run("x", lambda i: (i * 2, f"k{i % 3}"), workloads)
    assert set(result.checks) == {"k0", "k1", "k2"}


def test_first_value_per_key_wins_so_checks_are_stable(workloads):
    # setdefault, not assignment: otherwise the recorded value depends on iteration
    # count and two platforms with different iteration counts could not be compared.
    seq = iter(range(1000))
    run("x", lambda i: (next(seq), "same"), workloads)
    result = run("y", lambda i: (i, "same"), workloads)
    assert result.checks["same"] == workloads.warmup


def test_soft_failures_are_counted_and_the_loop_continues(workloads):
    def call(i):
        if i % 10 == 0:
            raise ValueError("transient")
        return 1, "value"

    result = run("x", call, workloads)
    assert not result.abandoned
    assert result.latency.errors
    # everything that did not raise still produced a sample
    assert len(result.latency) == workloads.iterations - workloads.iterations // 10


def test_out_of_memory_abandons_the_workload(workloads):
    def call(i):
        if i > 5:
            raise RuntimeError("Memory limit exceeded! Current use is 202.86MiB")
        return 1, "value"

    result = run("x", call, workloads)
    assert result.abandoned
    assert len(result.latency) < workloads.iterations


def test_a_dead_connection_abandons_during_warmup(workloads):
    def call(i):
        raise RuntimeError("Failed to read from defunct connection")

    result = run("x", call, workloads)
    assert result.abandoned
    assert len(result.latency) == 0


def test_a_hung_call_is_cut_off_at_the_ceiling(workloads):
    """The ceiling used to be checked after the call returned, which enforces nothing
    when the call never returns. One stalled CognoDB request cost a 17 minute hang."""
    import time

    calls = {"n": 0}

    def call(i):
        calls["n"] += 1
        if calls["n"] == 4:
            time.sleep(30)
        return 1, "value"

    began = time.perf_counter()
    result = run("x", call, replace(workloads, timeout_s=0.2, warmup=1))
    elapsed = time.perf_counter() - began

    assert elapsed < 5, "did not return promptly, so the ceiling is not enforced"
    assert result.abandoned
    assert result.latency.timeouts == 1
    # the samples collected before the hang are kept
    assert len(result.latency) == 2
    assert "per-query ceiling" in result.latency.errors[0]


def test_expected_value_mismatch_is_recorded(workloads):
    result = run("x", lambda i: (41, "value"), workloads, expected=42)
    out = result.to_dict()
    assert out["expected"] == 42
    assert out["matches_expected"] is False


def test_expected_value_match_is_recorded(workloads):
    result = run("x", lambda i: (42, "value"), workloads, expected=42)
    assert result.to_dict()["matches_expected"] is True


def test_observed_value_is_none_when_many_keys_were_touched(workloads):
    result = run("x", lambda i: (i, f"k{i}"), workloads)
    assert result.observed_value is None


def test_fatal_classifier():
    assert reads._is_fatal(RuntimeError("Java heap space"))
    assert reads._is_fatal(RuntimeError("OutOfMemoryError"))
    assert reads._is_fatal(RuntimeError("not enough memory"))
    assert reads._is_fatal(RuntimeError("defunct connection"))
    assert not reads._is_fatal(ValueError("bad parameter"))
    assert not reads._is_fatal(TimeoutError("slow query"))


def test_all_read_workloads_covers_every_required_metric(adapter, workloads):
    adapter.load()
    results = reads.all_read_workloads(adapter, workloads)
    names = [r.name for r in results]

    assert "baseline_noop" in names
    assert "point_lookup" in names
    assert "filtered_lookup" in names
    assert "aggregation_groupby_cohort" in names
    assert "aggregation_rel_count" in names
    for hop in workloads.hops:
        assert f"traversal_{hop}hop" in names


def test_baseline_can_be_switched_off(adapter, workloads):
    adapter.load()
    results = reads.all_read_workloads(adapter, replace(workloads, baseline_query=False))
    assert "baseline_noop" not in [r.name for r in results]


def test_aggregations_carry_their_known_correct_answers(adapter, workloads):
    adapter.load()
    results = {r.name: r for r in reads.all_read_workloads(adapter, workloads)}
    assert results["aggregation_groupby_cohort"].expected == adapter.graph.node_count
    assert results["aggregation_rel_count"].expected == adapter.graph.edge_count
    assert results["aggregation_groupby_cohort"].to_dict()["matches_expected"] is True
    assert results["aggregation_rel_count"].to_dict()["matches_expected"] is True


def test_start_keys_rotate_rather_than_repeating(adapter, workloads):
    adapter.load()
    results = {r.name: r for r in reads.all_read_workloads(adapter, workloads)}
    # 5 start keys in the fixture, and a 100-iteration run should touch all of them
    assert len(results["point_lookup"].checks) == len(adapter.graph.start_keys)


def test_traversal_counts_are_monotonic_in_depth(adapter, workloads):
    adapter.load()
    results = {r.name: r for r in reads.all_read_workloads(adapter, workloads)}
    one = results["traversal_1hop"].checks
    two = results["traversal_2hop"].checks
    for key in set(one) & set(two):
        assert one[key] <= two[key]


class Flaky:
    """Adapter stand-in that drops the connection on cue."""

    def __init__(self, drop_at, error):
        self.drop_at = set(drop_at)
        self.error = error
        self.resets = 0
        self.seen = 0

    def reset_connection(self):
        self.resets += 1

    def call(self, i):
        self.seen += 1
        if self.seen in self.drop_at:
            raise self.error
        return 1, "value"


def test_a_lost_connection_is_retried_not_fatal(workloads):
    """A cloud engine 240ms away loses a packet occasionally. Abandoning 100
    iterations over one dropped socket published a missing row for CognoDB's 3-hop
    that read as 'cannot run this query'."""
    flaky = Flaky(
        drop_at=[5, 40],
        error=RuntimeError("Failed to read from defunct connection IPv4Address(...)"),
    )
    result = reads.run_read_workload("x", flaky.call, workloads, adapter=flaky)

    assert not result.abandoned
    assert result.reconnects == 2
    assert flaky.resets == 2
    assert len(result.latency) == workloads.iterations


def test_reconnects_are_reported(workloads):
    flaky = Flaky(drop_at=[5], error=RuntimeError("defunct connection"))
    result = reads.run_read_workload("x", flaky.call, workloads, adapter=flaky)
    assert result.to_dict()["reconnects"] == 1


def test_an_out_of_memory_error_is_not_retried(workloads):
    """A reconnect fixes a lost packet and does nothing for an exhausted heap, so
    retrying would just produce a second identical failure."""
    flaky = Flaky(drop_at=[5], error=RuntimeError("Memory limit exceeded! 202.86MiB"))
    result = reads.run_read_workload("x", flaky.call, workloads, adapter=flaky)

    assert result.abandoned
    assert result.reconnects == 0
    assert flaky.resets == 0


def test_a_genuinely_dead_connection_abandons_after_exhausting_its_retries(workloads):
    """Every attempt fails, so it gives up after the backoff schedule is spent rather
    than flapping MAX_RECONNECTS times against a dead endpoint."""
    flaky = Flaky(
        drop_at=range(1, 500), error=RuntimeError("Failed to read from defunct connection")
    )
    result = reads.run_read_workload("x", flaky.call, workloads, adapter=flaky)

    assert result.abandoned
    assert result.reconnects == len(reads.RETRY_BACKOFF_S) - 1
    assert len(result.latency) == 0


def test_a_short_cascade_of_failures_is_survived(workloads):
    """The real shape from CognoDB: one long query kills the connection and the next
    few fail fast before it recovers. Retrying once lands inside the cascade."""
    flaky = Flaky(
        drop_at=[5, 6], error=RuntimeError("Failed to read from defunct connection")
    )
    result = reads.run_read_workload("x", flaky.call, workloads, adapter=flaky)

    assert not result.abandoned
    assert result.reconnects == 2
    assert len(result.latency) == workloads.iterations


def test_intermittent_drops_are_capped_across_the_workload(workloads):
    """Every other call fails. Each one is individually recoverable, so the run only
    stops once the total budget is spent."""
    flaky = Flaky(
        drop_at=range(1, 500, 2), error=RuntimeError("Failed to read from defunct connection")
    )
    result = reads.run_read_workload("x", flaky.call, workloads, adapter=flaky)

    assert result.reconnects == reads.MAX_RECONNECTS
    # it kept collecting samples in between, rather than throwing the run away
    assert len(result.latency) > 0


def test_without_an_adapter_there_is_nothing_to_reconnect(workloads):
    flaky = Flaky(drop_at=[5], error=RuntimeError("defunct connection"))
    result = reads.run_read_workload("x", flaky.call, workloads)
    assert result.abandoned
    assert result.reconnects == 0
