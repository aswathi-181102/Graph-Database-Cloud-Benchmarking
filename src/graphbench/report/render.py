"""Build docs/RESULTS.md and the charts from a run directory.

Generated, not hand-written, so the tables cannot drift from the JSON they came
from. The README links to it rather than embedding it, since the README should
still be readable before anyone has run anything.
"""

import json
from pathlib import Path
from typing import Any

from graphbench import paths
from graphbench.report import charts, tables

TRACK_TITLES = {
    "local": (
        "Resource-matched track (primary)",
        "Every engine in Docker at 0.5 vCPU / 256 MB, CognoDB's free c0 envelope, "
        "on one client machine over loopback. This is the only comparison here that "
        "can honestly claim resource parity.",
    ),
    "cloud": (
        "Managed free tier track (not resource-matched)",
        "Free tiers exactly as they ship. These tiers are **not equal to each other** "
        "(Aura Free advertises 1 GB, Memgraph Cloud 2 GB against CognoDB's 256 MB), so "
        "this table answers \"what do you get from the free tier you would actually sign "
        "up for\" and nothing stronger. Ranking these against the capped track would be "
        "a methodology error.",
    ),
    "reference": (
        "Embedded reference (not ranked)",
        "In-process, so there is no network round trip at all. Included as a floor: the "
        "gap between this and the Bolt engines is protocol and network cost rather than "
        "graph engine cost.",
    ),
}


def latest_run() -> Path | None:
    runs = sorted(d for d in paths.RESULTS_DIR.glob("*/") if (d / "run.json").exists())
    return runs[-1] if runs else None


def load_run(run_id: str | None = None) -> tuple[dict[str, Any], list[dict[str, Any]], Path]:
    run_dir = paths.RESULTS_DIR / run_id if run_id else latest_run()
    if run_dir is None or not (run_dir / "run.json").exists():
        raise FileNotFoundError("no completed run found in results/, run `graphbench run` first")

    summary = json.loads((run_dir / "run.json").read_text())
    records = [
        json.loads(f.read_text()) for f in sorted(run_dir.glob("*.json")) if f.name != "run.json"
    ]
    return summary, records, run_dir


BEGIN_MARKER = "<!-- BEGIN GENERATED RESULTS -->"
END_MARKER = "<!-- END GENERATED RESULTS -->"


def build(run_id: str | None = None) -> int:
    summary, records, run_dir = load_run(run_id)
    hops = summary["workloads"]["hops"]

    made = charts.build_all(records, hops, paths.CHARTS_DIR)
    text = _document(summary, records, hops, made)

    out = paths.DOCS_DIR / "RESULTS.md"
    out.write_text(text)
    print(f"wrote {out.relative_to(paths.ROOT)} from {run_dir.name}")
    for chart in made:
        print(f"wrote {Path(chart['path']).relative_to(paths.ROOT)}")

    injected = inject_into_readme(summary, records, hops)
    print(f"{'updated' if injected else 'left alone'} README.md results block")
    return 0


def inject_into_readme(
    summary: dict[str, Any], records: list[dict[str, Any]], hops: list[int]
) -> bool:
    """Replace the marked block in README.md with the generated matrix.

    The brief asks for the full results matrix in the README itself, and a
    hand-maintained copy of numbers that live in JSON drifts within a day. The
    markers mean the narrative around it stays hand-written.
    """
    readme = paths.ROOT / "README.md"
    text = readme.read_text()
    if BEGIN_MARKER not in text or END_MARKER not in text:
        return False

    block = _readme_block(summary, records, hops)
    head = text.split(BEGIN_MARKER)[0]
    tail = text.split(END_MARKER)[1]
    readme.write_text(f"{head}{BEGIN_MARKER}\n{block}\n{END_MARKER}{tail}")
    return True


def _readme_block(
    summary: dict[str, Any], records: list[dict[str, Any]], hops: list[int]
) -> str:
    ds = summary["dataset"]
    parts = [
        "",
        f"Run `{summary['run_id']}` on **{ds.get('dataset')}** "
        f"({ds.get('nodes', 0):,} nodes / {ds.get('edges', 0):,} relationships). "
        f"Regenerate with `make report`. Full detail, charts, per-iteration samples "
        f"and verbatim errors: [docs/RESULTS.md](docs/RESULTS.md).",
        "",
    ]

    v = summary.get("verification") or {}
    if v.get("compared") and v.get("agree"):
        parts += [
            f"**Cross-checked:** every platform returned identical values on "
            f"{v['compared']} query results, so the latencies below are comparing "
            f"equivalent queries.",
            "",
        ]
    elif v.get("mismatches"):
        parts += [
            f"**Warning:** {len(v['mismatches'])} cross-platform disagreements. "
            f"See [docs/RESULTS.md](docs/RESULTS.md#verification).",
            "",
        ]

    for track in ("local", "cloud", "reference"):
        in_track = [r for r in records if r["platform"]["track"] == track]
        if not in_track:
            continue
        title, blurb = TRACK_TITLES[track]
        parts += [f"### {title}", "", blurb, ""]
        parts += ["**Tiers**", "", tables.tier_table(in_track), ""]
        parts += ["**Data loading**", "", tables.ingest_table(in_track), ""]
        parts += ["**Read latency (ms)**", "", tables.latency_table(in_track, hops), ""]
        parts += ["**Mixed workload**", "", tables.mixed_table(in_track), ""]
        parts += ["**Footprint**", "", tables.footprint_table(in_track), ""]

    if summary.get("skipped"):
        parts += [
            "### Not run",
            "",
            "No credentials configured, so these were skipped rather than failed:",
            "",
        ]
        parts += [f"- `{s['id']}`: {s['reason']}" for s in summary["skipped"]]
        parts += [""]
    return "\n".join(parts)


def _document(
    summary: dict[str, Any],
    records: list[dict[str, Any]],
    hops: list[int],
    made: list[dict[str, Any]],
) -> str:
    ds = summary["dataset"]
    env = summary["environment"]
    wl = summary["workloads"]
    chart_names = {c["name"] for c in made}

    parts: list[str] = [
        "# Results",
        "",
        f"Generated from `results/{summary['run_id']}/` by `graphbench report`. "
        "Do not edit by hand.",
        "",
        "## Run",
        "",
        f"- Run id: `{summary['run_id']}`, started {summary['started_at']}, "
        f"finished {summary.get('finished_at', 'n/a')}",
        f"- Dataset: **{ds.get('dataset', '?')}**, {ds.get('nodes', 0):,} nodes / "
        f"{ds.get('edges', 0):,} relationships, sha256 "
        f"`{ds.get('raw_sha256', '')[:16]}...`",
        # .get throughout: a run recorded before a manifest field existed should
        # still render, with a gap rather than a traceback.
        f"- Degree: min {ds.get('degree_min', '?')}, mean {ds.get('degree_mean', '?')}, "
        f"max {ds.get('degree_max', '?')}. Start nodes: {ds.get('start_nodes', '?')} "
        f"from degree band {ds.get('degree_band', 'unrecorded')}, seed {ds.get('seed', '?')}",
        f"- Source: {ds.get('source_url', '?')}",
        f"- Client: {env['client_os']} / {env['client_machine']}, "
        f"{env['client_cpu_count']} cores, "
        f"{(env['client_memory_bytes'] or 0) / 1e9:.0f} GB RAM, Python {env['python']}",
        "- Drivers: " + ", ".join(f"{k} {v}" for k, v in env["packages"].items()),
        f"- Code: commit `{(env.get('git_commit') or 'unknown')[:12]}`"
        + (" (working tree dirty)" if env.get("git_dirty") else ""),
        f"- Workload: {wl['iterations']} measured iterations after {wl['warmup']} warm-up, "
        f"batch size {wl['batch_size']}, per-query ceiling {wl['timeout_s']}s",
        f"- Mixed: {wl['read_ratio']:.0%} read at {wl['concurrency']} clients "
        f"for {wl['duration_s']}s each"
        + (" (**skipped this run**)" if wl.get("mixed_skipped") else ""),
        "",
    ]

    parts += _verification_section(summary)
    parts += ["## Platforms and tiers", "", tables.tier_table(records), ""]

    if summary.get("skipped"):
        parts += ["### Not run", ""]
        parts += [f"- `{s['id']}`: {s['reason']}" for s in summary["skipped"]]
        parts += [
            "",
            "A skipped platform means no credentials were configured, not that it failed.",
            "",
        ]

    for track in ("local", "cloud", "reference"):
        in_track = [r for r in records if r["platform"]["track"] == track]
        if not in_track:
            continue
        title, blurb = TRACK_TITLES[track]
        parts += [f"## {title}", "", blurb, ""]
        parts += ["### Ingest", "", tables.ingest_table(in_track), ""]
        parts += ["### Read latency", "", tables.latency_table(in_track, hops), ""]
        parts += [
            "A ⚠ marks a workload that was abandoned or had failed iterations. "
            "Its percentiles cover only the iterations that completed.",
            "",
        ]
        parts += [
            "### Engine cost with the network subtracted",
            "",
            "Traversal p50 minus the `RETURN 1` p50 on the same platform.",
            "",
            tables.engine_cost_table(in_track, hops),
            "",
        ]
        parts += ["### Mixed workload", "", tables.mixed_table(in_track), ""]
        parts += ["### Footprint", "", tables.footprint_table(in_track), ""]
        parts += ["### Indexes actually created", "", tables.index_table(in_track)]
        parts += ["### Run status", "", tables.status_table(in_track), ""]

    if made:
        parts += ["## Charts", ""]
        if "traversal" in chart_names:
            parts += ["![Traversal latency by depth](charts/traversal_latency.png)", ""]
        if "tail" in chart_names:
            parts += ["![p50 against p95](charts/p50_vs_p95.png)", ""]
        if "ingest" in chart_names:
            parts += ["![Ingest throughput](charts/ingest_throughput.png)", ""]
        if "concurrency" in chart_names:
            parts += ["![Concurrency sweep](charts/concurrency_sweep.png)", ""]

    parts += _errors_section(records)
    parts += [
        "## Reading these numbers",
        "",
        "- Percentiles are nearest-rank over the raw samples, which are kept in the "
        "results JSON so any of this can be recomputed or plotted differently.",
        "- Latency is measured client-side and includes driver and protocol cost. "
        "The `RETURN 1` column is that cost with no graph work in it.",
        "- `cpus: 0.5` on the local track is a CFS quota, a hard ceiling every "
        "period. CognoDB's 0.5 vCPU is burstable and can exceed baseline while it "
        "has credit, so tail latency is not directly comparable across tracks.",
        "- Two runs of identical configuration disagree by 60-360% on ingest and "
        "saturated throughput while agreeing within 6% on `RETURN 1`. Which metrics "
        "survive repetition is in [VARIANCE.md](VARIANCE.md), and any difference "
        "smaller than the spread there is not a finding.",
        "- Interpretation, mechanisms and known gaps: [ANALYSIS.md](ANALYSIS.md).",
        "- Every judgement call, and what this suite does not measure: "
        "[DECISIONS.md](DECISIONS.md).",
        "",
    ]
    return "\n".join(parts) + "\n"


def _verification_section(summary: dict[str, Any]) -> list[str]:
    v = summary.get("verification") or {}
    parts = ["## Verification", ""]

    if not v.get("compared"):
        parts += [
            "Only one platform answered, so there was nothing to cross-check. "
            "Agreement between platforms needs at least two.",
            "",
        ]
    elif v.get("agree"):
        parts += [
            f"All platforms returned identical values on **{v['compared']}** checked "
            "query results. Same input, same answer, so the latency comparison is "
            "between equivalent queries.",
            "",
        ]
    else:
        parts += [
            f"**{len(v['mismatches'])} disagreements** between platforms. The timing "
            "comparison is not valid until these are resolved.",
            "",
        ]
        for m in v["mismatches"][:15]:
            parts.append(f"- `{m['workload']}` at `{m['key']}`: {m['values']}")
        parts.append("")

    if v.get("expectation_failures"):
        parts += ["Workloads with a known correct answer that did not match:", ""]
        for f in v["expectation_failures"][:15]:
            parts.append(
                f"- `{f['platform']}` `{f['workload']}`: expected {f['expected']}, got {f['got']}"
            )
        parts.append("")
    if v.get("monotonic_failures"):
        parts += ["Non-monotonic hop counts (a k-hop neighbourhood cannot shrink):", ""]
        for f in v["monotonic_failures"][:15]:
            parts.append(f"- `{f['platform']}` at `{f['key']}`: {f}")
        parts.append("")
    return parts


def _errors_section(records: list[dict[str, Any]]) -> list[str]:
    with_errors = [r for r in records if r.get("errors")]
    if not with_errors:
        return ["## Errors", "", "No platform reported an error on this run.", ""]

    parts = [
        "## Errors",
        "",
        "Verbatim, because a benchmark that hides its failures is not worth reading.",
        "",
    ]
    for r in with_errors:
        parts.append(f"**{r['platform']['display']}** ({r.get('status')})")
        parts.append("")
        parts.append("```text")
        parts.extend(str(e) for e in r["errors"][:5])
        parts.append("```")
        parts.append("")
    return parts
