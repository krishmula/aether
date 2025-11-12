import random
from typing import List
from network import NetworkNode, NodeAddress
from message import Message
from log_utils import log_network, log_error

class NetworkPublisher:
    def __init__(self, address: NodeAddress, broker_addresses: List[NodeAddress]) -> None:
        self.address = address
        self.network = NetworkNode(address)
        self.broker_addresses = broker_addresses

    def publish(self, msg: Message, redundancy: int = 2) -> None:
        num_targets = min(redundancy, len(self.broker_addresses))
        targets = random.sample(self.broker_addresses, num_targets)

        for broker_addr in targets:
            try:
                self.network.send(msg, broker_addr)
                log_network(f"Publisher:{self.address.port}", "PUBLISH", f"payload={msg.payload} → {broker_addr}")
            except Exception as e:
                log_error(f"Publisher:{self.address.port}", f"Failed to send to {broker_addr}: {e}")

    def close(self) -> None:
        self.network.close()