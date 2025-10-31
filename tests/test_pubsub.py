"""Test suite for the minimal pub-sub implementation."""
import unittest

from broker import Broker
from message import Message
from publisher import Publisher
from subscriber import Subscriber
from uint8 import UInt8


class PubSubTests(unittest.TestCase):
    def setUp(self) -> None:
        self.broker = Broker()
        self.publisher = Publisher(self.broker)

    def test_single_subscriber_counts_messages(self) -> None:
        sub = Subscriber(self.broker, lambda _: True)
        self.broker.register(sub, UInt8(0))

        for _ in range(3):
            self.publisher.publish(Message(UInt8(42)))

        self.assertEqual(self.broker.get_count(sub, UInt8(42)), 3)

    def test_selective_filters_receive_matching_payloads(self) -> None:
        even_sub = Subscriber(self.broker, lambda msg: int(msg.payload) % 2 == 0)
        odd_sub = Subscriber(self.broker, lambda msg: int(msg.payload) % 2 == 1)
        self.broker.register(even_sub, UInt8(0))
        self.broker.register(odd_sub, UInt8(1))

        for payload in range(4):
            self.publisher.publish(Message(UInt8(payload)))

        self.assertEqual(self.broker.get_count(even_sub, UInt8(0)), 1)
        self.assertEqual(self.broker.get_count(even_sub, UInt8(2)), 1)
        self.assertEqual(self.broker.get_count(odd_sub, UInt8(1)), 1)
        self.assertEqual(self.broker.get_count(odd_sub, UInt8(3)), 1)

    def test_registering_duplicate_index_raises(self) -> None:
        sub1 = Subscriber(self.broker, lambda _: True)
        self.broker.register(sub1, UInt8(0))

        sub2 = Subscriber(self.broker, lambda _: True)
        with self.assertRaises(ValueError):
            self.broker.register(sub2, UInt8(0))

    def test_unregister_requires_matching_subscriber(self) -> None:
        sub1 = Subscriber(self.broker, lambda _: True)
        sub2 = Subscriber(self.broker, lambda _: True)
        self.broker.register(sub1, UInt8(0))

        with self.assertRaises(ValueError):
            self.broker.unregister(sub2, UInt8(0))

        self.broker.unregister(sub1, UInt8(0))
        self.publisher.publish(Message(UInt8(42)))
        self.assertEqual(sub1.counts[42], 0)


if __name__ == "__main__":
    unittest.main()
