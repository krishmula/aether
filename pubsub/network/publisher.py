import random
import uuid
from typing import List

from pubsub.gossip.protocol import GossipMessage
from pubsub.utils.log import log_error, log_network
from pubsub.core.message import Message
from pubsub.network.node import NetworkNode, NodeAddress


class NetworkPublisher:
    def __init__(
        self,
        address: NodeAddress,
        broker_addresses: List[NodeAddress],
        ttl: int = 5,
    ) -> None:
        self.address = address
        self.network = NetworkNode(address)
        self.broker_addresses = broker_addresses
        self.ttl = ttl

    def publish(self, msg: Message, redundancy: int = 2) -> int:
        """
        Publish a message to multiple brokers for redundancy.

        Creates a single GossipMessage with one msg_id, ensuring that
        brokers can deduplicate the message even when received from
        multiple sources.

        Returns the number of brokers the message was successfully sent to.
        """
        num_targets = min(redundancy, len(self.broker_addresses))
        targets = random.sample(self.broker_addresses, num_targets)

        msg_id = str(uuid.uuid4())
        gossip_msg = GossipMessage(
            msg=msg,
            msg_id=msg_id,
            ttl=self.ttl,
            source=self.address,
        )

        sent_count = 0
        for broker_addr in targets:
            try:
                self.network.send(gossip_msg, broker_addr)
                log_network(
                    f"Publisher:{self.address.port}",
                    "PUBLISH",
                    f"payload={msg.payload} (id={msg_id[:8]}...) → {broker_addr}",
                )
                sent_count += 1
            except Exception as e:
                log_error(
                    f"Publisher:{self.address.port}",
                    f"Failed to send to {broker_addr}: {e}",
                )

        return sent_count

    def close(self) -> None:
        self.network.close()
