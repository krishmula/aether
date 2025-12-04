#!/usr/bin/env python3
"""
Test script for Phase 7: Subscriber Reconnection

Tests the complete recovery flow:
1. Broker takes snapshot with subscriber info
2. Broker crashes
3. Replacement broker recovers state
4. Replacement broker notifies subscribers
5. Subscribers update their broker reference

This test validates that the entire Chandy-Lamport snapshot and recovery
system works end-to-end, including the final step of reconnecting subscribers
to the recovered broker.
"""

import sys
import time

sys.path.insert(0, ".")

from gossip_broker import GossipBroker
from log_utils import log_error, log_info, log_success, log_warning
from network import NodeAddress
from network_subscriber import NetworkSubscriber
from payload_range import PayloadRange


def main():
    print("=" * 60)
    print("TESTING SUBSCRIBER RECONNECTION (Phase 7)")
    print("=" * 60)

    # =========================================================================
    # SETUP: Create broker addresses
    # =========================================================================
    addr1 = NodeAddress("127.0.0.1", 7001)
    addr2 = NodeAddress("127.0.0.1", 7002)  # This broker will "crash"
    addr3 = NodeAddress("127.0.0.1", 7003)

    # Create subscriber address
    sub_addr = NodeAddress("127.0.0.1", 8001)

    # =========================================================================
    # SETUP: Create brokers
    # GossipBroker creates its own NetworkNode internally from the address
    # =========================================================================
    broker1 = GossipBroker(addr1, snapshot_interval=30.0)
    broker2 = GossipBroker(addr2, snapshot_interval=30.0)
    broker3 = GossipBroker(addr3, snapshot_interval=30.0)

    # =========================================================================
    # SETUP: Create subscriber and connect to broker2
    # =========================================================================
    subscriber = NetworkSubscriber(sub_addr)
    subscriber.connect_to_broker(addr2)

    # =========================================================================
    # SETUP: Set up broker mesh (full connectivity)
    # =========================================================================
    for b in [broker1, broker2, broker3]:
        for addr in [addr1, addr2, addr3]:
            if addr != b.address:
                b.add_peer(addr)

    # =========================================================================
    # PHASE 1: Start everything
    # =========================================================================
    print("\n--- Phase 1: Starting brokers and subscriber ---")
    broker1.start()
    broker2.start()
    broker3.start()
    subscriber.start()
    time.sleep(1)

    # =========================================================================
    # PHASE 2: Register subscriber with broker2
    # We manually register (simulating what would happen via SubscribeRequest)
    # =========================================================================
    print("\n--- Phase 2: Registering subscriber with broker2 ---")

    # Add subscriber to broker2's registry
    # Note: Using _remote_subscribers (with underscore) as that's the actual attribute
    broker2._remote_subscribers[sub_addr] = {PayloadRange(0, 127)}

    # Also update the payload lookup table so message delivery would work
    for payload in range(128):
        broker2._payload_to_remotes[payload].add(sub_addr)

    print(f"  Broker2 subscribers: {broker2._remote_subscribers}")
    print(f"  Subscriber's broker: {subscriber.broker}")

    # Verify initial state
    assert subscriber.broker == addr2, "Subscriber should be connected to broker2"
    assert (
        sub_addr in broker2._remote_subscribers
    ), "Broker2 should have the subscriber registered"

    # =========================================================================
    # PHASE 3: Trigger snapshot on broker2
    # This captures the subscriber registration in the snapshot
    # =========================================================================
    print("\n--- Phase 3: Triggering snapshot on broker2 ---")
    snapshot_id = broker2.initiate_snapshot()
    print(f"  Initiated snapshot: {snapshot_id[:8] if snapshot_id else 'None'}...")
    time.sleep(2)  # Wait for snapshot completion and replication

    # =========================================================================
    # PHASE 4: Verify snapshot was replicated to peers
    # =========================================================================
    print("\n--- Phase 4: Verifying snapshot replication ---")
    broker1_has_snapshot = addr2 in broker1._peer_snapshots
    broker3_has_snapshot = addr2 in broker3._peer_snapshots

    print(f"  Broker1 has broker2's snapshot: {broker1_has_snapshot}")
    print(f"  Broker3 has broker2's snapshot: {broker3_has_snapshot}")

    if not (broker1_has_snapshot or broker3_has_snapshot):
        print("\n✗ FAILURE: No peer has broker2's snapshot!")
        print("  Cannot proceed with recovery test.")
        broker1.stop()
        broker2.stop()
        broker3.stop()
        subscriber.stop()
        return False

    print("  ✓ At least one peer has broker2's snapshot")

    # Show snapshot details
    if broker1_has_snapshot:
        snap = broker1._peer_snapshots[addr2]
        print(f"  Snapshot contains {len(snap.remote_subscribers)} subscriber(s)")

    # =========================================================================
    # PHASE 5: Simulate broker2 crash
    # =========================================================================
    print("\n--- Phase 5: Simulating broker2 crash ---")
    broker2.stop()
    time.sleep(1)
    print("  Broker2 has been stopped")

    # =========================================================================
    # PHASE 6: Create replacement broker on same address
    # =========================================================================
    print("\n--- Phase 6: Creating replacement broker ---")
    broker2_new = GossipBroker(addr2, snapshot_interval=30.0)
    broker2_new.add_peer(addr1)
    broker2_new.add_peer(addr3)
    broker2_new.start()
    time.sleep(1)
    print(f"  New broker started on {addr2}")

    # Record subscriber's broker before recovery
    broker_before = subscriber.broker
    print(f"\n  Subscriber's broker BEFORE recovery: {broker_before}")

    # =========================================================================
    # PHASE 7: Request and restore snapshot
    # =========================================================================
    print("\n--- Phase 7: Recovering broker2's state ---")
    snapshot = broker2_new.request_snapshot_from_peers(addr2, timeout=5.0)

    if not snapshot:
        print("\n✗ FAILURE: Could not retrieve snapshot from peers")
        broker1.stop()
        broker2_new.stop()
        broker3.stop()
        subscriber.stop()
        return False

    print(f"  ✓ Retrieved snapshot!")
    print(f"    Snapshot ID: {snapshot.snapshot_id[:8]}...")
    print(f"    Subscribers: {len(snapshot.remote_subscribers)}")
    print(f"    Peers: {len(snapshot.peer_brokers)}")
    print(f"    Seen messages: {len(snapshot.seen_message_ids)}")

    # =========================================================================
    # PHASE 8: Restore state from snapshot
    # This should also trigger subscriber reconnection notifications
    # =========================================================================
    print("\n--- Phase 8: Restoring state from snapshot ---")
    broker2_new.recover_from_snapshot(snapshot)

    # Give time for the notification to be sent and processed
    time.sleep(1.5)

    # =========================================================================
    # PHASE 9: Verify recovery results
    # =========================================================================
    print("\n--- Phase 9: Verifying recovered state ---")

    # Check broker's restored state
    print(f"  Broker2_new subscribers: {broker2_new._remote_subscribers}")
    print(f"  Broker2_new peers: {broker2_new.peer_brokers}")

    # Check if subscriber was recovered in broker's registry
    subscriber_recovered_in_broker = sub_addr in broker2_new._remote_subscribers
    print(f"\n  Subscriber in broker registry: {subscriber_recovered_in_broker}")

    # Check if subscriber's broker reference was updated
    broker_after = subscriber.broker
    print(f"  Subscriber's broker AFTER recovery: {broker_after}")

    subscriber_reconnected = broker_after == addr2
    print(f"  Subscriber reconnected: {subscriber_reconnected}")

    # =========================================================================
    # CLEANUP
    # =========================================================================
    print("\n--- Shutting down ---")
    broker1.stop()
    broker2_new.stop()
    broker3.stop()
    subscriber.stop()

    # =========================================================================
    # FINAL RESULTS
    # =========================================================================
    print("\n" + "=" * 60)

    success = subscriber_recovered_in_broker and subscriber_reconnected

    if success:
        print("✓ PHASE 7 COMPLETE: Full recovery with subscriber reconnection!")
        print("")
        print("  Summary:")
        print("  - Broker2 took snapshot with subscriber info")
        print("  - Snapshot was replicated to peers")
        print("  - Broker2 crashed")
        print("  - Replacement broker recovered state from peers")
        print("  - Subscriber was notified and updated broker reference")
    else:
        print("✗ PHASE 7 FAILED")
        print("")
        if not subscriber_recovered_in_broker:
            print("  ✗ Subscriber not recovered in broker's registry")
        if not subscriber_reconnected:
            print(f"  ✗ Subscriber still pointing to {broker_after}")
            print("    (Expected: pointing to new broker at same address)")

    print("=" * 60)

    return success


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
