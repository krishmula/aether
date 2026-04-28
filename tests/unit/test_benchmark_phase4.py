"""Unit tests for Phase 4 throughput and scaling benchmark repairs."""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from benchmarks.config import BenchmarkConfig
from benchmarks.scaling import _find_saturation_point


def _metrics_snapshot() -> dict[str, float | int]:
    return {
        "topology_generation": 7,
        "fetched_at": time.time(),
        "sample_interval_seconds": 1.0,
        "total_messages_processed": 100,
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
    client.get_metrics = AsyncMock(return_value=_metrics_snapshot())
    client.wait_for_metrics_generation = AsyncMock(
        return_value=_metrics_snapshot()
    )
    client.assert_valid_metrics_snapshot = MagicMock()
    client.assert_metrics_generation = AsyncMock()
    client.assert_topology_matches = AsyncMock()
    client.get_state = AsyncMock(
        return_value={"brokers": [{"component_id": 1}, {"component_id": 2}]}
    )
    client.add_publisher = AsyncMock(return_value={"component": {"component_id": 3}})
    return client


class TestThroughputPhase4(unittest.IsolatedAsyncioTestCase):
    async def test_measure_waits_for_fresh_metrics_generation_before_warmup(
        self,
    ) -> None:
        from benchmarks import throughput

        cfg = BenchmarkConfig(warmup_seconds=0, measurement_seconds=1)
        client = _fake_client()
        client.wait_for_metrics_generation = AsyncMock(return_value=_metrics_snapshot())

        with (
            patch(
                "benchmarks.throughput.collect_throughput",
                new=AsyncMock(
                    return_value=[
                        {"msgs_per_sec": 50.0},
                        {"msgs_per_sec": 55.0},
                        {"msgs_per_sec": 60.0},
                    ]
                ),
            ),
            patch("benchmarks.throughput.asyncio.sleep", new=AsyncMock()),
        ):
            await throughput._measure_throughput_attempt(
                client,
                cfg,
                n_publishers=1,
            )

        client.wait_for_metrics_generation.assert_awaited_once()

    async def test_run_retries_invalid_row_then_succeeds(self) -> None:
        from benchmarks import throughput

        bad_samples = [
            {"msgs_per_sec": 0.0},
            {"msgs_per_sec": 0.2},
            {"msgs_per_sec": 10.0},
        ]
        good_samples = [
            {"msgs_per_sec": 50.0},
            {"msgs_per_sec": 55.0},
            {"msgs_per_sec": 60.0},
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            cfg = BenchmarkConfig(
                broker_counts=[3],
                publisher_counts=[1],
                warmup_seconds=0,
                measurement_seconds=1,
                results_dir=Path(tmpdir),
                throughput_retry_backoff_seconds=0,
            )
            client = _fake_client()

            with (
                patch("benchmarks.throughput.AetherClient", return_value=client),
                patch(
                    "benchmarks.throughput.collect_throughput",
                    new=AsyncMock(side_effect=[bad_samples, good_samples]),
                ) as collect_mock,
                patch("benchmarks.throughput.asyncio.sleep", new=AsyncMock()),
            ):
                output = await throughput.run(cfg)

        result = output["results"][0]
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["attempts"], 2)
        self.assertEqual(collect_mock.await_count, 2)

    async def test_run_records_invalid_row_after_retry_budget_and_continues(
        self,
    ) -> None:
        from benchmarks import throughput

        bad_samples = [
            {"msgs_per_sec": 0.0},
            {"msgs_per_sec": 0.3},
            {"msgs_per_sec": 10.0},
        ]
        good_samples = [
            {"msgs_per_sec": 80.0},
            {"msgs_per_sec": 85.0},
            {"msgs_per_sec": 90.0},
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            cfg = BenchmarkConfig(
                broker_counts=[3],
                publisher_counts=[1, 2],
                warmup_seconds=0,
                measurement_seconds=1,
                results_dir=Path(tmpdir),
                throughput_retry_backoff_seconds=0,
            )
            client = _fake_client()

            with (
                patch("benchmarks.throughput.AetherClient", return_value=client),
                patch(
                    "benchmarks.throughput.collect_throughput",
                    new=AsyncMock(
                        side_effect=[
                            bad_samples,
                            bad_samples,
                            bad_samples,
                            good_samples,
                        ]
                    ),
                ) as collect_mock,
                patch("benchmarks.throughput.asyncio.sleep", new=AsyncMock()),
            ):
                output = await throughput.run(cfg)

        first, second = output["results"]
        self.assertEqual(first["status"], "invalid")
        self.assertEqual(first["attempts"], 3)
        self.assertIn("near-zero", first["invalid_reason"])
        self.assertEqual(second["status"], "ok")
        self.assertEqual(collect_mock.await_count, 4)


class TestScalingPhase4(unittest.IsolatedAsyncioTestCase):
    def test_find_saturation_point_requires_consecutive_low_gain_steps(self) -> None:
        timeline = [
            {"publishers": 1, "mean_msgs_per_sec": 100.0},
            {"publishers": 2, "mean_msgs_per_sec": 140.0},
            {"publishers": 3, "mean_msgs_per_sec": 143.0},
            {"publishers": 4, "mean_msgs_per_sec": 145.0},
        ]

        saturation = _find_saturation_point(
            timeline,
            gain_threshold_pct=5.0,
            consecutive_steps=2,
        )

        self.assertIsNotNone(saturation)
        self.assertEqual(saturation["publishers"], 4)
        self.assertEqual(saturation["consecutive_low_gain_steps"], 2)

    async def test_run_retries_invalid_scaling_step_then_succeeds(self) -> None:
        from benchmarks import scaling

        step1_samples = [
            {"msgs_per_sec": 100.0},
            {"msgs_per_sec": 105.0},
            {"msgs_per_sec": 110.0},
        ]
        bad_step2_samples = [
            {"msgs_per_sec": 0.0},
            {"msgs_per_sec": 0.2},
            {"msgs_per_sec": 15.0},
        ]
        good_step2_samples = [
            {"msgs_per_sec": 130.0},
            {"msgs_per_sec": 132.0},
            {"msgs_per_sec": 134.0},
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            cfg = BenchmarkConfig(
                warmup_seconds=0,
                scaling_initial_publishers=1,
                scaling_max_publishers=2,
                scaling_step_seconds=1,
                results_dir=Path(tmpdir),
                scaling_retry_backoff_seconds=0,
            )
            client = _fake_client()

            with (
                patch("benchmarks.scaling.AetherClient", return_value=client),
                patch(
                    "benchmarks.scaling.collect_throughput",
                    new=AsyncMock(
                        side_effect=[
                            step1_samples,
                            bad_step2_samples,
                            good_step2_samples,
                        ]
                    ),
                ) as collect_mock,
                patch("benchmarks.scaling.asyncio.sleep", new=AsyncMock()),
            ):
                output = await scaling.run(cfg)

        self.assertEqual(len(output["timeline"]), 2)
        self.assertEqual(output["timeline"][1]["attempts"], 2)
        self.assertEqual(collect_mock.await_count, 3)
