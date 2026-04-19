"""Unit tests for benchmark client helpers around metrics generations."""

from __future__ import annotations

import time
import unittest
from unittest.mock import AsyncMock, patch

from benchmarks.client import AetherClient
from benchmarks.config import BenchmarkConfig


class TestBenchmarkMetricsGenerationHelpers(unittest.IsolatedAsyncioTestCase):
    async def asyncTearDown(self) -> None:
        client = getattr(self, "client", None)
        if client is not None:
            await client.close()

    async def test_wait_for_metrics_generation_waits_for_fresh_sample(self) -> None:
        self.client = AetherClient(BenchmarkConfig())
        self.client.get_metrics = AsyncMock(  # type: ignore[method-assign]
            side_effect=[
                {"topology_generation": 1, "fetched_at": 0},
                {"topology_generation": 2, "fetched_at": 0},
                {"topology_generation": 2, "fetched_at": 1_700_000_100.0},
            ]
        )

        metrics = await self.client.wait_for_metrics_generation(
            2,
            timeout=1.0,
            poll_interval=0,
        )

        assert metrics["topology_generation"] == 2
        assert metrics["fetched_at"] == 1_700_000_100.0

    async def test_assert_metrics_generation_raises_on_drift(self) -> None:
        self.client = AetherClient(BenchmarkConfig())
        self.client.get_metrics = AsyncMock(  # type: ignore[method-assign]
            return_value={"topology_generation": 3}
        )

        with self.assertRaisesRegex(
            RuntimeError,
            "metrics generation changed during throughput window",
        ):
            await self.client.assert_metrics_generation(
                2,
                stage="throughput window",
            )


class TestBenchmarkMetricsContract(unittest.IsolatedAsyncioTestCase):
    async def asyncTearDown(self) -> None:
        client = getattr(self, "client", None)
        if client is not None:
            await client.close()

    async def test_assert_valid_metrics_snapshot_rejects_stale_metrics(self) -> None:
        self.client = AetherClient(BenchmarkConfig(metrics_max_staleness_seconds=2.0))

        with patch("benchmarks.client.time.time", return_value=100.0):
            with self.assertRaisesRegex(
                RuntimeError,
                "stale metrics snapshot during warmup",
            ):
                self.client.assert_valid_metrics_snapshot(
                    {
                        "topology_generation": 2,
                        "fetched_at": 97.0,
                        "sample_interval_seconds": 1.0,
                    },
                    stage="warmup",
                    expected_generation=2,
                )

    async def test_assert_valid_metrics_snapshot_rejects_non_positive_sample_interval(
        self,
    ) -> None:
        self.client = AetherClient(BenchmarkConfig())

        with self.assertRaisesRegex(
            RuntimeError,
            "invalid metrics sample interval during readiness",
        ):
            self.client.assert_valid_metrics_snapshot(
                {
                    "topology_generation": 5,
                    "fetched_at": time.time(),
                    "sample_interval_seconds": 0.0,
                },
                stage="readiness",
                expected_generation=5,
            )

    async def test_wait_latency_ready_rejects_invalid_metrics_contract(self) -> None:
        self.client = AetherClient(BenchmarkConfig(metrics_max_staleness_seconds=2.0))
        self.client.get_state = AsyncMock(  # type: ignore[method-assign]
            return_value={
                "subscribers": [{"component_id": 1}],
            }
        )
        self.client.get_metrics = AsyncMock(  # type: ignore[method-assign]
            return_value={
                "topology_generation": 1,
                "fetched_at": 90.0,
                "sample_interval_seconds": 1.0,
                "total_messages_processed": 10,
                "subscribers": [
                    {
                        "subscriber_id": 1,
                        "total_received": 5,
                        "latency_us": {"sample_count": 5},
                    }
                ],
            }
        )

        with patch("benchmarks.client.time.time", return_value=100.0):
            with self.assertRaisesRegex(
                RuntimeError,
                "stale metrics snapshot during latency readiness",
            ):
                await self.client.wait_latency_ready(
                    expected_subscribers=1,
                    min_samples_per_subscriber=1,
                    stable_polls=1,
                    timeout=0.1,
                    poll_interval=0,
                )


class TestBenchmarkSeedTopology(unittest.IsolatedAsyncioTestCase):
    async def asyncTearDown(self) -> None:
        client = getattr(self, "client", None)
        if client is not None:
            await client.close()

    async def test_seed_topology_rechecks_live_state_before_publishers_and_subscribers(
        self,
    ) -> None:
        self.client = AetherClient(BenchmarkConfig())
        self.client.get_state = AsyncMock(  # type: ignore[method-assign]
            side_effect=[
                {"brokers": [], "publishers": [], "subscribers": []},
                {
                    "brokers": [{"component_id": 1}],
                    "publishers": [{"component_id": 21}],
                    "subscribers": [{"component_id": 31}],
                },
                {
                    "brokers": [{"component_id": 1}],
                    "publishers": [{"component_id": 21}],
                    "subscribers": [{"component_id": 31}],
                },
                {
                    "brokers": [{"component_id": 1}],
                    "publishers": [{"component_id": 21}],
                    "subscribers": [{"component_id": 31}],
                },
            ]
        )
        self.client._create_broker = AsyncMock(  # type: ignore[method-assign]
            return_value={"component": {"component_id": 1}}
        )
        self.client._create_publisher = AsyncMock(  # type: ignore[method-assign]
            return_value={"component": {"component_id": 41}}
        )
        self.client._create_subscriber = AsyncMock(  # type: ignore[method-assign]
            return_value={"component": {"component_id": 51}}
        )

        await self.client.seed_topology(brokers=1, publishers=2, subscribers=2)

        self.client._create_publisher.assert_awaited_once()
        self.client._create_subscriber.assert_awaited_once()
