# test_tcp_basic.py
import time

from pubsub.network.node import NetworkNode, NodeAddress
from pubsub.utils.log import log_error, log_header, log_info, log_success

log_header("BASIC TCP CONNECTIVITY TEST")

# Test 1: Can we create a node and have it listen?
log_info("Test 1", "Creating node A...")
node_a = NetworkNode(NodeAddress("localhost", 8000))
time.sleep(1.0)  # Give it time to start listening
log_success("Test 1", "Node A created and listening")

# Test 2: Can another node connect to it?
log_info("Test 2", "Creating node B...")
node_b = NetworkNode(NodeAddress("localhost", 8001))
time.sleep(1.0)
log_success("Test 2", "Node B created and listening")

# Test 3: Can node B send to node A?
log_info("Test 3", "Node B sending to Node A...")
try:
    node_b.send("Test message", NodeAddress("localhost", 8000))
    time.sleep(1.0)
    msg, sender = node_a.receive(timeout=2.0)
    if msg:
        log_success("Test 3", f"Node A received: '{msg}' from {sender}")
    else:
        log_error("Test 3", "Node A did not receive message")
except Exception as e:
    log_error("Test 3", f"Failed: {e}")

# Test 4: Can node A send back to node B on the same connection?
log_info("Test 4", "Node A sending back to Node B...")
try:
    node_a.send("Response message", NodeAddress("localhost", 8001))
    time.sleep(1.0)
    msg, sender = node_b.receive(timeout=2.0)
    if msg:
        log_success("Test 4", f"Node B received: '{msg}' from {sender}")
    else:
        log_error("Test 4", "Node B did not receive message")
except Exception as e:
    log_error("Test 4", f"Failed: {e}")

# Cleanup
node_a.close()
node_b.close()

log_success("Complete", "Basic TCP test finished")
