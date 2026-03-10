import time

from pubsub.core.payload_range import PayloadRange
from pubsub.core.uint8 import UInt8
from pubsub.gossip.broker import GossipBroker
from pubsub.network.node import NodeAddress


def test_snapshot_replication():
    print("=" * 60)
    print("TESTING SNAPSHOT REPLICATION (Phases 4 & 5)")
    print("=" * 60)

    # Create 3 brokers with short snapshot interval for testing
    addr1 = NodeAddress("localhost", 7001)
    addr2 = NodeAddress("localhost", 7002)
    addr3 = NodeAddress("localhost", 7003)

    # Use 10-second snapshot interval for faster testing
    broker1 = GossipBroker(addr1, fanout=2, ttl=3, snapshot_interval=10.0)
    broker2 = GossipBroker(addr2, fanout=2, ttl=3, snapshot_interval=10.0)
    broker3 = GossipBroker(addr3, fanout=2, ttl=3, snapshot_interval=10.0)

    # Connect them as peers (full mesh)
    broker1.add_peer(addr2)
    broker1.add_peer(addr3)
    broker2.add_peer(addr1)
    broker2.add_peer(addr3)
    broker3.add_peer(addr1)
    broker3.add_peer(addr2)

    # Add some fake subscribers to make the snapshots interesting
    fake_sub1 = NodeAddress("localhost", 10001)
    fake_sub2 = NodeAddress("localhost", 10002)

    broker1._remote_subscribers[fake_sub1] = {PayloadRange(UInt8(0), UInt8(100))}
    broker2._remote_subscribers[fake_sub2] = {PayloadRange(UInt8(100), UInt8(200))}

    # Rebuild payload_to_remotes
    for i in range(101):
        broker1._payload_to_remotes[i].add(fake_sub1)
    for i in range(100, 201):
        broker2._payload_to_remotes[i].add(fake_sub2)

    # Start all brokers
    print("\n--- Starting brokers ---")
    broker1.start()
    broker2.start()
    broker3.start()

    print("\nWaiting for periodic snapshot to trigger...")
    print("(Snapshot interval: 10 seconds, plus 5 second initial delay)")
    print("Expected first snapshot at ~15 seconds after start\n")

    # Wait for at least one snapshot cycle
    # Initial delay (5s) + snapshot interval (10s) + propagation time (2s)
    for i in range(18, 0, -1):
        print(f"\r  Waiting... {i}s remaining", end="", flush=True)
        time.sleep(1)
    print("\n")

    # Check that snapshots were replicated
    print("--- Checking snapshot replication ---\n")

    print(
        f"Broker1 (port 7001) has snapshots from: "
        f"{list(broker1._peer_snapshots.keys())}"
    )
    print(
        f"Broker2 (port 7002) has snapshots from: "
        f"{list(broker2._peer_snapshots.keys())}"
    )
    print(
        f"Broker3 (port 7003) has snapshots from: "
        f"{list(broker3._peer_snapshots.keys())}"
    )

    # Verify that at least some snapshots were replicated
    total_replicas = (
        len(broker1._peer_snapshots)
        + len(broker2._peer_snapshots)
        + len(broker3._peer_snapshots)
    )

    print(f"\nTotal snapshot replicas stored across all brokers: {total_replicas}")

    # Check the content of stored snapshots
    print("\n--- Snapshot contents ---")
    for broker, name in [
        (broker1, "Broker1"),
        (broker2, "Broker2"),
        (broker3, "Broker3"),
    ]:
        for source_addr, snapshot in broker._peer_snapshots.items():
            print(f"\n{name} has snapshot from {source_addr}:")
            print(f"  Snapshot ID: {snapshot.snapshot_id[:8]}...")
            print(f"  Subscribers: {len(snapshot.remote_subscribers)}")
            print(f"  Peers: {len(snapshot.peer_brokers)}")

    # Clean up
    print("\n--- Shutting down ---")
    broker1.stop()
    time.sleep(0.5)
    broker2.stop()
    time.sleep(0.5)
    broker3.stop()

    # Report results
    print("\n" + "=" * 60)
    if total_replicas >= 2:
        print("✓ SUCCESS: Snapshots are being replicated to peers!")
        print(f"  Found {total_replicas} snapshot replicas across the cluster")
    else:
        print("✗ FAILURE: Not enough snapshot replicas found")
        print(f"  Expected at least 2, found {total_replicas}")
    print("=" * 60)


if __name__ == "__main__":
    test_snapshot_replication()
