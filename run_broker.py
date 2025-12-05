#!/usr/bin/env python3
"""Run a broker on EC2."""
import argparse
import signal
import sys
import time

from config_loader import get_config
from gossip_broker import GossipBroker
from log_utils import log_header, log_info, log_success, log_warning
from network import NodeAddress


def main():
    parser = argparse.ArgumentParser(description="Run Gossip Broker")
    parser.add_argument("--config", default="config.yaml", help="Path to config file")
    parser.add_argument("--broker-id", type=int, required=True, help="Broker ID (1-4)")
    parser.add_argument("--host", help="Override host from config")
    parser.add_argument("--port", type=int, help="Override port from config")
    args = parser.parse_args()

    config = get_config(args.config)

    # Find broker config by ID
    broker_config = None
    for b in config.brokers:
        if b.id == args.broker_id:
            broker_config = b
            break

    if broker_config is None:
        log_warning("Broker", f"Broker ID {args.broker_id} not found in config")
        sys.exit(1)

    host = args.host or broker_config.host
    port = args.port or broker_config.port

    log_header(f"BROKER {args.broker_id}")
    log_info("Broker", f"Starting on {host}:{port}")

    address = NodeAddress(host, port)
    broker = GossipBroker(
        address,
        fanout=config.fanout,
        ttl=config.ttl,
        snapshot_interval=config.snapshot_interval,
    )

    # Register with bootstrap
    log_info("Broker", f"Registering with bootstrap at {config.bootstrap_address}")
    try:
        broker.network.send("JOIN", config.bootstrap_address)
        membership, _ = broker.network.receive(timeout=10.0)

        if membership:
            for peer_addr in membership.brokers:
                if peer_addr != address:
                    broker.add_peer(peer_addr)
            log_success("Broker", f"Registered with {len(membership.brokers)} peer(s)")
        else:
            log_warning("Broker", "No membership response from bootstrap")
    except Exception as e:
        log_warning("Broker", f"Failed to register with bootstrap: {e}")

    broker.start()

    log_success("Broker", f"Broker {args.broker_id} running on {host}:{port}")
    log_info("Broker", "Press Ctrl+C to stop")

    # Handle graceful shutdown
    def signal_handler(sig, frame):
        log_info("Broker", "Shutting down...")
        broker.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Keep running
    while True:
        time.sleep(1)


if __name__ == "__main__":
    main()
