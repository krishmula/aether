import random
import threading
import time
import uuid
from typing import Dict, List, Optional, Set

from bootstrap import BootstrapServer
from broker import Broker
from gossip_protocol import (
    GossipMessage,
    Heartbeat,
    MembershipUpdate,
    PayloadMessageDelivery,
    SubscribeAck,
    SubscribeRequest,
    UnsubscribeAck,
    UnsubscribeRequest,
)
from log_utils import (
    log_debug,
    log_error,
    log_info,
    log_network,
    log_success,
    log_warning,
)
from message import Message
from network import NetworkNode, NodeAddress
from payload_range import PayloadRange
from snapshot import BrokerSnapshot, SnapshotMarker
from subscriber import Subscriber


class GossipBroker:
    def __init__(self, address: NodeAddress, fanout: int = 3, ttl: int = 5) -> None:
        self.address = address
        self.network = NetworkNode(address)

        self._local_broker = Broker()

        self._remote_subscribers: Dict[NodeAddress, Set[PayloadRange]] = {}
        self._payload_to_remotes: List[Set[NodeAddress]] = [set() for _ in range(256)]

        self.fanout = fanout
        self.ttl = ttl

        self.peer_brokers: Set[NodeAddress] = set()
        self.last_seen: Dict[NodeAddress, float] = {}

        self.seen_messages: Set[str] = set()

        self._lock = threading.Lock()

        self.running = False
        self.recv_thread: threading.Thread = None
        self.heartbeat_thread: threading.Thread = None
        self.check_heartbeat_thread: threading.Thread = None

        self._snapshot_in_progress: Optional[str] = None
        self._snapshot_recorded_state: Optional[BrokerSnapshot] = None
        self._channels_recording: Dict[NodeAddress, List[GossipMessage]] = {}
        self._channels_closed: Set[NodeAddress] = set()
        self._peer_snapshots: Dict[NodeAddress, BrokerSnapshot] = {}

    def register(self, subscriber: Subscriber, payload_range: PayloadRange) -> None:
        self._local_broker.register(subscriber, payload_range)

    def unregister(self, subscriber: Subscriber) -> None:
        self._local_broker.unregister(subscriber)

    def add_peer(self, peer: NodeAddress) -> None:
        if peer != self.address:
            with self._lock:
                self.peer_brokers.add(peer)
                if peer not in self.last_seen:
                    self.last_seen[peer] = time.time()
            log_network(f"Broker:{self.address.port}", "PEER ADDED", f"{peer}")

    def start(self) -> None:
        self.running = True
        self.recv_thread = threading.Thread(
            target=self._receive_loop,
            name=f"broker-{self.address.port}-recv",
            daemon=True,
        )
        self.recv_thread.start()

        self.heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop,
            name=f"broker-{self.address.port}-hb",
            daemon=True,
        )
        self.heartbeat_thread.start()

        self.check_heartbeat_thread = threading.Thread(
            target=self._check_heartbeat_loop,
            name=f"broker-{self.address.port}-check-hb",
            daemon=True,
        )
        self.check_heartbeat_thread.start()

        log_success(
            f"Broker:{self.address.port}",
            f"Started with {len(self.peer_brokers)} peer(s)",
        )

    def stop(self) -> None:
        """
        stop all background threads and close network connections.
        """
        self.running = False
        if self.recv_thread:
            self.recv_thread.join(timeout=2.0)
        if self.heartbeat_thread:
            self.heartbeat_thread.join(timeout=2.0)
        if self.check_heartbeat_thread:
            self.check_heartbeat_thread.join(timeout=2.0)
        self.network.close()

    def _handle_gossip_message(self, gossip_msg: GossipMessage) -> None:
        with self._lock:
            if gossip_msg.msg_id in self.seen_messages:
                log_info(
                    "GossipMessage",
                    f"Already seen this message, it's a duplicate. FROM BROKER {self.address}",
                )
                return

            self.seen_messages.add(gossip_msg.msg_id)

        self._local_broker.publish(gossip_msg.msg)

        self._deliver_to_remote_subscribers(gossip_msg.msg)

        if gossip_msg.ttl > 0:
            self._gossip_to_peers(gossip_msg)

    def _gossip_to_peers(self, gossip_msg: GossipMessage) -> None:
        """
        Gossip the message payload to it's peer brokers.
        """
        forwarded_msg = GossipMessage(
            msg=gossip_msg.msg,
            msg_id=gossip_msg.msg_id,
            ttl=gossip_msg.ttl - 1,
            source=gossip_msg.source,
        )

        if len(self.peer_brokers) == 0:
            return

        num_targets = min(self.fanout, len(self.peer_brokers))
        targets = random.sample(list(self.peer_brokers), num_targets)

        for peer in targets:
            try:
                self.network.send(forwarded_msg, peer)
            except Exception as e:
                log_error(
                    f"Broker:{self.address.port}", f"Failed to gossip to {peer}: {e}"
                )

    def _record_local_state(self, snapshot_id: str) -> BrokerSnapshot:
        """
        Capture current broker state for a Chandy-Lamport snapshot.
        This creates an independent copy of all relevant state at this moment.
        Must be called under self._lock to ensure consistency.
        """
        max_seen_messages = 10000
        seen_subset = set(list(self.seen_messages)[:max_seen_messages])

        snapshot = BrokerSnapshot(
            snapshot_id=snapshot_id,
            broker_address=self.address,
            peer_brokers=set(self.peer_brokers),
            remote_subscribers={
                addr: set(ranges) for addr, ranges in self._remote_subscribers.items()
            },
            seen_message_ids=seen_subset,
            timestamp=time.time(),
        )

        log_info(
            f"Broker:{self.address.port}",
            f"Recorded local state for snapshot {snapshot_id[:8]}... "
            f"({len(snapshot.remote_subscribers)} subscribers, {len(snapshot.peer_brokers)} peers)",
        )

        return snapshot

    def take_snapshot(self, snapshot_id: str = None) -> BrokerSnapshot:
        """
        Take an immediate local snapshot (for testing purposes).
        This is a simplified version that doesn't do full Chandy-Lamport
        coordination. It just captures local state right now.
        In the full implementation, snapshots will be coordinated via markers.
        """
        if snapshot_id is None:
            snapshot_id = str(uuid.uuid4())

        with self._lock:
            snapshot = self._record_local_state(snapshot_id)

        return snapshot

    def initiate_snapshot(self, snapshot_id: str = None) -> Optional[str]:
        """
        Initiate a new distributed snapshot using the Chandy-Lamport algorithm.

        This broker becomes the initiator: it records its local state immediately
        and sends markers on all outgoing channels (to all peers).

        Returns the snapshot_id if initiated, or None if a snapshot is already in progress.
        """
        with self._lock:
            # Don't start a new snapshot if one is already in progress
            if self._snapshot_in_progress is not None:
                log_info(
                    f"Broker:{self.address.port}",
                    f"Snapshot already in progress ({self._snapshot_in_progress[:8]}...), skipping",
                )
                return None

            # Generate snapshot ID if not provided
            if snapshot_id is None:
                snapshot_id = str(uuid.uuid4())

            # Record our local state immediately
            self._snapshot_in_progress = snapshot_id
            self._snapshot_recorded_state = self._record_local_state(snapshot_id)

            # Initialize channel recording for all current peers
            # We'll record messages from each peer until we receive their marker
            self._channels_recording = {peer: [] for peer in self.peer_brokers}
            self._channels_closed = set()

            # Get a copy of peers to send markers to (outside the lock for sending)
            peers_to_notify = set(self.peer_brokers)

        # Create the marker message
        marker = SnapshotMarker(
            snapshot_id=snapshot_id,
            initiator_address=self.address,
            timestamp=time.time(),
        )

        # Send marker to all peers
        log_info(
            f"Broker:{self.address.port}",
            f"Initiating snapshot {snapshot_id[:8]}..., sending markers to {len(peers_to_notify)} peers",
        )

        for peer in peers_to_notify:
            try:
                self.network.send(marker, peer)
                log_debug(
                    f"Broker:{self.address.port}", f"Sent snapshot marker to {peer}"
                )
            except Exception as e:
                log_error(
                    f"Broker:{self.address.port}",
                    f"Failed to send marker to {peer}: {e}",
                )

        # Check if we're already done (no peers = no markers to wait for)
        self._check_snapshot_complete()

        return snapshot_id

    def _handle_snapshot_marker(
        self, marker: SnapshotMarker, sender: NodeAddress
    ) -> None:
        """
        Handle receiving a Chandy-Lamport snapshot marker.

        If this is the first marker we've seen for this snapshot:
          - Record our local state
          - Forward markers to all our peers (except sender)

        Regardless, mark the sender's channel as "closed" for this snapshot.
        When all channels are closed, the snapshot is complete for this broker.
        """
        with self._lock:
            first_marker = self._snapshot_in_progress != marker.snapshot_id

            if first_marker:
                # This is the first marker we've received for this snapshot

                if self._snapshot_in_progress is not None:
                    # We were in the middle of a different snapshot - this shouldn't happen
                    # in our single-snapshot-at-a-time design, but let's handle it gracefully
                    log_warning(
                        f"Broker:{self.address.port}",
                        f"Received marker for {marker.snapshot_id[:8]}... while "
                        f"{self._snapshot_in_progress[:8]}... in progress. Ignoring.",
                    )
                    return

                # Start participating in this snapshot
                self._snapshot_in_progress = marker.snapshot_id
                self._snapshot_recorded_state = self._record_local_state(
                    marker.snapshot_id
                )

                # Initialize channel recording for all peers
                self._channels_recording = {peer: [] for peer in self.peer_brokers}
                self._channels_closed = set()

                # The sender's channel is already closed (they sent us the marker)
                self._channels_closed.add(sender)

                # Get peers to forward marker to
                peers_to_notify = set(self.peer_brokers)
            else:
                # We've already seen a marker for this snapshot
                # Just close this channel
                self._channels_closed.add(sender)
                peers_to_notify = set()

            log_debug(
                f"Broker:{self.address.port}",
                f"Received marker from {sender} for snapshot {marker.snapshot_id[:8]}... "
                f"(first={first_marker}, closed={len(self._channels_closed)}/{len(self.peer_brokers)})",
            )

        # Forward marker to peers (if this was our first marker)
        if first_marker and peers_to_notify:
            forward_marker = SnapshotMarker(
                snapshot_id=marker.snapshot_id,
                initiator_address=marker.initiator_address,
                timestamp=marker.timestamp,
            )

            for peer in peers_to_notify:
                try:
                    self.network.send(forward_marker, peer)
                    log_debug(
                        f"Broker:{self.address.port}",
                        f"Forwarded snapshot marker to {peer}",
                    )
                except Exception as e:
                    log_error(
                        f"Broker:{self.address.port}",
                        f"Failed to forward marker to {peer}: {e}",
                    )

        # Check if snapshot is complete
        self._check_snapshot_complete()

    def _check_snapshot_complete(self) -> None:
        """
        Check if the current snapshot is complete (all channels closed).

        If complete, finalize the snapshot and trigger replication.
        Must be called after any channel is closed.
        """
        with self._lock:
            if self._snapshot_in_progress is None:
                return

            if self._snapshot_recorded_state is None:
                return

            # Check if all peer channels have been closed
            # A channel is closed when we receive a marker from that peer
            all_closed = self._channels_closed >= set(self._channels_recording.keys())

            if not all_closed:
                return

            # Snapshot is complete!
            snapshot = self._snapshot_recorded_state
            snapshot_id = self._snapshot_in_progress

            log_success(
                f"Broker:{self.address.port}",
                f"Snapshot {snapshot_id[:8]}... complete! "
                f"({len(snapshot.remote_subscribers)} subscribers, {len(snapshot.peer_brokers)} peers)",
            )

            # Reset snapshot state
            self._snapshot_in_progress = None
            self._snapshot_recorded_state = None
            self._channels_recording.clear()
            self._channels_closed.clear()

        # Trigger replication (Phase 4 - we'll implement this later)
        self._replicate_snapshot(snapshot)

    def _replicate_snapshot(self, snapshot: BrokerSnapshot) -> None:
        """
        Replicate this broker's snapshot to peer brokers for redundant storage.
        TODO: Implement in Phase 4.
        """
        print(f"TODO: Replicate snapshot ... to peers")

        # log_info(
        #     f"Broker:{self.address_port}",
        #     f"TODO: Replicate snapshot {snapshot.snapshot_id[:8]}... to peers"
        # )

    def _register_remote(
        self, subscriber: NodeAddress, payload_range: PayloadRange
    ) -> None:
        if subscriber not in self._remote_subscribers:
            self._remote_subscribers[subscriber] = set()
        self._remote_subscribers[subscriber].add(payload_range)

        for payload in range(payload_range.low, payload_range.high + 1):
            self._payload_to_remotes[payload].add(subscriber)

        log_info(
            f"Broker:{self.address.port}",
            f"Registered remote subscriber {subscriber} for {payload_range}",
        )

    def _unregister_remote(
        self, subscriber: NodeAddress, payload_range: PayloadRange
    ) -> None:
        if subscriber in self._remote_subscribers:
            self._remote_subscribers[subscriber].discard(payload_range)
            if not self._remote_subscribers[subscriber]:
                del self._remote_subscribers[subscriber]

        for payload in range(payload_range.low, payload_range.high + 1):
            self._payload_to_remotes[payload].discard(subscriber)

        log_info(
            f"Broker:{self.address.port}",
            f"Unregistered remote subscriber {subscriber} from {payload_range}",
        )

    def _deliver_to_remote_subscribers(self, msg: Message) -> None:
        remote_subs = self._payload_to_remotes[msg.payload]
        for subscriber_addr in remote_subs:
            try:
                delivery = PayloadMessageDelivery(msg)
                self.network.send(delivery, subscriber_addr)
                log_debug(
                    f"Broker:{self.address.port}",
                    f"Delivered message with payload {msg.payload} to remote subscriber {subscriber_addr}",
                )
            except Exception as e:
                log_error(
                    f"Broker:{self.address.port}",
                    f"Failed to deliver message to remote subscriber {subscriber_addr}: {e}",
                )

    def _receive_loop(self) -> None:
        while self.running:
            msg, sender = self.network.receive(timeout=1.0)
            if msg is None:
                continue

            try:
                if isinstance(msg, GossipMessage):
                    self._handle_gossip_message(msg)
                elif isinstance(msg, Heartbeat):
                    self.add_peer(sender)
                    with self._lock:
                        self.last_seen[sender] = time.time()
                elif isinstance(msg, MembershipUpdate):
                    for broker_addr in msg.brokers:
                        self.add_peer(broker_addr)
                elif isinstance(msg, SubscribeRequest):
                    subscriber_addr = msg.subscriber
                    payload_range = msg.payload_range
                    self._register_remote(subscriber_addr, payload_range)
                    ack = SubscribeAck(payload_range, success=True)
                    self.network.send(ack, subscriber_addr)
                elif isinstance(msg, UnsubscribeRequest):
                    subscriber_addr = msg.subscriber
                    payload_range = msg.payload_range
                    self._unregister_remote(subscriber_addr, payload_range)
                    ack = UnsubscribeAck(payload_range, success=True)
                    self.network.send(ack, subscriber_addr)
                elif isinstance(msg, SnapshotMarker):
                    self._handle_snapshot_marker(msg, sender)
                elif isinstance(msg, Message):
                    msg_id = str(uuid.uuid4())
                    gossip_msg = GossipMessage(
                        msg=msg, msg_id=msg_id, ttl=self.ttl, source=self.address
                    )
                    self._handle_gossip_message(gossip_msg)
                else:
                    log_error(
                        f"Broker:{self.address.port}",
                        f"Unknown message type from {sender}: {type(msg)}",
                    )

            except Exception as e:
                log_error(
                    f"Broker:{self.address.port}",
                    f"Error handling message from {sender}: {e}",
                )

    def _heartbeat_loop(self) -> None:
        sequence = 0
        while self.running:
            for _ in range(50):
                if not self.running:
                    return
                time.sleep(0.1)
            sequence += 1
            hb = Heartbeat(sender=self.address, sequence=sequence)
            with self._lock:
                peers_copy = list(self.peer_brokers)
            for peer in peers_copy:
                if not self.running:
                    return
                try:
                    self.network.send(hb, peer)
                except Exception as e:
                    pass

    def _check_heartbeat_loop(self) -> None:
        timeout_threshold = 15.0
        while self.running:
            for _ in range(50):
                if not self.running:
                    return
                time.sleep(0.1)
            current_time = time.time()
            dead_peers = []

            with self._lock:
                for peer, last_time in list(self.last_seen.items()):
                    if current_time - last_time > timeout_threshold:
                        dead_peers.append(peer)

                for peer in dead_peers:
                    self.peer_brokers.discard(peer)
                    del self.last_seen[peer]
                    log_info(
                        f"Broker:{self.address.port}",
                        f"Peer marked as deleted: {peer} and removed",
                    )

    def get_count(self, sub: Subscriber, payload) -> int:
        return self._local_broker.get_count(sub, payload)
