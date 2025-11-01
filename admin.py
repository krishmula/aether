"""Admin utility to spin up a pub-sub system."""
import argparse
import random
import threading
import time
from typing import List, Tuple, NamedTuple

from broker import Broker
from message import Message
from publisher import Publisher
from subscriber import Subscriber
from uint8 import UInt8
from payload_range import PayloadRange


class SubscriberInfo(NamedTuple):
    range: PayloadRange
    subscriber: Subscriber


def partition_payload_space(num_subs: UInt8) -> List[PayloadRange]:
    """
    Partition 0..255 inclusive into `num_subs` ranges.
    If the range cannot be evenly divided,
    the remainder is added into the first range(s).
    """
    payload_ranges: List[PayloadRange] = []
    for i in range(num_subs):
        start = (i * 256) // num_subs
        end = ((i + 1) * 256) // num_subs - 1
        payload_ranges.append(PayloadRange(UInt8(start), UInt8(end)))

    return payload_ranges


def configure_system(num_subs: UInt8, publish_interval: float):
    """Configure a broker with a publisher and subscribers."""
    broker = Broker()
    publisher = Publisher(broker)
    subs: List[Tuple[int, int, int, Subscriber]] = []

    def sub_filter_fn(msg: Message, payload_range: PayloadRange) -> bool:
        return payload_range.low <= msg.payload <= payload_range.high

    for payload_range in partition_payload_space(num_subs):
        sub = Subscriber(broker, sub_filter_fn)
        broker.register(sub, UInt8(idx))
        subs.append((idx, l, r, sub))

    stop_event = threading.Event()

    def run_publisher() -> None:
        while not stop_event.is_set():
            payload = UInt8(random.randint(0, 255))
            publisher.publish(Message(payload))
            stop_event.wait(publish_interval)

    thread = threading.Thread(
        target=run_publisher, name="publisher", daemon=True
    )
    thread.start()
    return broker, subs, stop_event, thread


def main() -> None:
    parser = argparse.ArgumentParser(description="Configure broker, publisher, and subscribers")
    parser.add_argument("subscribers", type=int, help="Number of subscribers within the range 1 to 256 inclusive")
    parser.add_argument(
        "--publish-interval",
        type=float,
        default=1.0,
        help="Seconds between random publishes (default: 1.0)",
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=None,
        help="Optional duration in seconds to run before exiting.",
    )
    args = parser.parse_args()

    broker, subscribers, stop_event, thread = configure_system(
        args.subscribers, args.publish_interval
    )
    print(f"Configured broker with {args.subscribers} subscribers")

    print("Publisher running; press Ctrl+C to stop.")
    time.sleep(args.publish_interval / 2)
    try:
        if args.duration is None:
            while True:
                for i, l, r, sub in subscribers:
                    print(
                        f"Slot {i:3d}: payload [{l}, {r}] "
                        f"counts sum={sum(sub.counts)}"
                    )
                print("---")
                time.sleep(args.publish_interval)
        else:
            end_time = time.time() + args.duration
            while time.time() < end_time:
                for i, l, r, sub in subscribers:
                    print(
                        f"Slot {i:3d}: payload [{l}, {r}] "
                        f"counts sum={sum(sub.counts)}"
                    )
                print("---")
                time.sleep(args.publish_interval)
    except KeyboardInterrupt:
        print("\nShutting down.")
    finally:
        stop_event.set()
        thread.join(timeout=1.0)


if __name__ == "__main__":
    main()
