import logging
import random
import time
import uuid
from typing import List, Optional

from aether.core.message import Message
from aether.gossip.protocol import GossipMessage
from aether.network.node import NetworkNode, NodeAddress
from aether.utils.log import BoundLogger

logger = logging.getLogger(__name__)

# How long to skip a broker after a send failure (seconds).
_DEAD_BROKER_COOLDOWN = 30.0


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
        self.log = BoundLogger(logger, {"publisher": str(address)})
        self.total_sent = 0
        self._start_time: float = time.time()
        self._status_port: Optional[int] = None
        # broker_address → time.time() when it last failed
        self._dead_brokers: dict[NodeAddress, float] = {}

    def publish(self, msg: Message, redundancy: int = 2) -> int:
        """Publish a message to multiple brokers for redundancy.

        Creates a single GossipMessage with one msg_id, ensuring that
        brokers can deduplicate the message even when received from
        multiple sources.

        Brokers that recently failed are skipped for _DEAD_BROKER_COOLDOWN
        seconds. After the cooldown a failed broker is retried automatically;
        a successful send clears it from the dead list.

        Returns the number of brokers the message was successfully sent to.
        """
        now = time.time()

        # Partition into live (cooldown expired or never failed) vs cooling-down.
        live_brokers = [
            addr for addr in self.broker_addresses
            if now - self._dead_brokers.get(addr, 0) >= _DEAD_BROKER_COOLDOWN
        ]

        num_targets = min(redundancy, len(live_brokers))
        if num_targets == 0:
            self.log.warning("all brokers in cooldown — dropping message")
            return 0

        targets = random.sample(live_brokers, num_targets)

        msg_id = str(uuid.uuid4())
        gossip_msg = GossipMessage(
            msg=msg,
            msg_id=msg_id,
            ttl=self.ttl,
            source=self.address,
            send_timestamp_ns=time.monotonic_ns(),
        )

        sent_count = 0
        for broker_addr in targets:
            try:
                self.network.send(gossip_msg, broker_addr)
                self.total_sent += 1
                self._dead_brokers.pop(broker_addr, None)  # clear on success
                self.log.debug(
                    "published payload=%d msg_id=%s -> %s",
                    msg.payload,
                    msg_id[:8],
                    broker_addr,
                    extra={"event_type": "message_published"},
                )
                sent_count += 1
            except Exception:
                self._dead_brokers[broker_addr] = time.time()
                self.log.error(
                    "failed to send msg_id=%s to %s — broker marked dead for %.0fs",
                    msg_id[:8],
                    broker_addr,
                    _DEAD_BROKER_COOLDOWN,
                    exc_info=True,
                    extra={"event_type": "send_failure", "error_kind": "send_error"},
                )

        return sent_count

    def close(self) -> None:
        self.network.close()
