"""Throughput benchmark — measures msgs/sec across broker x publisher configurations."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from benchmarks.client import AetherClient
from benchmarks.collectors import (
    classify_throughput_window,
    collect_throughput,
    compute_stats,
)
from benchmarks.config import BenchmarkConfig

logger = logging.getLogger(__name__)


async def _measure_throughput_attempt(
    client: AetherClient,
    cfg: BenchmarkConfig,
    *,
    n_publishers: int,
) -> list[dict[str, Any]]:
    expected_topology = await client.get_topology_fingerprint()
    metrics = await client.get_metrics()
    client.assert_valid_metrics_snapshot(metrics, stage="throughput warmup")
    expected_generation = metrics["topology_generation"]

    logger.info("  warmup (%ds)...", cfg.warmup_seconds)
    await asyncio.sleep(cfg.warmup_seconds)
    await client.assert_topology_matches(expected_topology, stage="throughput warmup")
    await client.assert_metrics_generation(
        expected_generation,
        stage="throughput warmup",
    )

    logger.info("  measuring (%ds)...", cfg.measurement_seconds)
    samples = await collect_throughput(
        client,
        cfg.measurement_seconds,
        cfg.poll_interval,
        expected_generation=expected_generation,
        stage="throughput measurement",
    )
    await client.assert_topology_matches(
        expected_topology,
        stage="throughput measurement",
    )

    invalid_reason = classify_throughput_window(
        samples,
        active_publishers=n_publishers,
        near_zero_msgs_per_sec=cfg.near_zero_msgs_per_sec,
        max_near_zero_sample_ratio=cfg.max_near_zero_sample_ratio,
    )
    if invalid_reason is not None:
        raise RuntimeError(invalid_reason)

    return samples


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
                n_subscribers = max(3, n_brokers)
                max_attempts = 1 + cfg.window_retry_limit
                last_error: RuntimeError | None = None

                for attempt in range(1, max_attempts + 1):
                    logger.info(
                        "  attempt %d/%d for %d broker(s), %d publisher(s)",
                        attempt,
                        max_attempts,
                        n_brokers,
                        n_publishers,
                    )
                    await client.cleanup()
                    await asyncio.sleep(2)
                    await client.seed_topology(n_brokers, n_publishers, n_subscribers)

                    try:
                        await client.wait_all_running(timeout=60)
                        samples = await _measure_throughput_attempt(
                            client,
                            cfg,
                            n_publishers=n_publishers,
                        )
                    except RuntimeError as exc:
                        last_error = exc
                        if attempt >= max_attempts:
                            status = (
                                "timeout"
                                if "RUNNING within" in str(exc)
                                else "invalid"
                            )
                            results.append(
                                {
                                    "brokers": n_brokers,
                                    "publishers": n_publishers,
                                    "subscribers": n_subscribers,
                                    "status": status,
                                    "attempts": attempt,
                                    "invalid_reason": str(exc),
                                }
                            )
                            logger.warning(
                                "  %s after %d attempt(s): %s",
                                status,
                                attempt,
                                exc,
                            )
                            break

                        logger.warning(
                            "  invalid throughput window, retrying after %.1fs: %s",
                            cfg.throughput_retry_backoff_seconds,
                            exc,
                        )
                        await asyncio.sleep(cfg.throughput_retry_backoff_seconds)
                        continue

                    rates = [s["msgs_per_sec"] for s in samples]
                    stats = compute_stats(rates)
                    entry = {
                        "brokers": n_brokers,
                        "publishers": n_publishers,
                        "subscribers": n_subscribers,
                        "status": "ok",
                        "attempts": attempt,
                        "stats": stats,
                        "samples": samples,
                    }
                    results.append(entry)
                    logger.info(
                        "  result: mean=%.1f msg/s, p95=%.1f msg/s",
                        stats["mean"],
                        stats["p95"],
                    )
                    break
                else:
                    if last_error is not None:
                        raise last_error

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
