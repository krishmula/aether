"""Unit tests for shared benchmark collector hardening."""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock

from benchmarks.collectors import (
    collect_recovery_events,
    collect_snapshot_events,
    collect_throughput,
)
from benchmarks.config import BenchmarkConfig


class _FakeClient:
    def __init__(self, cfg: BenchmarkConfig) -> None:
        self.cfg = cfg
        self.get_metrics = AsyncMock()


class TestCollectThroughput(unittest.IsolatedAsyncioTestCase):
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
            "incomplete snapshot rounds",
        ):
            await collect_snapshot_events(events, rounds=2, timeout_per_round=0.01)
