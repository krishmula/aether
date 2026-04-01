"""Unit tests for the extended broker status server endpoints.

Covers:
  - POST /recover  (200 success, 400 bad body, 500 no snapshot)
  - GET  /snapshots/{host}/{port}  (200, 404, 400 malformed path)
  - DELETE /deregister on the bootstrap status server

Uses real StatusServer / BootstrapStatusServer bound to ephemeral ports,
with the underlying GossipBroker / BootstrapServer objects patched so no
real networking threads are started.
"""

from __future__ import annotations

import json
import socket
import time
import unittest
import urllib.error
import urllib.request
from unittest.mock import MagicMock, patch

from aether.gossip.bootstrap import BootstrapServer
from aether.gossip.broker import GossipBroker
from aether.gossip.status import BootstrapStatusServer, StatusServer
from aether.network.node import NodeAddress
from aether.snapshot import BrokerSnapshot


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _request(port: int, method: str, path: str, body: dict | None = None):
    """Make an HTTP request and return (status_code, parsed_json)."""
    url = f"http://127.0.0.1:{port}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
        req.add_header("Content-Length", str(len(data)))
    try:
        with urllib.request.urlopen(req, timeout=3) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read())
        except Exception:
            return exc.code, None


def _make_broker_and_server() -> tuple[GossipBroker, StatusServer, int]:
    """Broker (no threads) + StatusServer on a free port."""
    broker_port = _free_port()
    status_port = _free_port()
    broker = GossipBroker(NodeAddress("127.0.0.1", broker_port), http_port=status_port)
    server = StatusServer(broker, status_port)
    return broker, server, status_port


def _make_snapshot(broker_addr: NodeAddress) -> BrokerSnapshot:
    return BrokerSnapshot(
        snapshot_id="test-snap-id",
        broker_address=broker_addr,
        peer_brokers=set(),
        remote_subscribers={},
        seen_message_ids=set(),
        timestamp=time.time(),
    )


# ---------------------------------------------------------------------------
# POST /recover
# ---------------------------------------------------------------------------

class TestPostRecover(unittest.TestCase):

    def setUp(self):
        self.broker, self.server, self.port = _make_broker_and_server()
        self.server.start()

    def tearDown(self):
        self.server.stop()

    def test_recover_200_on_success(self):
        snapshot = _make_snapshot(NodeAddress("127.0.0.1", 7002))
        self.broker.request_snapshot_from_peers = MagicMock(return_value=snapshot)
        self.broker.recover_from_snapshot = MagicMock()

        status, body = _request(self.port, "POST", "/recover", {
            "dead_broker_host": "127.0.0.1",
            "dead_broker_port": 7002,
        })

        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "recovered")
        self.broker.recover_from_snapshot.assert_called_once_with(snapshot)

    def test_recover_400_on_missing_fields(self):
        status, body = _request(self.port, "POST", "/recover", {"wrong": "field"})
        self.assertEqual(status, 400)

    def test_recover_400_on_invalid_json(self):
        url = f"http://127.0.0.1:{self.port}/recover"
        req = urllib.request.Request(url, data=b"not-json", method="POST")
        req.add_header("Content-Type", "application/json")
        try:
            urllib.request.urlopen(req, timeout=3)
        except urllib.error.HTTPError as exc:
            self.assertEqual(exc.code, 400)

    def test_recover_500_when_no_snapshot_available(self):
        self.broker.request_snapshot_from_peers = MagicMock(return_value=None)

        status, body = _request(self.port, "POST", "/recover", {
            "dead_broker_host": "127.0.0.1",
            "dead_broker_port": 7002,
        })

        self.assertEqual(status, 500)
        self.assertIn("no_snapshot_available", body.get("error", ""))

    def test_recover_500_on_exception_during_recovery(self):
        self.broker.request_snapshot_from_peers = MagicMock(
            side_effect=RuntimeError("disk full")
        )

        status, body = _request(self.port, "POST", "/recover", {
            "dead_broker_host": "127.0.0.1",
            "dead_broker_port": 7002,
        })

        self.assertEqual(status, 500)


# ---------------------------------------------------------------------------
# GET /snapshots/{host}/{port}
# ---------------------------------------------------------------------------

class TestGetSnapshots(unittest.TestCase):

    def setUp(self):
        self.broker, self.server, self.port = _make_broker_and_server()
        self.server.start()

    def tearDown(self):
        self.server.stop()

    def test_snapshot_200_when_present(self):
        dead_addr = NodeAddress("127.0.0.1", 7002)
        snap = _make_snapshot(dead_addr)
        self.broker._peer_snapshots[dead_addr] = snap

        status, body = _request(self.port, "GET", "/snapshots/127.0.0.1/7002")

        self.assertEqual(status, 200)
        self.assertEqual(body["snapshot_id"], "test-snap-id")
        self.assertIn("timestamp", body)

    def test_snapshot_404_when_absent(self):
        status, body = _request(self.port, "GET", "/snapshots/127.0.0.1/9999")
        self.assertEqual(status, 404)

    def test_snapshot_400_on_non_numeric_port(self):
        status, body = _request(self.port, "GET", "/snapshots/127.0.0.1/notaport")
        self.assertEqual(status, 400)

    def test_snapshot_404_on_unknown_path_prefix(self):
        # Too few path segments — should return 404 (falls through to default handler)
        status, body = _request(self.port, "GET", "/snapshots/only-one-segment")
        self.assertIn(status, (400, 404))


# ---------------------------------------------------------------------------
# DELETE /deregister on bootstrap status server
# ---------------------------------------------------------------------------

class TestBootstrapDeregister(unittest.TestCase):

    def _make_bootstrap_and_server(self):
        bs_port = _free_port()
        status_port = _free_port()
        bs = BootstrapServer(NodeAddress("127.0.0.1", bs_port))
        bs._status_port = status_port
        server = BootstrapStatusServer(bs, status_port)
        return bs, server, status_port

    def test_deregister_removes_broker(self):
        bs, server, port = self._make_bootstrap_and_server()
        server.start()
        try:
            broker_addr = NodeAddress("127.0.0.1", 8001)
            bs.registered_brokers.add(broker_addr)

            status, body = _request(port, "DELETE", "/deregister", {
                "host": "127.0.0.1",
                "port": 8001,
            })

            self.assertEqual(status, 200)
            self.assertNotIn(broker_addr, bs.registered_brokers)
        finally:
            server.stop()

    def test_deregister_unknown_broker_is_idempotent(self):
        bs, server, port = self._make_bootstrap_and_server()
        server.start()
        try:
            status, body = _request(port, "DELETE", "/deregister", {
                "host": "127.0.0.1",
                "port": 9999,
            })
            self.assertEqual(status, 200)
        finally:
            server.stop()

    def test_deregister_400_on_missing_fields(self):
        bs, server, port = self._make_bootstrap_and_server()
        server.start()
        try:
            status, body = _request(port, "DELETE", "/deregister", {"wrong": "field"})
            self.assertEqual(status, 400)
        finally:
            server.stop()


if __name__ == "__main__":
    unittest.main()
