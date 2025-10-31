"""Subscriber implementation."""
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from broker import Broker
from uint8 import UInt8
from message import Message


class Subscriber:
    """Subscriber that tracks per-payload counts."""

    __slots__ = ("_broker", "counts")

    def __init__(self, broker: "Broker") -> None:
        self._broker = broker
        self.counts = [0] * 256

    def filter(self, msg: Message) -> bool:
        return isinstance(msg.payload, UInt8)

    def _handle_msg(self, msg: Message) -> None:
        self.counts[msg.payload] += 1
