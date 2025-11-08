<<<<<<< HEAD
## listen for forwarded messages on a separate port, and print them to console.
=======
"""Subscriber implementation."""
from message import Message


class Subscriber:
    """Subscriber that tracks per-payload counts."""

    __slots__ = ("counts",)

    def __init__(self) -> None:
        self.counts = [0] * 256

    def handle_msg(self, msg: Message) -> None:
        self.counts[msg.payload] += 1
>>>>>>> origin/kevwjin/mvp
