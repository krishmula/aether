#!/usr/bin/env python3
"""Run a broker on EC2."""

import argparse
import logging
import signal
import sys
import time

from aether.config import get_config
from aether.gossip.broker import GossipBroker
from aether.network.node import NodeAddress
from aether.utils.log import log_header, setup_logging

logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(description="Run Gossip Broker")
    parser.add_argument("--config", default="config.yaml", help="Path to config file")
    parser.add_argument("--broker-id", type=int, required=True, help="Broker ID (1-4)")
    parser.add_argument(
        "--host", help="Broker hostname (required if broker ID not in config)"
    )
    parser.add_argument(
        "--port", type=int, help="Broker port (required if broker ID not in config)"
    )
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
        help="Port for the HTTP /status endpoint (default: broker_port + 10000)",
    )
    args = parser.parse_args()

    config = get_config(args.config)
    setup_logging(
        level=args.log_level,
        log_file=args.log_file or config.log_file,
        json_console=config.log_json_console,
    )

    if args.host and args.port is not None:
        host = args.host
        port = args.port
    else:
        broker_config = None
        for b in config.brokers:
            if b.id == args.broker_id:
                broker_config = b
                break

        if broker_config is None:
            logger.error(
                "broker ID %d not found in config — provide --host and --port to bypass",
                args.broker_id,
            )
            sys.exit(1)

        host = args.host or broker_config.host
        port = args.port if args.port is not None else broker_config.port

    log_header(f"BROKER {args.broker_id}")
    logger.info("starting on %s:%d", host, port)

    address = NodeAddress(host, port)
    status_port = args.status_port if args.status_port is not None else port + 10000
    broker = GossipBroker(
        address,
        fanout=config.fanout,
        ttl=config.ttl,
        heartbeat_interval=config.heartbeat_interval,
        heartbeat_timeout=config.heartbeat_timeout,
        snapshot_interval=config.snapshot_interval,
        http_port=status_port,
    )

    logger.info("registering with bootstrap at %s", config.bootstrap_address)
    try:
        broker.network.send("JOIN", config.bootstrap_address)
        membership, _ = broker.network.receive(timeout=10.0)

        if membership:
            for peer_addr in membership.brokers:
                if peer_addr != address:
                    broker.add_peer(peer_addr)
            logger.info("registered with %d peer(s)", len(membership.brokers))
        else:
            logger.warning("no membership response from bootstrap")
    except Exception:
        logger.warning("failed to register with bootstrap", exc_info=True)

    broker.start()

    logger.info(
        "broker %d running on %s:%d (status http://0.0.0.0:%d/status) — Ctrl+C to stop",
        args.broker_id,
        host,
        port,
        status_port,
    )

    def signal_handler(sig, frame):
        logger.info("shutting down")
        broker.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    while True:
        time.sleep(1)


if __name__ == "__main__":
    main()
