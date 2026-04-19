"""Throughput benchmark — measures msgs/sec across broker x publisher configurations."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from benchmarks.client import AetherClient
from benchmarks.collectors import collect_throughput, compute_stats
from benchmarks.config import BenchmarkConfig

logger = logging.getLogger(__name__)


async def run(cfg: BenchmarkConfig) -> dict[str, Any]:
    """Run the throughput benchmark matrix.

    For each (broker_count, publisher_count), seed a topology, wait for
    warmup, then measure throughput over the measurement window.
    """
    client = AetherClient(cfg)
    results: list[dict[str, Any]] = []

    try:
        for n_brokers in cfg.broker_counts:
            for n_publishers in cfg.publisher_counts:
                logger.info(
                    "throughput: %d brokers, %d publishers", n_brokers, n_publishers
                )

                # Clean slate.
                await client.cleanup()
                await asyncio.sleep(2)

                # Seed topology.
                n_subscribers = max(3, n_brokers)
                await client.seed_topology(n_brokers, n_publishers, n_subscribers)

                try:
                    await client.wait_all_running(timeout=60)
                except RuntimeError:
                    logger.warning(
                        "timeout waiting for components — skipping %d/%d",
                        n_brokers,
                        n_publishers,
                    )
                    results.append(
                        {
                            "brokers": n_brokers,
                            "publishers": n_publishers,
                            "status": "timeout",
                        }
                    )
                    continue

                # Warmup.
                logger.info("  warmup (%ds)...", cfg.warmup_seconds)
                await asyncio.sleep(cfg.warmup_seconds)

                # Measure.
                logger.info("  measuring (%ds)...", cfg.measurement_seconds)
                samples = await collect_throughput(
                    client, cfg.measurement_seconds, cfg.poll_interval
                )

                rates = [s["msgs_per_sec"] for s in samples]
                stats = compute_stats(rates)

                entry = {
                    "brokers": n_brokers,
                    "publishers": n_publishers,
                    "subscribers": n_subscribers,
                    "status": "ok",
                    "stats": stats,
                    "samples": samples,
                }
                results.append(entry)
                logger.info(
                    "  result: mean=%.1f msg/s, p95=%.1f msg/s",
                    stats["mean"],
                    stats["p95"],
                )

    finally:
        await client.cleanup()
        await client.close()

    output = {
        "benchmark": "throughput",
        "timestamp": time.time(),
        "config": {
            "broker_counts": cfg.broker_counts,
            "publisher_counts": cfg.publisher_counts,
            "warmup_seconds": cfg.warmup_seconds,
            "measurement_seconds": cfg.measurement_seconds,
        },
        "results": results,
    }

    out_path = cfg.results_dir / "throughput.json"
    out_path.write_text(json.dumps(output, indent=2))
    logger.info("throughput results written to %s", out_path)

    return output
