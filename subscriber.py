"""Subscriber implementation."""
from message import Message
from broker import Broker


class Subscriber:
    """Subscriber that tracks per-payload counts."""

    __slots__ = ("_broker", "counts")

    def __init__(self, broker: Broker) -> None:
        self._broker = broker
        self.counts = [0] * 256

    def filter(self, msg: Message) -> bool:
        return 0 <= msg.payload <= 255

    def _handle_msg(self, msg: Message) -> None:
        self.counts[msg.payload] += 1
