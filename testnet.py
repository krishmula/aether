from network import NetworkNode, NodeAddress
from log_utils import log_success, log_header

log_header("NETWORK TEST")

node_a = NetworkNode(NodeAddress("localhost", 8000))

node_b = NetworkNode(NodeAddress("localhost", 8001))

node_a.send("Hello from A to B", NodeAddress("localhost", 8001))

msg, sender = node_b.receive()
log_success("Test", f"Node B received: '{msg}' from {sender}")