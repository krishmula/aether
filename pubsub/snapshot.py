from dataclasses import dataclass
from typing import Dict, Optional, Set

from pubsub.network.node import NodeAddress
from pubsub.core.payload_range import PayloadRange


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


@dataclass
class SnapshotReplica:
    """
    A replica of a broker's snapshot state, sent to a backup broker for fault tolerance.
    """

    snapshot: BrokerSnapshot


@dataclass
class SnapshotRequest:
    """
    Request from a recovering broker to retrieve a failed broker's snapshot.

    Sent to surviving peers who may have stored a replica of the
    failed broker's snapshot. The recipient looks up the requested
    broker_address in its stored replicas and responds accordingly.
    """

    broker_address: NodeAddress


@dataclass
class SnapshotResponse:
    """
    Response to a SnapshotRequest, containing the requested snapshot if available.

    If the responding broker has a stored replica for the requested broker,
    the snapshot field contains it. Otherwise, snapshot is None, indicating
    the requester should try another peer.
    """

    broker_address: NodeAddress
    snapshot: Optional[BrokerSnapshot]


@dataclass
class BrokerRecoveryNotification:
    """
    Notification from a recovered broker to its subscribers.

    After a broker fails and a replacement loads the failed broker's
    snapshot, it sends this notification to all subscribers found in
    the snapshot. Subscribers update their broker reference to maintain
    connectivity.

    This implements the "passive subscriber" recovery model where the
    broker initiates reconnection rather than subscribers detecting
    failure and reconnecting themselves.
    """

    old_broker: NodeAddress
    new_broker: NodeAddress
