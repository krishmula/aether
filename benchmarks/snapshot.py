"""Snapshot coordination benchmark — measures Chandy-Lamport snapshot overhead."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from benchmarks.client import AetherClient
from benchmarks.collectors import collect_snapshot_rounds
from benchmarks.config import BenchmarkConfig

logger = logging.getLogger(__name__)


async def run(cfg: BenchmarkConfig) -> dict[str, Any]:
    """Run the snapshot coordination benchmark.

    For each broker count, seed a topology, poll broker /status endpoints
    directly to observe snapshot rounds, and measure coordination overhead
    (spread between first and last broker completing each round).
    """
    client = AetherClient(cfg)
    results: list[dict[str, Any]] = []

    try:
        for n_brokers in cfg.snapshot_broker_counts:
            logger.info("snapshot: %d brokers", n_brokers)

            await client.cleanup()
            await asyncio.sleep(2)

            n_subscribers = max(3, n_brokers)
            await client.seed_topology(n_brokers, 2, n_subscribers)

            try:
                await client.wait_all_running(timeout=60)
            except RuntimeError:
                logger.warning(
                    "timeout waiting for components — skipping %d brokers",
                    n_brokers,
                )
                results.append(
                    {"brokers": n_brokers, "status": "timeout", "rounds": []}
                )
                continue

            logger.info("  warmup (%ds)...", cfg.warmup_seconds)
            await asyncio.sleep(cfg.warmup_seconds)

            logger.info(
                "  observing %d snapshot rounds...", cfg.snapshot_rounds
            )
            try:
                rounds = await collect_snapshot_rounds(
                    client,
                    rounds=cfg.snapshot_rounds,
                    expected_brokers=n_brokers,
                    poll_interval=cfg.snapshot_poll_interval,
                    convergence_timeout=cfg.snapshot_convergence_timeout,
                )
            except RuntimeError as exc:
                logger.warning(
                    "  invalid snapshot capture for %d brokers: %s",
                    n_brokers,
                    exc,
                )
                results.append(
                    {
                        "brokers": n_brokers,
                        "subscribers": n_subscribers,
                        "status": "invalid",
                        "invalid_reason": str(exc),
                        "rounds": [],
                    }
                )
                continue

            coordination_times = [r["coordination_ms"] for r in rounds]
            mean_ms = sum(coordination_times) / len(coordination_times)
            entry = {
                "brokers": n_brokers,
                "subscribers": n_subscribers,
                "status": "ok",
                "rounds": rounds,
                "summary": {
                    "mean_ms": round(mean_ms, 1),
                    "min_ms": round(min(coordination_times), 1),
                    "max_ms": round(max(coordination_times), 1),
                    "rounds_captured": len(rounds),
                },
            }
            results.append(entry)
            logger.info(
                "  result: mean=%.1fms, min=%.1fms, max=%.1fms (%d rounds)",
                mean_ms,
                min(coordination_times),
                max(coordination_times),
                len(rounds),
            )

    finally:
        await client.cleanup()
        await client.close()

    output = {
        "benchmark": "snapshot",
        "timestamp": time.time(),
        "config": {
            "broker_counts": cfg.snapshot_broker_counts,
            "snapshot_rounds": cfg.snapshot_rounds,
        },
        "results": results,
    }

    out_path = cfg.results_dir / "snapshot.json"
    out_path.write_text(json.dumps(output, indent=2))
    logger.info("snapshot results written to %s", out_path)

    return output
