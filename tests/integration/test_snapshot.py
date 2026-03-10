from pubsub.core.payload_range import PayloadRange
from pubsub.core.uint8 import UInt8
from pubsub.gossip.broker import GossipBroker
from pubsub.network.node import NodeAddress


def test_local_snapshot():
    print("Testing local snapshot capture...")

    # Create a broker
    broker_addr = NodeAddress("localhost", 9000)
    broker = GossipBroker(broker_addr, fanout=2, ttl=5)

    # Simulate some state (normally these would come from real subscribers)
    # We'll directly manipulate the internal state for testing
    fake_sub1 = NodeAddress("localhost", 10001)
    fake_sub2 = NodeAddress("localhost", 10002)

    broker._remote_subscribers[fake_sub1] = {PayloadRange(UInt8(0), UInt8(50))}
    broker._remote_subscribers[fake_sub2] = {
        PayloadRange(UInt8(100), UInt8(150)),
        PayloadRange(UInt8(200), UInt8(255)),
    }

    # Rebuild _payload_to_remotes to match (normally register does this)
    for i in range(51):
        broker._payload_to_remotes[i].add(fake_sub1)
    for i in range(100, 151):
        broker._payload_to_remotes[i].add(fake_sub2)
    for i in range(200, 256):
        broker._payload_to_remotes[i].add(fake_sub2)

    # Add some fake peers
    peer1 = NodeAddress("localhost", 8001)
    peer2 = NodeAddress("localhost", 8002)
    broker.peer_brokers.add(peer1)
    broker.peer_brokers.add(peer2)

    # Add some seen messages
    broker.seen_messages.add("msg-001")
    broker.seen_messages.add("msg-002")
    broker.seen_messages.add("msg-003")

    # Take snapshot
    snapshot = broker.take_snapshot()

    # Verify snapshot contents
    print(f"Snapshot ID: {snapshot.snapshot_id}")
    print(f"Broker address: {snapshot.broker_address}")
    print(f"Timestamp: {snapshot.timestamp}")
    print(f"Peers: {snapshot.peer_brokers}")
    print(f"Subscribers: {snapshot.remote_subscribers}")
    print(f"Seen messages: {snapshot.seen_message_ids}")

    # Verify it's a copy, not a reference
    broker.peer_brokers.add(NodeAddress("localhost", 8003))
    assert NodeAddress("localhost", 8003) not in snapshot.peer_brokers, (
        "Snapshot should be independent of live state!"
    )

    print("\n✓ Snapshot capture test passed!")

    # Clean up
    broker.network.close()


if __name__ == "__main__":
    test_local_snapshot()
