"""Broker implementation."""
from typing import List, Optional

from uint8 import UInt8
from message import Message
from subscriber import Subscriber


class Broker:
    """Broker that coordinates a publisher and subscriber."""

    __slots__ = ("_subs",)

    def __init__(self) -> None:
        self._subs: List[Optional[Subscriber]] = [None] * 256

    def register(self, sub: Subscriber, sub_idx: UInt8) -> None:
        self._subs[sub_idx] = sub

    def unregister(self, sub_idx: UInt8) -> None:
        self._subs[sub_idx] = None

    def publish(self, msg: Message) -> None:
        targets = self._match(msg)
        self._deliver(msg, targets)

    def _match(self, msg: Message) -> List[Subscriber]:
        subs_snapshot = [sub for sub in self._subs if sub is not None]
        return [sub for sub in subs_snapshot if sub.filter(msg)]

    def _deliver(self, msg: Message, targets: List[Subscriber]) -> None:
        for sub in targets:
            sub._handle_msg(msg)

    def get_count(self, sub: Subscriber, content: UInt8) -> int:
        return sub.counts[content]
