"""Publisher implementation."""

from typing import TYPE_CHECKING

from aether.core.message import Message

if TYPE_CHECKING:
    from aether.core.broker import Broker


class Publisher:
    """Publisher that delegates message delivery to its broker."""

    __slots__ = ("_broker",)

    def __init__(self, broker: "Broker") -> None:
        self._broker = broker

    def publish(self, msg: Message) -> None:
        self._broker.publish(msg)
