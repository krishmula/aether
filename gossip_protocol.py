from dataclasses import dataclass
from typing import Set
from message import Message
from network import NodeAddress

@dataclass
class GossipMessage:
    """
    wrapper around message.py Message for gossip.
    """
    msg: Message
    msg_id: str
    ttl: int
    source: NodeAddress

@dataclass
class Heartbeat:
    """
    Brokers send this to their peers every few seconds to indicate liveness.
    """
    sender: NodeAddress
    sequence: int

@dataclass
class MembershipUpdate:
    """
    When a broker joins, it receives this with the addresses of it's peer brokers.
    """
    brokers: Set[NodeAddress]
