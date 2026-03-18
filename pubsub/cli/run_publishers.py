#!/usr/bin/env python3
"""Run a single publisher."""

import argparse
import logging
import random
import signal
import sys
import time

from pubsub.config import get_config
from pubsub.core.message import Message
from pubsub.core.uint8 import UInt8
from pubsub.gossip.status import PublisherStatusServer
from pubsub.network.node import NodeAddress
from pubsub.network.publisher import NetworkPublisher
from pubsub.utils.log import log_header, setup_logging

logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(description="Run a single Publisher")
    parser.add_argument("--config", default="config.yaml", help="Path to config file")
    parser.add_argument(
        "--publisher-id", type=int, required=True, help="Publisher ID (0-indexed)"
    )
    parser.add_argument("--host", help="Override host from config")
    parser.add_argument("--port", type=int, help="Override port from config")
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
    parser.add_argument(
        "--status-port",
        type=int,
        default=None,
        help="Port for the HTTP /status endpoint (default: port + 10000)",
    )
    args = parser.parse_args()

    if args.seed is not None:
        random.seed(args.seed)

    config = get_config(args.config)
    setup_logging(
        level=args.log_level,
        log_file=args.log_file or config.log_file,
        json_console=config.log_json_console,
    )

    if args.publisher_id < 0 or args.publisher_id >= config.publisher_count:
        logger.error(
            "publisher ID %d out of range [0, %d)",
            args.publisher_id,
            config.publisher_count,
        )
        sys.exit(1)

    host = args.host or config.publisher_host
    port = (
        args.port
        if args.port is not None
        else config.publisher_base_port + args.publisher_id
    )

    log_header(f"PUBLISHER {args.publisher_id}")
    logger.info("starting on %s:%d", host, port)

    address = NodeAddress(host, port)
    status_port = args.status_port if args.status_port is not None else port + 10000

    broker_addresses = config.broker_addresses
    publisher = NetworkPublisher(address, broker_addresses, ttl=config.ttl)
    publisher._status_port = status_port

    status_server = PublisherStatusServer(publisher, status_port)
    status_server.start()

    logger.info(
        "publisher %d on %s:%d targeting %d broker(s)",
        args.publisher_id,
        host,
        port,
        len(broker_addresses),
    )
    logger.info(
        "status http://0.0.0.0:%d/status — Ctrl+C to stop",
        status_port,
    )

    def signal_handler(sig, frame):
        logger.info("shutting down")
        publisher.close()
        status_server.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    msg_count = 0
    try:
        while True:
            payload = UInt8(random.randint(0, 255))
            sent = publisher.publish(Message(payload), redundancy=2)
            msg_count += 1

            logger.info(
                "published payload=%d sent_to=%d broker(s) (total: %d)",
                payload,
                sent,
                msg_count,
            )

            time.sleep(args.interval)
    except Exception:
        logger.exception("publisher error")

    signal_handler(None, None)


if __name__ == "__main__":
    main()
