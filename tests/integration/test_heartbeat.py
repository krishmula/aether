"""
Test script to verify heartbeat-based failure detection.
"""
import time
from pubsub.network.node import NodeAddress
from pubsub.gossip.broker import GossipBroker


def test_failure_detection():
    print("=" * 60)
    print("HEARTBEAT FAILURE DETECTION TEST")
    print("=" * 60)

    # Create 3 brokers
    addr1 = NodeAddress("localhost", 6001)
    addr2 = NodeAddress("localhost", 6002)
    addr3 = NodeAddress("localhost", 6003)

    broker1 = GossipBroker(addr1)
    broker2 = GossipBroker(addr2)
    broker3 = GossipBroker(addr3)

    # Manually add peers (normally bootstrap server does this)
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

    print("\n[Phase 1] All brokers started. Waiting for heartbeats to exchange...")
    time.sleep(12)  # Let heartbeats flow (at least 2 rounds)

    print(f"\nBroker1 (port 6001) peers: {broker1.peer_brokers}")
    print(f"Broker2 (port 6002) peers: {broker2.peer_brokers}")
    print(f"Broker3 (port 6003) peers: {broker3.peer_brokers}")

    print(f"\nBroker1 last_seen: {broker1.last_seen}")
    print(f"Broker2 last_seen: {broker2.last_seen}")

    # Verify all brokers see each other
    assert addr2 in broker1.peer_brokers, "Broker1 should see Broker2"
    assert addr3 in broker1.peer_brokers, "Broker1 should see Broker3"
    print("\n✓ All brokers are connected and exchanging heartbeats")

    # Kill broker3
    print("\n" + "=" * 60)
    print("[Phase 2] Stopping Broker3 (simulating crash)")
    print("=" * 60)
    broker3.stop()

    # Wait for timeout (15s) + check interval (5s) + buffer
    print("\nWaiting for failure detection...")
    print("  - Timeout threshold: 15 seconds")
    print("  - Check interval: 5 seconds")
    print("  - Expected detection time: ~20 seconds")
    
    for i in range(22, 0, -1):
        print(f"\r  Waiting... {i}s remaining", end="", flush=True)
        time.sleep(1)
    print()

    print(f"\nAfter failure detection:")
    print(f"Broker1 (port 6001) peers: {broker1.peer_brokers}")
    print(f"Broker2 (port 6002) peers: {broker2.peer_brokers}")

    # Verify broker3 was removed
    broker3_removed_from_1 = addr3 not in broker1.peer_brokers
    broker3_removed_from_2 = addr3 not in broker2.peer_brokers

    print("\n" + "=" * 60)
    print("RESULTS")
    print("=" * 60)
    
    if broker3_removed_from_1:
        print("✓ Broker1 detected Broker3 failure and removed it")
    else:
        print("✗ Broker1 did NOT remove Broker3")

    if broker3_removed_from_2:
        print("✓ Broker2 detected Broker3 failure and removed it")
    else:
        print("✗ Broker2 did NOT remove Broker3")

    # Check that broker1 and broker2 still see each other
    if addr2 in broker1.peer_brokers and addr1 in broker2.peer_brokers:
        print("✓ Broker1 and Broker2 still connected to each other")
    else:
        print("✗ Broker1 and Broker2 lost connection (unexpected)")

    # Cleanup
    broker1.stop()
    broker2.stop()

    if broker3_removed_from_1 and broker3_removed_from_2:
        print("\n" + "=" * 60)
        print("SUCCESS: Heartbeat failure detection is working!")
        print("=" * 60)
    else:
        print("\n" + "=" * 60)
        print("FAILURE: Heartbeat failure detection has issues")
        print("=" * 60)


if __name__ == "__main__":
    test_failure_detection()
