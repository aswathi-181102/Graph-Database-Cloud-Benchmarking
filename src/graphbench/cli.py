import argparse
import json
import sys

from graphbench import __version__, datasets, paths


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="graphbench")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="command", required=True)

    ds = sub.add_parser("dataset")
    ds.add_argument("action", choices=["fetch", "prepare", "info"])
    ds.add_argument("--dataset", default=datasets.DEFAULT_DATASET)
    ds.add_argument("--seed", type=int, default=None)
    ds.add_argument("--force", action="store_true", help="re-download even if cached")

    sub.add_parser("doctor")
    sub.add_parser("run")
    sub.add_parser("report")
    return p


def cmd_dataset(args: argparse.Namespace) -> int:
    from graphbench.datasets import fetch, prepare

    ds = datasets.get(args.dataset)

    if args.action == "fetch":
        fetch.fetch(ds, force=args.force)
        return 0

    if args.action == "prepare":
        seed = args.seed if args.seed is not None else prepare.DEFAULT_SEED
        prepare.prepare(ds, seed=seed, force=args.force)
        return 0

    graph = datasets.load(ds.name)
    print(json.dumps(graph.manifest, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    paths.ensure_dirs()

    if args.command == "dataset":
        return cmd_dataset(args)

    print(f"{args.command}: not wired up yet", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
