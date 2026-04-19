"""Unit tests for the HTTP /status endpoints (aether.gossip.status).

Each test spins up a real StatusServer or BootstrapStatusServer bound to an
ephemeral port in-process, hits it with urllib, and asserts on the response.
No daemon threads from GossipBroker.start() or BootstrapServer.start() are
involved — we construct the objects only, which keeps tests fast and leak-free.
"""

import json
import socket
import unittest
import urllib.error
import urllib.request

from aether.core.payload_range import PayloadRange
from aether.core.uint8 import UInt8
from aether.gossip.bootstrap import BootstrapServer
from aether.gossip.broker import GossipBroker
from aether.gossip.status import (
    BootstrapStatusServer,
    PublisherStatusServer,
    StatusServer,
    SubscriberStatusServer,
)
from aether.network.node import NodeAddress
from aether.network.publisher import NetworkPublisher
from aether.network.subscriber import NetworkSubscriber


def _free_port() -> int:
    """Return an OS-assigned free TCP port."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _get(port: int, path: str = "/status"):
    """Make a GET request and return (http_response, parsed_json_or_None)."""
    url = f"http://127.0.0.1:{port}{path}"
    try:
        with urllib.request.urlopen(url, timeout=3) as resp:
            body = resp.read().decode("utf-8")
            return resp, json.loads(body)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8")
        try:
            return exc, json.loads(body)
        except json.JSONDecodeError:
            return exc, None


def _post(port: int, path: str):
    """Make a POST request and return (http_response, parsed_json_or_None)."""
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=b"{}",
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=3) as resp:
            body = resp.read().decode("utf-8")
            return resp, json.loads(body)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8")
        try:
            return exc, json.loads(body)
        except json.JSONDecodeError:
            return exc, None


class TestStatusServer(unittest.TestCase):
    """Tests for StatusServer / _StatusHandler."""

    def _make_broker_and_server(self) -> tuple[GossipBroker, StatusServer, int]:
        """Create a broker (no threads started) and a StatusServer on a free port."""
        broker_port = _free_port()
        status_port = _free_port()
        broker = GossipBroker(
            NodeAddress("127.0.0.1", broker_port),
            http_port=status_port,
        )
        server = StatusServer(broker, status_port)
        return broker, server, status_port

    # ------------------------------------------------------------------ #
    # Test 1 — basic HTTP response shape                                   #
    # ------------------------------------------------------------------ #

    def test_status_returns_200_json(self) -> None:
        """GET /status returns HTTP 200 with Content-Type: application/json."""
        broker, server, port = self._make_broker_and_server()
        server.start()
        try:
            resp, data = _get(port, "/status")
            self.assertEqual(resp.status, 200)
            ct = resp.headers.get("Content-Type", "")
            self.assertIn("application/json", ct)
            self.assertIsInstance(data, dict)
        finally:
            server.stop()
            broker.network.close()

    # ------------------------------------------------------------------ #
    # Test 2 — all expected top-level keys present                         #
    # ------------------------------------------------------------------ #

    def test_status_fields_shape(self) -> None:
        """Response contains all required top-level keys with correct types."""
        broker, server, port = self._make_broker_and_server()
        server.start()
        try:
            _, data = _get(port, "/status")
            required_keys = {
                "broker",
                "host",
                "port",
                "status_port",
                "peers",
                "peer_count",
                "subscribers",
                "messages_processed",
                "seen_message_ids",
                "uptime_seconds",
                "snapshot_state",
            }
            missing = required_keys - data.keys()
            self.assertEqual(missing, set(), f"Missing keys: {missing}")

            # Type assertions
            self.assertIsInstance(data["peers"], list)
            self.assertIsInstance(data["peer_count"], int)
            self.assertIsInstance(data["subscribers"], dict)
            self.assertIn("count", data["subscribers"])
            self.assertIsInstance(data["messages_processed"], int)
            self.assertIsInstance(data["seen_message_ids"], int)
            self.assertIsInstance(data["uptime_seconds"], float)
            self.assertIn(data["snapshot_state"], ("idle", "recording"))
        finally:
            server.stop()
            broker.network.close()

    # ------------------------------------------------------------------ #
    # Test 3 — live state is reflected correctly                           #
    # ------------------------------------------------------------------ #

    def test_status_reflects_live_state(self) -> None:
        """Mutating broker state is immediately visible in /status responses."""
        broker, server, port = self._make_broker_and_server()
        server.start()
        try:
            # Baseline — no peers, no subscribers
            _, before = _get(port, "/status")
            self.assertEqual(before["peer_count"], 0)
            self.assertEqual(before["subscribers"]["count"], 0)

            # Add a fake peer and a fake remote subscriber
            peer = NodeAddress("127.0.0.1", _free_port())
            sub_addr = NodeAddress("127.0.0.1", _free_port())
            pr = PayloadRange(UInt8(0), UInt8(50))

            with broker._lock:
                broker.peer_brokers.add(peer)
                broker._remote_subscribers[sub_addr] = {pr}

            _, after = _get(port, "/status")
            self.assertEqual(after["peer_count"], 1)
            self.assertEqual(after["subscribers"]["count"], 1)
            self.assertIn(str(peer), after["peers"])
        finally:
            server.stop()
            broker.network.close()

    # ------------------------------------------------------------------ #
    # Test 4 — snapshot_state flips correctly                              #
    # ------------------------------------------------------------------ #

    def test_snapshot_state_field(self) -> None:
        """snapshot_state is 'idle' normally and 'recording' mid-snapshot."""
        broker, server, port = self._make_broker_and_server()
        server.start()
        try:
            _, data = _get(port, "/status")
            self.assertEqual(data["snapshot_state"], "idle")

            # Simulate a snapshot in progress
            with broker._lock:
                broker._snapshot_in_progress = "fake-snapshot-id"

            _, data = _get(port, "/status")
            self.assertEqual(data["snapshot_state"], "recording")

            # Simulate snapshot completion
            with broker._lock:
                broker._snapshot_in_progress = None

            _, data = _get(port, "/status")
            self.assertEqual(data["snapshot_state"], "idle")
        finally:
            server.stop()
            broker.network.close()

    # ------------------------------------------------------------------ #
    # Test 5 — unknown paths return 404                                    #
    # ------------------------------------------------------------------ #

    def test_unknown_path_returns_404(self) -> None:
        """GET on any path other than /status returns HTTP 404 with an error body."""
        broker, server, port = self._make_broker_and_server()
        server.start()
        try:
            for bad_path in ("/", "/health", "/metrics", "/notfound"):
                resp, data = _get(port, bad_path)
                self.assertEqual(
                    resp.status,
                    404,
                    f"Expected 404 for {bad_path}, got {resp.status}",
                )
                self.assertIsInstance(data, dict)
                self.assertIn("error", data)
        finally:
            server.stop()
            broker.network.close()


class TestBootstrapStatusServer(unittest.TestCase):
    """Tests for BootstrapStatusServer / _BootstrapStatusHandler."""

    def _make_bootstrap_and_server(
        self,
    ) -> tuple[BootstrapServer, BootstrapStatusServer, int]:
        """Create a BootstrapServer (no threads started) and a status server."""
        bootstrap_port = _free_port()
        status_port = _free_port()
        bootstrap = BootstrapServer(
            NodeAddress("127.0.0.1", bootstrap_port),
            http_port=status_port,
        )
        server = BootstrapStatusServer(bootstrap, status_port)
        return bootstrap, server, status_port

    # ------------------------------------------------------------------ #
    # Test 6 — basic HTTP response shape                                   #
    # ------------------------------------------------------------------ #

    def test_bootstrap_status_returns_200_json(self) -> None:
        """GET /status returns HTTP 200 with Content-Type: application/json."""
        bootstrap, server, port = self._make_bootstrap_and_server()
        server.start()
        try:
            resp, data = _get(port, "/status")
            self.assertEqual(resp.status, 200)
            ct = resp.headers.get("Content-Type", "")
            self.assertIn("application/json", ct)
            self.assertIsInstance(data, dict)
        finally:
            server.stop()
            bootstrap.network.close()

    # ------------------------------------------------------------------ #
    # Test 7 — all expected top-level keys present                         #
    # ------------------------------------------------------------------ #

    def test_bootstrap_status_fields_shape(self) -> None:
        """Response contains all required top-level keys with correct types."""
        bootstrap, server, port = self._make_bootstrap_and_server()
        server.start()
        try:
            _, data = _get(port, "/status")
            required_keys = {
                "host",
                "port",
                "status_port",
                "registered_brokers",
                "broker_count",
                "uptime_seconds",
            }
            missing = required_keys - data.keys()
            self.assertEqual(missing, set(), f"Missing keys: {missing}")

            self.assertIsInstance(data["registered_brokers"], list)
            self.assertIsInstance(data["broker_count"], int)
            self.assertIsInstance(data["uptime_seconds"], float)
        finally:
            server.stop()
            bootstrap.network.close()

    # ------------------------------------------------------------------ #
    # Test 8 — registered brokers reflected correctly                      #
    # ------------------------------------------------------------------ #

    def test_bootstrap_status_reflects_registered_brokers(self) -> None:
        """Registering brokers is immediately visible in /status responses."""
        bootstrap, server, port = self._make_bootstrap_and_server()
        server.start()
        try:
            _, before = _get(port, "/status")
            self.assertEqual(before["broker_count"], 0)
            self.assertEqual(before["registered_brokers"], [])

            # Register two fake brokers directly
            broker1 = NodeAddress("127.0.0.1", _free_port())
            broker2 = NodeAddress("127.0.0.1", _free_port())
            with bootstrap._lock:
                bootstrap.registered_brokers.add(broker1)
                bootstrap.registered_brokers.add(broker2)

            _, after = _get(port, "/status")
            self.assertEqual(after["broker_count"], 2)
            self.assertIn(str(broker1), after["registered_brokers"])
            self.assertIn(str(broker2), after["registered_brokers"])
        finally:
            server.stop()
            bootstrap.network.close()


class TestSubscriberStatusServer(unittest.TestCase):
    """Tests for SubscriberStatusServer / _SubscriberStatusHandler."""

    def _make_subscriber_and_server(
        self,
    ) -> tuple[NetworkSubscriber, SubscriberStatusServer, int]:
        """Create a subscriber and a status server on free ports."""
        sub_port = _free_port()
        status_port = _free_port()
        sub = NetworkSubscriber(NodeAddress("127.0.0.1", sub_port))
        sub._status_port = status_port
        server = SubscriberStatusServer(sub, status_port)
        return sub, server, status_port

    # ------------------------------------------------------------------ #
    # Test 9 — basic HTTP response shape                                   #
    # ------------------------------------------------------------------ #

    def test_status_returns_200_json(self) -> None:
        """GET /status returns HTTP 200 with Content-Type: application/json."""
        sub, server, port = self._make_subscriber_and_server()
        server.start()
        try:
            resp, data = _get(port, "/status")
            self.assertEqual(resp.status, 200)
            ct = resp.headers.get("Content-Type", "")
            self.assertIn("application/json", ct)
            self.assertIsInstance(data, dict)
        finally:
            server.stop()
            sub.node.close()

    # ------------------------------------------------------------------ #
    # Test 10 — all expected top-level keys present                        #
    # ------------------------------------------------------------------ #

    def test_status_fields_shape(self) -> None:
        """Response contains all required top-level keys with correct types."""
        sub, server, port = self._make_subscriber_and_server()
        server.start()
        try:
            _, data = _get(port, "/status")
            required_keys = {
                "subscriber",
                "host",
                "port",
                "status_port",
                "broker",
                "subscriptions",
                "total_received",
                "running",
                "uptime_seconds",
                "latency_us",
                "latency_samples_us",
            }
            missing = required_keys - data.keys()
            self.assertEqual(missing, set(), f"Missing keys: {missing}")

            self.assertIsInstance(data["subscriptions"], list)
            self.assertIsInstance(data["total_received"], int)
            self.assertIsInstance(data["running"], bool)
            self.assertIsInstance(data["uptime_seconds"], float)
            self.assertIsInstance(data["latency_us"], dict)
            self.assertIsInstance(data["latency_samples_us"], list)
        finally:
            server.stop()
            sub.node.close()

    # ------------------------------------------------------------------ #
    # Test 11 — live state is reflected correctly                          #
    # ------------------------------------------------------------------ #

    def test_status_reflects_live_state(self) -> None:
        """Mutating subscriber state is immediately visible in /status."""
        sub, server, port = self._make_subscriber_and_server()
        server.start()
        try:
            # Baseline — no broker, not running
            _, before = _get(port, "/status")
            self.assertIsNone(before["broker"])
            self.assertFalse(before["running"])
            self.assertEqual(before["total_received"], 0)

            # Connect to a broker and start
            broker_addr = NodeAddress("127.0.0.1", _free_port())
            sub.connect_to_broker(broker_addr)
            sub.running = True
            sub.total_received = 42

            _, after = _get(port, "/status")
            self.assertEqual(after["broker"], str(broker_addr))
            self.assertTrue(after["running"])
            self.assertEqual(after["total_received"], 42)
        finally:
            server.stop()
            sub.node.close()

    def test_status_reports_latency_samples_and_percentiles(self) -> None:
        """Subscriber /status exposes raw latency samples and percentile summary."""
        sub, server, port = self._make_subscriber_and_server()
        sub._latency_samples_ns.extend([1_000_000, 2_000_000, 3_000_000])
        server.start()
        try:
            _, data = _get(port, "/status")
            self.assertEqual(data["latency_us"]["sample_count"], 3)
            self.assertEqual(data["latency_us"]["p50"], 2000.0)
            self.assertEqual(data["latency_samples_us"], [1000.0, 2000.0, 3000.0])
        finally:
            server.stop()
            sub.node.close()

    def test_latency_reset_clears_subscriber_samples(self) -> None:
        """POST /latency/reset clears the in-memory subscriber latency buffer."""
        sub, server, port = self._make_subscriber_and_server()
        sub._latency_samples_ns.extend([1_000_000, 2_000_000])
        server.start()
        try:
            resp, data = _post(port, "/latency/reset")
            self.assertEqual(resp.status, 200)
            self.assertEqual(data["status"], "ok")
            self.assertEqual(data["cleared_samples"], 2)

            _, after = _get(port, "/status")
            self.assertEqual(after["latency_us"]["sample_count"], 0)
            self.assertEqual(after["latency_samples_us"], [])
        finally:
            server.stop()
            sub.node.close()

    # ------------------------------------------------------------------ #
    # Test 12 — unknown paths return 404                                   #
    # ------------------------------------------------------------------ #

    def test_unknown_path_returns_404(self) -> None:
        """GET on any path other than /status returns HTTP 404."""
        sub, server, port = self._make_subscriber_and_server()
        server.start()
        try:
            for bad_path in ("/", "/health", "/metrics"):
                resp, data = _get(port, bad_path)
                self.assertEqual(resp.status, 404)
                self.assertIsInstance(data, dict)
                self.assertIn("error", data)
        finally:
            server.stop()
            sub.node.close()


class TestPublisherStatusServer(unittest.TestCase):
    """Tests for PublisherStatusServer / _PublisherStatusHandler."""

    def _make_publisher_and_server(
        self,
    ) -> tuple[NetworkPublisher, PublisherStatusServer, int]:
        """Create a publisher and a status server on free ports."""
        pub_port = _free_port()
        status_port = _free_port()
        broker_addr = NodeAddress("127.0.0.1", _free_port())
        pub = NetworkPublisher(
            NodeAddress("127.0.0.1", pub_port),
            [broker_addr],
        )
        pub._status_port = status_port
        server = PublisherStatusServer(pub, status_port)
        return pub, server, status_port

    # ------------------------------------------------------------------ #
    # Test 13 — basic HTTP response shape                                  #
    # ------------------------------------------------------------------ #

    def test_status_returns_200_json(self) -> None:
        """GET /status returns HTTP 200 with Content-Type: application/json."""
        pub, server, port = self._make_publisher_and_server()
        server.start()
        try:
            resp, data = _get(port, "/status")
            self.assertEqual(resp.status, 200)
            ct = resp.headers.get("Content-Type", "")
            self.assertIn("application/json", ct)
            self.assertIsInstance(data, dict)
        finally:
            server.stop()
            pub.network.close()

    # ------------------------------------------------------------------ #
    # Test 14 — all expected top-level keys present                        #
    # ------------------------------------------------------------------ #

    def test_status_fields_shape(self) -> None:
        """Response contains all required top-level keys with correct types."""
        pub, server, port = self._make_publisher_and_server()
        server.start()
        try:
            _, data = _get(port, "/status")
            required_keys = {
                "publisher",
                "host",
                "port",
                "status_port",
                "brokers",
                "broker_count",
                "total_sent",
                "uptime_seconds",
            }
            missing = required_keys - data.keys()
            self.assertEqual(missing, set(), f"Missing keys: {missing}")

            self.assertIsInstance(data["brokers"], list)
            self.assertIsInstance(data["broker_count"], int)
            self.assertIsInstance(data["total_sent"], int)
            self.assertIsInstance(data["uptime_seconds"], float)
        finally:
            server.stop()
            pub.network.close()

    # ------------------------------------------------------------------ #
    # Test 15 — unknown paths return 404                                   #
    # ------------------------------------------------------------------ #

    def test_unknown_path_returns_404(self) -> None:
        """GET on any path other than /status returns HTTP 404."""
        pub, server, port = self._make_publisher_and_server()
        server.start()
        try:
            for bad_path in ("/", "/health", "/metrics"):
                resp, data = _get(port, bad_path)
                self.assertEqual(resp.status, 404)
                self.assertIsInstance(data, dict)
                self.assertIn("error", data)
        finally:
            server.stop()
            pub.network.close()


if __name__ == "__main__":
    unittest.main()
