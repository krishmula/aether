import logging
import threading
import time
from typing import Optional, Set

from pubsub.gossip.protocol import MembershipUpdate
from pubsub.gossip.status import BootstrapStatusServer
from pubsub.network.node import NetworkNode, NodeAddress
from pubsub.utils.log import BoundLogger

logger = logging.getLogger(__name__)


class BootstrapServer:
    def __init__(self, address: NodeAddress, http_port: Optional[int] = None) -> None:
        self.address = address
        self.network = NetworkNode(address)
        self.registered_brokers: Set[NodeAddress] = set()
        self.running = False
        self.thread: Optional[threading.Thread] = None
        self.log = BoundLogger(logger, {"bootstrap": str(address)})

        self._lock = threading.Lock()
        self._start_time: float = time.time()
        self._status_port: Optional[int] = http_port
        self._status_server: Optional[BootstrapStatusServer] = (
            BootstrapStatusServer(self, http_port) if http_port is not None else None
        )

    def start(self) -> None:
        self.running = True
        self.thread = threading.Thread(
            target=self._serve_loop,
            name="bootstrap-server",
            daemon=True,
        )
        self.thread.start()

        if self._status_server is not None:
            self._status_server.start()

        self.log.info("started")

    def stop(self) -> None:
        self.running = False
        if self.thread:
            self.thread.join(timeout=2.0)
        if self._status_server is not None:
            self._status_server.stop()
        self.log.info("stopped")

    def _serve_loop(self) -> None:
        while self.running:
            msg, sender = self.network.receive(timeout=1.0)
            if msg is None:
                continue

            if sender is not None:
                with self._lock:
                    self.registered_brokers.add(sender)
                self.log.info("broker registered: %s", sender)

            with self._lock:
                brokers_snapshot = self.registered_brokers.copy()

            response = MembershipUpdate(brokers=brokers_snapshot)

            for broker in brokers_snapshot:
                self.network.send(response, broker)

            self.log.debug(
                "membership update sent to %d broker(s)", len(brokers_snapshot)
            )

    def get_peer_list(self):
        return self.registered_brokers
