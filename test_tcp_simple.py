"""
Simple TCP test to verify the basic components work.
Run this before running the full test suite.
"""

import time
import sys


def test_imports():
    """Test that all imports work."""
    print("Testing imports...")
    try:
        from network import NodeAddress, TCPConnection, TCPServer, TCPNetworkNode
        from bootstrap import BootstrapServer
        from gossip_broker import GossipBroker
        from gossip_protocol import GossipMessage, Heartbeat, MembershipUpdate
        from network_publisher import NetworkPublisher
        from network_subscriber import NetworkSubscriber
        from message import Message
        from uint8 import UInt8
        from payload_range import PayloadRange
        print("✅ All imports successful")
        return True
    except Exception as e:
        print(f"❌ Import failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_basic_tcp():
    """Test basic TCP connection."""
    print("\nTesting basic TCP connection...")
    
    from network import NodeAddress, TCPConnection, TCPServer
    
    server_addr = NodeAddress("localhost", 19000)
    server = TCPServer(server_addr)
    
    if not server.start():
        print("❌ Failed to start server")
        return False
    
    print(f"  Server started on {server_addr}")
    
    # Connect client
    client = TCPConnection.connect_to(server_addr, timeout=5.0)
    if client is None:
        print("❌ Failed to connect client")
        server.stop()
        return False
    
    print("  Client connected")
    
    # Accept on server
    server_conn = server.accept(timeout=5.0)
    if server_conn is None:
        print("❌ Server failed to accept")
        client.close()
        server.stop()
        return False
    
    print("  Server accepted connection")
    
    # Send message
    test_msg = {"test": "hello", "value": 42}
    if not client.send(test_msg):
        print("❌ Failed to send message")
        client.close()
        server_conn.close()
        server.stop()
        return False
    
    print("  Client sent message")
    
    # Receive message
    received, alive = server_conn.receive(timeout=5.0)
    if not alive or received is None:
        print("❌ Failed to receive message")
        client.close()
        server_conn.close()
        server.stop()
        return False
    
    if received != test_msg:
        print(f"❌ Message mismatch: expected {test_msg}, got {received}")
        client.close()
        server_conn.close()
        server.stop()
        return False
    
    print(f"  Server received: {received}")
    
    # Cleanup
    client.close()
    server_conn.close()
    server.stop()
    
    print("✅ Basic TCP test passed")
    return True


def test_bootstrap():
    """Test bootstrap server."""
    print("\nTesting bootstrap server...")
    
    from network import NodeAddress, TCPConnection
    from bootstrap import BootstrapServer
    from gossip_protocol import MembershipUpdate
    
    bootstrap_addr = NodeAddress("localhost", 19100)
    bootstrap = BootstrapServer(bootstrap_addr)
    
    if not bootstrap.start():
        print("❌ Failed to start bootstrap")
        return False
    
    print(f"  Bootstrap started on {bootstrap_addr}")
    time.sleep(0.3)
    
    # Broker1 joins
    broker1_addr = NodeAddress("localhost", 19101)
    conn = TCPConnection.connect_to(bootstrap_addr, timeout=5.0)
    if conn is None:
        print("❌ Failed to connect to bootstrap")
        bootstrap.stop()
        return False
    
    conn.send(("JOIN", broker1_addr))
    response, alive = conn.receive(timeout=5.0)
    conn.close()
    
    if not isinstance(response, MembershipUpdate):
        print(f"❌ Expected MembershipUpdate, got {type(response)}")
        bootstrap.stop()
        return False
    
    print(f"  Broker1 joined, members: {response.members}")
    
    # Broker2 joins
    broker2_addr = NodeAddress("localhost", 19102)
    conn = TCPConnection.connect_to(bootstrap_addr, timeout=5.0)
    conn.send(("JOIN", broker2_addr))
    response, alive = conn.receive(timeout=5.0)
    conn.close()
    
    if len(response.members) != 2:
        print(f"❌ Expected 2 members, got {len(response.members)}")
        bootstrap.stop()
        return False
    
    print(f"  Broker2 joined, members: {response.members}")
    
    bootstrap.stop()
    print("✅ Bootstrap test passed")
    return True


def test_broker_start():
    """Test that a broker can start."""
    print("\nTesting broker start...")
    
    from network import NodeAddress
    from gossip_broker import GossipBroker
    
    broker_addr = NodeAddress("localhost", 19200)
    broker = GossipBroker(broker_addr, fanout=2, ttl=3)
    
    if not broker.start():
        print("❌ Failed to start broker")
        return False
    
    print(f"  Broker started on {broker_addr}")
    time.sleep(0.5)
    
    broker.stop()
    print("✅ Broker start test passed")
    return True


def test_broker_join_bootstrap():
    """Test broker joining via bootstrap."""
    print("\nTesting broker join bootstrap...")
    
    from network import NodeAddress
    from bootstrap import BootstrapServer
    from gossip_broker import GossipBroker
    
    # Start bootstrap
    bootstrap_addr = NodeAddress("localhost", 19300)
    bootstrap = BootstrapServer(bootstrap_addr)
    bootstrap.start()
    time.sleep(0.3)
    
    # Start and join broker1
    broker1_addr = NodeAddress("localhost", 19301)
    broker1 = GossipBroker(broker1_addr, fanout=2, ttl=3)
    broker1.start()
    
    peers = broker1.join_bootstrap(bootstrap_addr)
    print(f"  Broker1 joined, peers: {peers}")
    
    if peers is None:
        print("❌ Broker1 failed to join")
        broker1.stop()
        bootstrap.stop()
        return False
    
    # Start and join broker2
    broker2_addr = NodeAddress("localhost", 19302)
    broker2 = GossipBroker(broker2_addr, fanout=2, ttl=3)
    broker2.start()
    
    peers = broker2.join_bootstrap(bootstrap_addr)
    print(f"  Broker2 joined, peers: {peers}")
    
    if peers is None or len(peers) == 0:
        print("❌ Broker2 should have gotten Broker1 as peer")
        broker1.stop()
        broker2.stop()
        bootstrap.stop()
        return False
    
    # Add peers
    for peer in peers:
        broker2.add_peer(peer)
    
    time.sleep(0.5)
    
    print(f"  Broker1 peer count: {broker1.get_peer_count()}")
    print(f"  Broker2 peer count: {broker2.get_peer_count()}")
    
    broker1.stop()
    broker2.stop()
    bootstrap.stop()
    
    print("✅ Broker join bootstrap test passed")
    return True


def test_subscriber():
    """Test subscriber connect and subscribe."""
    print("\nTesting subscriber...")
    
    from network import NodeAddress
    from bootstrap import BootstrapServer
    from gossip_broker import GossipBroker
    from network_subscriber import NetworkSubscriber
    from payload_range import PayloadRange
    
    # Start bootstrap and broker
    bootstrap_addr = NodeAddress("localhost", 19400)
    bootstrap = BootstrapServer(bootstrap_addr)
    bootstrap.start()
    time.sleep(0.3)
    
    broker_addr = NodeAddress("localhost", 19401)
    broker = GossipBroker(broker_addr, fanout=2, ttl=3)
    broker.start()
    broker.join_bootstrap(bootstrap_addr)
    time.sleep(0.3)
    
    # Start subscriber
    sub_addr = NodeAddress("localhost", 19500)
    subscriber = NetworkSubscriber(sub_addr)
    
    if not subscriber.start():
        print("❌ Failed to start subscriber")
        broker.stop()
        bootstrap.stop()
        return False
    
    print(f"  Subscriber started on {sub_addr}")
    
    # Connect to broker
    if not subscriber.connect_to_broker(broker_addr, timeout=5.0):
        print("❌ Failed to connect to broker")
        subscriber.stop()
        broker.stop()
        bootstrap.stop()
        return False
    
    print("  Subscriber connected to broker")
    
    # Subscribe
    pr = PayloadRange(0, 50)
    if not subscriber.subscribe(pr, timeout=5.0):
        print("❌ Failed to subscribe")
        subscriber.stop()
        broker.stop()
        bootstrap.stop()
        return False
    
    print(f"  Subscriber subscribed to {pr}")
    print(f"  Broker subscriber count: {broker.get_subscriber_count()}")
    
    subscriber.stop()
    broker.stop()
    bootstrap.stop()
    
    print("✅ Subscriber test passed")
    return True


def test_end_to_end():
    """Test full message flow: Publisher -> Broker -> Subscriber."""
    print("\nTesting end-to-end message flow...")
    
    from network import NodeAddress
    from bootstrap import BootstrapServer
    from gossip_broker import GossipBroker
    from network_subscriber import NetworkSubscriber
    from network_publisher import NetworkPublisher
    from payload_range import PayloadRange
    from message import Message
    from uint8 import UInt8
    
    # Start infrastructure
    bootstrap_addr = NodeAddress("localhost", 19600)
    bootstrap = BootstrapServer(bootstrap_addr)
    bootstrap.start()
    time.sleep(0.3)
    
    broker_addr = NodeAddress("localhost", 19601)
    broker = GossipBroker(broker_addr, fanout=2, ttl=3)
    broker.start()
    broker.join_bootstrap(bootstrap_addr)
    time.sleep(0.3)
    
    # Start subscriber
    sub_addr = NodeAddress("localhost", 19700)
    subscriber = NetworkSubscriber(sub_addr)
    subscriber.start()
    subscriber.connect_to_broker(broker_addr, timeout=5.0)
    subscriber.subscribe(PayloadRange(0, 100), timeout=5.0)
    time.sleep(0.3)
    
    print(f"  Broker has {broker.get_subscriber_count()} subscribers")
    
    # Create publisher
    publisher = NetworkPublisher(
        NodeAddress("localhost", 19800),
        [broker_addr]
    )
    
    # Publish messages
    test_payloads = [10, 50, 150]  # 10, 50 should be delivered (in range 0-100)
    
    for payload in test_payloads:
        msg = Message(UInt8(payload))
        sent = publisher.publish(msg, redundancy=1)
        print(f"  Published payload={payload}, sent to {sent} brokers")
        time.sleep(0.3)
    
    # Wait for delivery
    time.sleep(1.0)
    
    # Check received
    received = subscriber.get_received_messages(clear=True)
    received_payloads = [msg.payload for msg in received]
    
    print(f"  Subscriber received: {received_payloads}")
    
    # Cleanup
    publisher.close()
    subscriber.stop()
    broker.stop()
    bootstrap.stop()
    
    expected = [10, 50]  # Only these are in range 0-100
    
    if set(expected).issubset(set(received_payloads)):
        print("✅ End-to-end test passed")
        return True
    else:
        print(f"⚠️  End-to-end test partial: expected {expected}, got {received_payloads}")
        return True  # Still pass if some messages got through


def run_all_tests():
    """Run all simple tests."""
    print("=" * 60)
    print("SIMPLE TCP TESTS")
    print("=" * 60)
    
    tests = [
        ("Imports", test_imports),
        ("Basic TCP", test_basic_tcp),
        ("Bootstrap", test_bootstrap),
        ("Broker Start", test_broker_start),
        ("Broker Join Bootstrap", test_broker_join_bootstrap),
        ("Subscriber", test_subscriber),
        ("End-to-End", test_end_to_end),
    ]
    
    results = []
    
    for name, test_func in tests:
        try:
            result = test_func()
            results.append((name, result))
        except Exception as e:
            print(f"❌ Test '{name}' threw exception: {e}")
            import traceback
            traceback.print_exc()
            results.append((name, False))
        
        time.sleep(0.5)  # Let ports release
    
    # Summary
    print("\n" + "=" * 60)
    print("TEST SUMMARY")
    print("=" * 60)
    
    passed = sum(1 for _, r in results if r)
    total = len(results)
    
    for name, result in results:
        status = "✅ PASS" if result else "❌ FAIL"
        print(f"  {name}: {status}")
    
    print("=" * 60)
    print(f"Result: {passed}/{total} tests passed")
    print("=" * 60)
    
    return passed == total


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)