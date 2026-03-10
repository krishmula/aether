"""Subscriber implementation."""

from pubsub.core.message import Message


class Subscriber:
    """Subscriber that tracks per-payload counts."""

    __slots__ = ("counts",)

    def __init__(self) -> None:
        self.counts = [0] * 256

    def handle_msg(self, msg: Message) -> None:
        self.counts[msg.payload] += 1
