"""HTTP status servers for GossipBroker and BootstrapServer.

Each runs a lightweight ThreadingHTTPServer on a separate daemon thread
alongside the component's TCP socket server.  Both serve a single endpoint:

    GET /status  →  200 JSON with live component state

This is the data source for the orchestration control plane (Phase 2) and the
React dashboard (Phase 3).  No third-party dependencies — stdlib only.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING, Any

from aether.network.node import NodeAddress

if TYPE_CHECKING:
    from aether.gossip.bootstrap import BootstrapServer
    from aether.gossip.broker import GossipBroker
    from aether.network.publisher import NetworkPublisher
    from aether.network.subscriber import NetworkSubscriber
    from aether.snapshot import BrokerSnapshot

logger = logging.getLogger(__name__)


def _serialize_snapshot(snapshot: BrokerSnapshot) -> dict:
    """Serialize a BrokerSnapshot to a JSON-safe dict.

    NodeAddress → "host:port" string, PayloadRange → {"low": n, "high": n}.
    The orchestrator only needs `timestamp` for the Path A/B decision, but we
    serialize the full snapshot so callers have the option to inspect state.
    """
    return {
        "snapshot_id": snapshot.snapshot_id,
        "broker_address": str(snapshot.broker_address),
        "peer_brokers": [str(p) for p in snapshot.peer_brokers],
        "remote_subscribers": {
            str(addr): [{"low": int(pr.low), "high": int(pr.high)} for pr in ranges]
            for addr, ranges in snapshot.remote_subscribers.items()
        },
        "seen_message_ids": list(snapshot.seen_message_ids),
        "timestamp": snapshot.timestamp,
    }


class _StatusHandler(BaseHTTPRequestHandler):
    """Minimal HTTP request handler — only GET /status is supported."""

    # Injected by StatusServer before the server is started.
    broker: GossipBroker

    # ------------------------------------------------------------------ #
    # Request routing                                                       #
    # ------------------------------------------------------------------ #

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/status":
            self._handle_status()
        elif self.path == "/peer-snapshots":
            self._handle_list_peer_snapshots()
        elif self.path.startswith("/snapshots/"):
            self._handle_get_snapshot()
        else:
            self._send_json({"error": "not found"}, status=404)

    def do_POST(self) -> None:  # noqa: N802
        if self.path == "/recover":
            self._handle_recover()
        else:
            self._send_json({"error": "not found"}, status=404)

    # ------------------------------------------------------------------ #
    # /status implementation                                               #
    # ------------------------------------------------------------------ #

    def _handle_status(self) -> None:
        broker = self.broker
        now = time.time()

        with broker._lock:
            peers = [str(p) for p in broker.peer_brokers]
            subscriber_count = len(broker._remote_subscribers)
            seen_count = len(broker._seen_set)
            snapshot_state = (
                "recording" if broker._snapshot_in_progress is not None else "idle"
            )
            latest = broker._latest_local_snapshot

        uptime = round(now - broker._start_time, 1)

        payload: dict[str, Any] = {
            "broker": str(broker.address),
            "host": broker.address.host,
            "port": broker.address.port,
            "status_port": broker._status_port,
            "peers": peers,
            "peer_count": len(peers),
            "subscribers": {"count": subscriber_count},
            "messages_processed": broker._messages_processed,
            "seen_message_ids": seen_count,
            "uptime_seconds": uptime,
            "snapshot_state": snapshot_state,
            "latest_snapshot": (
                {
                    "snapshot_id": latest.snapshot_id,
                    "timestamp": latest.timestamp,
                    "peer_count": len(latest.peer_brokers),
                }
                if latest is not None
                else None
            ),
        }

        self._send_json(payload, status=200)

    # ------------------------------------------------------------------ #
    # /recover implementation                                              #
    # ------------------------------------------------------------------ #

    def _handle_recover(self) -> None:
        # RecoveryManager sends:
        # {"dead_broker_host": ..., "dead_broker_port": ...}
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length))
            dead_host = body["dead_broker_host"]
            dead_port = int(body["dead_broker_port"])
        except (KeyError, ValueError, json.JSONDecodeError):
            self._send_json({"error": "invalid request body"}, status=400)
            return

        dead = NodeAddress(dead_host, dead_port)

        try:
            snapshot = self.broker.request_snapshot_from_peers(dead, timeout=5.0)
            if snapshot is None:
                self._send_json({"error": "no_snapshot_available"}, status=500)
                return
            self.broker.recover_from_snapshot(snapshot)
        except Exception as exc:
            logger.error("recovery failed for %s: %s", dead, exc, exc_info=True)
            self._send_json({"error": "recovery_failed"}, status=500)
            return

        self._send_json({"status": "recovered"}, status=200)

    # ------------------------------------------------------------------ #
    # /peer-snapshots implementation                                        #
    # ------------------------------------------------------------------ #

    def _handle_list_peer_snapshots(self) -> None:
        with self.broker._lock:
            snapshots = dict(self.broker._peer_snapshots)

        self._send_json(
            {str(addr): _serialize_snapshot(snap) for addr, snap in snapshots.items()},
            status=200,
        )

    # ------------------------------------------------------------------ #
    # /snapshots/{host}/{port} implementation                              #
    # ------------------------------------------------------------------ #

    def _handle_get_snapshot(self) -> None:
        # Path: /snapshots/{host}/{port}
        parts = self.path.strip("/").split("/")
        if len(parts) != 3 or parts[0] != "snapshots":
            self._send_json({"error": "invalid path"}, status=400)
            return

        try:
            host, port = parts[1], int(parts[2])
        except ValueError:
            self._send_json({"error": "invalid port"}, status=400)
            return

        addr = NodeAddress(host, port)
        with self.broker._lock:
            snapshot = self.broker._peer_snapshots.get(addr)

        if snapshot is None:
            self._send_json({"error": "snapshot not found"}, status=404)
            return

        self._send_json(_serialize_snapshot(snapshot), status=200)

    # ------------------------------------------------------------------ #
    # Helpers                                                              #
    # ------------------------------------------------------------------ #

    def _send_json(self, data: dict, *, status: int) -> None:
        body = json.dumps(data, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: N802, A002
        # Redirect HTTP server access logs to the aether logger at DEBUG level.
        logger.debug("status http: " + format, *args)


class StatusServer:
    """Thin wrapper around ThreadingHTTPServer for the broker /status endpoint.

    Usage::

        server = StatusServer(broker, port=15001)
        server.start()   # spawns daemon thread
        ...
        server.stop()    # graceful shutdown
    """

    def __init__(self, broker: GossipBroker, port: int) -> None:
        self._broker = broker
        self._port = port
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        # Build a handler class with the broker reference baked in.
        broker = self._broker

        class Handler(_StatusHandler):
            pass

        Handler.broker = broker

        self._httpd = ThreadingHTTPServer(("0.0.0.0", self._port), Handler)

        self._thread = threading.Thread(
            target=self._httpd.serve_forever,
            name=f"broker-{broker.address.port}-status-http",
            daemon=True,
        )
        self._thread.start()
        logger.info("status server listening on http://0.0.0.0:%d/status", self._port)

    def stop(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd = None
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None


# ======================================================================== #
# Bootstrap server status                                                    #
# ======================================================================== #


class _BootstrapStatusHandler(BaseHTTPRequestHandler):
    """Minimal HTTP request handler for the bootstrap server — GET /status only."""

    # Injected by BootstrapStatusServer before the server is started.
    bootstrap: BootstrapServer

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/status":
            self._handle_status()
        else:
            self._send_json({"error": "not found"}, status=404)

    def do_DELETE(self) -> None:  # noqa: N802
        if self.path == "/deregister":
            self._handle_deregister()
        else:
            self._send_json({"error": "not found"}, status=404)

    def _handle_deregister(self) -> None:
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length))
            host = body["host"]
            port = int(body["port"])
        except (KeyError, ValueError, json.JSONDecodeError):
            self._send_json({"error": "invalid request body"}, status=400)
            return

        addr = NodeAddress(host, port)
        with self.bootstrap._lock:
            self.bootstrap.registered_brokers.discard(addr)

        self._send_json({"status": "deregistered"}, status=200)

    def _handle_status(self) -> None:
        bs = self.bootstrap
        now = time.time()

        with bs._lock:
            brokers = [str(b) for b in bs.registered_brokers]

        uptime = round(now - bs._start_time, 1)

        payload: dict[str, Any] = {
            "host": bs.address.host,
            "port": bs.address.port,
            "status_port": bs._status_port,
            "registered_brokers": brokers,
            "broker_count": len(brokers),
            "uptime_seconds": uptime,
        }

        self._send_json(payload, status=200)

    def _send_json(self, data: dict, *, status: int) -> None:
        body = json.dumps(data, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: N802, A002
        logger.debug("bootstrap status http: " + format, *args)


class BootstrapStatusServer:
    """Thin wrapper around ThreadingHTTPServer for the bootstrap /status endpoint.

    Usage::

        server = BootstrapStatusServer(bootstrap, port=14000)
        server.start()   # spawns daemon thread
        ...
        server.stop()    # graceful shutdown
    """

    def __init__(self, bootstrap: BootstrapServer, port: int) -> None:
        self._bootstrap = bootstrap
        self._port = port
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        bootstrap = self._bootstrap

        class Handler(_BootstrapStatusHandler):
            pass

        Handler.bootstrap = bootstrap

        self._httpd = ThreadingHTTPServer(("0.0.0.0", self._port), Handler)

        self._thread = threading.Thread(
            target=self._httpd.serve_forever,
            name=f"bootstrap-{bootstrap.address.port}-status-http",
            daemon=True,
        )
        self._thread.start()
        logger.info(
            "bootstrap status server listening on http://0.0.0.0:%d/status",
            self._port,
        )

    def stop(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd = None
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None


# ======================================================================== #
# Subscriber status                                                          #
# ======================================================================== #


class _SubscriberStatusHandler(BaseHTTPRequestHandler):
    """Minimal HTTP request handler for a NetworkSubscriber — GET /status only."""

    # Injected by SubscriberStatusServer before the server is started.
    subscriber: NetworkSubscriber

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/status":
            self._handle_status()
        else:
            self._send_json({"error": "not found"}, status=404)

    def do_POST(self) -> None:  # noqa: N802
        if self.path == "/latency/reset":
            self._handle_latency_reset()
        else:
            self._send_json({"error": "not found"}, status=404)

    def _handle_status(self) -> None:
        sub = self.subscriber
        now = time.time()

        uptime = round(now - sub._start_time, 1)

        # Compute latency percentiles from collected samples.
        samples = sorted(sub.latency_samples_snapshot_ns())
        latency_samples_us = [round(sample / 1000, 1) for sample in samples]
        if samples:
            n = len(samples)
            latency_us: dict[str, float | int] = {
                "p50": round(samples[n // 2] / 1000, 1),
                "p95": round(samples[int(n * 0.95)] / 1000, 1),
                "p99": round(samples[int(n * 0.99)] / 1000, 1),
                "sample_count": n,
            }
        else:
            latency_us = {"p50": 0, "p95": 0, "p99": 0, "sample_count": 0}

        payload: dict[str, Any] = {
            "subscriber": str(sub.address),
            "host": sub.address.host,
            "port": sub.address.port,
            "status_port": sub._status_port,
            "broker": str(sub.broker) if sub.broker else None,
            "subscriptions": [
                {"low": int(pr.low), "high": int(pr.high)} for pr in sub.subscriptions
            ],
            "total_received": sub.total_received,
            "running": sub.running,
            "uptime_seconds": uptime,
            "latency_us": latency_us,
            "latency_samples_us": latency_samples_us,
        }

        self._send_json(payload, status=200)

    def _handle_latency_reset(self) -> None:
        cleared = self.subscriber.clear_latency_samples()
        self._send_json(
            {"status": "ok", "cleared_samples": cleared},
            status=200,
        )

    def _send_json(self, data: dict, *, status: int) -> None:
        body = json.dumps(data, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: N802, A002
        logger.debug("subscriber status http: " + format, *args)


class SubscriberStatusServer:
    """Thin wrapper around ThreadingHTTPServer for the subscriber /status endpoint.

    Usage::

        server = SubscriberStatusServer(subscriber, port=15001)
        server.start()   # spawns daemon thread
        ...
        server.stop()    # graceful shutdown
    """

    def __init__(self, subscriber: NetworkSubscriber, port: int) -> None:
        self._subscriber = subscriber
        self._port = port
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        subscriber = self._subscriber

        class Handler(_SubscriberStatusHandler):
            pass

        Handler.subscriber = subscriber

        self._httpd = ThreadingHTTPServer(("0.0.0.0", self._port), Handler)

        self._thread = threading.Thread(
            target=self._httpd.serve_forever,
            name=f"subscriber-{subscriber.address.port}-status-http",
            daemon=True,
        )
        self._thread.start()
        logger.info(
            "subscriber status server listening on http://0.0.0.0:%d/status",
            self._port,
        )

    def stop(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd = None
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None


# ======================================================================== #
# Publisher status                                                           #
# ======================================================================== #


class _PublisherStatusHandler(BaseHTTPRequestHandler):
    """Minimal HTTP request handler for a NetworkPublisher — GET /status only."""

    # Injected by PublisherStatusServer before the server is started.
    publisher: NetworkPublisher

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/status":
            self._handle_status()
        else:
            self._send_json({"error": "not found"}, status=404)

    def _handle_status(self) -> None:
        pub = self.publisher
        now = time.time()

        uptime = round(now - pub._start_time, 1)

        payload: dict[str, Any] = {
            "publisher": str(pub.address),
            "host": pub.address.host,
            "port": pub.address.port,
            "status_port": pub._status_port,
            "brokers": [str(b) for b in pub.broker_addresses],
            "broker_count": len(pub.broker_addresses),
            "total_sent": pub.total_sent,
            "uptime_seconds": uptime,
        }

        self._send_json(payload, status=200)

    def _send_json(self, data: dict, *, status: int) -> None:
        body = json.dumps(data, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: N802, A002
        logger.debug("publisher status http: " + format, *args)


class PublisherStatusServer:
    """Thin wrapper around ThreadingHTTPServer for the publisher /status endpoint.

    Usage::

        server = PublisherStatusServer(publisher, port=15001)
        server.start()   # spawns daemon thread
        ...
        server.stop()    # graceful shutdown
    """

    def __init__(self, publisher: NetworkPublisher, port: int) -> None:
        self._publisher = publisher
        self._port = port
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        publisher = self._publisher

        class Handler(_PublisherStatusHandler):
            pass

        Handler.publisher = publisher

        self._httpd = ThreadingHTTPServer(("0.0.0.0", self._port), Handler)

        self._thread = threading.Thread(
            target=self._httpd.serve_forever,
            name=f"publisher-{publisher.address.port}-status-http",
            daemon=True,
        )
        self._thread.start()
        logger.info(
            "publisher status server listening on http://0.0.0.0:%d/status",
            self._port,
        )

    def stop(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd = None
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
