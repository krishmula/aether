import argparse
import random
import time
from typing import List

from bootstrap import BootstrapServer
from gossip_broker import GossipBroker
from log_utils import (
    log_header,
    log_info,
    log_separator,
    log_success,
    log_system,
    log_warning,
)
from message import Message
from network import NodeAddress
from network_publisher import NetworkPublisher
from network_subscriber import NetworkSubscriber
from payload_range import partition_payload_space
from uint8 import UInt8


def main() -> None:
    parser = argparse.ArgumentParser(description="Distributed Gossip Pub-Sub Admin")
    parser.add_argument(
        "brokers", type=int, help="Number of gossip broker nodes to start"
    )
    parser.add_argument(
        "subscribers_per_broker", type=int, help="Number of subscribers per broker"
    )
    parser.add_argument("--base-port", type=int, default=8000)
    parser.add_argument("--publish-interval", type=float, default=1.0)
    parser.add_argument("--duration", type=float, default=1.0)
    parser.add_argument("--seed", type=int, default=None)
    args = parser.parse_args()

    if args.seed:
        random.seed(args.seed)

    log_header("DISTRIBUTED GOSSIP PUB-SUB SYSTEM")

    bootstrap_addr = NodeAddress("localhost", 7000)
    bootstrap = BootstrapServer(bootstrap_addr)
    bootstrap.start()

    # Give bootstrap MORE time to be fully ready
    log_info("Setup", "Waiting for bootstrap to be ready...")
    time.sleep(2.0)  # Increased from 0.5

    brokers: List[GossipBroker] = []
    all_subscribers: List[NetworkSubscriber] = []

    total_subscribers = args.brokers * args.subscribers_per_broker
    payload_ranges = partition_payload_space(UInt8(total_subscribers))

    # Create all brokers first without starting them
    log_info("Setup", f"Creating {args.brokers} brokers...")
    for broker_id in range(args.brokers):
        broker_port = args.base_port + broker_id
        broker_addr = NodeAddress("localhost", broker_port)
        broker = GossipBroker(broker_addr, fanout=2, ttl=5)
        brokers.append(broker)

        # Give each broker time to start its TCP server
        time.sleep(0.5)

    # Now that all broker TCP servers are listening, register them with bootstrap
    log_info("Setup", "Registering brokers with bootstrap...")
    for broker in brokers:
        try:
            # Send JOIN to bootstrap
            broker.network.send("JOIN", bootstrap_addr)

            # Wait for membership update
            membership, _ = broker.network.receive(timeout=5.0)  # Increased timeout
            if membership:
                for peer_addr in membership.brokers:
                    broker.add_peer(peer_addr)
                log_success(
                    "Setup",
                    f"Broker {broker.address.port} registered with {len(membership.brokers)} peers",
                )
            else:
                log_warning(
                    "Setup",
                    f"Broker {broker.address.port} did not receive membership update",
                )
        except Exception as e:
            log_warning("Setup", f"Error registering broker {broker.address.port}: {e}")

        time.sleep(0.5)  # Space out registrations

    # Start all brokers
    log_info("Setup", "Starting all brokers...")
    for broker in brokers:
        broker.start()
        time.sleep(0.3)  # Give each broker time to fully start

    # Give brokers time to establish connections with each other
    log_info("Setup", "Waiting for broker mesh to stabilize...")
    time.sleep(2.0)

    # Now create subscribers
    log_info("Setup", f"Creating {total_subscribers} subscribers...")
    for broker_id in range(args.brokers):
        broker_addr = brokers[broker_id].address

        for sub_num in range(args.subscribers_per_broker):
            pr = random.choice(payload_ranges)
            subscriber_port = 10000 + len(all_subscribers)

            sub = NetworkSubscriber(address=NodeAddress("localhost", subscriber_port))

            # Give subscriber time to start its TCP server
            time.sleep(0.2)

            sub.connect_to_broker(broker_addr)

            # Subscribe to payload range (BLOCKING - waits for ack)
            success = sub.subscribe(pr)
            if success:
                log_info(
                    f"Subscriber:{subscriber_port}",
                    f"Subscribed to range [{pr.low}-{pr.high}]",
                )
            else:
                log_warning(
                    f"Subscriber:{subscriber_port}",
                    f"Failed to subscribe to range [{pr.low}-{pr.high}]",
                )

            sub.start()
            all_subscribers.append(sub)

    # Give everything time to fully stabilize
    time.sleep(2.0)

    broker_addresses = [b.address for b in brokers]
    publisher_addr = NodeAddress("localhost", 9000)
    publisher = NetworkPublisher(publisher_addr, broker_addresses)

    # Give publisher time to start
    time.sleep(0.5)

    log_separator("SYSTEM STATUS")
    log_system("Configuration", f"{args.brokers} broker nodes active")
    log_system("Configuration", f"{len(all_subscribers)} subscribers registered")
    log_system("Configuration", f"Publisher targeting {len(broker_addresses)} brokers")
    log_separator()

    try:
        start_time = time.time()
        msg_count = 0

        while True:
            payload = UInt8(random.randint(0, 255))
            publisher.publish(Message(payload))
            msg_count += 1

            if msg_count % 5 == 0:
                log_separator(f"STATISTICS AFTER {msg_count} MESSAGES")
                for i, sub in enumerate(all_subscribers):
                    total = sum(sub.counts)
                    if total > 0:
                        log_info(
                            "Stats", f"Subscriber {i:2d}: {total:3d} messages received"
                        )
                log_separator()

            time.sleep(args.publish_interval)

            if args.duration and (time.time() - start_time) > args.duration:
                break

    except KeyboardInterrupt:
        log_warning("System", "Interrupted by user, shutting down...")
    finally:
        publisher.close()

        for sub in all_subscribers:
            sub.stop()

        for broker in brokers:
            broker.stop()

        bootstrap.stop()

        log_separator("FINAL STATISTICS")
        for i, sub in enumerate(all_subscribers):
            total = sum(sub.counts)
            log_success("Final", f"Subscriber {i:2d}: {total:3d} total messages")
        log_separator()


if __name__ == "__main__":
    main()
