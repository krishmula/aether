import argparse
import logging
import random
import time
from typing import List

from aether.core.message import Message
from aether.core.payload_range import partition_payload_space
from aether.core.uint8 import UInt8
from aether.gossip.bootstrap import BootstrapServer
from aether.gossip.broker import GossipBroker
from aether.network.node import NodeAddress
from aether.network.publisher import NetworkPublisher
from aether.network.subscriber import NetworkSubscriber
from aether.utils.log import log_header, log_separator, setup_logging

logger = logging.getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(description="Distributed Gossip Aether Admin")
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

    setup_logging(level=args.log_level, log_file=args.log_file)

    if args.seed:
        random.seed(args.seed)

    log_header("DISTRIBUTED GOSSIP AETHER SYSTEM")

    # Bootstrap server setup
    bootstrap_addr = NodeAddress("localhost", 7000)
    bootstrap = BootstrapServer(bootstrap_addr)
    bootstrap.start()

    logger.info("waiting for bootstrap to be ready")
    time.sleep(2.0)

    # Broker setup
    brokers: List[GossipBroker] = []
    all_subscribers: List[NetworkSubscriber] = []

    total_subscribers = args.brokers * args.subscribers_per_broker
    payload_ranges = partition_payload_space(UInt8(total_subscribers))

    logger.info("creating %d brokers", args.brokers)
    for broker_id in range(args.brokers):
        broker_port = args.base_port + broker_id
        broker_addr = NodeAddress("localhost", broker_port)
        broker = GossipBroker(broker_addr, fanout=2, ttl=5)
        brokers.append(broker)
        time.sleep(0.5)

    logger.info("registering brokers with bootstrap")
    for broker in brokers:
        try:
            broker.network.send("JOIN", bootstrap_addr)
            membership, _ = broker.network.receive(timeout=5.0)
            if membership:
                for peer_addr in membership.brokers:
                    broker.add_peer(peer_addr)
                logger.info(
                    "broker:%d registered with %d peer(s)",
                    broker.address.port,
                    len(membership.brokers),
                )
            else:
                logger.warning(
                    "broker:%d did not receive membership update", broker.address.port
                )
        except Exception:
            logger.warning(
                "error registering broker:%d", broker.address.port, exc_info=True
            )
        time.sleep(0.5)

    logger.info("starting all brokers")
    for broker in brokers:
        broker.start()
        time.sleep(0.3)

    logger.info("waiting for broker mesh to stabilize")
    time.sleep(2.0)

    # Subscriber setup
    logger.info("creating %d subscribers", total_subscribers)
    for broker_id in range(args.brokers):
        broker_addr = brokers[broker_id].address

        for _sub_num in range(args.subscribers_per_broker):
            pr = random.choice(payload_ranges)
            subscriber_port = 10000 + len(all_subscribers)

            sub = NetworkSubscriber(address=NodeAddress("localhost", subscriber_port))
            time.sleep(0.2)

            sub.connect_to_broker(broker_addr)
            success = sub.subscribe(pr)
            if success:
                logger.info(
                    "subscriber:%d subscribed range=[%s-%s]",
                    subscriber_port,
                    pr.low,
                    pr.high,
                )
            else:
                logger.warning(
                    "subscriber:%d failed to subscribe range=[%s-%s]",
                    subscriber_port,
                    pr.low,
                    pr.high,
                )

            sub.start()
            all_subscribers.append(sub)

    time.sleep(2.0)

    # Publisher setup
    broker_addresses = [b.address for b in brokers]
    publishers: List[NetworkPublisher] = []

    logger.info("creating %d publishers", args.publishers)
    for pub_id in range(args.publishers):
        publisher_port = 9000 + pub_id
        publisher_addr = NodeAddress("localhost", publisher_port)

        publisher = NetworkPublisher(publisher_addr, broker_addresses)
        publishers.append(publisher)

        logger.info("publisher:%d created", publisher_port)
        time.sleep(0.5)

    log_separator("SYSTEM STATUS")
    logger.info("%d broker nodes active", args.brokers)
    logger.info("%d subscribers registered", len(all_subscribers))
    logger.info("%d publishers active", len(publishers))
    logger.info("each publisher targeting up to 2 random brokers")
    log_separator()

    try:
        start_time = time.time()
        msg_count = 0

        while True:
            for pub_id, publisher in enumerate(publishers):
                payload = UInt8(random.randint(0, 255))
                publisher.publish(Message(payload))
                logger.debug(
                    "publisher-%d:%d published payload=%d",
                    pub_id,
                    publisher.address.port,
                    payload,
                )

            msg_count += len(publishers)

            if msg_count % 5 == 0:
                log_separator(f"STATISTICS AFTER {msg_count} MESSAGES")

                for i, sub in enumerate(all_subscribers):
                    total = sum(sub.counts)
                    if total > 0:
                        logger.info("subscriber %02d: %3d messages received", i, total)

                logger.info(
                    "each publisher has sent ~%d messages",
                    msg_count // len(publishers),
                )
                log_separator()

            time.sleep(args.publish_interval)

            if args.duration and (time.time() - start_time) > args.duration:
                break

    except KeyboardInterrupt:
        logger.warning("interrupted by user, shutting down")
    finally:
        logger.info("stopping publishers")
        for pub_id, publisher in enumerate(publishers):
            publisher.close()
            logger.debug("publisher %d closed", pub_id)

        logger.info("waiting for connections to drain")
        time.sleep(2.0)

        logger.info("stopping subscribers")
        for sub in all_subscribers:
            sub.stop()

        time.sleep(1.0)

        logger.info("stopping brokers")
        for broker in brokers:
            broker.stop()

        time.sleep(0.5)
        bootstrap.stop()

        log_separator("FINAL STATISTICS")
        logger.info("total messages published: %d", msg_count)
        logger.info("messages per publisher: %d", msg_count // len(publishers))

        for i, sub in enumerate(all_subscribers):
            total = sum(sub.counts)
            logger.info("subscriber %02d: %3d total messages", i, total)

        log_separator("SNAPSHOT STATISTICS")

        for i, broker in enumerate(brokers):
            num_replicas = len(broker._peer_snapshots)
            logger.info(
                "broker %d (port %d): %d peer snapshot(s) stored",
                i,
                broker.address.port,
                num_replicas,
            )

            for peer_addr, snapshot in broker._peer_snapshots.items():
                logger.info(
                    "  broker %d replica from port %d: subs=%d seen_msgs=%d",
                    i,
                    peer_addr.port,
                    len(snapshot.remote_subscribers),
                    len(snapshot.seen_message_ids),
                )

        total_replicas = sum(len(b._peer_snapshots) for b in brokers)
        logger.info("total snapshot replicas across cluster: %d", total_replicas)

        if total_replicas >= len(brokers):
            logger.info("all brokers have at least one replica stored")
        else:
            logger.warning("some brokers may not have redundant snapshots")

        log_separator()


if __name__ == "__main__":
    main()
