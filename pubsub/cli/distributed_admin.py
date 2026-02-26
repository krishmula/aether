import argparse
import random
import time
from typing import List

from pubsub.gossip.bootstrap import BootstrapServer
from pubsub.gossip.broker import GossipBroker
from pubsub.utils.log import (
    log_header,
    log_info,
    log_network,
    log_separator,
    log_success,
    log_system,
    log_warning,
)
from pubsub.core.message import Message
from pubsub.network.node import NodeAddress
from pubsub.network.publisher import NetworkPublisher
from pubsub.network.subscriber import NetworkSubscriber
from pubsub.core.payload_range import partition_payload_space
from pubsub.core.uint8 import UInt8


def main() -> None:
    parser = argparse.ArgumentParser(description="Distributed Gossip Pub-Sub Admin")
    parser.add_argument(
        "brokers", type=int, help="Number of gossip broker nodes to start"
    )
    parser.add_argument(
        "subscribers_per_broker", type=int, help="Number of subscribers per broker"
    )
    parser.add_argument(
        "publishers", type=int, help="Number of publisher nodes to start"
    )
    parser.add_argument("--base-port", type=int, default=8000)
    parser.add_argument("--publish-interval", type=float, default=1.0)
    parser.add_argument("--duration", type=float, default=1.0)
    parser.add_argument("--seed", type=int, default=None)
    args = parser.parse_args()

    if args.seed:
        random.seed(args.seed)

    log_header("DISTRIBUTED GOSSIP PUB-SUB SYSTEM")

    # Bootstrap server setup
    bootstrap_addr = NodeAddress("localhost", 7000)
    bootstrap = BootstrapServer(bootstrap_addr)
    bootstrap.start()

    log_info("Setup", "Waiting for bootstrap to be ready...")
    time.sleep(2.0)

    # Broker setup
    brokers: List[GossipBroker] = []
    all_subscribers: List[NetworkSubscriber] = []

    total_subscribers = args.brokers * args.subscribers_per_broker
    payload_ranges = partition_payload_space(UInt8(total_subscribers))

    log_info("Setup", f"Creating {args.brokers} brokers...")
    for broker_id in range(args.brokers):
        broker_port = args.base_port + broker_id
        broker_addr = NodeAddress("localhost", broker_port)
        broker = GossipBroker(broker_addr, fanout=2, ttl=5)
        brokers.append(broker)
        time.sleep(0.5)

    log_info("Setup", "Registering brokers with bootstrap...")
    for broker in brokers:
        try:
            broker.network.send("JOIN", bootstrap_addr)
            membership, _ = broker.network.receive(timeout=5.0)
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
        time.sleep(0.5)

    log_info("Setup", "Starting all brokers...")
    for broker in brokers:
        broker.start()
        time.sleep(0.3)

    log_info("Setup", "Waiting for broker mesh to stabilize...")
    time.sleep(2.0)

    # Subscriber setup
    log_info("Setup", f"Creating {total_subscribers} subscribers...")
    for broker_id in range(args.brokers):
        broker_addr = brokers[broker_id].address

        for sub_num in range(args.subscribers_per_broker):
            pr = random.choice(payload_ranges)
            subscriber_port = 10000 + len(all_subscribers)

            sub = NetworkSubscriber(address=NodeAddress("localhost", subscriber_port))
            time.sleep(0.2)

            sub.connect_to_broker(broker_addr)
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

    time.sleep(2.0)

    # Publisher setup with multiple publishers
    broker_addresses = [b.address for b in brokers]
    publishers: List[NetworkPublisher] = []

    log_info("Setup", f"Creating {args.publishers} publishers...")
    for pub_id in range(args.publishers):
        publisher_port = 9000 + pub_id
        publisher_addr = NodeAddress("localhost", publisher_port)

        publisher = NetworkPublisher(publisher_addr, broker_addresses)
        publishers.append(publisher)

        log_success("Setup", f"Publisher {pub_id} created on port {publisher_port}")

        time.sleep(0.5)

    log_separator("SYSTEM STATUS")
    log_system("Configuration", f"{args.brokers} broker nodes active")
    log_system("Configuration", f"{len(all_subscribers)} subscribers registered")
    log_system("Configuration", f"{len(publishers)} publishers active")
    log_system("Configuration", f"Each publisher targeting up to 2 random brokers")
    log_separator()

    try:
        start_time = time.time()
        msg_count = 0

        while True:
            for pub_id, publisher in enumerate(publishers):
                payload = UInt8(random.randint(0, 255))

                publisher.publish(Message(payload))

                log_network(
                    f"Publisher-{pub_id}:{publisher.address.port}",
                    "PUBLISH",
                    f"payload={payload}",
                )

            msg_count += len(publishers)

            if msg_count % 5 == 0:
                log_separator(f"STATISTICS AFTER {msg_count} MESSAGES")

                for i, sub in enumerate(all_subscribers):
                    total = sum(sub.counts)
                    if total > 0:
                        log_info(
                            "Stats", f"Subscriber {i:2d}: {total:3d} messages received"
                        )

                messages_per_publisher = msg_count // len(publishers)
                log_info(
                    "Stats",
                    f"Each publisher has sent ~{messages_per_publisher} messages",
                )

                log_separator()

            time.sleep(args.publish_interval)

            if args.duration and (time.time() - start_time) > args.duration:
                break

    except KeyboardInterrupt:
        log_warning("System", "Interrupted by user, shutting down...")
    finally:
        # PHASE 1: Stop publishers first
        log_info("Cleanup", "Stopping all publishers...")
        for pub_id, publisher in enumerate(publishers):
            publisher.close()
            log_success("Cleanup", f"Publisher {pub_id} closed")

        # CRITICAL: Add grace period for connections to drain
        log_info("Cleanup", "Waiting for connections to drain...")
        time.sleep(2.0)

        # PHASE 2: Stop subscribers
        log_info("Cleanup", "Stopping all subscribers...")
        for sub in all_subscribers:
            sub.stop()

        time.sleep(1.0)

        # PHASE 3: Stop brokers
        log_info("Cleanup", "Stopping all brokers...")
        for broker in brokers:
            broker.stop()

        # PHASE 4: Stop bootstrap server
        time.sleep(0.5)
        bootstrap.stop()

        # Final statistics
        log_separator("FINAL STATISTICS")
        log_system("Summary", f"Total messages published: {msg_count}")
        log_system("Summary", f"Messages per publisher: {msg_count // len(publishers)}")

        for i, sub in enumerate(all_subscribers):
            total = sum(sub.counts)
            log_success("Final", f"Subscriber {i:2d}: {total:3d} total messages")

        log_separator()

        log_separator("SNAPSHOT STATISTICS")

        for i, broker in enumerate(brokers):
            num_replicas = len(broker._peer_snapshots)

            log_info(
                "Snapshot",
                f"Broker {i} (port {broker.address.port}): {num_replicas} peer snapshot(s) stored",
            )

            for peer_addr, snapshot in broker._peer_snapshots.items():
                log_info(
                    "Snapshot",
                    f"  └─ From {peer_addr.port}: "
                    f"{len(snapshot.remote_subscribers)} subs, "
                    f"{len(snapshot.seen_message_ids)} seen msgs",
                )

        total_replicas = sum(len(b._peer_snapshots) for b in brokers)
        log_success(
            "Snapshot", f"Total snapshot replicas across cluster: {total_replicas}"
        )

        if total_replicas >= len(brokers):
            log_success("Snapshot", "✓ All brokers have at least one replica stored")
        else:
            log_warning("Snapshot", "⚠ Some brokers may not have redundant snapshots")


if __name__ == "__main__":
    main()
