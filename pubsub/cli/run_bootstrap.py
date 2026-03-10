#!/usr/bin/env python3
"""Run the bootstrap server on EC2."""

import argparse
import logging
import signal
import sys

from pubsub.config import get_config
from pubsub.gossip.bootstrap import BootstrapServer
from pubsub.network.node import NodeAddress
from pubsub.utils.log import log_header, setup_logging

logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(description="Run Bootstrap Server")
    parser.add_argument("--config", default="config.yaml", help="Path to config file")
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
    args = parser.parse_args()

    config = get_config(args.config)
    setup_logging(
        level=args.log_level,
        log_file=args.log_file or config.log_file,
        json_console=config.log_json_console,
    )

    host = args.host or config.bootstrap_host
    port = args.port or config.bootstrap_port

    log_header("BOOTSTRAP SERVER")
    logger.info("starting on %s:%d", host, port)

    address = NodeAddress(host, port)
    bootstrap = BootstrapServer(address)
    bootstrap.start()

    logger.info("server running on %s:%d — press Ctrl+C to stop", host, port)

    def signal_handler(sig, frame):
        logger.info("shutting down")
        bootstrap.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    signal.pause()


if __name__ == "__main__":
    main()
