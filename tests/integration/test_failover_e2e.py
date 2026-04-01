"""End-to-end failover integration test.

Uses real GossipBroker instances on localhost loopback ports with a tiny
in-process HTTP server standing in for the orchestrator. No Docker required.

What this test covers end-to-end:
  1. Snapshot taken by broker-2, replicated to broker-1 and broker-3
  2. GET /snapshots/{host}/{port} returns the correct data from a peer
  3. Replacement broker recovers full state via POST /recover
  4. Subscriber detects broker death (accelerated ping timeout) and reconnects
     to the replacement broker by querying the mock orchestrator
"""

from __future__ import annotations

import json
import socket
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

from aether.core.payload_range import PayloadRange
from aether.core.uint8 import UInt8
from aether.gossip.broker import GossipBroker
from aether.gossip.status import StatusServer
from aether.network.node import NodeAddress
from aether.network.subscriber import NetworkSubscriber
import aether.network.subscriber as subscriber_module


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ---------------------------------------------------------------------------
# Tiny mock orchestrator HTTP server
# ---------------------------------------------------------------------------

class _MockOrchestratorHandler(BaseHTTPRequestHandler):
    """Serves GET /api/assignment?subscriber_id=N with a fixed response."""

    response_body: bytes = b"{}"

    def do_GET(self):  # noqa: N802
        if self.path.startswith("/api/assignment"):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(self.response_body)))
            self.end_headers()
            self.wfile.write(self.response_body)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *args):  # suppress access logs
        pass


def _start_mock_orchestrator(broker_host: str, broker_port: int) -> tuple[HTTPServer, int]:
    port = _free_port()

    class Handler(_MockOrchestratorHandler):
        response_body = json.dumps(
            {"broker_host": broker_host, "broker_port": broker_port}
        ).encode()

    server = HTTPServer(("127.0.0.1", port), Handler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return server, port


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _broker_mesh(*brokers: GossipBroker) -> None:
    """Wire every broker as a peer of every other broker."""
    for b in brokers:
        for other in brokers:
            if other is not b:
                b.add_peer(other.address)


def _wait_for_status(status_port: int, timeout: float = 5.0) -> bool:
    """Poll GET /status until 200 or timeout."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", status_port), timeout=0.5):
                pass
            url = f"http://127.0.0.1:{status_port}/status"
            import urllib.request
            with urllib.request.urlopen(url, timeout=1) as r:
                if r.status == 200:
                    return True
        except Exception:
            pass
        time.sleep(0.1)
    return False


# ---------------------------------------------------------------------------
# Test: GET /snapshots returns snapshot replicated to a peer
# ---------------------------------------------------------------------------

class TestGetSnapshotsEndToEnd(unittest.TestCase):
    """Broker-2 takes snapshot, replicates to broker-1; verify via HTTP."""

    def setUp(self):
        p1, p2 = _free_port(), _free_port()
        sp1, sp2 = _free_port(), _free_port()
        self.addr1 = NodeAddress("127.0.0.1", p1)
        self.addr2 = NodeAddress("127.0.0.1", p2)

        self.b1 = GossipBroker(self.addr1, snapshot_interval=9999, http_port=sp1)
        self.b2 = GossipBroker(self.addr2, snapshot_interval=9999, http_port=sp2)
        _broker_mesh(self.b1, self.b2)

        self.b1.start()
        self.b2.start()
        time.sleep(0.3)

        self.sp1 = sp1

    def tearDown(self):
        self.b1.stop()
        self.b2.stop()

    def test_peer_exposes_replicated_snapshot(self):
        # Add a fake subscriber to broker-2 so the snapshot has something in it
        fake_sub = NodeAddress("127.0.0.1", _free_port())
        self.b2._remote_subscribers[fake_sub] = {PayloadRange(UInt8(0), UInt8(127))}

        # Trigger snapshot on broker-2 — it will replicate to broker-1
        self.b2.initiate_snapshot()
        time.sleep(1.0)  # let replication complete

        # broker-1's status server should now expose broker-2's snapshot
        import urllib.request
        url = f"http://127.0.0.1:{self.sp1}/snapshots/127.0.0.1/{self.addr2.port}"
        with urllib.request.urlopen(url, timeout=3) as resp:
            body = json.loads(resp.read())

        self.assertIn("snapshot_id", body)
        self.assertIn("timestamp", body)
        self.assertIn(str(fake_sub), str(body.get("remote_subscribers", {})))


# ---------------------------------------------------------------------------
# Test: POST /recover restores subscriber mapping from peer snapshot
# ---------------------------------------------------------------------------

class TestPostRecoverEndToEnd(unittest.TestCase):
    """Full replacement path: snapshot → broker stop → new broker recovers state."""

    def setUp(self):
        p1, p2, p3 = _free_port(), _free_port(), _free_port()
        sp1, sp2, sp3 = _free_port(), _free_port(), _free_port()

        self.addr1 = NodeAddress("127.0.0.1", p1)
        self.addr2 = NodeAddress("127.0.0.1", p2)
        self.addr3 = NodeAddress("127.0.0.1", p3)

        self.b1 = GossipBroker(self.addr1, snapshot_interval=9999, http_port=sp1)
        self.b2 = GossipBroker(self.addr2, snapshot_interval=9999, http_port=sp2)
        self.b3 = GossipBroker(self.addr3, snapshot_interval=9999, http_port=sp3)
        _broker_mesh(self.b1, self.b2, self.b3)

        self.b1.start()
        self.b2.start()
        self.b3.start()
        time.sleep(0.3)

        self.sp1 = sp1
        self.sp3 = sp3

    def tearDown(self):
        self.b1.stop()
        try:
            self.b3.stop()
        except Exception:
            pass
        if hasattr(self, "replacement") and self.replacement:
            try:
                self.replacement.stop()
            except Exception:
                pass

    def test_replacement_recovers_subscriber_mapping(self):
        fake_sub = NodeAddress("127.0.0.1", _free_port())
        self.b2._remote_subscribers[fake_sub] = {PayloadRange(UInt8(0), UInt8(84))}

        # Snapshot broker-2; wait for replication to b1 + b3
        self.b2.initiate_snapshot()
        time.sleep(1.0)

        # Verify at least one peer has the snapshot
        has_snap = (
            self.addr2 in self.b1._peer_snapshots
            or self.addr2 in self.b3._peer_snapshots
        )
        self.assertTrue(has_snap, "no peer received broker-2's snapshot")

        # Kill broker-2
        self.b2.stop()
        time.sleep(0.2)

        # Spin up replacement on the same address, connected to surviving peers
        replacement_sp = _free_port()
        self.replacement = GossipBroker(
            self.addr2, snapshot_interval=9999, http_port=replacement_sp
        )
        self.replacement.add_peer(self.addr1)
        self.replacement.add_peer(self.addr3)
        self.replacement.start()

        # Wait for replacement's HTTP server to come up
        self.assertTrue(
            _wait_for_status(replacement_sp, timeout=5.0),
            "replacement broker HTTP server did not start",
        )

        # POST /recover
        import urllib.request
        body = json.dumps({
            "dead_broker_host": "127.0.0.1",
            "dead_broker_port": self.addr2.port,
        }).encode()
        req = urllib.request.Request(
            f"http://127.0.0.1:{replacement_sp}/recover",
            data=body,
            method="POST",
        )
        req.add_header("Content-Type", "application/json")
        req.add_header("Content-Length", str(len(body)))
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read())

        self.assertEqual(result["status"], "recovered")
        self.assertIn(fake_sub, self.replacement._remote_subscribers)


# ---------------------------------------------------------------------------
# Test: Subscriber reconnects after broker death (accelerated timeouts)
# ---------------------------------------------------------------------------

class TestSubscriberReconnectEndToEnd(unittest.TestCase):
    """Subscriber detects dead broker via Ping timeout and reconnects."""

    def setUp(self):
        # Two brokers: subscriber starts on b1, should move to b2 after b1 dies
        p1, p2 = _free_port(), _free_port()
        sp1 = _free_port()
        self.addr1 = NodeAddress("127.0.0.1", p1)
        self.addr2 = NodeAddress("127.0.0.1", p2)

        self.b1 = GossipBroker(self.addr1, snapshot_interval=9999, http_port=sp1)
        self.b2 = GossipBroker(self.addr2, snapshot_interval=9999)
        _broker_mesh(self.b1, self.b2)
        self.b1.start()
        self.b2.start()
        time.sleep(0.3)

        # Mock orchestrator that always points to b2
        self.mock_server, orch_port = _start_mock_orchestrator(
            "127.0.0.1", p2
        )
        self.orch_url = f"http://127.0.0.1:{orch_port}"

    def tearDown(self):
        self.mock_server.shutdown()
        self.b1.stop()
        self.b2.stop()
        if hasattr(self, "sub"):
            self.sub.stop()

    def test_subscriber_reconnects_to_new_broker(self):
        sub_port = _free_port()
        self.sub = NetworkSubscriber(
            NodeAddress("127.0.0.1", sub_port),
            orchestrator_url=self.orch_url,
            subscriber_id=1,
        )
        self.sub.connect_to_broker(self.addr1)

        payload_range = PayloadRange(UInt8(0), UInt8(84))
        ok = self.sub.subscribe(payload_range)
        self.assertTrue(ok, "initial subscribe failed")

        # Accelerate ping timeouts so the test completes in ~2s not 15s
        original_interval = subscriber_module._PING_INTERVAL
        original_timeout = subscriber_module._PING_TIMEOUT
        subscriber_module._PING_INTERVAL = 0.2
        subscriber_module._PING_TIMEOUT = 0.6

        try:
            self.sub.start()
            time.sleep(0.1)

            # Kill broker-1 — pings will stop getting Pong responses
            self.b1.stop()

            # Wait for the subscriber to detect the failure and reconnect
            deadline = time.time() + 10.0
            while time.time() < deadline:
                if self.sub.broker == self.addr2:
                    break
                time.sleep(0.1)

            self.assertEqual(
                self.sub.broker, self.addr2,
                f"subscriber still pointing at {self.sub.broker} after broker-1 died",
            )
            # broker-2 should have received a SubscribeRequest
            self.assertIn(
                NodeAddress("127.0.0.1", sub_port),
                self.b2._remote_subscribers,
            )
        finally:
            subscriber_module._PING_INTERVAL = original_interval
            subscriber_module._PING_TIMEOUT = original_timeout


if __name__ == "__main__":
    unittest.main()
