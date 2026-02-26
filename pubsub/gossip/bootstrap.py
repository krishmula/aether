import threading
from typing import Set

from pubsub.gossip.protocol import GossipMessage, Heartbeat, MembershipUpdate
from pubsub.utils.log import log_debug, log_info, log_network, log_success
from pubsub.network.node import NetworkNode, NodeAddress


class BootstrapServer:

    def __init__(self, address: NodeAddress) -> None:
        self.address = address
        self.network = NetworkNode(address)
        self.registered_brokers: Set[NodeAddress] = set()
        self.running = False
        self.thread: threading.Thread = None

    def start(self) -> None:
        self.running = True
        self.thread = threading.Thread(
            target=self._serve_loop,
            name="bootstrap-server",
            daemon=True,
        )
        self.thread.start()
        log_success("BootstrapServer", f"Started on {self.address}")

    def stop(self) -> None:
        self.running = False
        if self.thread:
            self.thread.join(timeout=2.0)
        log_info("BootstrapServer", "Stopped")

    def _serve_loop(self) -> None:
        while self.running:
            msg, sender = self.network.receive(timeout=1.0)
            if msg is None:
                continue

            self.registered_brokers.add(sender)
            log_network("BootstrapServer", "REGISTERED", f"broker {sender}")

            response = MembershipUpdate(brokers=self.registered_brokers.copy())

            for broker in self.registered_brokers:
                self.network.send(response, broker)

            log_network(
                "BootstrapServer",
                "SENT PEERS",
                f"to {sender} → {len(self.registered_brokers)} broker(s)",
            )

    def get_peer_list(self):
        return self.registered_brokers
