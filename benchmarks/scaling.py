"""Scaling / saturation benchmark — measures throughput as publishers ramp up."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from benchmarks.client import AetherClient
from benchmarks.collectors import collect_throughput
from benchmarks.config import BenchmarkConfig

logger = logging.getLogger(__name__)


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
        await client.wait_all_running(timeout=60)

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

            # Measure throughput for one step.
            samples = await collect_throughput(
                client, cfg.scaling_step_seconds, cfg.poll_interval
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
                publisher_additions.append(
                    {
                        "elapsed": round(time.monotonic() - start, 1),
                        "publisher_count": current_publishers,
                    }
                )
                # Brief pause for the new publisher to start.
                await asyncio.sleep(3)
            except Exception as exc:
                logger.warning("failed to add publisher: %s", exc)
                break

    finally:
        await client.cleanup()
        await client.close()

    # Find saturation point: where adding a publisher increases throughput < 5%.
    saturation_point: dict[str, Any] | None = None
    for i in range(1, len(timeline)):
        prev_rate = timeline[i - 1]["mean_msgs_per_sec"]
        curr_rate = timeline[i]["mean_msgs_per_sec"]
        if prev_rate > 0:
            pct_increase = (curr_rate - prev_rate) / prev_rate * 100
            if pct_increase < 5.0:
                saturation_point = {
                    "publishers": timeline[i]["publishers"],
                    "throughput": curr_rate,
                    "pct_increase_from_prev": round(pct_increase, 1),
                }
                break

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
