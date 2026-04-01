import json
import logging
import random
import threading
import time
import urllib.request
from typing import Optional, Set

from aether.core.message import Message
from aether.core.payload_range import PayloadRange
from aether.core.subscriber import Subscriber
from aether.gossip.protocol import (
    PayloadMessageDelivery,
    SubscribeAck,
    SubscribeRequest,
    UnsubscribeAck,
    UnsubscribeRequest,
)
from aether.network.node import NetworkNode, NodeAddress
from aether.snapshot import BrokerRecoveryNotification, Ping, Pong
from aether.utils.log import BoundLogger

logger = logging.getLogger(__name__)

# How often the subscriber pings its broker (seconds).
_PING_INTERVAL = 5.0
# How long with no Pong before the broker is declared dead (seconds).
_PING_TIMEOUT = 15.0


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
        "_orchestrator_url",
        "_subscriber_id",
        "_broker_alive",
        "_last_pong_time",
        "_ping_sequence",
        "_health_thread",
    )

    def __init__(
        self,
        address: NodeAddress,
        orchestrator_url: Optional[str] = None,
        subscriber_id: Optional[int] = None,
    ) -> None:
        self.subscriber = Subscriber()

        self.running = False

        self.address = address
        self.node = NetworkNode(address)
        self.broker: Optional[NodeAddress] = None
        self.subscriptions: Set[PayloadRange] = set()

        self.recv_thread: Optional[threading.Thread] = None
        self._health_thread: Optional[threading.Thread] = None
        self.log = BoundLogger(logger, {"subscriber": str(address)})
        self.total_received = 0
        self._start_time: float = time.time()
        self._status_port: Optional[int] = None

        self._orchestrator_url: Optional[str] = orchestrator_url
        self._subscriber_id: Optional[int] = subscriber_id
        self._broker_alive: bool = True
        self._last_pong_time: float = time.time()
        self._ping_sequence: int = 0

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
        """Fast-path: broker pushes a recovery notification directly to us.

        Updates our broker reference and resets health state so the ping loop
        doesn't also trigger a pull-based reconnect for the same failure.
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
            self._broker_alive = True
            self._last_pong_time = time.time()
        else:
            self.log.debug(
                "received recovery notification but was not connected to %s", old_broker
            )

    def start(self) -> None:
        if self.running:
            return
        self.running = True
        self._last_pong_time = time.time()

        self.recv_thread = threading.Thread(
            target=self._receive_loop,
            name=f"network-subscriber-{self.address.port}-recv",
            daemon=True,
        )
        self.recv_thread.start()

        if self._orchestrator_url is not None and self._subscriber_id is not None:
            self._health_thread = threading.Thread(
                target=self._broker_health_loop,
                name=f"network-subscriber-{self.address.port}-health",
                daemon=True,
            )
            self._health_thread.start()

        self.log.info("receive loop started")

    def stop(self) -> None:
        self.running = False
        if self.recv_thread:
            self.recv_thread.join(timeout=2.0)
            self.recv_thread = None
        if self._health_thread:
            self._health_thread.join(timeout=2.0)
            self._health_thread = None
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
                self.log.info(
                    "received payload=%d from broker %s", msg.msg.payload, sender
                )
            elif isinstance(msg, Pong):
                self._last_pong_time = time.time()
            elif isinstance(msg, BrokerRecoveryNotification):
                if sender is not None:
                    self._handle_broker_recovery(msg, sender)
            else:
                self.log.debug(
                    "received unexpected message type %s from %s",
                    type(msg).__name__,
                    sender,
                )

    def _broker_health_loop(self) -> None:
        """Send pings to the broker and trigger reconnect on timeout."""
        while self.running:
            time.sleep(_PING_INTERVAL)

            if not self._broker_alive or self.broker is None:
                continue

            try:
                self.node.send(
                    Ping(sender=self.address, sequence=self._ping_sequence),
                    self.broker,
                )
                self._ping_sequence += 1
            except Exception:
                pass  # send failure will show up as a pong timeout

            if time.time() - self._last_pong_time > _PING_TIMEOUT:
                self.log.warning(
                    "broker %s ping timeout — starting reconnect", self.broker
                )
                self._broker_alive = False
                self._reconnect()

    def _reconnect(self) -> None:
        """Pull-based reconnect: query orchestrator for new assignment, re-subscribe.

        Uses exponential backoff with full jitter so a mass of subscribers
        reconnecting simultaneously don't all hammer the orchestrator at once.
        """
        base, cap = 1.0, 30.0
        attempt = 0

        while self.running and not self._broker_alive:
            delay = random.uniform(0, min(cap, base * (2 ** attempt)))
            if delay > 0:
                time.sleep(delay)

            try:
                url = (
                    f"{self._orchestrator_url}/api/assignment"
                    f"?subscriber_id={self._subscriber_id}"
                )
                with urllib.request.urlopen(url, timeout=5) as resp:
                    data = json.loads(resp.read().decode())

                new_broker = NodeAddress(data["broker_host"], data["broker_port"])
                self.broker = new_broker
                self._last_pong_time = time.time()
                self._broker_alive = True

                # Re-subscribe on the new broker for every known range.
                # We don't wait for SubscribeAck here — the receive loop is
                # running concurrently and the broker will process the request.
                for payload_range in list(self.subscriptions):
                    try:
                        self.node.send(
                            SubscribeRequest(
                                subscriber=self.address, payload_range=payload_range
                            ),
                            self.broker,
                        )
                    except Exception:
                        self.log.warning(
                            "failed to send SubscribeRequest to %s", self.broker
                        )

                self.log.info(
                    "reconnected to broker %s after %d attempt(s)",
                    new_broker,
                    attempt + 1,
                )

            except Exception:
                self.log.debug(
                    "reconnect attempt %d failed (will retry)", attempt + 1
                )
                attempt += 1

    @property
    def counts(self):
        return self.subscriber.counts
