import threading
from typing import Optional, Set

from pubsub.gossip.protocol import (
    PayloadMessageDelivery,
    SubscribeAck,
    SubscribeRequest,
    UnsubscribeAck,
    UnsubscribeRequest,
)
from pubsub.utils.log import log_debug, log_info
from pubsub.core.message import Message
from pubsub.network.node import NetworkNode, NodeAddress
from pubsub.core.payload_range import PayloadRange
from pubsub.snapshot import BrokerRecoveryNotification
from pubsub.core.subscriber import Subscriber


class NetworkSubscriber:

    __slots__ = (
        "subscriber",
        "address",
        "node",
        "broker",
        "subscriptions",
        "running",
        "recv_thread",
    )

    def __init__(self, address: NodeAddress) -> None:
        self.subscriber = Subscriber()

        self.running = False

        self.address = address
        self.node = NetworkNode(address)
        self.broker: Optional[NodeAddress] = None
        self.subscriptions: Set[PayloadRange] = set()

        self.recv_thread: Optional[threading.Thread] = None

    def connect_to_broker(self, broker_address: NodeAddress) -> None:
        self.broker = broker_address
        log_info("NetworkSubscriber", f"Connected to broker at {broker_address}")

    def subscribe(self, payload_range: PayloadRange, retries: int = 3) -> bool:
        if self.broker is None:
            raise RuntimeError("Broker not connected")
        if self.running:
            raise RuntimeError("Cannot subscribe after start()")

        request = SubscribeRequest(subscriber=self.address, payload_range=payload_range)
        for attempt in range(retries):
            self.node.send(request, self.broker)
            log_debug(
                "NetworkSubscriber",
                f"Sent Subscription Request to {self.broker} for range {payload_range} (attempt {attempt + 1})",
            )
            msg, sender = self.node.receive(timeout=2.0)
            if isinstance(msg, SubscribeAck):
                if msg.success:
                    self.subscriptions.add(payload_range)
                    log_info(
                        "NetworkSubscriber",
                        f"Subscription to range {payload_range} acknowledged by broker {self.broker}",
                    )
                    return True
                else:
                    log_info(
                        "NetworkSubscriber",
                        f"Subscription to range {payload_range} rejected by broker {self.broker}",
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
            log_debug(
                "NetworkSubscriber",
                f"Sent Unsubscription Request to {self.broker} for range {payload_range} (attempt {attempt + 1})",
            )
            msg, sender = self.node.receive(timeout=2.0)
            if isinstance(msg, UnsubscribeAck):
                if msg.success:
                    self.subscriptions.discard(payload_range)
                    log_info(
                        "NetworkSubscriber",
                        f"Unsubscription from range {payload_range} acknowledged by broker {self.broker}",
                    )
                    return True
                else:
                    log_info(
                        "NetworkSubscriber",
                        f"Unsubscription from range {payload_range} rejected by broker {self.broker}",
                    )
                    return False
        return False

    def _handle_broker_recovery(
        self, notification: BrokerRecoveryNotification, sender: NodeAddress
    ) -> None:
        """
        Handle notification that a broker has recovered and taken over
        for a dead broker.

        Updates our broker reference if we were connected to the old broker.
        """
        old_broker = notification.old_broker
        new_broker = notification.new_broker

        if self.broker == old_broker:
            log_info(
                f"Subscriber:{self.address.port}",
                f"Broker recovery: {old_broker} -> {new_broker}",
            )
            self.broker = new_broker
            log_info(
                f"Subscriber:{self.address.port}",
                f"Updated broker reference to {new_broker}",
            )
        else:
            log_debug(
                f"Subscriber:{self.address.port}",
                f"Received recovery notification but was not connected to {old_broker}",
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
        log_info(
            "NetworkSubscriber", f"Started receiving messages loop at {self.address}"
        )

    def stop(self) -> None:
        self.running = False
        if self.recv_thread:
            self.recv_thread.join(timeout=2.0)
            self.recv_thread = None
        self.node.close()
        log_info("NetworkSubscriber", f"Stopped subscriber on {self.address}")

    def _receive_loop(self) -> None:
        while self.running:
            msg, sender = self.node.receive(timeout=1.0)
            if msg is None:
                continue
            if isinstance(msg, PayloadMessageDelivery):
                self.subscriber.handle_msg(msg.msg)
                log_debug(
                    "NetworkSubscriber",
                    f"Received message {msg.msg} from broker {sender} with payload {msg.msg.payload}",
                )
            if isinstance(msg, BrokerRecoveryNotification):
                self._handle_broker_recovery(msg, sender)
            else:
                log_debug(
                    "NetworkSubscriber",
                    f"Received unknown message type from {sender}: {msg} of type {type(msg)}",
                )

    @property
    def counts(self):
        return self.subscriber.counts
