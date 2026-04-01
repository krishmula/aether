"""Unit tests for NetworkPublisher dead-broker cooldown (item 11).

NetworkNode is replaced with a MagicMock so no real TCP sockets are opened.
time.time() is patched to control cooldown expiry without sleeping.
"""

from __future__ import annotations

import socket
import unittest
from unittest.mock import MagicMock, patch

from aether.core.message import Message
from aether.core.uint8 import UInt8
from aether.network.node import NodeAddress
from aether.network.publisher import NetworkPublisher, _DEAD_BROKER_COOLDOWN


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _make_publisher(*broker_ports: int) -> NetworkPublisher:
    """Create a publisher with mocked NetworkNode and N broker addresses."""
    pub_addr = NodeAddress("127.0.0.1", _free_port())
    broker_addrs = [NodeAddress("127.0.0.1", p) for p in broker_ports]
    pub = NetworkPublisher(pub_addr, broker_addrs)
    pub.network = MagicMock()
    return pub


def _msg() -> Message:
    return Message(UInt8(42))


class TestNormalPublish(unittest.TestCase):
    """Publishes succeed when no brokers are dead."""

    def test_sends_to_broker(self):
        pub = _make_publisher(_free_port())
        pub.publish(_msg(), redundancy=1)
        pub.network.send.assert_called_once()

    def test_redundancy_caps_at_broker_count(self):
        pub = _make_publisher(_free_port(), _free_port())
        pub.publish(_msg(), redundancy=5)
        self.assertEqual(pub.network.send.call_count, 2)


class TestBrokerMarkedDeadOnError(unittest.TestCase):
    """Failed send marks broker dead and excludes it during cooldown."""

    def test_failed_broker_added_to_dead_set(self):
        port = _free_port()
        pub = _make_publisher(port)
        broker_addr = pub.broker_addresses[0]

        pub.network.send.side_effect = OSError("connection refused")
        pub.publish(_msg(), redundancy=1)

        self.assertIn(broker_addr, pub._dead_brokers)

    def test_dead_broker_skipped_during_cooldown(self):
        port1, port2 = _free_port(), _free_port()
        pub = _make_publisher(port1, port2)
        broker1 = pub.broker_addresses[0]
        broker2 = pub.broker_addresses[1]

        # Mark broker1 dead right now
        pub._dead_brokers[broker1] = 1_000_000.0

        with patch("aether.network.publisher.time") as mock_time:
            # Current time is 10s after failure — still in cooldown
            mock_time.time.return_value = 1_000_000.0 + 10

            # With redundancy=1 only live brokers are eligible — must be broker2
            for _ in range(20):  # enough iterations to rule out randomness
                pub.network.send.reset_mock()
                pub.publish(_msg(), redundancy=1)
                call_args = pub.network.send.call_args
                if call_args:
                    sent_to = call_args[0][1]
                    self.assertNotEqual(sent_to, broker1)

    def test_all_brokers_dead_returns_zero(self):
        port = _free_port()
        pub = _make_publisher(port)
        broker_addr = pub.broker_addresses[0]
        pub._dead_brokers[broker_addr] = 1_000_000.0

        with patch("aether.network.publisher.time") as mock_time:
            mock_time.time.return_value = 1_000_000.0 + 5  # still in cooldown

            result = pub.publish(_msg(), redundancy=1)

        self.assertEqual(result, 0)
        pub.network.send.assert_not_called()


class TestCooldownExpiry(unittest.TestCase):
    """After cooldown expires the broker is retried and cleared on success."""

    def test_broker_retried_after_cooldown(self):
        port = _free_port()
        pub = _make_publisher(port)
        broker_addr = pub.broker_addresses[0]

        # Mark dead at T=0
        pub._dead_brokers[broker_addr] = 0.0

        with patch("aether.network.publisher.time") as mock_time:
            # Now it's T = cooldown + 1 — expired
            mock_time.time.return_value = _DEAD_BROKER_COOLDOWN + 1

            pub.publish(_msg(), redundancy=1)

        pub.network.send.assert_called_once()

    def test_successful_retry_clears_dead_entry(self):
        port = _free_port()
        pub = _make_publisher(port)
        broker_addr = pub.broker_addresses[0]

        pub._dead_brokers[broker_addr] = 0.0

        with patch("aether.network.publisher.time") as mock_time:
            mock_time.time.return_value = _DEAD_BROKER_COOLDOWN + 1
            pub.publish(_msg(), redundancy=1)

        # Send succeeded (no side_effect) → should be cleared
        self.assertNotIn(broker_addr, pub._dead_brokers)

    def test_failed_retry_resets_cooldown_timestamp(self):
        port = _free_port()
        pub = _make_publisher(port)
        broker_addr = pub.broker_addresses[0]

        pub._dead_brokers[broker_addr] = 0.0
        pub.network.send.side_effect = OSError("still down")

        with patch("aether.network.publisher.time") as mock_time:
            mock_time.time.return_value = _DEAD_BROKER_COOLDOWN + 1
            pub.publish(_msg(), redundancy=1)

        # Timestamp should be updated to the new failure time
        self.assertAlmostEqual(
            pub._dead_brokers[broker_addr], _DEAD_BROKER_COOLDOWN + 1, delta=1
        )


class TestSuccessfulSendClearsDead(unittest.TestCase):
    """A send that succeeds removes the broker from the dead set."""

    def test_success_removes_from_dead_brokers(self):
        port = _free_port()
        pub = _make_publisher(port)
        broker_addr = pub.broker_addresses[0]

        # Pre-populate as dead but already past cooldown
        pub._dead_brokers[broker_addr] = 0.0

        with patch("aether.network.publisher.time") as mock_time:
            mock_time.time.return_value = _DEAD_BROKER_COOLDOWN + 1
            pub.publish(_msg(), redundancy=1)

        self.assertNotIn(broker_addr, pub._dead_brokers)


if __name__ == "__main__":
    unittest.main()
