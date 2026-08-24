from graphbench.report import tables


def platform(pid="p1", track="local", display="P One"):
    return {
        "id": pid,
        "display": display,
        "engine": "memgraph",
        "track": track,
        "tier": {
            "name": "docker",
            "vcpu": 0.5,
            "ram_mb": 256,
            "disk_gb": 1,
            "burstable": False,
            "notes": "",
            "source": "docker-compose.yml",
        },
    }


def record(loaded_edges=198050, **extra):
    base = {
        "platform": platform(),
        "dataset": {"nodes": 18771, "edges": 198050},
        "server_version": "Memgraph 3.12.0",
        "status": "ok",
        "errors": [],
        "load": {
            "method": "driver batching",
            "nodes_per_second": 13172.0,
            "rels_per_second": 48712.0,
            "total_seconds": 5.6,
            "phases": [
                {"name": "nodes", "rows": 18771, "seconds": 1.4},
                {"name": "indexes", "rows": 0, "seconds": 0.06},
                {"name": "edges", "rows": loaded_edges, "seconds": 4.1},
            ],
        },
        "reads": [
            {
                "name": "baseline_noop",
                "latency": {"p50_ms": 0.37, "p95_ms": 0.81, "errors": 0},
                "abandoned": False,
            },
            {
                "name": "traversal_2hop",
                "latency": {"p50_ms": 1.05, "p95_ms": 2.77, "errors": 0},
                "abandoned": False,
            },
        ],
    }
    base.update(extra)
    return base


def test_partial_load_is_flagged_in_the_table():
    # An ingest rate over 5k of 198k edges is a different measurement, not just a
    # slower one, so the row has to say so.
    out = tables.ingest_table([record(loaded_edges=5000)])
    assert "23,771/216,821" in out
    assert "⚠" in out


def test_complete_load_is_not_flagged():
    out = tables.ingest_table([record()])
    assert "216,821/216,821" in out
    assert "⚠" not in out


def test_platform_that_never_loaded_still_gets_a_row():
    r = record()
    del r["load"]
    out = tables.ingest_table([r])
    assert "did not load" in out


def test_abandoned_workload_is_flagged_in_latency_table():
    r = record()
    r["reads"][1]["abandoned"] = True
    out = tables.latency_table([r], [2])
    assert "⚠" in out


def test_workload_with_errors_is_flagged_even_if_not_abandoned():
    r = record()
    r["reads"][1]["latency"]["errors"] = 3
    out = tables.latency_table([r], [2])
    assert "⚠" in out


def test_missing_workload_renders_a_dash_not_a_crash():
    out = tables.latency_table([record()], [1, 2, 3])
    assert tables.DASH in out


def test_engine_cost_subtracts_the_baseline():
    out = tables.engine_cost_table([record()], [2])
    # 1.05 - 0.37 = 0.68
    assert "0.68" in out


def test_engine_cost_never_goes_negative():
    # Noise can put a traversal below the no-op. A negative "engine cost" is
    # meaningless, so it clamps at zero rather than printing nonsense.
    r = record()
    r["reads"][1]["latency"]["p50_ms"] = 0.1
    out = tables.engine_cost_table([r], [2])
    assert "-" not in out.split("|")[-2]


def test_unpublished_tier_values_render_as_dash():
    r = record()
    r["platform"]["tier"]["vcpu"] = None
    r["platform"]["tier"]["ram_mb"] = None
    out = tables.tier_table([r])
    assert out.count(tables.DASH) >= 2


def test_mixed_table_says_so_when_not_run():
    assert "not run" in tables.mixed_table([record()])


def test_mixed_table_columns_follow_the_levels_present():
    r = record(
        mixed={
            "levels": [
                {"concurrency": 1, "qps": 100.0, "read_latency": {"p95_ms": 1.0, "errors": 0}},
                {"concurrency": 40, "qps": 250.0, "read_latency": {"p95_ms": 90.0, "errors": 2}},
            ]
        }
    )
    out = tables.mixed_table([r])
    assert "1 client (qps)" in out
    assert "40 clients (qps)" in out
    assert "250.0" in out


def test_footprint_says_not_observable_rather_than_guessing():
    out = tables.footprint_table([record(footprint={"observable": False, "reason": "x"})])
    assert "not observable" in out
