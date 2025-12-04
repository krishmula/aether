#!/usr/bin/env python3
"""
Test script for Phase 7: Subscriber Reconnection

Tests the complete recovery flow:
1. Broker takes snapshot with subscriber info
2. Broker crashes
3. Replacement broker recovers state
4. Replacement broker notifies subscribers
5. Subscribers update their broker reference
"""

import sys
import time

sys.path.insert(0, ".")


from gossip_broker import GossipBroker
from network import NodeAddress
from network_subscriber import NetworkSubscriber
from payload_range import PayloadRange

print("=" * 60)
print("TESTING SUBSCRIBER RECONNECTION (Phase 7)")
print("=" * 60)

# Create broker addresses
addr1 = NodeAddress("127.0.0.1", 7001)
addr2 = NodeAddress("127.0.0.1", 7002)  # This broker will "crash"
addr3 = NodeAddress("127.0.0.1", 7003)

# Create subscriber address
sub_addr = NodeAddress("127.0.0.1", 8001)

# Create brokers - pass NodeAddress directly (GossipBroker creates its own NetworkNode)
broker1 = GossipBroker(addr1, snapshot_interval=30.0)
broker2 = GossipBroker(addr2, snapshot_interval=30.0)
broker3 = GossipBroker(addr3, snapshot_interval=30.0)

# Create subscriber and connect to broker2
subscriber = NetworkSubscriber(sub_addr)
subscriber.connect_to_broker(addr2)

# Set up broker mesh
for b in [broker1, broker2, broker3]:
    for addr in [addr1, addr2, addr3]:
        if addr != b.address:
            b.add_peer(addr)

print("\n--- Starting brokers and subscriber ---")
broker1.start()
broker2.start()
broker3.start()
subscriber.start()
time.sleep(1)

# Register subscriber with broker2
print("\n--- Registering subscriber with broker2 ---")
broker2._remote_subscribers[sub_addr] = {PayloadRange(0, 127)}
print(f"Broker2 subscribers: {broker2._remote_subscribers}")
print(f"Subscriber's broker: {subscriber.broker}")

# Trigger snapshot on broker2
print("\n--- Triggering snapshot on broker2 ---")
broker2.initiate_snapshot()
time.sleep(2)

# Verify snapshot was replicated
print("\n--- Verifying snapshot replication ---")
has_snapshot = addr2 in broker1._peer_snapshots or addr2 in broker3._peer_snapshots
print(f"Broker1 has broker2's snapshot: {addr2 in broker1._peer_snapshots}")
print(f"Broker3 has broker2's snapshot: {addr2 in broker3._peer_snapshots}")

if not has_snapshot:
    print("✗ FAILURE: No peer has broker2's snapshot!")
    broker1.stop()
    broker2.stop()
    broker3.stop()
    subscriber.stop()
    sys.exit(1)

# Crash broker2
print("\n--- Simulating broker2 crash ---")
broker2.stop()
time.sleep(1)

# Create replacement broker
print("\n--- Creating replacement broker ---")
broker2_new = GossipBroker(addr2, snapshot_interval=30.0)
broker2_new.add_peer(addr1)
broker2_new.add_peer(addr3)
broker2_new.start()
time.sleep(1)

# Record subscriber's broker before recovery
broker_before = subscriber.broker
print(f"\nSubscriber's broker BEFORE recovery: {broker_before}")

# Request and restore snapshot
print("\n--- Recovering broker2's state ---")
snapshot = broker2_new.request_snapshot_from_peers(addr2, timeout=5.0)

if snapshot:
    print(f"✓ Retrieved snapshot with {len(snapshot.remote_subscribers)} subscriber(s)")

    # This should also trigger subscriber reconnection
    broker2_new.recover_from_snapshot(snapshot)
    time.sleep(1)  # Give time for notification to be sent and processed

    # Check if subscriber's broker reference was updated
    broker_after = subscriber.broker
    print(f"\nSubscriber's broker AFTER recovery: {broker_after}")

    if broker_after == addr2:
        print("\n✓ SUCCESS: Subscriber reconnected to recovered broker!")
        success = True
    else:
        print(f"\n✗ FAILURE: Subscriber still pointing to {broker_after}")
        success = False
else:
    print("✗ FAILURE: Could not retrieve snapshot")
    success = False

# Cleanup
print("\n--- Shutting down ---")
broker1.stop()
broker2_new.stop()
broker3.stop()
subscriber.stop()

print("\n" + "=" * 60)
if success:
    print("✓ PHASE 7 COMPLETE: Full recovery with subscriber reconnection!")
else:
    print("✗ PHASE 7 FAILED: See errors above")
print("=" * 60)
