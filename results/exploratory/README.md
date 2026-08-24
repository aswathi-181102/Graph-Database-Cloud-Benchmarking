# Exploratory runs

Probe runs from building the harness, kept as evidence rather than as results. Not
part of the published comparison and not included in the variance analysis.

Several of them are wrong on purpose, in the sense that they are what the bugs
looked like before they were found:

- `20260823T181624Z` and `20260823T183227Z` — Memgraph reporting a 3-hop p50 of
  0.64 ms against a graph holding 5,000 of 198,050 relationships, because a reload
  into un-freed memory failed silently. The fast, wrong number.
- `20260823T183227Z` — the run where the cross-platform check first earned its
  keep: Memgraph answered 0 where the other three answered 42, and the run exited
  non-zero instead of publishing.
- `20260824T045802Z` — after the container restart fix but before the volumes were
  destroyed, so Memgraph recovered a snapshot on boot and loaded 98,050 of 198,050.
- `20260824T054424Z` — first working Kùzu run, single platform.

- `20260824T171739Z` - first run at the corrected 512 MB cap and the first with
  CognoDB in it. Kept because it is the evidence for the reconnect fix: CognoDB's
  3-hop came back ABANDONED on `Failed to read from defunct connection`, and
  probing it straight afterwards showed 3-hop working on every start degree and
  returning counts matching all four other engines. A lost packet on a 240 ms link
  was being reported as an engine that could not run the query. Superseded by the
  run that follows the fix.

The published runs are the 5-platform and 6-platform ones in `results/`.
