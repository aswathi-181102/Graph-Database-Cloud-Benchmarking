"""`graphbench doctor`: what is configured, what is reachable, what gets skipped.

This exists because the failure mode it prevents is expensive. Without it you
start a run, wait through a 3 minute ingest, and then find out platform four had
a typo in its password. Doctor answers "will this run do what I think" in about
a second, before anything is loaded.

Connectivity checking is added in the adapter layer; at this stage it only
reports config-level readiness.
"""

from graphbench.config import Platform


def _column_widths(rows: list[tuple[str, ...]]) -> list[int]:
    return [max(len(r[i]) for r in rows) for i in range(len(rows[0]))]


def render(platforms: list[Platform]) -> str:
    rows: list[tuple[str, ...]] = [("PLATFORM", "TRACK", "ENGINE", "RESOURCES", "STATUS")]
    for p in platforms:
        status = "ready" if p.usable else f"skip: {p.skip_reason}"
        rows.append((p.id, p.track, p.engine, p.tier.summary(), status))

    widths = _column_widths(rows)
    lines = []
    for i, row in enumerate(rows):
        lines.append("  ".join(cell.ljust(w) for cell, w in zip(row, widths, strict=True)).rstrip())
        if i == 0:
            lines.append("  ".join("-" * w for w in widths))

    ready = [p for p in platforms if p.usable]
    by_track: dict[str, int] = {}
    for p in ready:
        by_track[p.track] = by_track.get(p.track, 0) + 1

    lines.append("")
    lines.append(f"{len(ready)} of {len(platforms)} platforms ready " + f"({by_track})")

    # The fairness rule the assignment cares about applies within the local
    # track, so warn when that track is too thin to say anything. A cloud-only
    # run is still publishable, it just cannot claim resource parity.
    local_ready = by_track.get("local", 0)
    if local_ready < 2:
        lines.append(
            "note: fewer than 2 local platforms are up, so there is no "
            "resource-matched comparison. run `make up` first."
        )
    if not any(p.engine == "cognodb" and p.usable for p in platforms):
        lines.append("note: CognoDB is not configured, so the subject of the study is missing.")

    return "\n".join(lines)
