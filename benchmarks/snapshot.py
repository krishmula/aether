"""Snapshot coordination benchmark — measures Chandy-Lamport snapshot overhead."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from benchmarks.client import AetherClient, event_stream
from benchmarks.collectors import collect_snapshot_events
from benchmarks.config import BenchmarkConfig

logger = logging.getLogger(__name__)


async def run(cfg: BenchmarkConfig) -> dict[str, Any]:
    """Run the snapshot coordination benchmark.

    For each broker count, seed a topology, observe SNAPSHOT_COMPLETE events,
    and measure the coordination overhead (time between first and last broker).
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

            # Wait for system to stabilize and snapshots to start firing.
            logger.info("  warmup (%ds)...", cfg.warmup_seconds)
            await asyncio.sleep(cfg.warmup_seconds)

            # Collect snapshot events via WebSocket.
            logger.info(
                "  observing %d snapshot rounds...", cfg.snapshot_rounds
            )
            try:
                async with event_stream(cfg) as events:
                    rounds = await collect_snapshot_events(
                        events,
                        rounds=cfg.snapshot_rounds,
                        timeout_per_round=cfg.snapshot_timeout_per_round,
                        expected_brokers=n_brokers,
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
            if coordination_times:
                mean_ms = sum(coordination_times) / len(coordination_times)
                min_ms = min(coordination_times)
                max_ms = max(coordination_times)
            else:
                mean_ms = min_ms = max_ms = 0

            entry = {
                "brokers": n_brokers,
                "subscribers": n_subscribers,
                "status": "ok",
                "rounds": rounds,
                "summary": {
                    "mean_ms": round(mean_ms, 1),
                    "min_ms": round(min_ms, 1),
                    "max_ms": round(max_ms, 1),
                    "rounds_captured": len(rounds),
                },
            }
            results.append(entry)
            logger.info(
                "  result: mean=%.1fms, min=%.1fms, max=%.1fms (%d rounds)",
                mean_ms,
                min_ms,
                max_ms,
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
