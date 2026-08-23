import argparse
import sys

from graphbench import __version__


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="graphbench")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="command", required=True)

    ds = sub.add_parser("dataset")
    ds.add_argument("action", choices=["fetch", "prepare", "info"])

    sub.add_parser("doctor")
    sub.add_parser("run")
    sub.add_parser("report")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    print(f"{args.command}: not wired up yet", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
