"""Publisher implementation."""
import random
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from broker import Broker
from uint8 import UInt8
from message import Message


class Publisher:
    """Publisher that delegates message delivery to its broker."""

    __slots__ = ("_broker",)

    def __init__(self, broker: "Broker") -> None:
        self._broker = broker

    def _create_msg(self) -> Message:
        i = random.randint(0, 255)
        return Message(UInt8(i))

    def publish(self, msg: Message) -> None:
        self._broker.publish(msg)
