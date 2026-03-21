"""Broker implementation."""

from dataclasses import dataclass
from typing import Dict, List, Set

from aether.core.message import Message
from aether.core.payload_range import PayloadRange
from aether.core.subscriber import Subscriber
from aether.core.uint8 import UInt8


@dataclass(frozen=True)
class Subscription:
    broker: "Broker"
    subscriber: "Subscriber"


class Broker:
    """Broker that coordinates subscriptions."""

    __slots__ = (
        "_buckets",
        "_subscriber_to_range",
    )

    def __init__(self) -> None:
        self._buckets: List[Set[Subscriber]] = [set() for _ in range(256)]
        self._subscriber_to_range: Dict[Subscriber, List[PayloadRange]] = {}

    def register(
        self, subscriber: Subscriber, payload_range: PayloadRange
    ) -> Subscription:
        payload_ranges = self._subscriber_to_range.setdefault(subscriber, [])
        payload_ranges.append(payload_range)
        for i in range(payload_range.low, payload_range.high + 1):
            self._buckets[i].add(subscriber)
        return Subscription(self, subscriber)

    def unregister(self, subscriber: Subscriber) -> None:
        payload_ranges = self._subscriber_to_range.pop(subscriber, [])
        for payload_range in payload_ranges:
            for i in range(payload_range.low, payload_range.high + 1):
                self._buckets[i].discard(subscriber)

    def publish(self, msg: Message) -> None:
        for subscriber in tuple(self._buckets[msg.payload]):
            subscriber.handle_msg(msg)

    def get_count(self, sub: Subscriber, content: UInt8) -> int:
        return sub.counts[content]
