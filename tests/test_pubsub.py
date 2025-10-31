"""Test suite for the minimal pub-sub implementation."""
import unittest

from broker import Broker
from message import Message
from publisher import Publisher
from subscriber import Subscriber
from uint8 import UInt8


class EvenSubscriber(Subscriber):
    def filter(self, msg: Message) -> bool:
        return msg.payload % 2 == 0


class OddSubscriber(Subscriber):
    def filter(self, msg: Message) -> bool:
        return msg.payload % 2 == 1


class PubSubTests(unittest.TestCase):
    def setUp(self) -> None:
        self.broker = Broker()
        self.publisher = Publisher(self.broker)

    def test_single_subscriber_counts_messages(self) -> None:
        sub = Subscriber(self.broker)
        self.broker.register(sub, UInt8(7))

        for _ in range(3):
            self.publisher.publish(Message(UInt8(42)))

        self.assertEqual(self.broker.get_count(sub, UInt8(42)), 3)

    def test_selective_filters_receive_matching_payloads(self) -> None:
        even_sub = EvenSubscriber(self.broker)
        odd_sub = OddSubscriber(self.broker)
        self.broker.register(even_sub, UInt8(0))
        self.broker.register(odd_sub, UInt8(1))

        for payload in range(4):
            self.publisher.publish(Message(UInt8(payload)))

        self.assertEqual(self.broker.get_count(even_sub, UInt8(0)), 1)
        self.assertEqual(self.broker.get_count(even_sub, UInt8(2)), 1)
        self.assertEqual(self.broker.get_count(odd_sub, UInt8(1)), 1)
        self.assertEqual(self.broker.get_count(odd_sub, UInt8(3)), 1)

    def test_registering_duplicate_index_raises(self) -> None:
        sub1 = Subscriber(self.broker)
        self.broker.register(sub1, UInt8(0))
        with self.assertRaises(ValueError):
            self.broker.register(Subscriber(self.broker), UInt8(0))

    def test_unregister_requires_matching_subscriber(self) -> None:
        sub = Subscriber(self.broker)
        other = Subscriber(self.broker)
        self.broker.register(sub, UInt8(0))

        with self.assertRaises(ValueError):
            self.broker.unregister(other, UInt8(0))

        self.broker.unregister(sub, UInt8(0))

        self.publisher.publish(Message(UInt8(123)))
        self.assertEqual(sub.counts[123], 0)


if __name__ == "__main__":
    unittest.main()
