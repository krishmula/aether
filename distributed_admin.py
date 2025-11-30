import argparse
import random
import time
from typing import List

from bootstrap import BootstrapServer
from gossip_broker import GossipBroker
from message import Message
from network import NodeAddress
from network_publisher import NetworkPublisher
from network_subscriber import NetworkSubscriber
from payload_range import partition_payload_space
from uint8 import UInt8
from log_utils import log_info, log_success, log_warning, log_separator, log_header, log_system


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

    time.sleep(0.5)

    brokers: List[GossipBroker] = []
    all_subscribers: List[NetworkSubscriber] = []  # Changed type

    total_subscribers = args.brokers * args.subscribers_per_broker
    payload_ranges = partition_payload_space(UInt8(total_subscribers))

    for broker_id in range(args.brokers):
        broker_port = args.base_port + broker_id
        broker_addr = NodeAddress("localhost", broker_port)

        broker = GossipBroker(broker_addr, fanout=2, ttl=5)

        # broker sends JOIN payload to the bootstrap server. receives peer list in response.
        broker.network.send("JOIN", bootstrap_addr)

        membership, _ = broker.network.receive(timeout=2.0)
        if membership:
            for peer_addr in membership.brokers:
                broker.add_peer(peer_addr)

        # Start broker BEFORE subscribers connect to it
        broker.start()
        brokers.append(broker)

    # Give brokers time to start their receive loops
    time.sleep(0.5)

    for broker_id in range(args.brokers):
        broker_addr = brokers[broker_id].address

        for _ in range(args.subscribers_per_broker):
            pr = random.choice(payload_ranges)
            subscriber_port = 10000 + len(all_subscribers)

            sub = NetworkSubscriber(
                address=NodeAddress("localhost", subscriber_port)
            )

            sub.connect_to_broker(broker_addr)

            # Subscribe to payload range (BLOCKING - waits for ack)
            success = sub.subscribe(pr)
            if success:
                log_info(f"Subscriber:{subscriber_port}", f"Subscribed to range [{pr.low}-{pr.high}]")
            else:
                log_warning(f"Subscriber:{subscriber_port}", f"Failed to subscribe to range [{pr.low}-{pr.high}]")

            sub.start()

            all_subscribers.append(sub)

    time.sleep(1.0)

    broker_addresses = [b.address for b in brokers]
    publisher_addr = NodeAddress("localhost", 9000)
    publisher = NetworkPublisher(publisher_addr, broker_addresses)

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
                    total = sum(sub.counts)  # Works via @property
                    if total > 0:
                        log_info("Stats", f"Subscriber {i:2d}: {total:3d} messages received")
                log_separator()

            time.sleep(args.publish_interval)

            if args.duration and (time.time() - start_time) > args.duration:
                break

    except KeyboardInterrupt:
        log_warning("System", "Interrupted by user, shutting down...")
    finally:
        publisher.close()

        # Stop subscribers first
        for sub in all_subscribers:
            sub.stop()

        # Then stop brokers
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
