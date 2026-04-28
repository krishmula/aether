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


async def collect_snapshot_events(
    events: asyncio.Queue[dict[str, Any]],
    rounds: int = 3,
    timeout_per_round: float = 30.0,
    expected_brokers: int | None = None,
) -> list[dict[str, Any]]:
    """Capture SNAPSHOT_COMPLETE events and measure coordination overhead.

    Returns per-round timing: time between first and last broker completing.
    """
    results: list[dict[str, Any]] = []
    round_events: list[dict[str, Any]] = []
    current_snapshot_id: str | None = None

    while len(results) < rounds:
        try:
            event = await asyncio.wait_for(events.get(), timeout=timeout_per_round)
        except asyncio.TimeoutError:
            if round_events:
                _append_snapshot_round(
                    results,
                    round_events,
                    expected_brokers=expected_brokers,
                )
                round_events = []
            break

        if event.get("type") != "snapshot_complete":
            continue

        snapshot_id = str(event.get("data", {}).get("snapshot_id") or "")
        if current_snapshot_id is None:
            current_snapshot_id = snapshot_id
        elif snapshot_id != current_snapshot_id:
            _append_snapshot_round(
                results,
                round_events,
                expected_brokers=expected_brokers,
            )
            round_events = []
            current_snapshot_id = snapshot_id

        round_events.append(event)

    if round_events and len(results) < rounds:
        _append_snapshot_round(
            results,
            round_events,
            expected_brokers=expected_brokers,
        )

    if len(results) < rounds:
        raise RuntimeError(
            "incomplete snapshot rounds: "
            f"expected {rounds}, got {len(results)}"
        )

    return results


def classify_snapshot_round(
    events: list[dict[str, Any]],
    *,
    expected_brokers: int | None,
) -> str | None:
    """Return an explicit invalidity reason when a snapshot round is not coherent."""
    if not events:
        return "snapshot round was empty"

    snapshot_ids = {
        str(event.get("data", {}).get("snapshot_id") or "") for event in events
    }
    if "" in snapshot_ids:
        return "snapshot round missing snapshot_id"
    if len(snapshot_ids) != 1:
        return (
            "snapshot round mixed snapshot ids: "
            + ", ".join(sorted(snapshot_ids))
        )

    broker_ids = [event.get("data", {}).get("broker_id") for event in events]
    if any(broker_id is None for broker_id in broker_ids):
        return "snapshot round missing broker_id"

    unique_broker_ids = {int(broker_id) for broker_id in broker_ids}
    if len(unique_broker_ids) != len(broker_ids):
        return "snapshot round contains duplicate broker completions"
    if expected_brokers is not None and len(unique_broker_ids) != expected_brokers:
        return (
            f"snapshot round expected {expected_brokers} brokers, got "
            f"{len(unique_broker_ids)}"
        )

    missing_timestamps = [
        str(int(broker_id))
        for broker_id, event in zip(broker_ids, events, strict=True)
        if float(event.get("data", {}).get("snapshot_timestamp") or 0) <= 0
    ]
    if missing_timestamps:
        return (
            "snapshot round missing snapshot_timestamp for brokers "
            + ", ".join(missing_timestamps)
        )

    return None


def _summarize_snapshot_round(events: list[dict[str, Any]]) -> dict[str, Any]:
    # Use each broker's own snapshot_timestamp (when it actually completed the
    # snapshot) rather than the WebSocket event's timestamp (when the orchestrator
    # emitted the event). The orchestrator emits all events in the same for-loop
    # so event timestamps are indistinguishable; broker timestamps reflect real
    # per-broker completion times.
    timestamps = [float(e.get("data", {}).get("snapshot_timestamp", 0)) for e in events]
    if not timestamps:
        return {"broker_count": 0, "coordination_ms": 0}
    return {
        "snapshot_id": events[0].get("data", {}).get("snapshot_id"),
        "broker_ids": sorted(
            int(e.get("data", {}).get("broker_id")) for e in events
        ),
        "broker_count": len(events),
        "coordination_ms": round((max(timestamps) - min(timestamps)) * 1000, 1),
    }


def _append_snapshot_round(
    results: list[dict[str, Any]],
    round_events: list[dict[str, Any]],
    *,
    expected_brokers: int | None,
) -> None:
    reason = classify_snapshot_round(
        round_events,
        expected_brokers=expected_brokers,
    )
    if reason is None:
        results.append(_summarize_snapshot_round(round_events))
        return

    if _can_skip_initial_partial_snapshot_round(
        results,
        round_events,
        expected_brokers=expected_brokers,
    ):
        return

    raise RuntimeError("invalid snapshot round: " + reason)


def _can_skip_initial_partial_snapshot_round(
    results: list[dict[str, Any]],
    round_events: list[dict[str, Any]],
    *,
    expected_brokers: int | None,
) -> bool:
    """Allow the very first observed round to be partial if we attached mid-cycle."""
    if results or expected_brokers is None:
        return False

    broker_ids = [event.get("data", {}).get("broker_id") for event in round_events]
    if any(broker_id is None for broker_id in broker_ids):
        return False

    unique_broker_ids = {int(broker_id) for broker_id in broker_ids}
    return (
        0 < len(unique_broker_ids) < expected_brokers
        and len(unique_broker_ids) == len(broker_ids)
    )
