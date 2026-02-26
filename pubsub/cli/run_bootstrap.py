#!/usr/bin/env python3
"""Run the bootstrap server on EC2."""
import argparse
import signal
import sys

from pubsub.gossip.bootstrap import BootstrapServer
from pubsub.config import get_config
from pubsub.utils.log import log_header, log_info, log_success
from pubsub.network.node import NodeAddress


def main():
    parser = argparse.ArgumentParser(description="Run Bootstrap Server")
    parser.add_argument("--config", default="config.yaml", help="Path to config file")
    parser.add_argument("--host", help="Override host from config")
    parser.add_argument("--port", type=int, help="Override port from config")
    args = parser.parse_args()

    config = get_config(args.config)

    host = args.host or config.bootstrap_host
    port = args.port or config.bootstrap_port

    log_header("BOOTSTRAP SERVER")
    log_info("Bootstrap", f"Starting on {host}:{port}")

    address = NodeAddress(host, port)
    bootstrap = BootstrapServer(address)
    bootstrap.start()

    log_success("Bootstrap", f"Server running on {host}:{port}")
    log_info("Bootstrap", "Press Ctrl+C to stop")

    # Handle graceful shutdown
    def signal_handler(sig, frame):
        log_info("Bootstrap", "Shutting down...")
        bootstrap.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Keep running
    signal.pause()


if __name__ == "__main__":
    main()
