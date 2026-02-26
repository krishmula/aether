import time

from pubsub.gossip.broker import GossipBroker
from pubsub.network.node import NodeAddress


def test_marker_propagation():
    print("=" * 60)
    print("TESTING CHANDY-LAMPORT MARKER PROPAGATION")
    print("=" * 60)

    # Create 3 brokers
    addr1 = NodeAddress("localhost", 7001)
    addr2 = NodeAddress("localhost", 7002)
    addr3 = NodeAddress("localhost", 7003)

    broker1 = GossipBroker(addr1, fanout=2, ttl=3)
    broker2 = GossipBroker(addr2, fanout=2, ttl=3)
    broker3 = GossipBroker(addr3, fanout=2, ttl=3)

    # Connect them as peers (full mesh)
    broker1.add_peer(addr2)
    broker1.add_peer(addr3)
    broker2.add_peer(addr1)
    broker2.add_peer(addr3)
    broker3.add_peer(addr1)
    broker3.add_peer(addr2)

    # Start all brokers
    broker1.start()
    broker2.start()
    broker3.start()

    # Let them stabilize
    print("\nWaiting for brokers to stabilize...")
    time.sleep(2.0)

    # Broker 1 initiates a snapshot
    print("\n--- Broker 1 initiating snapshot ---")
    snapshot_id = broker1.initiate_snapshot()
    print(f"Snapshot ID: {snapshot_id}")

    # Wait for markers to propagate
    print("\nWaiting for markers to propagate...")
    time.sleep(3.0)

    # Check that all brokers completed the snapshot
    print("\n--- Checking snapshot completion ---")

    # All brokers should have no snapshot in progress (they all completed)
    b1_done = broker1._snapshot_in_progress is None
    b2_done = broker2._snapshot_in_progress is None
    b3_done = broker3._snapshot_in_progress is None

    print(f"Broker1 snapshot complete: {b1_done}")
    print(f"Broker2 snapshot complete: {b2_done}")
    print(f"Broker3 snapshot complete: {b3_done}")

    # Clean up
    broker1.stop()
    broker2.stop()
    broker3.stop()

    if b1_done and b2_done and b3_done:
        print("\n" + "=" * 60)
        print("✓ SUCCESS: All brokers completed the snapshot!")
        print("=" * 60)
    else:
        print("\n" + "=" * 60)
        print("✗ FAILURE: Not all brokers completed the snapshot")
        print("=" * 60)


if __name__ == "__main__":
    test_marker_propagation()
