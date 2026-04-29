"""Unit tests for shared benchmark collector hardening."""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock

from unittest.mock import patch

from benchmarks.collectors import (
    classify_latency_window,
    classify_throughput_window,
    collect_recovery_events,
    collect_snapshot_rounds,
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

    async def test_collect_snapshot_rounds_records_coordination_ms(self) -> None:
        """Happy path: pre-flight absorbs solo snapshots, round 1 measures coordinated one."""
        client = AsyncMock()
        t0 = 1000.0
        client.get_broker_snapshot_status.side_effect = [
            # pre-flight poll 1: brokers still starting — no snapshots yet
            [
                {"broker_id": i, "timestamp": None, "snapshot_id": None, "snapshot_state": "idle"}
                for i in (1, 2, 3)
            ],
            # pre-flight poll 2: solo snapshots fired — pre-flight passes, baseline set here
            [
                {"broker_id": 1, "timestamp": t0, "snapshot_id": "solo-1", "snapshot_state": "idle"},
                {"broker_id": 2, "timestamp": t0, "snapshot_id": "solo-2", "snapshot_state": "idle"},
                {"broker_id": 3, "timestamp": t0, "snapshot_id": "solo-3", "snapshot_state": "idle"},
            ],
            # round 1 poll: coordinated snapshot, all share one ID, measured spread
            [
                {"broker_id": 1, "timestamp": t0 + 90.010, "snapshot_id": "snap-coord", "snapshot_state": "idle"},
                {"broker_id": 2, "timestamp": t0 + 90.020, "snapshot_id": "snap-coord", "snapshot_state": "idle"},
                {"broker_id": 3, "timestamp": t0 + 90.005, "snapshot_id": "snap-coord", "snapshot_state": "idle"},
            ],
        ]
        with patch("benchmarks.collectors.asyncio.sleep", new=AsyncMock()):
            rounds = await collect_snapshot_rounds(client, rounds=1, expected_brokers=3)

        self.assertEqual(len(rounds), 1)
        self.assertEqual(rounds[0]["snapshot_id"], "snap-coord")
        self.assertEqual(rounds[0]["broker_count"], 3)
        self.assertAlmostEqual(rounds[0]["coordination_ms"], 15.0, places=0)

    async def test_collect_snapshot_rounds_asserts_cl_invariant(self) -> None:
        """Raises when brokers advance but report different snapshot_ids (CL invariant violation)."""
        client = AsyncMock()
        client.get_broker_snapshot_status.side_effect = [
            # pre-flight: all have first snapshot — passes immediately, baseline set here
            [
                {"broker_id": 1, "timestamp": 1000.0, "snapshot_id": "solo-1", "snapshot_state": "idle"},
                {"broker_id": 2, "timestamp": 1000.0, "snapshot_id": "solo-2", "snapshot_state": "idle"},
                {"broker_id": 3, "timestamp": 1000.0, "snapshot_id": "solo-3", "snapshot_state": "idle"},
            ],
            # round 1 poll: all advance but still have different snapshot_ids
            [
                {"broker_id": 1, "timestamp": 1090.1, "snapshot_id": "snap-A", "snapshot_state": "idle"},
                {"broker_id": 2, "timestamp": 1090.2, "snapshot_id": "snap-B", "snapshot_state": "idle"},
                {"broker_id": 3, "timestamp": 1090.3, "snapshot_id": "snap-A", "snapshot_state": "idle"},
            ],
        ]
        with patch("benchmarks.collectors.asyncio.sleep", new=AsyncMock()):
            with self.assertRaisesRegex(RuntimeError, "CL invariant violated"):
                await collect_snapshot_rounds(client, rounds=1, expected_brokers=3)

    async def test_collect_snapshot_rounds_times_out_when_brokers_stall(self) -> None:
        """Raises with broker count info when a broker never completes its first snapshot."""
        client = AsyncMock()

        async def _side_effect():
            # Broker 3 never gets a first snapshot — pre-flight never passes.
            return [
                {"broker_id": 1, "timestamp": 1000.1, "snapshot_id": "snap-A", "snapshot_state": "idle"},
                {"broker_id": 2, "timestamp": 1000.2, "snapshot_id": "snap-A", "snapshot_state": "idle"},
                {"broker_id": 3, "timestamp": None, "snapshot_id": None, "snapshot_state": "idle"},
            ]

        client.get_broker_snapshot_status.side_effect = _side_effect
        with patch("benchmarks.collectors.asyncio.sleep", new=AsyncMock()):
            with self.assertRaisesRegex(RuntimeError, r"2/3 brokers completed"):
                await collect_snapshot_rounds(
                    client, rounds=1, expected_brokers=3, convergence_timeout=0.001
                )

    async def test_collect_snapshot_rounds_advances_baseline_between_rounds(self) -> None:
        """Second round must advance past first round's timestamps, not the pre-flight baseline."""
        client = AsyncMock()
        client.get_broker_snapshot_status.side_effect = [
            # pre-flight: all have first snapshot — passes immediately
            [
                {"broker_id": 1, "timestamp": 1000.0, "snapshot_id": "snap-0", "snapshot_state": "idle"},
                {"broker_id": 2, "timestamp": 1000.1, "snapshot_id": "snap-0", "snapshot_state": "idle"},
            ],
            # round 1 poll: advance past pre-flight baseline
            [
                {"broker_id": 1, "timestamp": 1090.0, "snapshot_id": "snap-1", "snapshot_state": "idle"},
                {"broker_id": 2, "timestamp": 1090.1, "snapshot_id": "snap-1", "snapshot_state": "idle"},
            ],
            # round 2 poll: must advance past round 1 timestamps (not pre-flight baseline)
            [
                {"broker_id": 1, "timestamp": 1180.0, "snapshot_id": "snap-2", "snapshot_state": "idle"},
                {"broker_id": 2, "timestamp": 1180.1, "snapshot_id": "snap-2", "snapshot_state": "idle"},
            ],
        ]
        with patch("benchmarks.collectors.asyncio.sleep", new=AsyncMock()):
            rounds = await collect_snapshot_rounds(client, rounds=2, expected_brokers=2)

        self.assertEqual(len(rounds), 2)
        self.assertEqual(rounds[0]["snapshot_id"], "snap-1")
        self.assertEqual(rounds[1]["snapshot_id"], "snap-2")

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


