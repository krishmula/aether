"""Unit tests for the HTTP /status endpoints (pubsub.gossip.status).

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

from pubsub.core.payload_range import PayloadRange
from pubsub.core.uint8 import UInt8
from pubsub.gossip.bootstrap import BootstrapServer
from pubsub.gossip.broker import GossipBroker
from pubsub.gossip.status import BootstrapStatusServer, StatusServer
from pubsub.network.node import NodeAddress


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


if __name__ == "__main__":
    unittest.main()
