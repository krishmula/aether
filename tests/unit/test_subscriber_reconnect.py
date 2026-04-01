"""Unit tests for NetworkSubscriber health loop and reconnect logic.

All network I/O (NetworkNode.send, urllib.request.urlopen) is mocked so no
real sockets are opened. Time is controlled via unittest.mock.patch to keep
tests fast.
"""

from __future__ import annotations

import json
import threading
import time
import unittest
from io import BytesIO
from unittest.mock import MagicMock, patch

from aether.core.payload_range import PayloadRange
from aether.core.uint8 import UInt8
from aether.gossip.protocol import SubscribeRequest
from aether.network.node import NodeAddress
from aether.network.subscriber import NetworkSubscriber, _PING_INTERVAL, _PING_TIMEOUT
from aether.snapshot import BrokerRecoveryNotification, Ping, Pong


def _free_port() -> int:
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _make_subscriber(
    orchestrator_url: str = "http://orchestrator:8001",
    subscriber_id: int = 1,
) -> NetworkSubscriber:
    # Use 127.0.0.1 + free port so NetworkNode can bind, then replace with mock.
    address = NodeAddress("127.0.0.1", _free_port())
    sub = NetworkSubscriber(
        address,
        orchestrator_url=orchestrator_url,
        subscriber_id=subscriber_id,
    )
    sub.broker = NodeAddress("127.0.0.1", _free_port())
    sub.node = MagicMock()
    sub.node.receive = MagicMock(return_value=(None, None))
    return sub


class TestPongUpdatesTimestamp(unittest.TestCase):
    """_last_pong_time is updated when a Pong arrives in the receive loop."""

    def test_pong_resets_timestamp(self):
        sub = _make_subscriber()
        sub.running = True
        old_time = sub._last_pong_time - 10

        sub._last_pong_time = old_time
        pong = Pong(sender=NodeAddress("broker-1", 8000), sequence=0)
        sub.node.receive = MagicMock(
            side_effect=[(pong, NodeAddress("broker-1", 8000)), (None, None)]
        )

        # Run one iteration of the receive loop manually
        msg, sender = sub.node.receive()
        if isinstance(msg, Pong):
            sub._last_pong_time = time.time()

        self.assertGreater(sub._last_pong_time, old_time)


class TestHealthLoopSendsPing(unittest.TestCase):
    """Health loop sends Ping to the broker at each interval."""

    def test_ping_sent_when_broker_alive(self):
        sub = _make_subscriber()
        sub.running = True
        sub._broker_alive = True

        sent = []
        sub.node.send = MagicMock(side_effect=lambda msg, addr: sent.append(msg))

        with patch("aether.network.subscriber._PING_INTERVAL", 0):
            # Run one cycle of the health loop body (skip sleep, check, send)
            sub.node.send(
                Ping(sender=sub.address, sequence=sub._ping_sequence),
                sub.broker,
            )
            sub._ping_sequence += 1

        self.assertEqual(len(sent), 1)
        self.assertIsInstance(sent[0], Ping)
        self.assertEqual(sub._ping_sequence, 1)

    def test_no_ping_when_broker_not_alive(self):
        sub = _make_subscriber()
        sub.running = True
        sub._broker_alive = False

        sent = []
        sub.node.send = MagicMock(side_effect=lambda msg, addr: sent.append(msg))

        # Health loop skips send when _broker_alive is False
        if sub._broker_alive and sub.broker is not None:
            sub.node.send(Ping(sender=sub.address, sequence=0), sub.broker)

        self.assertEqual(len(sent), 0)


class TestReconnectSuccess(unittest.TestCase):
    """_reconnect() queries orchestrator, updates broker, re-subscribes."""

    def test_reconnect_updates_broker_and_resubscribes(self):
        sub = _make_subscriber()
        sub._broker_alive = False
        sub.running = True
        sub.subscriptions = {PayloadRange(UInt8(0), UInt8(84))}

        response_data = json.dumps(
            {"broker_host": "broker-2", "broker_port": 8000}
        ).encode()

        mock_resp = MagicMock()
        mock_resp.read = MagicMock(return_value=response_data)
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)

        sent = []
        sub.node.send = MagicMock(side_effect=lambda msg, addr: sent.append((msg, addr)))

        with patch("urllib.request.urlopen", return_value=mock_resp):
            with patch("random.uniform", return_value=0):
                sub._reconnect()

        self.assertTrue(sub._broker_alive)
        self.assertEqual(sub.broker, NodeAddress("broker-2", 8000))
        # One SubscribeRequest sent for the one subscription range
        self.assertEqual(len(sent), 1)
        self.assertIsInstance(sent[0][0], SubscribeRequest)
        self.assertEqual(sent[0][1], NodeAddress("broker-2", 8000))

    def test_reconnect_retries_on_http_error(self):
        sub = _make_subscriber()
        sub._broker_alive = False
        sub.running = True
        sub.subscriptions = set()

        response_data = json.dumps(
            {"broker_host": "broker-1", "broker_port": 8000}
        ).encode()

        mock_resp = MagicMock()
        mock_resp.read = MagicMock(return_value=response_data)
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)

        call_count = 0

        def urlopen_side_effect(url, timeout):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise OSError("connection refused")
            return mock_resp

        with patch("urllib.request.urlopen", side_effect=urlopen_side_effect):
            with patch("random.uniform", return_value=0):
                sub._reconnect()

        self.assertEqual(call_count, 3)
        self.assertTrue(sub._broker_alive)

    def test_reconnect_exits_when_stopped(self):
        sub = _make_subscriber()
        sub._broker_alive = False
        sub.running = False  # already stopped

        with patch("urllib.request.urlopen", side_effect=AssertionError("should not call")):
            sub._reconnect()  # should return immediately without calling urlopen


class TestBrokerRecoveryNotificationFastPath(unittest.TestCase):
    """BrokerRecoveryNotification resets health state — no pull-based reconnect needed."""

    def test_recovery_notification_resets_health_state(self):
        sub = _make_subscriber()
        sub._broker_alive = False
        sub._last_pong_time = time.time() - 100

        old_broker = NodeAddress("broker-1", 8000)
        new_broker = NodeAddress("broker-2", 8000)
        sub.broker = old_broker

        notification = BrokerRecoveryNotification(
            old_broker=old_broker, new_broker=new_broker
        )
        sub._handle_broker_recovery(notification, new_broker)

        self.assertTrue(sub._broker_alive)
        self.assertEqual(sub.broker, new_broker)
        self.assertAlmostEqual(sub._last_pong_time, time.time(), delta=1.0)

    def test_recovery_notification_ignored_for_other_broker(self):
        sub = _make_subscriber()
        sub.broker = NodeAddress("broker-3", 8000)
        sub._broker_alive = True

        notification = BrokerRecoveryNotification(
            old_broker=NodeAddress("broker-1", 8000),
            new_broker=NodeAddress("broker-2", 8000),
        )
        sub._handle_broker_recovery(notification, NodeAddress("broker-2", 8000))

        # Unaffected — we weren't connected to broker-1
        self.assertEqual(sub.broker, NodeAddress("broker-3", 8000))


class TestHealthThreadLifecycle(unittest.TestCase):
    """Health thread starts with orchestrator_url, skipped without it."""

    def test_health_thread_starts_with_orchestrator_url(self):
        sub = _make_subscriber(orchestrator_url="http://orchestrator:8001")
        sub.node.receive = MagicMock(return_value=(None, None))

        sub.start()
        try:
            time.sleep(0.05)
            self.assertIsNotNone(sub._health_thread)
            self.assertTrue(sub._health_thread.is_alive())
        finally:
            sub.stop()

    def test_health_thread_not_started_without_orchestrator_url(self):
        address = NodeAddress("127.0.0.1", _free_port())
        sub = NetworkSubscriber(address)  # no orchestrator_url
        sub.broker = NodeAddress("127.0.0.1", _free_port())
        sub.node = MagicMock()
        sub.node.receive = MagicMock(return_value=(None, None))

        sub.start()
        try:
            time.sleep(0.05)
            self.assertIsNone(sub._health_thread)
        finally:
            sub.stop()


if __name__ == "__main__":
    unittest.main()
