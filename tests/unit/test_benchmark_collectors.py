"""Unit tests for shared benchmark collector hardening."""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock

from benchmarks.collectors import (
    classify_latency_window,
    classify_snapshot_round,
    classify_throughput_window,
    collect_recovery_events,
    collect_snapshot_events,
    collect_throughput,
)
from benchmarks.config import BenchmarkConfig


class _FakeClient:
    def __init__(self, cfg: BenchmarkConfig) -> None:
        self.cfg = cfg
        self.get_metrics = AsyncMock()
        self.assert_valid_metrics_snapshot = MagicMock()


class TestCollectThroughput(unittest.IsolatedAsyncioTestCase):
    async def test_collect_throughput_validates_metrics_contract_per_sample(
        self,
    ) -> None:
        client = _FakeClient(BenchmarkConfig(min_throughput_samples=2))
        client.get_metrics.side_effect = [
            {
                "topology_generation": 4,
                "fetched_at": 100.0,
                "sample_interval_seconds": 1.0,
                "total_messages_processed": 100,
            },
            {
                "topology_generation": 4,
                "fetched_at": 101.0,
                "sample_interval_seconds": 1.0,
                "total_messages_processed": 110,
            },
            {
                "topology_generation": 4,
                "fetched_at": 102.0,
                "sample_interval_seconds": 1.0,
                "total_messages_processed": 120,
            },
        ]

        samples = await collect_throughput(
            client,
            duration=0.03,
            poll_interval=0.01,
            expected_generation=4,
            stage="throughput measurement",
        )

        self.assertGreaterEqual(len(samples), 2)
        client.assert_valid_metrics_snapshot.assert_called()

    async def test_collect_throughput_raises_after_repeated_metrics_failures(
        self,
    ) -> None:
        client = _FakeClient(BenchmarkConfig(max_metrics_read_failures=2))
        client.get_metrics.side_effect = RuntimeError("metrics unavailable")

        with self.assertRaisesRegex(
            RuntimeError,
            "failed to collect throughput metrics after 2 consecutive errors",
        ):
            await collect_throughput(client, duration=0.02, poll_interval=0)

    async def test_collect_throughput_rejects_insufficient_samples(self) -> None:
        client = _FakeClient(BenchmarkConfig(min_throughput_samples=2))
        client.get_metrics.side_effect = [
            {"total_messages_processed": 100},
            {"total_messages_processed": 110},
        ]

        with self.assertRaisesRegex(
            RuntimeError,
            "insufficient throughput samples",
        ):
            await collect_throughput(client, duration=0.03, poll_interval=0.02)


class TestClassifyThroughputWindow(unittest.TestCase):
    def test_invalidates_window_with_too_many_near_zero_samples(self) -> None:
        reason = classify_throughput_window(
            [
                {"msgs_per_sec": 0.0},
                {"msgs_per_sec": 0.5},
                {"msgs_per_sec": 25.0},
            ],
            active_publishers=2,
            near_zero_msgs_per_sec=1.0,
            max_near_zero_sample_ratio=0.5,
        )

        self.assertIn("too many near-zero samples", reason)

    def test_allows_window_with_healthy_rate_distribution(self) -> None:
        reason = classify_throughput_window(
            [
                {"msgs_per_sec": 50.0},
                {"msgs_per_sec": 55.0},
                {"msgs_per_sec": 60.0},
            ],
            active_publishers=2,
            near_zero_msgs_per_sec=1.0,
            max_near_zero_sample_ratio=0.5,
        )

        self.assertIsNone(reason)


class TestClassifyLatencyWindow(unittest.TestCase):
    def test_rejects_undersampled_subscribers(self) -> None:
        reason = classify_latency_window(
            {
                "subscribers": [
                    {"subscriber_id": 1, "sample_count": 5},
                    {"subscriber_id": 2, "sample_count": 25},
                ],
                "aggregate": {"sample_count": 30},
            },
            expected_subscribers=2,
            min_samples_per_subscriber=20,
            processed_messages_delta=100,
            expected_messages=100.0,
            min_delivery_ratio=0.7,
        )

        self.assertIn("below minimum sample count", reason)

    def test_rejects_window_with_low_processed_message_ratio(self) -> None:
        reason = classify_latency_window(
            {
                "subscribers": [
                    {"subscriber_id": 1, "sample_count": 25},
                    {"subscriber_id": 2, "sample_count": 25},
                ],
                "aggregate": {"sample_count": 50},
            },
            expected_subscribers=2,
            min_samples_per_subscriber=20,
            processed_messages_delta=20,
            expected_messages=100.0,
            min_delivery_ratio=0.7,
        )

        self.assertIn("processed-message ratio", reason)


class TestEventCollectors(unittest.IsolatedAsyncioTestCase):
    async def test_collect_recovery_events_rejects_incomplete_timeline(self) -> None:
        events: asyncio.Queue[dict] = asyncio.Queue()
        events.put_nowait({"type": "broker_declared_dead", "timestamp": 10.0})
        events.put_nowait({"type": "broker_recovery_started", "timestamp": 11.0})

        with self.assertRaisesRegex(
            RuntimeError,
            "incomplete recovery event timeline",
        ):
            await collect_recovery_events(events, timeout=0.05)

    async def test_collect_snapshot_events_rejects_missing_rounds(self) -> None:
        events: asyncio.Queue[dict] = asyncio.Queue()
        events.put_nowait(
            {
                "type": "snapshot_complete",
                "timestamp": 10.0,
                "data": {"snapshot_timestamp": 10.0},
            }
        )

        with self.assertRaisesRegex(
            RuntimeError,
            "invalid snapshot round: snapshot round missing snapshot_id",
        ):
            await collect_snapshot_events(events, rounds=2, timeout_per_round=0.01)

    async def test_collect_snapshot_events_skips_initial_partial_round(self) -> None:
        events: asyncio.Queue[dict] = asyncio.Queue()
        events.put_nowait(
            {
                "type": "snapshot_complete",
                "timestamp": 10.0,
                "data": {
                    "broker_id": 2,
                    "snapshot_id": "stale-round",
                    "snapshot_timestamp": 10.0,
                },
            }
        )
        for broker_id, ts in ((1, 20.0), (2, 20.1), (3, 20.2)):
            events.put_nowait(
                {
                    "type": "snapshot_complete",
                    "timestamp": ts,
                    "data": {
                        "broker_id": broker_id,
                        "snapshot_id": "round-1",
                        "snapshot_timestamp": ts,
                    },
                }
            )

        rounds = await collect_snapshot_events(
            events,
            rounds=1,
            timeout_per_round=0.01,
            expected_brokers=3,
        )

        self.assertEqual(len(rounds), 1)
        self.assertEqual(rounds[0]["broker_count"], 3)
        self.assertEqual(rounds[0]["snapshot_id"], "round-1")

    async def test_collect_snapshot_events_rejects_duplicate_broker_completion(
        self,
    ) -> None:
        events: asyncio.Queue[dict] = asyncio.Queue()
        for broker_id, ts in ((1, 10.0), (1, 10.1), (2, 10.2)):
            events.put_nowait(
                {
                    "type": "snapshot_complete",
                    "timestamp": ts,
                    "data": {
                        "broker_id": broker_id,
                        "snapshot_id": "round-1",
                        "snapshot_timestamp": ts,
                    },
                }
            )

        with self.assertRaisesRegex(
            RuntimeError,
            (
                "invalid snapshot round: snapshot round contains duplicate "
                "broker completions"
            ),
        ):
            await collect_snapshot_events(
                events,
                rounds=1,
                timeout_per_round=0.01,
                expected_brokers=3,
            )

    async def test_collect_recovery_events_rejects_out_of_order_timeline(self) -> None:
        events: asyncio.Queue[dict] = asyncio.Queue()
        events.put_nowait({"type": "broker_declared_dead", "timestamp": 10.0})
        events.put_nowait(
            {
                "type": "subscriber_reconnected",
                "timestamp": 10.5,
            }
        )
        events.put_nowait(
            {
                "type": "broker_recovery_started",
                "timestamp": 11.0,
                "data": {"recovery_path": "replacement"},
            }
        )
        events.put_nowait(
            {
                "type": "broker_recovered",
                "timestamp": 12.0,
                "data": {"recovery_path": "replacement"},
            }
        )

        with self.assertRaisesRegex(
            RuntimeError,
            "invalid recovery event timeline",
        ):
            await collect_recovery_events(events, timeout=0.05)


class TestClassifySnapshotRound(unittest.TestCase):
    def test_rejects_mixed_snapshot_ids(self) -> None:
        reason = classify_snapshot_round(
            [
                {
                    "data": {
                        "broker_id": 1,
                        "snapshot_id": "round-1",
                        "snapshot_timestamp": 10.0,
                    }
                },
                {
                    "data": {
                        "broker_id": 2,
                        "snapshot_id": "round-2",
                        "snapshot_timestamp": 10.1,
                    }
                },
            ],
            expected_brokers=2,
        )

        self.assertIn("mixed snapshot ids", reason)
