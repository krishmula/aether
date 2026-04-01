"""Unit tests for HealthMonitor (aether.orchestrator.health).

Uses unittest.IsolatedAsyncioTestCase so we get a real asyncio event loop
per test without pulling in pytest-asyncio. httpx.AsyncClient is mocked via
unittest.mock so no real network calls are made.
"""

from __future__ import annotations

import asyncio
import unittest
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

from aether.orchestrator.health import HealthMonitor
from aether.orchestrator.models import ComponentInfo, ComponentStatus, ComponentType


def _make_broker(broker_id: int, status: ComponentStatus = ComponentStatus.RUNNING) -> ComponentInfo:
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


def _components(*brokers: ComponentInfo) -> dict[str, ComponentInfo]:
    return {b.container_name: b for b in brokers}


class TestHealthMonitorCallbackFires(unittest.IsolatedAsyncioTestCase):
    """Callback fires after failure_threshold consecutive failures."""

    async def test_callback_fires_after_3_failures(self):
        callback = AsyncMock()
        broker = _make_broker(1)
        monitor = HealthMonitor(
            components=_components(broker),
            on_broker_dead=callback,
            poll_interval=999,  # never triggers sleep between manual polls
            failure_threshold=3,
        )

        mock_resp = MagicMock()
        mock_resp.status_code = 503

        with patch("httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_resp)
            MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

            # Poll 3 times — callback must fire on the 3rd
            await monitor._poll_one(mock_client, broker)
            callback.assert_not_called()

            await monitor._poll_one(mock_client, broker)
            callback.assert_not_called()

            await monitor._poll_one(mock_client, broker)
            # create_task schedules the callback — drain the event loop
            await asyncio.sleep(0)
            callback.assert_called_once_with(1, "broker-1", 8000)

    async def test_callback_fires_on_connection_error(self):
        """Exception (e.g. ConnectionRefused) counts the same as non-200."""
        callback = AsyncMock()
        broker = _make_broker(2)
        monitor = HealthMonitor(
            components=_components(broker),
            on_broker_dead=callback,
            failure_threshold=3,
        )

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=Exception("connection refused"))

        for _ in range(3):
            await monitor._poll_one(mock_client, broker)
        await asyncio.sleep(0)
        callback.assert_called_once_with(2, "broker-2", 8000)


class TestHealthMonitorCounterReset(unittest.IsolatedAsyncioTestCase):
    """Failure counter resets on a successful poll — no spurious callbacks."""

    async def test_counter_resets_on_success(self):
        callback = AsyncMock()
        broker = _make_broker(1)
        monitor = HealthMonitor(
            components=_components(broker),
            on_broker_dead=callback,
            failure_threshold=3,
        )

        bad_resp = MagicMock()
        bad_resp.status_code = 503
        good_resp = MagicMock()
        good_resp.status_code = 200

        mock_client = AsyncMock()

        # 2 failures
        mock_client.get = AsyncMock(return_value=bad_resp)
        await monitor._poll_one(mock_client, broker)
        await monitor._poll_one(mock_client, broker)
        self.assertEqual(monitor._failure_counts[1], 2)

        # 1 success — counter must reset to 0
        mock_client.get = AsyncMock(return_value=good_resp)
        await monitor._poll_one(mock_client, broker)
        self.assertEqual(monitor._failure_counts.get(1, 0), 0)

        # 2 more failures — still below threshold, no callback
        mock_client.get = AsyncMock(return_value=bad_resp)
        await monitor._poll_one(mock_client, broker)
        await monitor._poll_one(mock_client, broker)
        await asyncio.sleep(0)
        callback.assert_not_called()

    async def test_counter_resets_after_callback_fires(self):
        """After the callback fires the counter is zeroed — won't re-fire immediately."""
        callback = AsyncMock()
        broker = _make_broker(1)
        monitor = HealthMonitor(
            components=_components(broker),
            on_broker_dead=callback,
            failure_threshold=3,
        )

        bad_resp = MagicMock()
        bad_resp.status_code = 503
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=bad_resp)

        # Trigger callback
        for _ in range(3):
            await monitor._poll_one(mock_client, broker)
        await asyncio.sleep(0)
        callback.assert_called_once()

        # 2 more failures — still below threshold again, no second callback
        await monitor._poll_one(mock_client, broker)
        await monitor._poll_one(mock_client, broker)
        await asyncio.sleep(0)
        callback.assert_called_once()  # still only once


class TestHealthMonitorConcurrentPolling(unittest.IsolatedAsyncioTestCase):
    """_poll_all fans out to all RUNNING brokers concurrently."""

    async def test_polls_all_running_brokers(self):
        callback = AsyncMock()
        brokers = [_make_broker(i) for i in range(1, 4)]
        components = _components(*brokers)
        monitor = HealthMonitor(
            components=components,
            on_broker_dead=callback,
            failure_threshold=3,
            startup_grace_seconds=0,
        )

        good_resp = MagicMock()
        good_resp.status_code = 200
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=good_resp)

        await monitor._poll_all(mock_client)

        # One GET per broker
        self.assertEqual(mock_client.get.call_count, 3)
        urls_called = {call.args[0] for call in mock_client.get.call_args_list}
        self.assertIn("http://broker-1:18000/status", urls_called)
        self.assertIn("http://broker-2:18000/status", urls_called)
        self.assertIn("http://broker-3:18000/status", urls_called)

    async def test_skips_non_running_brokers(self):
        callback = AsyncMock()
        running = _make_broker(1, ComponentStatus.RUNNING)
        stopped = _make_broker(2, ComponentStatus.STOPPED)
        monitor = HealthMonitor(
            components=_components(running, stopped),
            on_broker_dead=callback,
            failure_threshold=3,
            startup_grace_seconds=0,
        )

        good_resp = MagicMock()
        good_resp.status_code = 200
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=good_resp)

        await monitor._poll_all(mock_client)

        self.assertEqual(mock_client.get.call_count, 1)
        self.assertIn("broker-1", mock_client.get.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
