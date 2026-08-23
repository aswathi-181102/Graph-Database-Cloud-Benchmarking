import pytest

from graphbench import config

PLATFORMS = """
defaults:
  dataset: ca-astroph
platforms:
  - id: needs-creds
    display: Needs Creds
    engine: cognodb
    adapter: bolt
    track: cloud
    tier:
      name: c0
      vcpu: 0.5
      ram_mb: 256
      disk_gb: 1
      burstable: true
    connection:
      uri: ${TEST_URI}
      user: ${TEST_USER:-cognodb}
      password: ${TEST_PASSWORD}
      database: ${TEST_DB:-}
  - id: no-creds-needed
    display: Local
    engine: memgraph
    adapter: bolt
    track: local
    tier:
      name: docker
      vcpu: 0.5
      ram_mb: 256
    connection:
      uri: bolt://localhost:7688
      port: 7688
"""

WORKLOADS = """
load:
  batch_size: 5000
reads:
  iterations: 100
  warmup: 20
  baseline_query: true
  hops: [1, 2, 3]
  timeout_s: 30
mixed:
  concurrency: [1, 10, 40]
  duration_s: 30
  read_ratio: 0.9
  cleanup_writes: true
lookups:
  cohort: 7
  min_degree: 10
"""


def write(tmp_path, name, body):
    p = tmp_path / name
    p.write_text(body)
    return p


def test_missing_required_vars_are_collected_not_raised(tmp_path, monkeypatch):
    monkeypatch.delenv("TEST_URI", raising=False)
    monkeypatch.delenv("TEST_PASSWORD", raising=False)
    p = config.load_platforms(write(tmp_path, "p.yaml", PLATFORMS))[0]
    assert not p.usable
    assert p.missing_env == ("TEST_URI", "TEST_PASSWORD")
    assert "TEST_URI" in p.skip_reason


def test_env_values_are_substituted(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_URI", "bolt+s://x.databases.cognodb.cloud")
    monkeypatch.setenv("TEST_PASSWORD", "secret")
    p = config.load_platforms(write(tmp_path, "p.yaml", PLATFORMS))[0]
    assert p.usable
    assert p.connection["uri"] == "bolt+s://x.databases.cognodb.cloud"


def test_defaults_apply_when_var_is_unset(tmp_path, monkeypatch):
    monkeypatch.delenv("TEST_USER", raising=False)
    p = config.load_platforms(write(tmp_path, "p.yaml", PLATFORMS))[0]
    assert p.connection["user"] == "cognodb"


def test_env_overrides_the_default(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_USER", "someone-else")
    p = config.load_platforms(write(tmp_path, "p.yaml", PLATFORMS))[0]
    assert p.connection["user"] == "someone-else"


def test_empty_env_counts_as_missing(tmp_path, monkeypatch):
    # This is the .env.example case: the key exists but was never filled in.
    monkeypatch.setenv("TEST_URI", "")
    monkeypatch.setenv("TEST_PASSWORD", "x")
    p = config.load_platforms(write(tmp_path, "p.yaml", PLATFORMS))[0]
    assert p.missing_env == ("TEST_URI",)


def test_empty_default_is_not_missing(tmp_path, monkeypatch):
    # ${TEST_DB:-} means "optional, blank is fine", e.g. engines that reject a
    # database parameter entirely.
    monkeypatch.delenv("TEST_DB", raising=False)
    monkeypatch.setenv("TEST_URI", "x")
    monkeypatch.setenv("TEST_PASSWORD", "y")
    p = config.load_platforms(write(tmp_path, "p.yaml", PLATFORMS))[0]
    assert p.connection["database"] == ""
    assert p.usable


def test_platforms_without_env_refs_are_always_usable(tmp_path):
    p = config.load_platforms(write(tmp_path, "p.yaml", PLATFORMS))[1]
    assert p.usable
    assert p.connection["port"] == 7688  # non-strings pass through untouched


def test_duplicate_ids_are_rejected(tmp_path):
    body = PLATFORMS.replace("id: no-creds-needed", "id: needs-creds")
    with pytest.raises(ValueError, match="duplicate platform ids"):
        config.load_platforms(write(tmp_path, "dupes.yaml", body))


def test_workloads_load(tmp_path):
    w = config.load_workloads(write(tmp_path, "w.yaml", WORKLOADS))
    assert w.hops == (1, 2, 3)
    assert w.concurrency == (1, 10, 40)
    assert w.read_ratio == 0.9


def test_too_few_iterations_is_refused(tmp_path):
    body = WORKLOADS.replace("iterations: 100", "iterations: 30")
    with pytest.raises(ValueError, match="below the 100"):
        config.load_workloads(write(tmp_path, "w.yaml", body))


def test_nonsense_read_ratio_is_refused(tmp_path):
    body = WORKLOADS.replace("read_ratio: 0.9", "read_ratio: 9")
    with pytest.raises(ValueError, match="fraction"):
        config.load_workloads(write(tmp_path, "w.yaml", body))


def test_tier_summary_marks_unpublished_values():
    t = config.Tier(name="aura free", vcpu=None, ram_mb=1024, disk_gb=2, burstable=True)
    assert t.summary() == "? vCPU / 1024 MB / 2 GB, burstable"


def test_shipped_config_parses():
    # Guards against a typo in the real YAML that unit tests on fixtures miss.
    platforms = config.load_platforms()
    ids = {p.id for p in platforms}
    assert "cognodb-cloud" in ids
    assert len({p.engine for p in platforms}) >= 5
    config.load_workloads()
