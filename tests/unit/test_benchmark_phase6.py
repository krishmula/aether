"""Unit tests for Phase 6 snapshot and recovery benchmark repairs."""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from benchmarks.config import BenchmarkConfig


def _fake_client() -> MagicMock:
    client = MagicMock()
    client.cleanup = AsyncMock()
    client.close = AsyncMock()
    client.seed_topology = AsyncMock(return_value={"seeded": 0})
    client.wait_all_running = AsyncMock()
    client.get_state = AsyncMock(
        return_value={
            "brokers": [
                {"component_id": 1, "status": "running"},
                {"component_id": 2, "status": "running"},
                {"component_id": 3, "status": "running"},
            ]
        }
    )
    client.trigger_chaos = AsyncMock(return_value={"chaos_target": "broker-2"})
    return client


@asynccontextmanager
async def _event_stream_with_queue(
    queue: asyncio.Queue[dict[str, object]],
):
    yield queue


class TestSnapshotPhase6(unittest.IsolatedAsyncioTestCase):
    async def test_run_records_invalid_result_for_incoherent_snapshot_round(
        self,
    ) -> None:
        from benchmarks import snapshot

        with tempfile.TemporaryDirectory() as tmpdir:
            cfg = BenchmarkConfig(
                snapshot_broker_counts=[3],
                snapshot_rounds=1,
                warmup_seconds=0,
                results_dir=Path(tmpdir),
            )
            client = _fake_client()
            events: asyncio.Queue[dict[str, object]] = asyncio.Queue()

            with (
                patch("benchmarks.snapshot.AetherClient", return_value=client),
                patch(
                    "benchmarks.snapshot.event_stream",
                    return_value=_event_stream_with_queue(events),
                ),
                patch(
                    "benchmarks.snapshot.collect_snapshot_events",
                    new=AsyncMock(
                        side_effect=RuntimeError(
                            "invalid snapshot round: "
                            "snapshot round expected 3 brokers, got 2"
                        )
                    ),
                ),
                patch("benchmarks.snapshot.asyncio.sleep", new=AsyncMock()),
            ):
                output = await snapshot.run(cfg)

        result = output["results"][0]
        self.assertEqual(result["status"], "invalid")
        self.assertIn("expected 3 brokers, got 2", result["invalid_reason"])


class TestRecoveryPhase6(unittest.IsolatedAsyncioTestCase):
    async def test_run_trial_invalidates_recovery_path_mismatch(self) -> None:
        from benchmarks import recovery

        cfg = BenchmarkConfig()
        client = _fake_client()
        events: asyncio.Queue[dict[str, object]] = asyncio.Queue()

        with (
            patch(
                "benchmarks.recovery._wait_for_snapshot",
                new=AsyncMock(return_value=10.0),
            ),
            patch(
                "benchmarks.recovery.collect_recovery_events",
                new=AsyncMock(
                    return_value={
                        "recovery_path": "redistribution",
                        "failover_start_latency_s": 0.2,
                        "recovery_execution_time_s": 0.8,
                        "recovery_time_s": 1.0,
                        "subscriber_reconnect_time_s": 1.2,
                    }
                ),
            ),
        ):
            result = await recovery._run_trial(
                client,
                cfg,
                events,
                trial=1,
                path_a=True,
            )

        self.assertEqual(result["status"], "invalid")
        self.assertEqual(result["expected_path"], "replacement")
        self.assertEqual(result["actual_path"], "redistribution")
        self.assertIn("recovery path mismatch", result["invalid_reason"])
