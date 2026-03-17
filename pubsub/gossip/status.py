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

if TYPE_CHECKING:
    from pubsub.gossip.bootstrap import BootstrapServer
    from pubsub.gossip.broker import GossipBroker

logger = logging.getLogger(__name__)


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
            seen_count = len(broker.seen_messages)
            snapshot_state = (
                "recording" if broker._snapshot_in_progress is not None else "idle"
            )

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
        }

        self._send_json(payload, status=200)

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
        # Redirect HTTP server access logs to the pubsub logger at DEBUG level.
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
