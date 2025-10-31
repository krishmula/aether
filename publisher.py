"""Publisher implementation."""
from message import Message
from broker import Broker


class Publisher:
    """Publisher that delegates message delivery to its broker."""

    __slots__ = ("_broker",)

    def __init__(self, broker: Broker) -> None:
        self._broker = broker

    def _create_msg(self) -> Message:  # pragma: no cover - user defined
        raise NotImplementedError

    def publish(self, msg: Message) -> None:
        self._broker.publish(msg)
