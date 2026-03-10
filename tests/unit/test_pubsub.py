"""Test suite for the minimal pub-sub implementation."""

import unittest

from pubsub.core.broker import Broker
from pubsub.core.message import Message
from pubsub.core.payload_range import PayloadRange
from pubsub.core.publisher import Publisher
from pubsub.core.subscriber import Subscriber
from pubsub.core.uint8 import UInt8


class PubSubTests(unittest.TestCase):
    def setUp(self) -> None:
        self.broker = Broker()
        self.publisher = Publisher(self.broker)

    def test_subscriber_counting(self) -> None:
        sub1 = Subscriber()
        self.broker.register(sub1, PayloadRange(UInt8(0), UInt8(42)))
        sub2 = Subscriber()
        self.broker.register(sub2, PayloadRange(UInt8(42), UInt8(123)))

        self.publisher.publish(Message(UInt8(0)))
        self.publisher.publish(Message(UInt8(42)))
        self.publisher.publish(Message(UInt8(42)))
        self.publisher.publish(Message(UInt8(123)))

        self.assertEqual(self.broker.get_count(sub1, UInt8(0)), 1)
        self.assertEqual(self.broker.get_count(sub1, UInt8(42)), 2)
        self.assertEqual(self.broker.get_count(sub2, UInt8(42)), 2)
        self.assertEqual(self.broker.get_count(sub2, UInt8(123)), 1)

    def test_unregister(self) -> None:
        sub1 = Subscriber()
        sub2 = Subscriber()
        self.broker.register(sub1, PayloadRange(UInt8(0), UInt8(42)))
        self.broker.register(sub2, PayloadRange(UInt8(0), UInt8(42)))

        self.broker.unregister(sub1)
        self.publisher.publish(Message(UInt8(42)))
        self.assertEqual(sub1.counts[42], 0)
        self.assertEqual(sub2.counts[42], 1)


if __name__ == "__main__":
    unittest.main()
