"""Orchestration: run every configured platform, write results to disk.

One platform at a time, never in parallel. Two engines under load on the same
8-core host would contend for CPU and page cache, and the numbers would describe
the host rather than the engines.

Each platform gets its own JSON file, so a crash on platform four does not lose
platforms one to three.
"""

import json
import time
import traceback
from datetime import UTC, datetime
from typing import Any

from graphbench import adapters, dockerctl, environment, paths, verify
from graphbench.config import Platform, Workloads
from graphbench.datasets import PreparedGraph
from graphbench.workloads import mixed, reads


def _say(message: str) -> None:
    # flush because a benchmark run takes minutes and python buffers stdout when it
    # is redirected, so `graphbench run > log` would show nothing until the end.
    print(message, flush=True)


def new_run_id() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def _fmt_ms(value: float | None) -> str:
    return "     -" if value is None else f"{value:6.2f}"


def _print_load(stats) -> None:
    nps = stats.nodes_per_second
    rps = stats.rels_per_second
    rates = []
    if nps:
        rates.append(f"{nps:,.0f} nodes/s")
    if rps:
        rates.append(f"{rps:,.0f} rels/s")
    suffix = f" ({', '.join(rates)})" if rates else ""
    _say(f"    load {stats.total_seconds:.1f}s{suffix}")


def _print_read(result) -> None:
    state = "ABANDONED" if result.abandoned else "ok"
    if not len(result.latency):
        _say(f"    {result.name:28} {state:9} no samples, {len(result.latency.errors)} errors")
        return
    _say(
        f"    {result.name:28} {state:9} "
        f"p50 {_fmt_ms(result.latency.p50)}ms  p95 {_fmt_ms(result.latency.p95)}ms  "
        f"n={len(result.latency)}"
    )


def run_platform(
    platform: Platform,
    graph: PreparedGraph,
    workloads: Workloads,
    run_id: str,
    skip_mixed: bool = False,
    restart: bool = True,
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "platform": {
            "id": platform.id,
            "display": platform.display,
            "engine": platform.engine,
            "track": platform.track,
            "tier": {
                "name": platform.tier.name,
                "vcpu": platform.tier.vcpu,
                "ram_mb": platform.tier.ram_mb,
                "disk_gb": platform.tier.disk_gb,
                "burstable": platform.tier.burstable,
                "notes": platform.tier.notes,
                "source": platform.tier.source,
            },
        },
        "dataset": {
            "name": graph.manifest["dataset"],
            "nodes": graph.node_count,
            "edges": graph.edge_count,
            "raw_sha256": graph.manifest["raw_sha256"],
        },
        "status": "failed",
        "errors": [],
        "started_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    began = time.perf_counter()

    # Virgin store per platform, local track only. A restart is not enough: see
    # dockerctl for the two ways this went wrong before landing on destroy-and-
    # rebuild.
    if restart and platform.container and platform.service:
        try:
            record["reset"] = dockerctl.recreate(platform.service, platform.container)
            _say(
                f"    recreated {platform.container} "
                f"({record['reset']['seconds_to_healthy']}s to healthy, "
                f"volumes wiped: {len(record['reset']['volumes_removed'])})"
            )
        except dockerctl.DockerUnavailable as exc:
            record["reset"] = {"reset": False, "reason": str(exc)}
            record["errors"].append(f"reset skipped: {exc}")
    else:
        record["reset"] = {
            "reset": False,
            "reason": "not a local container platform"
            if not platform.container
            else "disabled with --no-reset",
        }

    adapter = adapters.build(platform, graph, workloads)

    try:
        adapter.connect()
        record["server_version"] = adapter.server_version()
        record["dialect"] = adapter.dialect
        record["load_method"] = adapter.load_method
        _say(f"    {record['server_version']}")

        load_stats = adapter.load()
        record["load"] = load_stats.to_dict()
        record["indexes"] = getattr(adapter, "created_indexes", [])
        if getattr(adapter, "wipe_batch_used", None) is not None:
            record["wipe_batch_used"] = adapter.wipe_batch_used
        record["errors"].extend(load_stats.errors[:5])
        _print_load(load_stats)

        read_results = reads.all_read_workloads(adapter, workloads)
        record["reads"] = [r.to_dict() for r in read_results]
        for r in read_results:
            _print_read(r)

        if skip_mixed:
            record["mixed"] = {"skipped": True, "reason": "--skip-mixed"}
        else:
            outcome = mixed.run_mixed(adapter, workloads, tag=run_id)
            record["mixed"] = {
                "read_ratio": workloads.read_ratio,
                "read_composition": f"{mixed.POINT_SHARE:.0%} point lookup, rest 2-hop",
                "levels": [level.to_dict() for level in outcome.results],
                "writes_cleaned": outcome.writes_cleaned,
                "errors": outcome.errors,
            }
            for level in outcome.results:
                _say(
                    f"    mixed c={level.concurrency:<3} {level.qps:8.1f} qps  "
                    f"reads={level.reads:<7} writes={level.writes:<6} "
                    f"read p95 {_fmt_ms(level.read_latency.p95)}ms"
                )
            record["errors"].extend(outcome.errors[:5])

        record["footprint"] = adapter.footprint()
        if platform.container:
            record["container"] = environment.container_stats(
                platform.container, platform.data_dir
            )

        # "partial" rather than "ok" whenever anything was abandoned or errored, so
        # the report can mark a row instead of presenting incomplete data as clean.
        abandoned = any(r.get("abandoned") for r in record.get("reads", []))
        record["status"] = "partial" if (abandoned or record["errors"]) else "ok"

    except Exception as exc:  # noqa: BLE001
        record["errors"].append(f"{type(exc).__name__}: {exc}")
        record["traceback"] = traceback.format_exc(limit=6)
        _say(f"    FAILED: {type(exc).__name__}: {exc}")
    finally:
        try:
            adapter.close()
        except Exception:  # noqa: BLE001, S110
            pass

    record["finished_at"] = datetime.now(UTC).isoformat(timespec="seconds")
    record["duration_s"] = round(time.perf_counter() - began, 1)
    return record


def run_all(
    platforms: list[Platform],
    graph: PreparedGraph,
    workloads: Workloads,
    skip_mixed: bool = False,
    run_id: str | None = None,
    restart: bool = True,
) -> dict[str, Any]:
    run_id = run_id or new_run_id()
    out_dir = paths.RESULTS_DIR / run_id
    out_dir.mkdir(parents=True, exist_ok=True)

    usable = [p for p in platforms if p.usable]
    skipped = [
        {"id": p.id, "reason": p.skip_reason} for p in platforms if not p.usable
    ]

    summary: dict[str, Any] = {
        "run_id": run_id,
        "started_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "environment": environment.describe(),
        "dataset": graph.manifest,
        "workloads": {
            "batch_size": workloads.batch_size,
            "iterations": workloads.iterations,
            "warmup": workloads.warmup,
            "hops": list(workloads.hops),
            "timeout_s": workloads.timeout_s,
            "concurrency": list(workloads.concurrency),
            "duration_s": workloads.duration_s,
            "read_ratio": workloads.read_ratio,
            "cohort": workloads.cohort,
            "min_degree": workloads.min_degree,
            "mixed_skipped": skip_mixed,
            "restart_local_containers": restart,
        },
        "skipped": skipped,
        "platforms": [],
    }

    _say(f"run {run_id}: {len(usable)} platforms, {len(skipped)} skipped")
    records = []
    for i, platform in enumerate(usable, 1):
        _say(f"\n[{i}/{len(usable)}] {platform.display} ({platform.track})")
        record = run_platform(
            platform, graph, workloads, run_id, skip_mixed=skip_mixed, restart=restart
        )
        records.append(record)
        (out_dir / f"{platform.id}.json").write_text(json.dumps(record, indent=2, default=str))
        summary["platforms"].append(
            {
                "id": platform.id,
                "status": record["status"],
                "duration_s": record["duration_s"],
                "errors": len(record["errors"]),
            }
        )

    # Cross-platform agreement check. Runs last because it needs every platform's
    # answers, and it is the thing that decides whether the timings mean anything.
    summary["verification"] = verify.compare(records)
    summary["finished_at"] = datetime.now(UTC).isoformat(timespec="seconds")
    (out_dir / "run.json").write_text(json.dumps(summary, indent=2, default=str))

    _say(f"\nwrote {out_dir.relative_to(paths.ROOT)}/")
    _print_verification(summary["verification"])
    return summary


def _print_verification(report: dict[str, Any]) -> None:
    for failure in report.get("expectation_failures", [])[:5]:
        _say(
            f"verification: {failure['platform']} {failure['workload']} "
            f"expected {failure['expected']}, got {failure['got']}"
        )
    for failure in report.get("monotonic_failures", [])[:5]:
        _say(f"verification: non-monotonic hops on {failure['platform']} at {failure['key']}")

    if not report.get("compared"):
        # Agreement needs two platforms to have answered the same question.
        _say("verification: only one platform answered, nothing to cross-check")
        return
    if report.get("agree"):
        _say(f"verification: all platforms agree on {report['compared']} checked values")
        return
    _say(f"verification: {len(report.get('mismatches', []))} DISAGREEMENTS")
    for m in report.get("mismatches", [])[:10]:
        _say(f"  {m['workload']} / {m['key']}: {m['values']}")
