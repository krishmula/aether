#!/usr/bin/env python3
"""Run a single subscriber."""

import argparse
import logging
import signal
import sys
import time

from aether.config import get_config
from aether.core.payload_range import partition_payload_space
from aether.core.uint8 import UInt8
from aether.gossip.status import SubscriberStatusServer
from aether.network.node import NodeAddress
from aether.network.subscriber import NetworkSubscriber
from aether.utils.log import log_header, setup_logging

logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(description="Run a single Subscriber")
    parser.add_argument("--config", default="config.yaml", help="Path to config file")
    parser.add_argument(
        "--subscriber-id", type=int, required=True, help="Subscriber ID (0-indexed)"
    )
    parser.add_argument("--host", help="Override host from config")
    parser.add_argument("--port", type=int, help="Override port from config")
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
    parser.add_argument(
        "--broker-host",
        default=None,
        help="Override broker host (for dynamic orchestration)",
    )
    parser.add_argument(
        "--broker-port",
        type=int,
        default=None,
        help="Override broker port (for dynamic orchestration)",
    )
    args = parser.parse_args()

    config = get_config(args.config)
    setup_logging(
        level=args.log_level,
        log_file=args.log_file or config.log_file,
        json_console=config.log_json_console,
    )

    explicit_broker = args.broker_host is not None and args.broker_port is not None

    num_brokers = len(config.brokers)
    total_subscribers = num_brokers * config.subscribers_per_broker

    if not explicit_broker:
        if args.subscriber_id < 0 or args.subscriber_id >= total_subscribers:
            logger.error(
                "subscriber ID %d out of range [0, %d)",
                args.subscriber_id,
                total_subscribers,
            )
            sys.exit(1)

    # Determine broker address: explicit override takes priority over config
    if explicit_broker:
        broker_addr = NodeAddress(args.broker_host, args.broker_port)
    else:
        broker_idx = args.subscriber_id // config.subscribers_per_broker
        broker_config = config.brokers[broker_idx]
        broker_addr = broker_config.to_address()

    payload_ranges = partition_payload_space(UInt8(total_subscribers))
    payload_range = payload_ranges[args.subscriber_id % len(payload_ranges)]

    host = args.host or config.subscriber_host
    port = (
        args.port
        if args.port is not None
        else config.subscriber_base_port + args.subscriber_id
    )

    log_header(f"SUBSCRIBER {args.subscriber_id}")
    logger.info("starting on %s:%d", host, port)

    address = NodeAddress(host, port)
    status_port = args.status_port if args.status_port is not None else port + 10000

    sub = NetworkSubscriber(address)
    sub._status_port = status_port
    sub.connect_to_broker(broker_addr)

    success = sub.subscribe(payload_range)
    if success:
        logger.info(
            "subscribed to broker %s range=[%s-%s]",
            broker_addr,
            payload_range.low,
            payload_range.high,
        )
    else:
        logger.error(
            "failed to subscribe to broker %s range=[%s-%s]",
            broker_addr,
            payload_range.low,
            payload_range.high,
        )
        sys.exit(1)

    sub.start()

    status_server = SubscriberStatusServer(sub, status_port)
    status_server.start()

    logger.info(
        "subscriber %d on %s:%d (status http://0.0.0.0:%d/status) — Ctrl+C to stop",
        args.subscriber_id,
        host,
        port,
        status_port,
    )

    def signal_handler(sig, frame):
        logger.info("shutting down")
        sub.stop()
        status_server.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    while True:
        time.sleep(1)


if __name__ == "__main__":
    main()
