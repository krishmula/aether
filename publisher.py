<<<<<<< HEAD
## This file will contain logic that generates random integers, and sends them to the broker via TCP sockets.
=======
"""Publisher implementation."""
from typing import TYPE_CHECKING

from message import Message

if TYPE_CHECKING:   # pragma: no cover - type checking only
    from broker import Broker

class Publisher:
    """Publisher that delegates message delivery to its broker."""

    __slots__ = ("_broker",)

    def __init__(self, broker: "Broker") -> None:
        self._broker = broker

    def publish(self, msg: Message) -> None:
        self._broker.publish(msg)
>>>>>>> origin/kevwjin/mvp
