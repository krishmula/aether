#!/usr/bin/env python3
"""Run subscribers locally."""

import argparse
import logging
import signal
import time
from typing import List

from pubsub.config import get_config
from pubsub.core.payload_range import partition_payload_space
from pubsub.core.uint8 import UInt8
from pubsub.network.node import NodeAddress
from pubsub.network.subscriber import NetworkSubscriber
from pubsub.utils.log import log_header, log_separator, setup_logging

logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(description="Run Local Subscribers")
    parser.add_argument("--config", default="config.yaml", help="Path to config file")
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Console log level (default: INFO)",
    )
    parser.add_argument(
        "--log-file", default=None, help="Optional path to write rotating JSON logs"
    )
    args = parser.parse_args()

    config = get_config(args.config)
    setup_logging(
        level=args.log_level,
        log_file=args.log_file or config.log_file,
        json_console=config.log_json_console,
    )

    log_header("LOCAL SUBSCRIBERS")

    subscribers: List[NetworkSubscriber] = []

    total_subscribers = len(config.brokers) * config.subscribers_per_broker
    payload_ranges = partition_payload_space(UInt8(total_subscribers))

    logger.info(
        "creating %d subscribers on %s", total_subscribers, config.subscriber_host
    )

    subscriber_idx = 0
    for broker_config in config.brokers:
        broker_addr = broker_config.to_address()

        for _ in range(config.subscribers_per_broker):
            port = config.subscriber_base_port + subscriber_idx
            sub_addr = NodeAddress(config.subscriber_host, port)

            sub = NetworkSubscriber(sub_addr)
            sub.connect_to_broker(broker_addr)

            pr = payload_ranges[subscriber_idx % len(payload_ranges)]
            success = sub.subscribe(pr)

            if success:
                logger.info(
                    "subscriber:%d connected to broker %d range=[%s-%s]",
                    port,
                    broker_config.id,
                    pr.low,
                    pr.high,
                )

            sub.start()
            subscribers.append(sub)
            subscriber_idx += 1

    log_separator()
    logger.info("all %d subscribers started — press Ctrl+C to stop", len(subscribers))

    running = True

    def signal_handler(sig, frame):
        nonlocal running
        running = False

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    try:
        while running:
            time.sleep(5.0)
            if running:
                log_separator("SUBSCRIBER STATISTICS")
                for i, sub in enumerate(subscribers):
                    total = sum(sub.counts)
                    logger.info("subscriber %d: %d messages received", i, total)
    except Exception:
        pass

    log_separator("FINAL STATISTICS")
    for i, sub in enumerate(subscribers):
        total = sum(sub.counts)
        logger.info("subscriber %d: %d total messages", i, total)

    logger.info("stopping subscribers")
    for sub in subscribers:
        sub.stop()

    logger.info("all subscribers stopped")


if __name__ == "__main__":
    main()
