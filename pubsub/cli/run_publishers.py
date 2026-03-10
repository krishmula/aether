#!/usr/bin/env python3
"""Run publishers locally."""

import argparse
import logging
import random
import signal
import time
from typing import List

from pubsub.config import get_config
from pubsub.core.message import Message
from pubsub.core.uint8 import UInt8
from pubsub.network.node import NodeAddress
from pubsub.network.publisher import NetworkPublisher
from pubsub.utils.log import log_header, log_separator, setup_logging

logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(description="Run Local Publishers")
    parser.add_argument("--config", default="config.yaml", help="Path to config file")
    parser.add_argument(
        "--interval", type=float, default=1.0, help="Publish interval in seconds"
    )
    parser.add_argument("--seed", type=int, help="Random seed for reproducibility")
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

    if args.seed:
        random.seed(args.seed)

    config = get_config(args.config)
    setup_logging(
        level=args.log_level,
        log_file=args.log_file or config.log_file,
        json_console=config.log_json_console,
    )

    log_header("LOCAL PUBLISHERS")

    publishers: List[NetworkPublisher] = []
    broker_addresses = config.broker_addresses

    logger.info(
        "creating %d publishers targeting %d brokers",
        config.publisher_count,
        len(broker_addresses),
    )

    for i in range(config.publisher_count):
        port = config.publisher_base_port + i
        pub_addr = NodeAddress(config.publisher_host, port)

        publisher = NetworkPublisher(pub_addr, broker_addresses, ttl=config.ttl)
        publishers.append(publisher)

        logger.info(
            "publisher:%d created, targeting %d brokers", port, len(broker_addresses)
        )

    log_separator()
    logger.info(
        "all %d publishers ready — publishing every %ss", len(publishers), args.interval
    )

    running = True
    msg_count = 0

    def signal_handler(sig, frame):
        nonlocal running
        running = False

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    try:
        while running:
            for pub_id, publisher in enumerate(publishers):
                if not running:
                    break

                payload = UInt8(random.randint(0, 255))
                sent = publisher.publish(Message(payload), redundancy=2)
                msg_count += 1

                logger.info(
                    "publisher-%d published payload=%d sent_to=%d broker(s)",
                    pub_id,
                    payload,
                    sent,
                )

            time.sleep(args.interval)
    except Exception:
        pass

    log_separator("SUMMARY")
    logger.info("total messages published: %d", msg_count)

    logger.info("closing publishers")
    for pub in publishers:
        pub.close()

    logger.info("all publishers closed")


if __name__ == "__main__":
    main()
