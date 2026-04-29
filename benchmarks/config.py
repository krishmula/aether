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
    window_retry_limit: int = 2
    throughput_retry_backoff_seconds: float = 2.0
    scaling_retry_backoff_seconds: float = 3.0
    near_zero_msgs_per_sec: float = 1.0
    max_near_zero_sample_ratio: float = 0.5
    scaling_saturation_gain_threshold_pct: float = 5.0
    scaling_saturation_consecutive_steps: int = 2
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
    latency_ready_timeout_seconds: float = 60.0
    latency_ready_poll_interval: float = 2.0
    latency_ready_consecutive_polls: int = 3
    latency_min_samples_per_subscriber: int = 20
    latency_min_delivery_ratio: float = 0.7

    # Snapshot benchmark
    snapshot_broker_counts: list[int] = field(default_factory=lambda: [3, 5, 7, 10])
    snapshot_rounds: int = 2
    snapshot_poll_interval: float = 2.0
    # Pre-flight waits for first snapshot per broker (up to 90s from process
    # start under Docker load). Each measured round waits up to one full
    # snapshot_interval (90s). 200s covers both phases with headroom.
    snapshot_convergence_timeout: float = 200.0

    # Recovery benchmark
    recovery_trials: int = 5
    recovery_path_a_trials: int = 3
    recovery_path_a_delay_seconds: float = 2.0
    recovery_snapshot_max_age_seconds: float = field(
        default_factory=_default_recovery_snapshot_max_age
    )
    recovery_stale_margin_seconds: float = 5.0
    recovery_snapshot_poll_interval: float = 3.0
    # snapshot_interval=90s: Path A must wait up to 90s for the first snapshot.
    recovery_path_a_snapshot_wait_seconds: float = 100.0
    # Startup timeout for wait_all_running during recovery re-seeds; longer than
    # the default 60s to accommodate Docker resource pressure mid-benchmark.
    recovery_startup_timeout: float = 120.0

    publisher_redundancy: int = 2

    # Phase 7 verification gate
    verification_proof_brokers: int = 3
    verification_proof_publishers: int = 2
    verification_proof_warmup_seconds: int = 5
    verification_proof_measurement_seconds: int = 15
    verification_metrics_reader_poll_interval: float = 0.2
    verification_max_throughput_drift_pct: float = 20.0

    # Scaling benchmark
    scaling_brokers: int = 3
    scaling_initial_publishers: int = 1
    scaling_max_publishers: int = 10
    scaling_subscribers: int = 3
    scaling_step_seconds: int = 20
