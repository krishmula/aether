import logging
import threading
from typing import Optional, Set

from pubsub.gossip.protocol import MembershipUpdate
from pubsub.network.node import NetworkNode, NodeAddress
from pubsub.utils.log import BoundLogger

logger = logging.getLogger(__name__)


class BootstrapServer:
    def __init__(self, address: NodeAddress) -> None:
        self.address = address
        self.network = NetworkNode(address)
        self.registered_brokers: Set[NodeAddress] = set()
        self.running = False
        self.thread: Optional[threading.Thread] = None
        self.log = BoundLogger(logger, {"bootstrap": str(address)})

    def start(self) -> None:
        self.running = True
        self.thread = threading.Thread(
            target=self._serve_loop,
            name="bootstrap-server",
            daemon=True,
        )
        self.thread.start()
        self.log.info("started")

    def stop(self) -> None:
        self.running = False
        if self.thread:
            self.thread.join(timeout=2.0)
        self.log.info("stopped")

    def _serve_loop(self) -> None:
        while self.running:
            msg, sender = self.network.receive(timeout=1.0)
            if msg is None:
                continue

            if sender is not None:
                self.registered_brokers.add(sender)
                self.log.info("broker registered: %s", sender)

            response = MembershipUpdate(brokers=self.registered_brokers.copy())

            for broker in self.registered_brokers:
                self.network.send(response, broker)

            self.log.debug(
                "membership update sent to %d broker(s)", len(self.registered_brokers)
            )

    def get_peer_list(self):
        return self.registered_brokers
