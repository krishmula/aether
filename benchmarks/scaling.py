"""Scaling / saturation benchmark — measures throughput as publishers ramp up."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from benchmarks.client import AetherClient
from benchmarks.collectors import classify_throughput_window, collect_throughput
from benchmarks.config import BenchmarkConfig

logger = logging.getLogger(__name__)


def _find_saturation_point(
    timeline: list[dict[str, Any]],
    *,
    gain_threshold_pct: float,
    consecutive_steps: int,
) -> dict[str, Any] | None:
    """Return saturation point after consecutive low-gain steps."""
    low_gain_streak = 0

    for i in range(1, len(timeline)):
        prev_rate = timeline[i - 1]["mean_msgs_per_sec"]
        curr_rate = timeline[i]["mean_msgs_per_sec"]
        if prev_rate <= 0:
            low_gain_streak = 0
            continue

        pct_increase = (curr_rate - prev_rate) / prev_rate * 100
        if pct_increase < gain_threshold_pct:
            low_gain_streak += 1
            if low_gain_streak >= consecutive_steps:
                return {
                    "publishers": timeline[i]["publishers"],
                    "throughput": curr_rate,
                    "pct_increase_from_prev": round(pct_increase, 1),
                    "gain_threshold_pct": gain_threshold_pct,
                    "consecutive_low_gain_steps": low_gain_streak,
                }
        else:
            low_gain_streak = 0

    return None


async def _measure_scaling_step(
    client: AetherClient,
    cfg: BenchmarkConfig,
    *,
    publishers: int,
) -> list[dict[str, Any]]:
    expected_topology = await client.get_topology_fingerprint()
    metrics = await client.get_metrics()
    client.assert_valid_metrics_snapshot(metrics, stage="scaling measurement")
    expected_generation = metrics["topology_generation"]

    samples = await collect_throughput(
        client,
        cfg.scaling_step_seconds,
        cfg.poll_interval,
        expected_generation=expected_generation,
        stage="scaling measurement",
    )
    await client.assert_topology_matches(
        expected_topology,
        stage="scaling measurement",
    )

    invalid_reason = classify_throughput_window(
        samples,
        active_publishers=publishers,
        near_zero_msgs_per_sec=cfg.near_zero_msgs_per_sec,
        max_near_zero_sample_ratio=cfg.max_near_zero_sample_ratio,
    )
    if invalid_reason is not None:
        raise RuntimeError(invalid_reason)

    return samples


async def run(cfg: BenchmarkConfig) -> dict[str, Any]:
    """Run the scaling benchmark.

    Starts with a base topology, then adds one publisher every
    scaling_step_seconds. Measures throughput continuously and identifies
    the saturation point.
    """
    client = AetherClient(cfg)
    timeline: list[dict[str, Any]] = []
    publisher_additions: list[dict[str, Any]] = []

    try:
        await client.cleanup()
        await asyncio.sleep(2)

        logger.info(
            "scaling: seeding %d brokers, %d publishers, %d subscribers",
            cfg.scaling_brokers,
            cfg.scaling_initial_publishers,
            cfg.scaling_subscribers,
        )
        await client.seed_topology(
            cfg.scaling_brokers,
            cfg.scaling_initial_publishers,
            cfg.scaling_subscribers,
        )
        try:
            await client.wait_all_running(timeout=60)
        except RuntimeError as exc:
            raise RuntimeError(
                "scaling startup timeout before first measurement: "
                f"{exc}"
            ) from exc

        logger.info("scaling: warmup (%ds)...", cfg.warmup_seconds)
        await asyncio.sleep(cfg.warmup_seconds)

        current_publishers = cfg.scaling_initial_publishers
        start = time.monotonic()

        # Get broker IDs for new publishers.
        state = await client.get_state()
        broker_ids = [b["component_id"] for b in state.get("brokers", [])]

        while current_publishers <= cfg.scaling_max_publishers:
            logger.info(
                "scaling: measuring with %d publishers (%ds window)...",
                current_publishers,
                cfg.scaling_step_seconds,
            )
            max_attempts = 1 + cfg.window_retry_limit
            samples: list[dict[str, Any]] | None = None
            attempt_used = 0
            last_error: RuntimeError | None = None

            for attempt in range(1, max_attempts + 1):
                attempt_used = attempt
                try:
                    samples = await _measure_scaling_step(
                        client,
                        cfg,
                        publishers=current_publishers,
                    )
                    break
                except RuntimeError as exc:
                    last_error = exc
                    if attempt >= max_attempts:
                        raise RuntimeError(
                            "scaling step invalid after retry budget at "
                            f"{current_publishers} publishers: {exc}"
                        ) from exc

                    logger.warning(
                        "scaling step invalid, retrying after %.1fs: %s",
                        cfg.scaling_retry_backoff_seconds,
                        exc,
                    )
                    await asyncio.sleep(cfg.scaling_retry_backoff_seconds)

            if samples is None:
                raise RuntimeError(
                    "scaling step failed without samples"
                    if last_error is None
                    else str(last_error)
                )

            rates = [s["msgs_per_sec"] for s in samples]
            if rates:
                mean_rate = sum(rates) / len(rates)
            else:
                mean_rate = 0

            step_entry = {
                "elapsed": round(time.monotonic() - start, 1),
                "publishers": current_publishers,
                "mean_msgs_per_sec": round(mean_rate, 1),
                "attempts": attempt_used,
                "samples": samples,
            }
            timeline.append(step_entry)
            logger.info(
                "  %d publishers → %.1f msg/s",
                current_publishers,
                mean_rate,
            )

            # Check if we've reached the max.
            if current_publishers >= cfg.scaling_max_publishers:
                break

            # Add one more publisher.
            current_publishers += 1
            try:
                await client.add_publisher(broker_ids=broker_ids)
                await client.wait_all_running(timeout=60)
                publisher_additions.append(
                    {
                        "elapsed": round(time.monotonic() - start, 1),
                        "publisher_count": current_publishers,
                    }
                )
                # Brief pause for the new publisher to start.
                await asyncio.sleep(3)
            except Exception as exc:
                raise RuntimeError(
                    "scaling publisher startup timeout at "
                    f"{current_publishers} publishers: {exc}"
                ) from exc

    finally:
        await client.cleanup()
        await client.close()

    saturation_point = _find_saturation_point(
        timeline,
        gain_threshold_pct=cfg.scaling_saturation_gain_threshold_pct,
        consecutive_steps=cfg.scaling_saturation_consecutive_steps,
    )

    output = {
        "benchmark": "scaling",
        "timestamp": time.time(),
        "config": {
            "brokers": cfg.scaling_brokers,
            "initial_publishers": cfg.scaling_initial_publishers,
            "max_publishers": cfg.scaling_max_publishers,
            "subscribers": cfg.scaling_subscribers,
            "step_seconds": cfg.scaling_step_seconds,
        },
        "timeline": timeline,
        "publisher_additions": publisher_additions,
        "saturation_point": saturation_point,
    }

    out_path = cfg.results_dir / "scaling.json"
    out_path.write_text(json.dumps(output, indent=2))
    logger.info("scaling results written to %s", out_path)

    return output
