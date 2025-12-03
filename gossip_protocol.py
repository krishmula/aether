from dataclasses import dataclass
from re import M
from typing import Dict, Set

from message import Message
from network import NodeAddress
from payload_range import PayloadRange


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


@dataclass
class SubscribeRequest:
    """
    Sent by subscribers to brokers to subscribe to a PayloadRange.
    """

    subscriber: NodeAddress
    payload_range: PayloadRange


@dataclass
class SubscribeAck:
    """
    Acknowledgement from broker to subscriber for subscription request.
    """

    payload_range: PayloadRange
    success: bool


@dataclass
class UnsubscribeRequest:
    """
    Sent by subscribers to brokers to unsubscribe from a PayloadRange.
    """

    subscriber: NodeAddress
    payload_range: PayloadRange


@dataclass
class UnsubscribeAck:
    """
    Acknowledgement from broker to subscriber for unsubscription request.
    """

    payload_range: PayloadRange
    success: bool


@dataclass
class PayloadMessageDelivery:
    """
    Message sent from broker to subscriber containing the payload data.
    """

    msg: Message


@dataclass
class BrokerSnapshot:
    """
    Captured state of a GossipBroker at a point in time.
    Used for recovery after broker failure via Chandy-Lamport snapshots.
    """

    snapshot_id: str
    broker_address: NodeAddress
    peer_brokers: Set[NodeAddress]
    remote_subscribers: Dict[NodeAddress, Set[PayloadRange]]
    seen_message_ids: Set[str]
    timestamp: float


@dataclass
class SnapshotMarker:
    """
     Chandy-Lamport marker message for distributed snapshot coordination.
    When a broker receives this marker on a channel, it knows:
    1. The sender has recorded its local state for this snapshot
    2. Any future messages on this channel were sent after the snapshot
    The marker propagates through the entire broker network, triggering
    each broker to record its state and forward the marker to its peers.
    """

    snapshot_id: str
    initiator_address: NodeAddress
    timestamp: float
