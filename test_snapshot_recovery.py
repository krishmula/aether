#!/usr/bin/env python3
"""
Test script for Phase 6: Snapshot Recovery Protocol

Tests that a replacement broker can retrieve a dead broker's snapshot
from surviving peers and restore its state.
"""

import sys
import time

sys.path.insert(0, ".")

from gossip_broker import GossipBroker
from network import NodeAddress
from payload_range import PayloadRange

print("=" * 60)
print("TESTING SNAPSHOT RECOVERY (Phase 6)")
print("=" * 60)

# Create three brokers
addr1 = NodeAddress("127.0.0.1", 7001)
addr2 = NodeAddress("127.0.0.1", 7002)
addr3 = NodeAddress("127.0.0.1", 7003)

# Pass NodeAddress objects directly to GossipBroker (it creates its own NetworkNode)
broker1 = GossipBroker(addr1, snapshot_interval=10.0)
broker2 = GossipBroker(addr2, snapshot_interval=10.0)
broker3 = GossipBroker(addr3, snapshot_interval=10.0)

# Set up full mesh
for b in [broker1, broker2, broker3]:
    for addr in [addr1, addr2, addr3]:
        if addr != b.address:
            b.add_peer(addr)

# Add a fake subscriber to broker2 (this is what we want to recover)
fake_sub = NodeAddress("127.0.0.1", 9999)
broker2._remote_subscribers[fake_sub] = {PayloadRange(0, 127)}

# Add some seen messages to broker2
broker2.seen_messages = {"msg1", "msg2", "msg3"}

print("\n--- Starting brokers ---")
broker1.start()
broker2.start()
broker3.start()
time.sleep(1)

print("\n--- Manually triggering snapshot on broker2 ---")
# We manually trigger so we don't have to wait for the timer
broker2.initiate_snapshot()
time.sleep(2)  # Wait for snapshot + replication

print("\n--- Checking who has broker2's snapshot ---")
print(f"Broker1 has snapshots from: {list(broker1._peer_snapshots.keys())}")
print(f"Broker3 has snapshots from: {list(broker3._peer_snapshots.keys())}")

has_broker2_snapshot = (
    addr2 in broker1._peer_snapshots or addr2 in broker3._peer_snapshots
)
if has_broker2_snapshot:
    print("✓ At least one peer has broker2's snapshot")
else:
    print("✗ No peer has broker2's snapshot!")

print("\n--- Simulating broker2 crash ---")
broker2.stop()
time.sleep(1)

print("\n--- Creating replacement broker on same address ---")
# Pass NodeAddress directly to GossipBroker
broker2_new = GossipBroker(addr2, snapshot_interval=10.0)

# The replacement needs to know about peers to ask them
broker2_new.add_peer(addr1)
broker2_new.add_peer(addr3)

broker2_new.start()
time.sleep(1)

print("\n--- Requesting snapshot from peers ---")
recovered_snapshot = broker2_new.request_snapshot_from_peers(addr2, timeout=5.0)

if recovered_snapshot:
    print(f"\n✓ Successfully retrieved snapshot!")
    print(f"  Snapshot ID: {recovered_snapshot.snapshot_id[:8]}...")
    print(f"  Subscribers: {len(recovered_snapshot.remote_subscribers)}")
    print(f"  Peers: {len(recovered_snapshot.peer_brokers)}")
    print(f"  Seen messages: {len(recovered_snapshot.seen_message_ids)}")

    print("\n--- Restoring state from snapshot ---")
    broker2_new.recover_from_snapshot(recovered_snapshot)

    print("\n--- Verifying recovered state ---")
    print(f"  Subscribers: {broker2_new._remote_subscribers}")
    print(f"  Peers: {broker2_new.peer_brokers}")
    print(f"  Seen messages: {broker2_new.seen_messages}")

    # Verify the fake subscriber was recovered
    if fake_sub in broker2_new._remote_subscribers:
        print("\n✓ SUCCESS: Subscriber was recovered!")
        success = True
    else:
        print("\n✗ FAILURE: Subscriber was NOT recovered")
        success = False
else:
    print("\n✗ FAILURE: Could not retrieve snapshot")
    success = False

print("\n--- Shutting down ---")
broker1.stop()
broker2_new.stop()
broker3.stop()

print("\n" + "=" * 60)
if success:
    print("✓ PHASE 6 COMPLETE: Recovery protocol working!")
else:
    print("✗ PHASE 6 FAILED: See errors above")
print("=" * 60)
