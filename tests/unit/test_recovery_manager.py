"""Unit tests for RecoveryManager (aether.orchestrator.recovery).

Uses unittest.IsolatedAsyncioTestCase matching the pattern in test_health_monitor.py.
DockerManager and EventBroadcaster are mocked; httpx calls are mocked via
unittest.mock so no real network calls are made.
"""

from __future__ import annotations

import asyncio
import time
import unittest
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

from aether.orchestrator.models import (
    ComponentInfo,
    ComponentStatus,
    ComponentType,
)
from aether.orchestrator.recovery import RecoveryManager


def _make_broker(
    broker_id: int, status: ComponentStatus = ComponentStatus.RUNNING
) -> ComponentInfo:
    return ComponentInfo(
        component_type=ComponentType.BROKER,
        component_id=broker_id,
        container_name=f"aether-broker-{broker_id}",
        hostname=f"broker-{broker_id}",
        internal_port=8000,
        internal_status_port=18000,
        status=status,
        created_at=datetime.utcnow(),
    )


def _make_subscriber(subscriber_id: int, broker_id: int) -> ComponentInfo:
    return ComponentInfo(
        component_type=ComponentType.SUBSCRIBER,
        component_id=subscriber_id,
        container_name=f"aether-subscriber-{subscriber_id}",
        hostname=f"subscriber-{subscriber_id}",
        internal_port=9100,
        internal_status_port=19100,
        broker_id=broker_id,
        created_at=datetime.utcnow(),
    )


def _components(*items: ComponentInfo) -> dict[str, ComponentInfo]:
    return {item.container_name: item for item in items}


def _mock_settings(**overrides):
    defaults = {
        "snapshot_max_age": 30.0,
        "health_poll_interval": 0.1,
        "health_timeout": 1.0,
        "recovery_timeout": 5.0,
        "recovery_debounce_window": 60.0,
        "bootstrap_host": "bootstrap",
        "bootstrap_port": 7000,
    }
    defaults.update(overrides)
    settings = MagicMock()
    for k, v in defaults.items():
        setattr(settings, k, v)
    return settings


def _mock_docker_mgr(components: dict[str, ComponentInfo]) -> MagicMock:
    mgr = MagicMock()
    mgr._components = components
    mgr.remove_broker = MagicMock()
    mgr.create_broker = MagicMock(side_effect=lambda req: _make_broker(req.broker_id))
    return mgr


class TestDebounce(unittest.IsolatedAsyncioTestCase):
    """Duplicate handle_broker_dead within debounce window is ignored."""

    async def test_debounce_ignores_duplicate(self):
        broker = _make_broker(1)
        components = _components(broker)
        docker_mgr = _mock_docker_mgr(components)
        broadcaster = MagicMock()
        broadcaster.emit = AsyncMock()
        settings = _mock_settings(recovery_debounce_window=60.0)

        recovery = RecoveryManager(docker_mgr, broadcaster, settings)

        # Mock _fetch_best_snapshot to return None (Path B)
        recovery._fetch_best_snapshot = AsyncMock(return_value=None)
        recovery._recover_redistribution = AsyncMock()

        await recovery.handle_broker_dead(1, "broker-1", 8000)

        # First call should have emitted BROKER_DECLARED_DEAD
        dead_calls = [
            c
            for c in broadcaster.emit.call_args_list
            if c.args[0].value == "broker_declared_dead"
        ]
        self.assertEqual(len(dead_calls), 1)

        # Second call within debounce window — should be skipped
        broadcaster.emit.reset_mock()
        await recovery.handle_broker_dead(1, "broker-1", 8000)
        dead_calls = [
            c
            for c in broadcaster.emit.call_args_list
            if c.args[0].value == "broker_declared_dead"
        ]
        self.assertEqual(len(dead_calls), 0)


class TestPathAFreshSnapshot(unittest.IsolatedAsyncioTestCase):
    """Fresh snapshot triggers replacement recovery."""

    async def test_path_a_fresh_snapshot(self):
        broker1 = _make_broker(1)
        broker2 = _make_broker(2)
        sub = _make_subscriber(1, broker_id=2)
        components = _components(broker1, broker2, sub)
        docker_mgr = _mock_docker_mgr(components)
        broadcaster = MagicMock()
        broadcaster.emit = AsyncMock()
        settings = _mock_settings()

        recovery = RecoveryManager(docker_mgr, broadcaster, settings)

        fresh_snapshot = {"timestamp": time.time() - 5.0, "data": {}}
        recovery._fetch_best_snapshot = AsyncMock(return_value=fresh_snapshot)
        recovery._recover_replacement = AsyncMock()
        recovery._recover_redistribution = AsyncMock()

        await recovery.handle_broker_dead(2, "broker-2", 8000)

        recovery._recover_replacement.assert_called_once()
        recovery._recover_redistribution.assert_not_called()


class TestPathBStaleSnapshot(unittest.IsolatedAsyncioTestCase):
    """Stale snapshot triggers redistribution."""

    async def test_path_b_stale_snapshot(self):
        broker1 = _make_broker(1)
        broker2 = _make_broker(2)
        components = _components(broker1, broker2)
        docker_mgr = _mock_docker_mgr(components)
        broadcaster = MagicMock()
        broadcaster.emit = AsyncMock()
        settings = _mock_settings()

        recovery = RecoveryManager(docker_mgr, broadcaster, settings)

        stale_snapshot = {"timestamp": time.time() - 60.0, "data": {}}
        recovery._fetch_best_snapshot = AsyncMock(return_value=stale_snapshot)
        recovery._recover_redistribution = AsyncMock()

        await recovery.handle_broker_dead(2, "broker-2", 8000)

        recovery._recover_redistribution.assert_awaited_once()
        self.assertEqual(recovery._recover_redistribution.await_args.args[0], 2)


class TestPathBNoSnapshot(unittest.IsolatedAsyncioTestCase):
    """Missing snapshot triggers redistribution."""

    async def test_path_b_no_snapshot(self):
        broker1 = _make_broker(1)
        broker2 = _make_broker(2)
        components = _components(broker1, broker2)
        docker_mgr = _mock_docker_mgr(components)
        broadcaster = MagicMock()
        broadcaster.emit = AsyncMock()
        settings = _mock_settings()

        recovery = RecoveryManager(docker_mgr, broadcaster, settings)
        recovery._fetch_best_snapshot = AsyncMock(return_value=None)
        recovery._recover_redistribution = AsyncMock()

        await recovery.handle_broker_dead(2, "broker-2", 8000)

        recovery._recover_redistribution.assert_awaited_once()
        self.assertEqual(recovery._recover_redistribution.await_args.args[0], 2)


class TestPathAFallbackToB(unittest.IsolatedAsyncioTestCase):
    """Replacement failure falls through to redistribution."""

    async def test_path_a_fallback_to_b(self):
        broker1 = _make_broker(1)
        broker2 = _make_broker(2)
        components = _components(broker1, broker2)
        docker_mgr = _mock_docker_mgr(components)
        broadcaster = MagicMock()
        broadcaster.emit = AsyncMock()
        settings = _mock_settings()

        recovery = RecoveryManager(docker_mgr, broadcaster, settings)

        fresh_snapshot = {"timestamp": time.time() - 5.0, "data": {}}
        recovery._fetch_best_snapshot = AsyncMock(return_value=fresh_snapshot)
        recovery._recover_replacement = AsyncMock(
            side_effect=RuntimeError("health timeout")
        )
        recovery._recover_redistribution = AsyncMock()

        await recovery.handle_broker_dead(2, "broker-2", 8000)

        recovery._recover_replacement.assert_called_once()
        recovery._recover_redistribution.assert_awaited_once()
        self.assertEqual(recovery._recover_redistribution.await_args.args[0], 2)


class TestRedistributionBalancing(unittest.IsolatedAsyncioTestCase):
    """Orphans are assigned to least-loaded surviving brokers."""

    async def test_redistribution_balancing(self):
        broker1 = _make_broker(1)
        broker2 = _make_broker(2)
        broker3 = _make_broker(3)
        # broker1 already has 1 subscriber, broker2 has 0
        existing_sub = _make_subscriber(10, broker_id=1)
        # 3 orphaned subscribers from dead broker 3
        orphan1 = _make_subscriber(1, broker_id=3)
        orphan2 = _make_subscriber(2, broker_id=3)
        orphan3 = _make_subscriber(3, broker_id=3)

        components = _components(
            broker1, broker2, broker3, existing_sub, orphan1, orphan2, orphan3
        )
        docker_mgr = _mock_docker_mgr(components)
        broadcaster = MagicMock()
        broadcaster.emit = AsyncMock()
        settings = _mock_settings()

        recovery = RecoveryManager(docker_mgr, broadcaster, settings)
        await recovery._recover_redistribution(3, "recovery-123", time.time())

        # broker2 had 0 subs, broker1 had 1 — least-loaded first
        # Expected: orphan1 → broker2 (0), orphan2 → broker1 (1) or broker2 (1),
        # then orphan3 to whichever has fewer
        assigned_to_1 = sum(1 for s in [orphan1, orphan2, orphan3] if s.broker_id == 1)
        assigned_to_2 = sum(1 for s in [orphan1, orphan2, orphan3] if s.broker_id == 2)
        # All 3 orphans should be reassigned (none left on broker 3)
        self.assertEqual(assigned_to_1 + assigned_to_2, 3)
        # With 1 existing on broker1, final counts should be balanced: 2 and 2
        self.assertIn(assigned_to_1 + 1, [2, 2])  # broker1 total
        self.assertIn(assigned_to_2, [1, 2])  # broker2 total

    async def test_redistribution_deregisters_from_bootstrap(self):
        """Redistribution path calls deregister_from_bootstrap for dead broker."""
        broker3 = _make_broker(3)
        orphan1 = _make_subscriber(1, broker_id=3)
        broker1 = _make_broker(1)
        components = _components(broker1, broker3, orphan1)
        docker_mgr = _mock_docker_mgr(components)
        broadcaster = MagicMock()
        broadcaster.emit = AsyncMock()
        settings = _mock_settings()

        recovery = RecoveryManager(docker_mgr, broadcaster, settings)

        with patch(
            "aether.orchestrator.recovery.deregister_from_bootstrap"
        ) as mock_deregister:
            await recovery._recover_redistribution(3, "recovery-456", time.time())

            mock_deregister.assert_awaited_once_with("broker-3", 8000, settings)


class TestReconnectMetrics(unittest.IsolatedAsyncioTestCase):
    """Subscriber reconnect metrics cover all recovery flows."""

    async def test_replacement_counts_snapshot_subscribers(self):
        broker1 = _make_broker(1)
        broker2 = _make_broker(2)
        docker_mgr = _mock_docker_mgr(_components(broker1, broker2))
        broadcaster = MagicMock()
        broadcaster.emit = AsyncMock()
        settings = _mock_settings()

        recovery = RecoveryManager(docker_mgr, broadcaster, settings)
        snapshot = {
            "timestamp": time.time() - 5.0,
            "remote_subscribers": {
                "subscriber-1:9100": [{"low": 0, "high": 84}],
                "subscriber-2:9100": [{"low": 85, "high": 169}],
            },
        }

        with patch(
            "aether.orchestrator.metrics.subscriber_reconnects_total.inc"
        ) as inc:
            with patch(
                "aether.orchestrator.bootstrap_client.httpx.AsyncClient"
            ) as MockClient:
                mock_client = AsyncMock()
                mock_client.request = AsyncMock()
                mock_client.get = AsyncMock(return_value=MagicMock(status_code=200))
                mock_client.post = AsyncMock(return_value=MagicMock(status_code=200))
                MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
                MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

                await recovery._recover_replacement(
                    2, "broker-2", 8000, snapshot, "recovery-123", time.time()
                )

        inc.assert_called_once_with(2)

    async def test_intentional_delete_counts_reassigned_subscribers(self):
        broker1 = _make_broker(1)
        broker2 = _make_broker(2)
        orphan1 = _make_subscriber(1, broker_id=2)
        orphan2 = _make_subscriber(2, broker_id=2)
        docker_mgr = _mock_docker_mgr(_components(broker1, broker2, orphan1, orphan2))
        broadcaster = MagicMock()
        broadcaster.emit = AsyncMock()
        settings = _mock_settings()

        recovery = RecoveryManager(docker_mgr, broadcaster, settings)

        with patch(
            "aether.orchestrator.metrics.subscriber_reconnects_total.inc"
        ) as inc:
            reassigned = await recovery.reassign_orphans(2)

        self.assertEqual(reassigned, 2)
        inc.assert_called_once_with(2)


class TestFetchBestSnapshotPicksFreshest(unittest.IsolatedAsyncioTestCase):
    """_fetch_best_snapshot returns the snapshot with the highest timestamp."""

    async def test_picks_freshest(self):
        broker1 = _make_broker(1)
        broker2 = _make_broker(2)
        broker3 = _make_broker(3)

        docker_mgr = _mock_docker_mgr(_components(broker1, broker2, broker3))
        broadcaster = MagicMock()
        broadcaster.emit = AsyncMock()
        settings = _mock_settings()

        recovery = RecoveryManager(docker_mgr, broadcaster, settings)

        now = time.time()
        responses = [
            MagicMock(status_code=200, json=lambda t=now - 20: {"timestamp": t}),
            MagicMock(status_code=200, json=lambda t=now - 5: {"timestamp": t}),
            MagicMock(status_code=404),
        ]
        call_count = 0

        async def mock_get(url, timeout=None):
            nonlocal call_count
            resp = responses[call_count]
            call_count += 1
            return resp

        with patch(
            "aether.orchestrator.bootstrap_client.httpx.AsyncClient"
        ) as MockClient:
            mock_client = AsyncMock()
            mock_client.get = mock_get
            MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await recovery._fetch_best_snapshot(
                [broker1, broker2, broker3], "broker-dead", 8000
            )

        self.assertIsNotNone(result)
        self.assertAlmostEqual(result["timestamp"], now - 5, delta=0.1)


class TestConcurrentRecoverySerialized(unittest.IsolatedAsyncioTestCase):
    """Lock prevents two recoveries from interleaving."""

    async def test_concurrent_recovery_serialized(self):
        broker1 = _make_broker(1)
        broker2 = _make_broker(2)
        broker3 = _make_broker(3)
        components = _components(broker1, broker2, broker3)
        docker_mgr = _mock_docker_mgr(components)
        broadcaster = MagicMock()
        broadcaster.emit = AsyncMock()
        settings = _mock_settings(recovery_debounce_window=0.0)

        recovery = RecoveryManager(docker_mgr, broadcaster, settings)
        recovery._fetch_best_snapshot = AsyncMock(return_value=None)

        execution_order = []

        async def tracked_redistribute(broker_id, recovery_id, t0):
            execution_order.append(("start", broker_id))
            await asyncio.sleep(0.05)
            execution_order.append(("end", broker_id))

        recovery._recover_redistribution = tracked_redistribute

        await asyncio.gather(
            recovery.handle_broker_dead(2, "broker-2", 8000),
            recovery.handle_broker_dead(3, "broker-3", 8000),
        )

        # Verify no interleaving — each start/end pair is contiguous
        self.assertEqual(execution_order[0][0], "start")
        self.assertEqual(execution_order[1][0], "end")
        self.assertEqual(execution_order[0][1], execution_order[1][1])
        self.assertEqual(execution_order[2][0], "start")
        self.assertEqual(execution_order[3][0], "end")
        self.assertEqual(execution_order[2][1], execution_order[3][1])


if __name__ == "__main__":
    unittest.main()
