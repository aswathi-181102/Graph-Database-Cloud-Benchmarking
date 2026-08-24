"""Markdown tables built from the results JSON.

Tables are grouped by track and never mix them, because the local track is
resource-matched and the cloud track is not, so one combined league table would be
the exact methodology error this whole study is trying to avoid.
"""

from typing import Any

DASH = "-"


def _md(rows: list[list[str]], align: list[str] | None = None) -> str:
    if not rows:
        return ""
    header, body = rows[0], rows[1:]
    align = align or ["left"] + ["right"] * (len(header) - 1)
    sep = ["---" if a == "left" else "---:" for a in align]
    out = ["| " + " | ".join(header) + " |", "| " + " | ".join(sep) + " |"]
    out += ["| " + " | ".join(r) + " |" for r in body]
    return "\n".join(out)


def _num(value: Any, digits: int = 2, thousands: bool = False) -> str:
    if value is None:
        return DASH
    if thousands:
        return f"{value:,.0f}"
    return f"{value:.{digits}f}"


def _read(record: dict, name: str) -> dict | None:
    return next((r for r in record.get("reads", []) if r["name"] == name), None)


def _lat(record: dict, name: str, key: str) -> float | None:
    workload = _read(record, name)
    if not workload:
        return None
    return workload["latency"].get(key)


def _flag(record: dict, name: str) -> str:
    workload = _read(record, name)
    if not workload:
        return ""
    if workload.get("abandoned"):
        return " ⚠"
    if workload["latency"].get("errors"):
        return " ⚠"
    return ""


def tier_table(records: list[dict]) -> str:
    rows = [["Platform", "Track", "Engine", "Version", "vCPU", "RAM", "Disk", "Tier source"]]
    for r in records:
        p, tier = r["platform"], r["platform"]["tier"]
        rows.append(
            [
                p["display"],
                p["track"],
                p["engine"],
                r.get("server_version", DASH),
                DASH if tier["vcpu"] is None else f"{tier['vcpu']:g}",
                DASH if tier["ram_mb"] is None else f"{tier['ram_mb']} MB",
                DASH if tier["disk_gb"] is None else f"{tier['disk_gb']} GB",
                tier["source"] or DASH,
            ]
        )
    return _md(rows, ["left", "left", "left", "left", "right", "right", "right", "left"])


def ingest_table(records: list[dict]) -> str:
    rows = [
        [
            "Platform",
            "Nodes/s",
            "Rels/s",
            "Index build (s)",
            "Total load (s)",
            "Rows loaded",
            "Load method",
        ]
    ]
    for r in records:
        load = r.get("load")
        if not load:
            rows.append([r["platform"]["display"]] + [DASH] * 5 + ["did not load"])
            continue
        phases = {p["name"]: p for p in load["phases"]}
        nodes, edges = phases.get("nodes", {}), phases.get("edges", {})
        expected = r["dataset"]["nodes"] + r["dataset"]["edges"]
        actual = nodes.get("rows", 0) + edges.get("rows", 0)
        # A partial load has to be visible in the table itself. An ingest rate
        # computed over 5k of 198k edges is not a slower number, it is a different
        # measurement.
        loaded = f"{actual:,}/{expected:,}" + (" ⚠" if actual < expected else "")
        rows.append(
            [
                r["platform"]["display"],
                _num(load.get("nodes_per_second"), thousands=True),
                _num(load.get("rels_per_second"), thousands=True),
                _num(phases.get("indexes", {}).get("seconds"), 2),
                _num(load.get("total_seconds"), 1),
                loaded,
                load.get("method", DASH),
            ]
        )
    return _md(rows, ["left", "right", "right", "right", "right", "right", "left"])


def latency_table(records: list[dict], hops: list[int]) -> str:
    header = ["Platform", "RETURN 1", "Point", *[f"{h}-hop" for h in hops], "Filtered", "Group-by"]
    rows = [header]
    for stat in ("p50_ms", "p95_ms"):
        rows.append([f"**{stat.replace('_ms', '').upper()} (ms)**"] + [""] * (len(header) - 1))
        for r in records:
            cells = [
                r["platform"]["display"],
                _num(_lat(r, "baseline_noop", stat)) + _flag(r, "baseline_noop"),
                _num(_lat(r, "point_lookup", stat)) + _flag(r, "point_lookup"),
            ]
            for h in hops:
                cells.append(
                    _num(_lat(r, f"traversal_{h}hop", stat)) + _flag(r, f"traversal_{h}hop")
                )
            cells.append(_num(_lat(r, "filtered_lookup", stat)) + _flag(r, "filtered_lookup"))
            cells.append(
                _num(_lat(r, "aggregation_groupby_cohort", stat))
                + _flag(r, "aggregation_groupby_cohort")
            )
            rows.append(cells)
    return _md(rows)


def engine_cost_table(records: list[dict], hops: list[int]) -> str:
    """Traversal p50 minus the RETURN 1 p50.

    The point of the baseline: a managed service on the internet and a container on
    loopback are not comparable on raw latency, but subtracting the no-op leaves
    something much closer to the engine's own work.
    """
    rows = [["Platform", "RETURN 1 p50", *[f"{h}-hop minus baseline" for h in hops]]]
    for r in records:
        base = _lat(r, "baseline_noop", "p50_ms")
        cells = [r["platform"]["display"], _num(base)]
        for h in hops:
            value = _lat(r, f"traversal_{h}hop", "p50_ms")
            if value is None or base is None:
                cells.append(DASH)
            else:
                cells.append(_num(max(0.0, value - base)))
        rows.append(cells)
    return _md(rows)


def mixed_table(records: list[dict]) -> str:
    levels = sorted(
        {
            level["concurrency"]
            for r in records
            for level in r.get("mixed", {}).get("levels", [])
        }
    )
    if not levels:
        return "_Mixed workload was not run._"

    rows = [["Platform", *[f"{c} client{'s' if c > 1 else ''} (qps)" for c in levels],
             "Read p95 @ max", "Errors"]]
    for r in records:
        by_level = {lv["concurrency"]: lv for lv in r.get("mixed", {}).get("levels", [])}
        cells = [r["platform"]["display"]]
        for c in levels:
            cells.append(_num(by_level.get(c, {}).get("qps"), 1) if c in by_level else DASH)
        top = by_level.get(levels[-1], {})
        cells.append(_num((top.get("read_latency") or {}).get("p95_ms")))
        errs = (top.get("read_latency") or {}).get("errors", 0)
        cells.append(str(errs) if errs else "0")
        rows.append(cells)
    return _md(rows)


def footprint_table(records: list[dict]) -> str:
    rows = [["Platform", "Observed RSS", "% of cap", "Store on disk", "Engine-reported", "Source"]]
    for r in records:
        container = r.get("container") or {}
        footprint = r.get("footprint") or {}
        disk = container.get("data_dir_bytes")
        rows.append(
            [
                r["platform"]["display"],
                container.get("mem_usage", "not observable"),
                container.get("mem_percent", DASH),
                f"{disk / 1e6:.1f} MB" if disk else DASH,
                "yes" if footprint.get("observable") else "not observable",
                footprint.get("source") or container.get("source") or DASH,
            ]
        )
    return _md(rows, ["left", "right", "right", "right", "left", "left"])


def index_table(records: list[dict]) -> str:
    lines = []
    for r in records:
        lines.append(f"**{r['platform']['display']}**")
        indexes = r.get("indexes") or []
        if not indexes:
            lines.append("- none recorded")
        else:
            lines.extend(f"- {i}" for i in indexes)
        lines.append("")
    return "\n".join(lines)


def status_table(records: list[dict]) -> str:
    rows = [["Platform", "Status", "Store reset first", "Wall clock (s)", "Errors"]]
    for r in records:
        reset = r.get("reset") or r.get("restart") or {}
        note = "yes, volumes wiped" if reset.get("reset") else reset.get("reason", "no")
        rows.append(
            [
                r["platform"]["display"],
                r.get("status", "?"),
                note,
                _num(r.get("duration_s"), 0),
                str(len(r.get("errors", []))),
            ]
        )
    return _md(rows, ["left", "left", "left", "right", "right"])
