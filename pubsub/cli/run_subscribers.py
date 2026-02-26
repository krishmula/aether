#!/usr/bin/env python3
"""Run subscribers locally."""
import argparse
import random
import signal
import sys
import time
from typing import List

from pubsub.config import get_config
from pubsub.utils.log import log_header, log_info, log_separator, log_success
from pubsub.network.node import NodeAddress
from pubsub.network.subscriber import NetworkSubscriber
from pubsub.core.payload_range import partition_payload_space
from pubsub.core.uint8 import UInt8


def main():
    parser = argparse.ArgumentParser(description="Run Local Subscribers")
    parser.add_argument("--config", default="config.yaml", help="Path to config file")
    args = parser.parse_args()

    config = get_config(args.config)

    log_header("LOCAL SUBSCRIBERS")

    subscribers: List[NetworkSubscriber] = []

    total_subscribers = len(config.brokers) * config.subscribers_per_broker
    payload_ranges = partition_payload_space(UInt8(total_subscribers))

    log_info("Setup", f"Creating {total_subscribers} subscribers")
    log_info("Setup", f"Local host: {config.subscriber_host}")

    subscriber_idx = 0
    for broker_config in config.brokers:
        broker_addr = broker_config.to_address()

        for _ in range(config.subscribers_per_broker):
            port = config.subscriber_base_port + subscriber_idx
            sub_addr = NodeAddress(config.subscriber_host, port)

            sub = NetworkSubscriber(sub_addr)
            sub.connect_to_broker(broker_addr)

            # Subscribe to a payload range
            pr = payload_ranges[subscriber_idx % len(payload_ranges)]
            success = sub.subscribe(pr)

            if success:
                log_success(
                    f"Subscriber:{port}",
                    f"Connected to broker {broker_config.id}, range [{pr.low}-{pr.high}]",
                )

            sub.start()
            subscribers.append(sub)
            subscriber_idx += 1

    log_separator()
    log_success("Setup", f"All {len(subscribers)} subscribers started")
    log_info("Info", "Press Ctrl+C to stop and see statistics")

    # Handle graceful shutdown
    running = True

    def signal_handler(sig, frame):
        nonlocal running
        running = False

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Print stats periodically
    try:
        while running:
            time.sleep(5.0)
            if running:
                log_separator("SUBSCRIBER STATISTICS")
                for i, sub in enumerate(subscribers):
                    total = sum(sub.counts)
                    log_info("Stats", f"Subscriber {i}: {total} messages received")
    except:
        pass

    # Final stats
    log_separator("FINAL STATISTICS")
    for i, sub in enumerate(subscribers):
        total = sum(sub.counts)
        log_success("Final", f"Subscriber {i}: {total} total messages")

    # Cleanup
    log_info("Cleanup", "Stopping subscribers...")
    for sub in subscribers:
        sub.stop()

    log_success("Done", "All subscribers stopped")


if __name__ == "__main__":
    main()
