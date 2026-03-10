"""Message implementation."""

from pubsub.core.uint8 import UInt8


class Message:
    """Message that carries a single payload."""

    __slots__ = ("_payload",)

    def __init__(self, payload: UInt8) -> None:
        self._payload = payload

    @property
    def payload(self) -> UInt8:
        return self._payload

    def __repr__(self) -> str:
        return f"Message(payload={self._payload})"
