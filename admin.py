"""Admin utility to spin up pub-sub system."""
import argparse
import random
import threading
import time
from typing import List, Tuple

from broker import Broker
from message import Message
from subscriber import Subscriber
from uint8 import UInt8


def partition_payload_space(num_subs: UInt8) -> List[Tuple[UInt8, UInt8]]:
    """
    Partition 0..255 inclusive into `num_subs` intervals
    If the range cannot be evenly divided,
    the remainder is added into the first interval(s).
    """
    ivls: List[Tuple[UInt8, UInt8]] = []
    for i in range(num_subs):
        start = (i * 256) // num_subs
        end = ((i + 1) * 256) // num_subs - 1
        ivls.append((UInt8(start), UInt8(end)))

    return ivls


def configure_broker_and_subscribers(num_subs: UInt8) -> Broker:
    broker = Broker()
    payload_ranges = partition_payload_space(num_subs)

    for i, (l, r) in enumerate(payload_ranges):
        subscriber = Subscriber(
            broker,
            lambda msg, l=l, r=r: l <= msg.payload <= r,
        )
        broker.register(subscriber, UInt8(i))
    return broker


def start_publisher(broker: Broker, seconds: float) -> threading.Thread:
    """
    Launch a publisher emitting payloads at the given interval length,
    specified by `seconds`.
    """

    def run() -> None:
        while True:
            payload = UInt8(random.randint(0, 255))
            broker.publish(Message(payload))
            time.sleep(seconds)

    thread = threading.Thread(target=run, name="publisher", daemon=True)
    thread.start()
    return thread


def main() -> None:
    parser = argparse.ArgumentParser(description="Configure broker and subscribers")
    parser.add_argument("subscribers", type=int, help="Number of subscribers (1..256)")
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

    broker = configure_broker(args.subscribers)
    start_publisher(broker, args.publish_interval)
    print(f"Configured broker with {args.subscribers} subscribers")
    for i, sub in enumerate(broker._subs):  # noqa: SLF001 - introspection for admin view
        if sub is None:
            continue
        print(f"Slot {i:3d}: counts sum={sum(sub.counts)}")

    print("Publisher running; press Ctrl+C to stop.")
    try:
        if args.duration is None:
            while True:
                time.sleep(1)
        else:
            time.sleep(args.duration)
    except KeyboardInterrupt:
        print("\nShutting down.")


if __name__ == "__main__":
    main()
