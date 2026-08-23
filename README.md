# Graph Database Cloud Benchmarking

Benchmarks CognoDB Cloud against four other graph databases on the same
dataset, the same queries, the same client machine, and the same amount of
CPU/RAM/disk.

Status: harness first, results once every platform is provisioned and run.

## Why bother

Most vendor graph benchmarks are unusable for one of two reasons: the databases
weren't given the same hardware, or the "same query" wasn't actually the same
query. This repo pins both down. The resource envelope is 0.5 vCPU / 256 MB
RAM / 1 GB disk, which is what CognoDB's free `c0` instance gets, and every
other engine is held to the same limit. Workloads live in one place and each
engine only supplies its own dialect of them.

## Layout

```text
config/          platform + workload definitions, env refs only, no secrets
src/graphbench/
  datasets/      download, verify, sample, write canonical CSV
  adapters/      one per engine, same interface
  workloads/     load, traversal, lookup, aggregation, mixed
  report/        markdown tables + charts
docker-compose.yml   resource-capped local stack
results/         raw run output, committed
docs/            methodology, platform specs, analysis
```

## Running it

```bash
make setup
cp .env.example .env    # fill in the credentials you have
make dataset
make doctor             # tells you which platforms are reachable
make bench
make report
```

Credentials come from the environment only. Nothing is read from a file that
gets committed.

## License

MIT
