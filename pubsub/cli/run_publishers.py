#!/usr/bin/env python3
"""Run publishers locally."""
import argparse
import random
import signal
import sys
import time
from typing import List

from pubsub.config import get_config
from pubsub.utils.log import log_header, log_info, log_network, log_separator, log_success
from pubsub.core.message import Message
from pubsub.network.node import NodeAddress
from pubsub.network.publisher import NetworkPublisher
from pubsub.core.uint8 import UInt8


def main():
    parser = argparse.ArgumentParser(description="Run Local Publishers")
    parser.add_argument("--config", default="config.yaml", help="Path to config file")
    parser.add_argument(
        "--interval", type=float, default=1.0, help="Publish interval in seconds"
    )
    parser.add_argument("--seed", type=int, help="Random seed for reproducibility")
    args = parser.parse_args()

    if args.seed:
        random.seed(args.seed)

    config = get_config(args.config)

    log_header("LOCAL PUBLISHERS")

    publishers: List[NetworkPublisher] = []
    broker_addresses = config.broker_addresses

    log_info("Setup", f"Creating {config.publisher_count} publishers")
    log_info("Setup", f"Target brokers: {len(broker_addresses)}")

    for i in range(config.publisher_count):
        port = config.publisher_base_port + i
        pub_addr = NodeAddress(config.publisher_host, port)

        publisher = NetworkPublisher(pub_addr, broker_addresses, ttl=config.ttl)
        publishers.append(publisher)

        log_success(
            f"Publisher:{port}", f"Created, targeting {len(broker_addresses)} brokers"
        )

    log_separator()
    log_success("Setup", f"All {len(publishers)} publishers ready")
    log_info("Info", f"Publishing every {args.interval}s - Press Ctrl+C to stop")

    # Handle graceful shutdown
    running = True
    msg_count = 0

    def signal_handler(sig, frame):
        nonlocal running
        running = False

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Publishing loop
    try:
        while running:
            for pub_id, publisher in enumerate(publishers):
                if not running:
                    break

                payload = UInt8(random.randint(0, 255))
                sent = publisher.publish(Message(payload), redundancy=2)
                msg_count += 1

                log_network(
                    f"Publisher-{pub_id}",
                    "PUBLISH",
                    f"payload={payload}, sent to {sent} broker(s)",
                )

            time.sleep(args.interval)
    except:
        pass

    # Cleanup
    log_separator("SUMMARY")
    log_success("Done", f"Total messages published: {msg_count}")

    log_info("Cleanup", "Closing publishers...")
    for pub in publishers:
        pub.close()

    log_success("Done", "All publishers closed")


if __name__ == "__main__":
    main()
