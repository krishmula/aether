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
