"""Admin utility to spin up a pub-sub system."""
import argparse
import random
import threading
import time
from dataclasses import dataclass
from typing import List, Tuple

from pubsub.core.broker import Broker
from pubsub.core.message import Message
from pubsub.core.payload_range import PayloadRange
from pubsub.core.publisher import Publisher
from pubsub.core.subscriber import Subscriber
from pubsub.core.uint8 import UInt8
from pubsub.utils.log import log_info, log_success, log_warning, log_separator, log_header, log_system


@dataclass(frozen=True)
class SubscriberInfo:
    payload_range: PayloadRange
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


def launch_system(
    sub_count: UInt8,
    publish_interval: float
) -> Tuple[Broker, List[SubscriberInfo], threading.Event, threading.Thread]:
    """
    Configure the broker and its subscribers.
    Start the publisher thread.
    """
    broker = Broker()
    publisher = Publisher(broker)

    subscriber_info_list: List[SubscriberInfo] = []
    for payload_range in partition_payload_space(sub_count):
        subscriber = Subscriber()
        broker.register(subscriber, payload_range)
        subscriber_info_list.append(SubscriberInfo(payload_range, subscriber))

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
    return broker, subscriber_info_list, stop_event, thread


def main() -> None:

    def unsigned_float(x: str) -> float:
        v = float(x)
        if v < 0:
            raise argparse.ArgumentTypeError("must be >= 0")
        return v

    def unsigned_byte_int(x: str) -> int:
        v = int(x)
        if not 0 <= v <= 255:
            raise argparse.ArgumentTypeError("must be within 0 to 255 inclusive")
        return v

    parser = argparse.ArgumentParser(description="Configure pub-sub system")
    parser.add_argument(
        "subscribers",
        type=unsigned_byte_int,
        help="Number of subscribers within the range 0 to 255 inclusive"
    )
    parser.add_argument(
        "--publish-interval",
        type=unsigned_float,
        default=1.0,
        help="Seconds between random publishes (default: 1.0)",
    )
    parser.add_argument(
        "--duration",
        type=unsigned_float,
        default=None,
        help="Optional duration in seconds to run before exiting.",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="Optional random seed for reproducible publishes.",
    )
    args = parser.parse_args()

    if args.seed is not None:
        random.seed(args.seed)

    broker, subscriber_info_list, stop_event, thread = launch_system(
        UInt8(args.subscribers), args.publish_interval
    )
    log_header("PUB-SUB SYSTEM")
    log_system("Configuration", f"{args.subscribers} subscribers configured")
    log_system("Configuration", f"Publish interval: {args.publish_interval}s")
    if args.duration:
        log_system("Configuration", f"Runtime: {args.duration}s")
    if args.seed is not None:
        log_system("Configuration", f"Random seed: {args.seed}")
    log_separator()

    def print_subscriber_stats(subscriber_info_list: List[SubscriberInfo]) -> None:
        log_separator("SUBSCRIBER STATISTICS")
        for i, subscriber_info in enumerate(subscriber_info_list):
            pr = subscriber_info.payload_range
            sub = subscriber_info.subscriber
            log_info(
                "Subscriber",
                f"#{i:3d} │ Range [{int(pr.low):3d}-{int(pr.high):3d}] │ Count: {sum(sub.counts):4d}"
            )
        log_separator()

    log_success("System", "Publisher running (press Ctrl+C to stop)")
    time.sleep(args.publish_interval / 2)
    try:
        if args.duration is None:
            while True:
                print_subscriber_stats(subscriber_info_list)
                time.sleep(args.publish_interval)
        else:
            end_time = time.time() + args.duration
            while time.time() < end_time:
                print_subscriber_stats(subscriber_info_list)
                time.sleep(args.publish_interval)
    except KeyboardInterrupt:
        log_warning("System", "Interrupted by user, shutting down...")
    finally:
        stop_event.set()
        thread.join(timeout=1.0)


if __name__ == "__main__":
    main()
