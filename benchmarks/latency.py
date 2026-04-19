"""Latency benchmark — measures end-to-end delivery latency percentiles."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from benchmarks.client import AetherClient
from benchmarks.collectors import classify_latency_window, collect_latency
from benchmarks.config import BenchmarkConfig

logger = logging.getLogger(__name__)


async def run(cfg: BenchmarkConfig) -> dict[str, Any]:
    """Run the latency benchmark.

    Seeds a standard topology, waits until traffic and subscriber sampling are
    stable, clears all subscriber latency buffers, then measures end-to-end
    delivery latency over the timed measurement window only.
    """
    client = AetherClient(cfg)

    try:
        await client.cleanup()
        await asyncio.sleep(2)

        logger.info(
            "latency: seeding %d brokers, %d publishers, %d subscribers "
            "(publish_interval=%.3fs to keep broker unsaturated)",
            cfg.latency_brokers,
            cfg.latency_publishers,
            cfg.latency_subscribers,
            cfg.latency_publish_interval,
        )
        await client.seed_topology(
            cfg.latency_brokers,
            cfg.latency_publishers,
            cfg.latency_subscribers,
            publish_interval=cfg.latency_publish_interval,
            publishers_last=True,
        )
        await client.wait_all_running(timeout=60)
        expected_topology = await client.get_topology_fingerprint()

        logger.info(
            "latency: waiting for measurement readiness "
            "(expected_subscribers=%d, min_samples_per_subscriber=%d, stable_polls=%d)",
            cfg.latency_subscribers,
            cfg.latency_min_samples_per_subscriber,
            cfg.latency_ready_consecutive_polls,
        )
        await client.wait_latency_ready(
            expected_subscribers=cfg.latency_subscribers,
            min_samples_per_subscriber=cfg.latency_min_samples_per_subscriber,
            stable_polls=cfg.latency_ready_consecutive_polls,
            timeout=cfg.latency_ready_timeout_seconds,
            poll_interval=cfg.latency_ready_poll_interval,
        )
        await client.assert_topology_matches(
            expected_topology,
            stage="latency readiness",
        )

        logger.info("latency: warmup (%ds)...", cfg.warmup_seconds)
        await asyncio.sleep(cfg.warmup_seconds)
        await client.assert_topology_matches(
            expected_topology,
            stage="latency warmup",
        )

        logger.info("latency: resetting subscriber latency buffers...")
        await client.reset_all_subscriber_latency_samples(
            expected_subscribers=cfg.latency_subscribers
        )
        metrics_before = await client.get_metrics()
        client.assert_valid_metrics_snapshot(
            metrics_before,
            stage="latency measurement start",
        )
        measurement_generation = metrics_before["topology_generation"]

        logger.info("latency: measuring for %ds...", cfg.measurement_seconds)
        await asyncio.sleep(cfg.measurement_seconds)
        metrics_after = await client.get_metrics()
        client.assert_valid_metrics_snapshot(
            metrics_after,
            stage="latency measurement end",
            expected_generation=measurement_generation,
        )
        await client.assert_topology_matches(
            expected_topology,
            stage="latency measurement",
        )

        # Harvest latency data from subscribers.
        latency_data = await collect_latency(client)
        processed_messages_delta = (
            int(metrics_after.get("total_messages_processed", 0))
            - int(metrics_before.get("total_messages_processed", 0))
        )
        expected_messages = (
            cfg.latency_publishers
            * cfg.measurement_seconds
            / cfg.latency_publish_interval
            if cfg.latency_publish_interval > 0
            else 0.0
        )
        invalid_reason = classify_latency_window(
            latency_data,
            expected_subscribers=cfg.latency_subscribers,
            min_samples_per_subscriber=cfg.latency_min_samples_per_subscriber,
            processed_messages_delta=processed_messages_delta,
            expected_messages=expected_messages,
            min_delivery_ratio=cfg.latency_min_delivery_ratio,
        )
        if invalid_reason is not None:
            raise RuntimeError(invalid_reason)

        agg = latency_data.get("aggregate", {})
        logger.info(
            "latency: p50=%.1fus  p95=%.1fus  p99=%.1fus  samples=%d",
            agg.get("p50", 0),
            agg.get("p95", 0),
            agg.get("p99", 0),
            agg.get("sample_count", 0),
        )

    finally:
        await client.cleanup()
        await client.close()

    output = {
        "benchmark": "latency",
        "timestamp": time.time(),
        "config": {
            "brokers": cfg.latency_brokers,
            "publishers": cfg.latency_publishers,
            "subscribers": cfg.latency_subscribers,
            "publish_interval": cfg.latency_publish_interval,
            "warmup_seconds": cfg.warmup_seconds,
            "measurement_seconds": cfg.measurement_seconds,
            "ready_timeout_seconds": cfg.latency_ready_timeout_seconds,
            "ready_poll_interval": cfg.latency_ready_poll_interval,
            "ready_consecutive_polls": cfg.latency_ready_consecutive_polls,
            "min_samples_per_subscriber": cfg.latency_min_samples_per_subscriber,
        },
        "results": latency_data,
    }

    out_path = cfg.results_dir / "latency.json"
    out_path.write_text(json.dumps(output, indent=2))
    logger.info("latency results written to %s", out_path)

    return output
