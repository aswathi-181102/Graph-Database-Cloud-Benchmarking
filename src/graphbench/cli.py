import argparse
import json
import sys

from dotenv import load_dotenv

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

    run = sub.add_parser("run")
    run.add_argument("--platforms", help="comma separated ids, default all usable")
    run.add_argument("--track", choices=["local", "cloud", "reference"])
    run.add_argument("--skip-mixed", action="store_true", help="reads only, much faster")
    run.add_argument(
        "--no-reset",
        action="store_true",
        help="reuse the local containers as-is (ingest numbers stop being comparable)",
    )
    run.add_argument("--dataset", default=None)
    run.add_argument("--run-id", default=None)

    cal = sub.add_parser("calibrate", help="batch size sweep and repeat-run variance")
    cal.add_argument("--platforms", help="comma separated ids, default the local track")
    cal.add_argument("--batch-sizes", default="1000,2500,5000,10000")
    cal.add_argument("--repeats", type=int, default=1)
    cal.add_argument("--dataset", default=None)

    cmp_ = sub.add_parser("compare", help="run-to-run variance across identical runs")
    cmp_.add_argument("run_ids", nargs="*", help="default is every completed run")

    rep = sub.add_parser("report")
    rep.add_argument("--run-id", default=None, help="default is the latest run")

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

    print(json.dumps(datasets.load(ds.name).manifest, indent=2))
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    from graphbench import config, doctor

    platforms = config.load_platforms()
    print(doctor.render(platforms))
    return 0 if any(p.usable for p in platforms) else 1


def cmd_run(args: argparse.Namespace) -> int:
    from graphbench import config, runner

    platforms = config.load_platforms()

    if args.track:
        platforms = [p for p in platforms if p.track == args.track]
    if args.platforms:
        wanted = {s.strip() for s in args.platforms.split(",") if s.strip()}
        unknown = wanted - {p.id for p in platforms}
        if unknown:
            print(f"unknown platform ids: {sorted(unknown)}", file=sys.stderr)
            return 2
        platforms = [p for p in platforms if p.id in wanted]

    if not any(p.usable for p in platforms):
        print("nothing to run, see `graphbench doctor`", file=sys.stderr)
        return 1

    dataset = args.dataset or config.default_dataset()
    summary = runner.run_all(
        platforms,
        datasets.load(dataset),
        config.load_workloads(),
        skip_mixed=args.skip_mixed,
        run_id=args.run_id,
        restart=not args.no_reset,
    )
    # Non-zero if the platforms disagreed about what a query returns. That is not a
    # slow benchmark, it is an invalid one, and it should be loud.
    return 0 if summary["verification"]["clean"] else 3


def cmd_calibrate(args: argparse.Namespace) -> int:
    from graphbench import calibrate, config

    platforms = [p for p in config.load_platforms() if p.usable]
    if args.platforms:
        wanted = {s.strip() for s in args.platforms.split(",") if s.strip()}
        platforms = [p for p in platforms if p.id in wanted]
    else:
        platforms = [p for p in platforms if p.track == "local"]

    if not platforms:
        print("nothing to calibrate, see `graphbench doctor`", file=sys.stderr)
        return 1

    sizes = sorted(int(s) for s in args.batch_sizes.split(","))
    calibrate.sweep(
        platforms,
        datasets.load(args.dataset or config.default_dataset()),
        config.load_workloads(),
        sizes,
        repeats=args.repeats,
    )
    return 0


def cmd_compare(args: argparse.Namespace) -> int:
    from graphbench.report import variance

    try:
        run_ids = variance.resolve(args.run_ids)
    except (ValueError, FileNotFoundError) as exc:
        print(exc, file=sys.stderr)
        return 1
    return variance.build(run_ids)


def cmd_report(args: argparse.Namespace) -> int:
    from graphbench.report import render

    return render.build(run_id=args.run_id)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    # override=False: an exported shell variable beats the file, which is what you
    # want when overriding one platform for a one-off.
    load_dotenv(paths.ROOT / ".env", override=False)
    paths.ensure_dirs()

    handlers = {
        "dataset": cmd_dataset,
        "doctor": cmd_doctor,
        "run": cmd_run,
        "calibrate": cmd_calibrate,
        "compare": cmd_compare,
        "report": cmd_report,
    }
    return handlers[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
