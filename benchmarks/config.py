"""Benchmark configuration — all knobs in one place."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

try:
    from aether.orchestrator.settings import settings as orchestrator_settings
except Exception:
    orchestrator_settings = None


def _default_recovery_snapshot_max_age() -> float:
    if orchestrator_settings is not None:
        return float(orchestrator_settings.snapshot_max_age)
    return 45.0


@dataclass
class BenchmarkConfig:
    orchestrator_url: str = "http://localhost:9000"
    warmup_seconds: int = 10
    measurement_seconds: int = 30
    poll_interval: float = 1.0
    min_throughput_samples: int = 3
    metrics_max_staleness_seconds: float = 3.0
    max_metrics_read_failures: int = 3
    # Publisher send interval in seconds. 0.001 = 1000 msg/s per publisher.
    # The default 1.0 used by /api/seed is intentionally throttled for the UI
    # demo; benchmarks need the mesh pushed hard to find real limits.
    publish_interval: float = 0.001
    results_dir: Path = field(default_factory=lambda: Path("benchmarks/results"))

    # Throughput benchmark matrix
    broker_counts: list[int] = field(default_factory=lambda: [3, 5, 7, 10])
    publisher_counts: list[int] = field(default_factory=lambda: [1, 2, 3, 5, 8])

    # Latency benchmark
    # publish_interval is intentionally slow here: the latency benchmark must
    # keep the broker well below saturation so that queueing delay does not
    # inflate the numbers.  0.05 s = 20 msg/s per publisher — roughly 1% of
    # the broker's throughput capacity, so the measured latency reflects actual
    # network + processing overhead rather than head-of-line blocking.
    latency_brokers: int = 3
    latency_publishers: int = 2
    latency_subscribers: int = 3
    latency_publish_interval: float = 0.05
    latency_ready_timeout_seconds: float = 30.0
    latency_ready_poll_interval: float = 1.0
    latency_ready_consecutive_polls: int = 3
    latency_min_samples_per_subscriber: int = 20

    # Snapshot benchmark
    snapshot_broker_counts: list[int] = field(default_factory=lambda: [3, 5, 7, 10])
    snapshot_rounds: int = 3

    # Recovery benchmark
    recovery_trials: int = 5
    recovery_path_a_trials: int = 3
    recovery_path_a_delay_seconds: float = 2.0
    recovery_snapshot_max_age_seconds: float = field(
        default_factory=_default_recovery_snapshot_max_age
    )
    recovery_stale_margin_seconds: float = 5.0

    # Scaling benchmark
    scaling_brokers: int = 3
    scaling_initial_publishers: int = 1
    scaling_max_publishers: int = 10
    scaling_subscribers: int = 3
    scaling_step_seconds: int = 20
