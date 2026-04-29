"""Metric collection helpers for benchmarks."""

from __future__ import annotations

import asyncio
import time
from typing import Any

from benchmarks.client import AetherClient


async def collect_throughput(
    client: AetherClient,
    duration: float,
    poll_interval: float = 1.0,
    *,
    expected_generation: int | None = None,
    stage: str = "throughput measurement",
) -> list[dict[str, Any]]:
    """Poll /api/metrics for *duration* seconds and return per-interval samples.

    Each sample: {"elapsed": float, "messages_total": int, "msgs_per_sec": float}
    """
    samples: list[dict[str, Any]] = []
    start = time.monotonic()
    prev_total: int | None = None
    prev_time: float | None = None
    consecutive_failures = 0

    current_generation = expected_generation
    while time.monotonic() - start < duration:
        try:
            metrics = await client.get_metrics()
            client.assert_valid_metrics_snapshot(
                metrics,
                stage=stage,
                expected_generation=current_generation,
            )
            if current_generation is None:
                current_generation = metrics["topology_generation"]
            consecutive_failures = 0
            now = time.monotonic()
            if "total_messages_processed" not in metrics:
                raise RuntimeError("metrics payload missing total_messages_processed")
            total = metrics["total_messages_processed"]

            if prev_total is not None and prev_time is not None:
                dt = now - prev_time
                if dt > 0:
                    msgs_per_sec = (total - prev_total) / dt
                    samples.append(
                        {
                            "elapsed": round(now - start, 2),
                            "messages_total": total,
                            "msgs_per_sec": round(msgs_per_sec, 1),
                        }
                    )

            prev_total = total
            prev_time = now
        except Exception as exc:
            consecutive_failures += 1
            if consecutive_failures >= client.cfg.max_metrics_read_failures:
                raise RuntimeError(
                    "failed to collect throughput metrics after "
                    f"{consecutive_failures} consecutive errors: {exc}"
                ) from exc

        await asyncio.sleep(poll_interval)

    if len(samples) < client.cfg.min_throughput_samples:
        raise RuntimeError(
            "insufficient throughput samples: "
            f"expected at least {client.cfg.min_throughput_samples}, got {len(samples)}"
        )

    return samples


def classify_throughput_window(
    samples: list[dict[str, Any]],
    *,
    active_publishers: int,
    near_zero_msgs_per_sec: float,
    max_near_zero_sample_ratio: float,
) -> str | None:
    """Return an explicit invalidity reason when a throughput window looks poisoned."""
    if not samples:
        return "throughput window produced no samples"

    if active_publishers <= 0:
        return None

    near_zero_count = sum(
        1
        for sample in samples
        if float(sample.get("msgs_per_sec", 0.0)) <= near_zero_msgs_per_sec
    )
    near_zero_ratio = near_zero_count / len(samples)
    if near_zero_ratio > max_near_zero_sample_ratio:
        return (
            "throughput window has too many near-zero samples under active "
            f"publishers: {near_zero_count}/{len(samples)} at or below "
            f"{near_zero_msgs_per_sec:.1f} msg/s"
        )

    return None


def compute_stats(values: list[float]) -> dict[str, float]:
    """Compute summary statistics from a list of numeric values."""
    if not values:
        return {"mean": 0, "p50": 0, "p95": 0, "p99": 0, "max": 0, "min": 0, "count": 0}
    s = sorted(values)
    n = len(s)
    return {
        "mean": round(sum(s) / n, 2),
        "p50": round(s[n // 2], 2),
        "p95": round(s[int(n * 0.95)], 2),
        "p99": round(s[int(n * 0.99)], 2),
        "max": round(s[-1], 2),
        "min": round(s[0], 2),
        "count": n,
    }


async def collect_latency(
    client: AetherClient,
) -> dict[str, Any]:
    """Harvest raw latency samples from subscriber /status endpoints.

    Returns exact per-subscriber and cluster-wide percentiles computed from the
    merged raw sample set rather than averaged per-subscriber percentiles.
    """
    state = await client.get_state()
    subscriber_latencies: list[dict[str, Any]] = []
    all_samples_us: list[float] = []

    for sub in state.get("subscribers", []):
        host_port = sub.get("host_status_port")
        if host_port is None:
            continue

        status = await client.get_subscriber_status("localhost", host_port)
        samples_us = status.get("latency_samples_us", [])
        if not samples_us:
            continue

        sorted_samples_us = sorted(float(sample) for sample in samples_us)
        all_samples_us.extend(sorted_samples_us)
        subscriber_latencies.append(
            {
                "subscriber_id": sub.get("component_id"),
                "broker_id": sub.get("broker_id"),
                "sample_count": len(sorted_samples_us),
                "latency_us": _compute_percentiles(sorted_samples_us),
                "latency_samples_us": sorted_samples_us,
            }
        )

    if not all_samples_us:
        return {
            "subscribers": [],
            "aggregate": {"p50": 0, "p95": 0, "p99": 0, "sample_count": 0},
        }

    aggregate = _compute_percentiles(sorted(all_samples_us))
    aggregate["sample_count"] = len(all_samples_us)

    return {"subscribers": subscriber_latencies, "aggregate": aggregate}


def classify_latency_window(
    latency_data: dict[str, Any],
    *,
    expected_subscribers: int,
    min_samples_per_subscriber: int,
    processed_messages_delta: int,
    expected_messages: float,
    min_delivery_ratio: float,
) -> str | None:
    """Return an explicit invalidity reason when a latency window is not trustworthy."""
    subscriber_data = latency_data.get("subscribers", [])
    if len(subscriber_data) != expected_subscribers:
        return (
            "latency benchmark invalid: expected "
            f"{expected_subscribers} subscribers with samples, got "
            f"{len(subscriber_data)}"
        )

    undersampled = [
        f"{sub['subscriber_id']}:{sub['sample_count']}"
        for sub in subscriber_data
        if sub.get("sample_count", 0) < min_samples_per_subscriber
    ]
    if undersampled:
        return (
            "latency benchmark invalid: subscribers below minimum sample count "
            f"({min_samples_per_subscriber}): {undersampled}"
        )

    if expected_messages > 0:
        observed_ratio = processed_messages_delta / expected_messages
        if observed_ratio < min_delivery_ratio:
            return (
                "latency benchmark invalid: observed processed-message ratio "
                f"{observed_ratio:.2f} below unsaturated floor "
                f"{min_delivery_ratio:.2f} "
                f"(processed_delta={processed_messages_delta}, "
                f"expected_messages={expected_messages:.1f})"
            )

    return None


def _compute_percentiles(sorted_values: list[float]) -> dict[str, float]:
    """Compute exact percentile picks from an already-sorted sample list."""
    n = len(sorted_values)
    if n == 0:
        return {"p50": 0, "p95": 0, "p99": 0}
    return {
        "p50": round(sorted_values[n // 2], 1),
        "p95": round(sorted_values[int(n * 0.95)], 1),
        "p99": round(sorted_values[int(n * 0.99)], 1),
    }


async def collect_snapshot_rounds(
    client: Any,
    rounds: int,
    expected_brokers: int,
    *,
    poll_interval: float = 2.0,
    convergence_timeout: float = 120.0,
) -> list[dict[str, Any]]:
    """Poll broker /status directly to measure Chandy-Lamport coordination overhead.

    Pre-flight: wait until all expected_brokers have completed at least one
    snapshot.  This absorbs the startup race where the snapshot timer fires
    before gossip has converged (each broker initiates solo, producing N
    distinct snapshot_ids).  Only after every broker has a non-None timestamp
    do we baseline and start counting measured rounds.

    For each round: poll until all expected_brokers advance past their
    baseline, assert the CL invariant (all brokers share a single
    snapshot_id), then record coordination_ms.
    """
    # Pre-flight: wait for all expected_brokers to have completed at least one
    # snapshot (any UUID — coordinated or solo doesn't matter here).
    preflight_deadline = asyncio.get_event_loop().time() + convergence_timeout
    initial: list[dict[str, Any]] = []
    while asyncio.get_event_loop().time() < preflight_deadline:
        initial = await client.get_broker_snapshot_status()
        ready = [b for b in initial if b["timestamp"] is not None]
        if len(ready) >= expected_brokers:
            break
        await asyncio.sleep(poll_interval)
    else:
        have = sum(1 for b in initial if b["timestamp"] is not None)
        raise RuntimeError(
            f"pre-flight failed: {have}/{expected_brokers} brokers completed "
            f"a first snapshot within {convergence_timeout:.0f}s"
        )

    baselines: dict[int, float | None] = {
        b["broker_id"]: b["timestamp"] for b in initial
    }

    results: list[dict[str, Any]] = []

    for round_num in range(rounds):
        deadline = asyncio.get_event_loop().time() + convergence_timeout
        current: list[dict[str, Any]] = []
        completed: set[int] = set()

        while asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(poll_interval)
            current = await client.get_broker_snapshot_status()

            current_by_id = {b["broker_id"]: b for b in current}
            if len(current_by_id) < expected_brokers:
                continue

            completed = {
                bid
                for bid in current_by_id
                if bid in baselines
                and current_by_id[bid]["timestamp"] is not None
                and (
                    baselines[bid] is None
                    or current_by_id[bid]["timestamp"] > baselines[bid]
                )
            }
            if len(completed) >= expected_brokers:
                break
        else:
            raise RuntimeError(
                f"snapshot round {round_num + 1} timed out: "
                f"{len(completed)}/{expected_brokers} brokers completed "
                f"within {convergence_timeout:.0f}s"
            )

        current_by_id = {b["broker_id"]: b for b in current}

        # Assert the CL global snapshot invariant: one snapshot_id for the round.
        snapshot_ids = {
            current_by_id[bid]["snapshot_id"]
            for bid in completed
            if current_by_id[bid]["snapshot_id"] is not None
        }
        if len(snapshot_ids) != 1:
            raise RuntimeError(
                f"CL invariant violated in round {round_num + 1}: "
                f"expected 1 snapshot_id across {expected_brokers} brokers, "
                f"got {len(snapshot_ids)}: {sorted(snapshot_ids)}"
            )

        timestamps = [current_by_id[bid]["timestamp"] for bid in completed]
        coordination_ms = round((max(timestamps) - min(timestamps)) * 1000, 1)

        results.append(
            {
                "snapshot_id": next(iter(snapshot_ids)),
                "broker_ids": sorted(completed),
                "broker_count": len(completed),
                "coordination_ms": coordination_ms,
                "broker_timestamps": {
                    bid: current_by_id[bid]["timestamp"] for bid in sorted(completed)
                },
            }
        )

        baselines = {b["broker_id"]: b["timestamp"] for b in current}

    return results


async def collect_recovery_events(
    events: asyncio.Queue[dict[str, Any]],
    timeout: float = 60.0,
) -> dict[str, Any]:
    """Capture recovery event timestamps from the WebSocket event queue.

    Waits for BROKER_DECLARED_DEAD -> BROKER_RECOVERED -> SUBSCRIBER_RECONNECTED.
    Returns timing breakdown.
    """
    t_dead: float | None = None
    t_recovery_started: float | None = None
    t_recovered: float | None = None
    recovery_path_started: str | None = None
    recovery_path_recovered: str | None = None
    reconnect_times: list[float] = []

    deadline = asyncio.get_event_loop().time() + timeout

    while asyncio.get_event_loop().time() < deadline:
        try:
            event = await asyncio.wait_for(events.get(), timeout=2.0)
        except asyncio.TimeoutError:
            if t_recovered is not None:
                break
            continue

        etype = event.get("type", "")
        ts = event.get("timestamp", 0)

        if etype == "broker_declared_dead" and t_dead is None:
            t_dead = ts
        elif etype == "broker_recovery_started" and t_recovery_started is None:
            t_recovery_started = ts
            recovery_path_started = event.get("data", {}).get("recovery_path")
        elif etype == "broker_recovered" and t_recovered is None:
            t_recovered = ts
            recovery_path_recovered = event.get("data", {}).get("recovery_path")
        elif etype == "subscriber_reconnected":
            reconnect_times.append(ts)

    recovery_path = recovery_path_started or recovery_path_recovered
    result: dict[str, Any] = {
        "recovery_path": recovery_path,
        "subscriber_reconnect_count": len(reconnect_times),
    }

    if t_dead is not None and t_recovery_started is not None:
        result["failover_start_latency_s"] = round(
            t_recovery_started - t_dead, 3
        )
    if t_recovery_started is not None and t_recovered is not None:
        result["recovery_execution_time_s"] = round(
            t_recovered - t_recovery_started, 3
        )
    if t_dead is not None and t_recovered is not None:
        result["recovery_time_s"] = round(t_recovered - t_dead, 3)
    if t_dead is not None and reconnect_times:
        result["subscriber_reconnect_time_s"] = round(max(reconnect_times) - t_dead, 3)

    missing: list[str] = []
    if t_dead is None:
        missing.append("broker_declared_dead")
    if t_recovery_started is None:
        missing.append("broker_recovery_started")
    if t_recovered is None:
        missing.append("broker_recovered")
    if not reconnect_times:
        missing.append("subscriber_reconnected")
    if missing:
        raise RuntimeError(
            "incomplete recovery event timeline: missing " + ", ".join(missing)
        )

    ordering_errors: list[str] = []
    if (
        t_dead is not None
        and t_recovery_started is not None
        and t_recovery_started < t_dead
    ):
        ordering_errors.append("broker_recovery_started before broker_declared_dead")
    if (
        t_recovery_started is not None
        and t_recovered is not None
        and t_recovered < t_recovery_started
    ):
        ordering_errors.append("broker_recovered before broker_recovery_started")
    if (
        t_recovery_started is not None
        and any(ts < t_recovery_started for ts in reconnect_times)
    ):
        ordering_errors.append(
            "subscriber_reconnected before broker_recovery_started"
        )
    if (
        recovery_path_started is not None
        and recovery_path_recovered is not None
        and recovery_path_started != recovery_path_recovered
    ):
        ordering_errors.append(
            "recovery path changed during trial "
            f"({recovery_path_started} -> {recovery_path_recovered})"
        )
    if ordering_errors:
        raise RuntimeError(
            "invalid recovery event timeline: " + ", ".join(ordering_errors)
        )

    return result
