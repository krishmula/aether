import logging
import threading
import time
from typing import Optional, Set

from pubsub.core.message import Message
from pubsub.core.payload_range import PayloadRange
from pubsub.core.subscriber import Subscriber
from pubsub.gossip.protocol import (
    PayloadMessageDelivery,
    SubscribeAck,
    SubscribeRequest,
    UnsubscribeAck,
    UnsubscribeRequest,
)
from pubsub.network.node import NetworkNode, NodeAddress
from pubsub.snapshot import BrokerRecoveryNotification
from pubsub.utils.log import BoundLogger

logger = logging.getLogger(__name__)


class NetworkSubscriber:
    __slots__ = (
        "subscriber",
        "address",
        "node",
        "broker",
        "subscriptions",
        "running",
        "recv_thread",
        "log",
        "total_received",
        "_start_time",
        "_status_port",
    )

    def __init__(self, address: NodeAddress) -> None:
        self.subscriber = Subscriber()

        self.running = False

        self.address = address
        self.node = NetworkNode(address)
        self.broker: Optional[NodeAddress] = None
        self.subscriptions: Set[PayloadRange] = set()

        self.recv_thread: Optional[threading.Thread] = None
        self.log = BoundLogger(logger, {"subscriber": str(address)})
        self.total_received = 0
        self._start_time: float = time.time()
        self._status_port: Optional[int] = None

    def connect_to_broker(self, broker_address: NodeAddress) -> None:
        self.broker = broker_address
        self.log.info("connected to broker %s", broker_address)

    def subscribe(self, payload_range: PayloadRange, retries: int = 3) -> bool:
        if self.broker is None:
            raise RuntimeError("Broker not connected")
        if self.running:
            raise RuntimeError("Cannot subscribe after start()")

        request = SubscribeRequest(subscriber=self.address, payload_range=payload_range)
        for attempt in range(retries):
            self.node.send(request, self.broker)
            self.log.debug(
                "subscribe request sent to %s range=%s attempt=%d",
                self.broker,
                payload_range,
                attempt + 1,
            )
            msg, sender = self.node.receive(timeout=2.0)
            if isinstance(msg, SubscribeAck):
                if msg.success:
                    self.subscriptions.add(payload_range)
                    self.log.info(
                        "subscription acknowledged by broker %s range=%s",
                        self.broker,
                        payload_range,
                    )
                    return True
                else:
                    self.log.warning(
                        "subscription rejected by broker %s range=%s",
                        self.broker,
                        payload_range,
                    )
                    return False
        return False

    def handle_incoming_message(self, msg: Message) -> None:
        self.subscriber.handle_msg(msg)

    def unsubscribe(self, payload_range: PayloadRange, retries: int = 3) -> bool:
        if self.broker is None:
            raise RuntimeError("Broker not connected")
        if self.running:
            raise RuntimeError("Cannot unsubscribe after start()")

        request = UnsubscribeRequest(
            subscriber=self.address, payload_range=payload_range
        )

        for attempt in range(retries):
            self.node.send(request, self.broker)
            self.log.debug(
                "unsubscribe request sent to %s range=%s attempt=%d",
                self.broker,
                payload_range,
                attempt + 1,
            )
            msg, sender = self.node.receive(timeout=2.0)
            if isinstance(msg, UnsubscribeAck):
                if msg.success:
                    self.subscriptions.discard(payload_range)
                    self.log.info(
                        "unsubscription acknowledged by broker %s range=%s",
                        self.broker,
                        payload_range,
                    )
                    return True
                else:
                    self.log.warning(
                        "unsubscription rejected by broker %s range=%s",
                        self.broker,
                        payload_range,
                    )
                    return False
        return False

    def _handle_broker_recovery(
        self, notification: BrokerRecoveryNotification, sender: NodeAddress
    ) -> None:
        """Handle notification that a broker has recovered and taken over
        for a dead broker. Updates our broker reference if we were connected
        to the old broker.
        """
        old_broker = notification.old_broker
        new_broker = notification.new_broker

        if self.broker == old_broker:
            self.log.info(
                "broker recovered: %s -> %s, updating reference",
                old_broker,
                new_broker,
            )
            self.broker = new_broker
        else:
            self.log.debug(
                "received recovery notification but was not connected to %s", old_broker
            )

    def start(self) -> None:
        if self.running:
            return
        self.running = True
        self.recv_thread = threading.Thread(
            target=self._receive_loop,
            name=f"network-subscriber-{self.address.port}-recv",
            daemon=True,
        )
        self.recv_thread.start()
        self.log.info("receive loop started")

    def stop(self) -> None:
        self.running = False
        if self.recv_thread:
            self.recv_thread.join(timeout=2.0)
            self.recv_thread = None
        self.node.close()
        self.log.info("stopped")

    def _receive_loop(self) -> None:
        while self.running:
            msg, sender = self.node.receive(timeout=1.0)
            if msg is None:
                continue
            if isinstance(msg, PayloadMessageDelivery):
                self.total_received += 1
                self.subscriber.handle_msg(msg.msg)
                self.log.debug(
                    "received payload=%d from broker %s", msg.msg.payload, sender
                )
            elif isinstance(msg, BrokerRecoveryNotification):
                if sender is not None:
                    self._handle_broker_recovery(msg, sender)
            else:
                self.log.debug(
                    "received unexpected message type %s from %s",
                    type(msg).__name__,
                    sender,
                )

    @property
    def counts(self):
        return self.subscriber.counts
