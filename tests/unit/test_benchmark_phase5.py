"""Unit tests for Phase 5 latency benchmark repairs."""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from benchmarks.config import BenchmarkConfig


def _metrics_snapshot(total: int) -> dict[str, float | int]:
    return {
        "topology_generation": 9,
        "fetched_at": time.time(),
        "sample_interval_seconds": 1.0,
        "total_messages_processed": total,
    }


def _fake_client() -> MagicMock:
    client = MagicMock()
    client.cleanup = AsyncMock()
    client.close = AsyncMock()
    client.seed_topology = AsyncMock(return_value={"seeded": 0})
    client.wait_all_running = AsyncMock()
    client.get_topology_fingerprint = AsyncMock(
        return_value={"brokers": {1: "running"}}
    )
    client.wait_latency_ready = AsyncMock()
    client.assert_topology_matches = AsyncMock()
    client.reset_all_subscriber_latency_samples = AsyncMock()
    client.get_metrics = AsyncMock(
        side_effect=[_metrics_snapshot(100), _metrics_snapshot(120)]
    )
    client.assert_valid_metrics_snapshot = MagicMock()
    return client


class TestLatencyPhase5(unittest.IsolatedAsyncioTestCase):
    async def test_run_fails_fast_when_post_reset_window_is_underserved(self) -> None:
        from benchmarks import latency

        latency_data = {
            "subscribers": [
                {"subscriber_id": 1, "sample_count": 25},
                {"subscriber_id": 2, "sample_count": 25},
                {"subscriber_id": 3, "sample_count": 25},
            ],
            "aggregate": {
                "p50": 1000.0,
                "p95": 1500.0,
                "p99": 2000.0,
                "sample_count": 75,
            },
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            cfg = BenchmarkConfig(
                warmup_seconds=0,
                measurement_seconds=10,
                results_dir=Path(tmpdir),
                latency_publish_interval=0.05,
                latency_publishers=2,
                latency_subscribers=3,
                latency_min_samples_per_subscriber=20,
                latency_min_delivery_ratio=0.7,
            )
            client = _fake_client()

            with (
                patch("benchmarks.latency.AetherClient", return_value=client),
                patch(
                    "benchmarks.latency.collect_latency",
                    new=AsyncMock(return_value=latency_data),
                ),
                patch("benchmarks.latency.asyncio.sleep", new=AsyncMock()),
            ):
                with self.assertRaisesRegex(
                    RuntimeError,
                    "processed-message ratio",
                ):
                    await latency.run(cfg)

    async def test_run_succeeds_for_valid_post_reset_window(self) -> None:
        from benchmarks import latency

        latency_data = {
            "subscribers": [
                {"subscriber_id": 1, "sample_count": 25},
                {"subscriber_id": 2, "sample_count": 24},
                {"subscriber_id": 3, "sample_count": 23},
            ],
            "aggregate": {
                "p50": 900.0,
                "p95": 1400.0,
                "p99": 1800.0,
                "sample_count": 72,
            },
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            cfg = BenchmarkConfig(
                warmup_seconds=0,
                measurement_seconds=2,
                results_dir=Path(tmpdir),
                latency_publish_interval=0.05,
                latency_publishers=2,
                latency_subscribers=3,
                latency_min_samples_per_subscriber=20,
                latency_min_delivery_ratio=0.4,
            )
            client = _fake_client()
            client.get_metrics = AsyncMock(
                side_effect=[_metrics_snapshot(100), _metrics_snapshot(150)]
            )

            with (
                patch("benchmarks.latency.AetherClient", return_value=client),
                patch(
                    "benchmarks.latency.collect_latency",
                    new=AsyncMock(return_value=latency_data),
                ),
                patch("benchmarks.latency.asyncio.sleep", new=AsyncMock()),
            ):
                output = await latency.run(cfg)

        self.assertEqual(output["benchmark"], "latency")
        self.assertEqual(output["results"]["aggregate"]["sample_count"], 72)
