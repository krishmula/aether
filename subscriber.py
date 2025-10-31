"""Subscriber implementation."""
from typing import TYPE_CHECKING, Callable

from message import Message

if TYPE_CHECKING:  # pragma: no cover - type checking only
    from broker import Broker


class Subscriber:
    """Subscriber that tracks per-payload counts."""

    __slots__ = ("_broker", "counts", "_filter")

    FilterFn = Callable[[Message], bool]

    def __init__(self, broker: "Broker", filter_fn: FilterFn) -> None:
        self._broker = broker
        self._filter: Subscriber.FilterFn = filter_fn
        self.counts = [0] * 256

    def filter(self, msg: Message) -> bool:
        return self._filter(msg)

    def _handle_msg(self, msg: Message) -> None:
        self.counts[msg.payload] += 1
