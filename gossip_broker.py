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
from snapshot import (
    BrokerRecoveryNotification,
    BrokerSnapshot,
    SnapshotMarker,
    SnapshotReplica,
    SnapshotRequest,
    SnapshotResponse,
)
from subscriber import Subscriber


class GossipBroker:
    def __init__(
        self,
        address: NodeAddress,
        fanout: int = 3,
        ttl: int = 5,
        snapshot_interval: float = 15.0,
    ) -> None:
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

        self.snapshot_interval = snapshot_interval
        self.snapshot_thread: threading.Thread = None

        self._snapshot_in_progress: Optional[str] = None
        self._snapshot_recorded_state: Optional[BrokerSnapshot] = None
        self._channels_recording: Dict[NodeAddress, List[GossipMessage]] = {}
        self._channels_closed: Set[NodeAddress] = set()
        self._peer_snapshots: Dict[NodeAddress, BrokerSnapshot] = {}

        self._pending_recovery_request: Optional[NodeAddress] = None
        self._recovery_snapshot: Optional[BrokerSnapshot] = None
        self._recovery_responses_received: int = 0
        self._recovery_peers_asked: int = 0

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

        self.snapshot_thread = threading.Thread(
            target=self._snapshot_timer_loop,
            name=f"broker-{self.address.port}-snapshot",
            daemon=True,
        )
        self.snapshot_thread.start()

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
        if self.snapshot_thread:
            self.snapshot_thread.join(timeout=2.0)
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

    def _reconnect_subscribers(self, old_broker: NodeAddress) -> None:
        """
        Notify all subscribers from the recovered snapshot that this broker
        has taken over for the dead broker.

        Sends BrokerRecoveryNotification to each subscriber, allowing them
        to update their broker reference and resume normal operation.

        Args:
            old_broker: The address of the broker that died (which we're replacing)
        """
        with self._lock:
            subscribers = list(self._remote_subscribers.keys())

        if not subscribers:
            log_info(f"Broker:{self.address.port}", f"No subscribers to reconnect")
            return

        log_info(
            f"Broker:{self.address.port}",
            f"Sending recovery notifications to {len(subscribers)} subscriber(s)",
        )

        notification = BrokerRecoveryNotification(
            old_broker=old_broker, new_broker=self.address
        )

        for subscriber in subscribers:
            try:
                self.network.send(notification, subscriber)
                log_debug(
                    f"Broker:{self.address.port}",
                    f"Sent recovery notification to {subscriber}",
                )
            except Exception as e:
                log_warning(
                    f"Broker:{self.address.port}",
                    f"Failed to notify subscriber {subscriber}: {e}",
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
            if self._snapshot_in_progress is not None:
                log_info(
                    f"Broker:{self.address.port}",
                    f"Snapshot already in progress ({self._snapshot_in_progress[:8]}...), skipping",
                )
                return None

            if snapshot_id is None:
                snapshot_id = str(uuid.uuid4())

            self._snapshot_in_progress = snapshot_id
            self._snapshot_recorded_state = self._record_local_state(snapshot_id)

            self._channels_recording = {peer: [] for peer in self.peer_brokers}
            self._channels_closed = set()

            peers_to_notify = set(self.peer_brokers)

        marker = SnapshotMarker(
            snapshot_id=snapshot_id,
            initiator_address=self.address,
            timestamp=time.time(),
        )

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

                if self._snapshot_in_progress is not None:
                    log_warning(
                        f"Broker:{self.address.port}",
                        f"Received marker for {marker.snapshot_id[:8]}... while "
                        f"{self._snapshot_in_progress[:8]}... in progress. Ignoring.",
                    )
                    return

                self._snapshot_in_progress = marker.snapshot_id
                self._snapshot_recorded_state = self._record_local_state(
                    marker.snapshot_id
                )

                self._channels_recording = {peer: [] for peer in self.peer_brokers}
                self._channels_closed = set()

                self._channels_closed.add(sender)

                peers_to_notify = set(self.peer_brokers)
            else:
                self._channels_closed.add(sender)
                peers_to_notify = set()

            log_debug(
                f"Broker:{self.address.port}",
                f"Received marker from {sender} for snapshot {marker.snapshot_id[:8]}... "
                f"(first={first_marker}, closed={len(self._channels_closed)}/{len(self.peer_brokers)})",
            )

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

        self._check_snapshot_complete()

    def _check_snapshot_complete(self) -> None:
        """
        Check if all channels have been closed (markers received from all peers).
        If complete, finalize the snapshot and trigger replication.

        This method uses the frozen peer set from _channels_recording (established
        at snapshot initiation) rather than the live peer_brokers set, which could
        change during the snapshot due to heartbeat timeouts or new connections.
        """
        expected_peers = set(self._channels_recording.keys())

        if self._channels_closed >= expected_peers:
            snapshot = self._snapshot_recorded_state

            log_success(
                f"Broker:{self.address.port}",
                f"Snapshot {snapshot.snapshot_id}... complete! "
                f"({len(snapshot.remote_subscribers)} subscribers, {len(snapshot.peer_brokers)} peers)",
            )

            self._replicate_snapshot(snapshot)

            self._snapshot_in_progress = None
            self._snapshot_recorded_state = None
            self._channels_recording.clear()
            self._channels_closed.clear()

    def _replicate_snapshot(self, snapshot: BrokerSnapshot, k: int = 2) -> None:
        """
        Replicate this broker's snapshot to peer brokers for redundant storage.
        TODO: Implement in Phase 4.
        """

        with self._lock:
            available_peers = list(self.peer_brokers)

        if not available_peers:
            log_warning(
                f"Broker:{self.address.port}",
                f"No peers available to replicate snapshot {snapshot.snapshot_id[:8]}...",
            )
            return

        num_targets = min(k, len(available_peers))
        targets = random.sample(available_peers, num_targets)

        replica = SnapshotReplica(snapshot)

        log_info(
            f"Broker:{self.address.port}",
            f"Replicating snapshot {snapshot.snapshot_id[:8]}... to {num_targets} peer(s)",
        )

        for peer in targets:
            try:
                self.network.send(replica, peer)
                log_debug(
                    f"Broker:{self.address.port}", f"Sent snapshot replica to {peer}"
                )
            except Exception as e:
                log_error(
                    f"Broker:{self.address.port}",
                    f"Failed to send snapshot replica to {peer}: {e}",
                )

    def _handle_snapshot_replica(
        self, replica: SnapshotReplica, sender: NodeAddress
    ) -> None:
        """
        Handle receiving a snapshot replica from another broker.

        Store the snapshot so we can provide it during recovery if the
        original broker fails.

        Args:
            replica: The SnapshotReplica message containing the snapshot
            sender: The address of the broker that sent this replica
        """
        snapshot = replica.snapshot
        source_broker = snapshot.broker_address

        with self._lock:
            existing = self._peer_snapshots.get(source_broker)

            if existing is not None and existing.timestamp >= snapshot.timestamp:
                log_debug(
                    f"Broker:{self.address.port}",
                    f"Ignoring older snapshot from {source_broker} "
                    f"(have: {existing.timestamp}, received: {snapshot.timestamp})",
                )
                return

            self._peer_snapshots[source_broker] = snapshot

        log_info(
            f"Broker:{self.address.port}",
            f"Stored snapshot replica for {source_broker} "
            f"(snapshot {snapshot.snapshot_id[:8]}..., "
            f"{len(snapshot.remote_subscribers)} subscribers, "
            f"{len(snapshot.peer_brokers)} peers)",
        )

    def _snapshot_timer_loop(self) -> None:
        """
        Background thread that periodically initiates snapshots.

        Uses a simple leader election heuristic: only the broker with the
        lowest address (lexicographically) initiates. Others will participate
        when they receive markers.
        """
        initial_delay = 5.0
        for _ in range(int(initial_delay * 10)):
            if not self.running:
                return
            time.sleep(0.1)

        while self.running:
            for _ in range(int(self.snapshot_interval * 10)):
                if not self.running:
                    return
                time.sleep(0.1)

            with self._lock:
                if not self.peer_brokers:
                    should_initiate = True
                else:
                    all_addresses = self.peer_brokers | {self.address}
                    sorted_addresses = sorted(
                        all_addresses, key=lambda a: (a.host, a.port)
                    )
                    should_initiate = sorted_addresses[0] == self.address

            if should_initiate:
                log_info(
                    f"Broker:{self.address.port}",
                    f"Snapshot timer triggered, initiating periodic snapshot",
                )
                self.initiate_snapshot()
            else:
                log_debug(
                    f"Broker:{self.address.port}",
                    f"Snapshot timer triggered, but not leader - waiting for marker",
                )

    def _handle_snapshot_request(
        self, request: SnapshotRequest, sender: NodeAddress
    ) -> None:
        """
        Handle a request for a stored snapshot of a (presumably dead) broker.

        If we have a snapshot for the requested broker address, send it back.
        Otherwise, send a response with snapshot=None.

        Args:
            request: The snapshot request containing the dead broker's address
            sender: The node requesting the snapshot (the replacement broker)
        """
        requested_broker = request.broker_address

        with self._lock:
            stored_snapshot = self._peer_snapshots.get(requested_broker)

        if stored_snapshot:
            log_info(
                f"Broker:{self.address.port}",
                f"Sending stored snapshot for {requested_broker} to {sender}",
            )
        else:
            log_debug(
                f"Broker:{self.address.port}",
                f"No snapshot available for {requested_broker}",
            )

        response = SnapshotResponse(
            broker_address=requested_broker, snapshot=stored_snapshot
        )

        try:
            self.network.send(response, sender)
        except Exception as e:
            log_error(
                f"Broker:{self.address.port}",
                f"Failed to send snapshot response to {sender}: {e}",
            )

    def _handle_snapshot_response(
        self, response: SnapshotResponse, sender: NodeAddress
    ) -> None:
        """
        Handle a response to our snapshot request during recovery.

        We may receive multiple responses (from different peers). We use the first
        valid snapshot we receive and ignore subsequent ones.

        Args:
            response: The snapshot response (may contain None if peer didn't have it)
            sender: The peer who sent the response
        """
        with self._lock:
            self._recovery_responses_received += 1

            if self._recovery_snapshot is not None:
                log_debug(
                    f"Broker:{self.address.port}",
                    f"Already have recovery snapshot, ignoring response from {sender}",
                )
                return

            if response.snapshot is not None:
                self._recovery_snapshot = response.snapshot
                log_info(
                    f"Broker:{self.address.port}",
                    f"Received recovery snapshot from {sender} for {response.broker_address} "
                    f"({len(response.snapshot.remote_subscribers)} subscribers, "
                    f"{len(response.snapshot.peer_brokers)} peers)",
                )
            else:
                log_debug(
                    f"Broker:{self.address.port}",
                    f"Peer {sender} has no snapshot for {response.broker_address}",
                )

    def request_snapshot_from_peers(
        self, dead_broker: NodeAddress, timeout: float = 5.0
    ) -> Optional[BrokerSnapshot]:
        """
        Request the snapshot of a dead broker from our peers.

        Sends SnapshotRequest to all known peers and waits for responses.
        Returns the first valid snapshot received, or None if no peer has it.

        Args:
            dead_broker: The address of the broker whose snapshot we need
            timeout: How long to wait for responses (seconds)

        Returns:
            The recovered snapshot, or None if recovery failed
        """
        with self._lock:
            peers = list(self.peer_brokers)
            self._pending_recovery_request = dead_broker
            self._recovery_snapshot = None
            self._recovery_responses_received = 0
            self._recovery_peers_asked = len(peers)

        if not peers:
            log_warning(
                f"Broker:{self.address.port}",
                f"No peers available to request snapshot for {dead_broker}",
            )
            return None

        log_info(
            f"Broker:{self.address.port}",
            f"Requesting snapshot for {dead_broker} from {len(peers)} peer(s)",
        )

        request = SnapshotRequest(broker_address=dead_broker)
        for peer in peers:
            try:
                self.network.send(request, peer)
                log_debug(
                    f"Broker:{self.address.port}", f"Sent snapshot request to {peer}"
                )
            except Exception as e:
                log_error(
                    f"Broker:{self.address.port}",
                    f"Failed to send snapshot request to {peer}: {e}",
                )

        start_time = time.time()
        while time.time() - start_time < timeout:
            with self._lock:
                if self._recovery_snapshot is not None:
                    snapshot = self._recovery_snapshot
                    self._pending_recovery_request = None
                    return snapshot

                if self._recovery_responses_received >= self._recovery_peers_asked:
                    log_warning(
                        f"Broker:{self.address.port}",
                        f"All peers responded but none had snapshot for {dead_broker}",
                    )
                    self._pending_recovery_request = None
                    return None

            time.sleep(0.1)

        log_warning(
            f"Broker:{self.address.port}",
            f"Timeout waiting for snapshot responses for {dead_broker}",
        )
        with self._lock:
            self._pending_recovery_request = None
        return None

    def recover_from_snapshot(self, snapshot: BrokerSnapshot) -> None:
        """
        Restore this broker's state from a snapshot.
        """
        with self._lock:
            self._remote_subscribers = {
                addr: set(ranges)
                for addr, ranges in snapshot.remote_subscribers.items()
            }

            self._payload_to_remotes = [set() for _ in range(256)]
            for sub_addr, ranges in self._remote_subscribers.items():
                for pr in ranges:
                    for payload in range(pr.low, pr.high + 1):
                        self._payload_to_remotes[payload].add(sub_addr)

            self.peer_brokers = set(snapshot.peer_brokers)

            self.seen_messages = set(snapshot.seen_message_ids)

            log_success(
                f"Broker:{self.address.port}",
                f"Recovered state from snapshot {snapshot.snapshot_id[:8]}...: "
                f"{len(self._remote_subscribers)} subscribers, "
                f"{len(self.peer_brokers)} peers, "
                f"{len(self.seen_messages)} seen messages",
            )

        self._reconnect_subscribers(snapshot.broker_address)

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
                elif isinstance(msg, SnapshotReplica):
                    self._handle_snapshot_replica(msg, sender)
                elif isinstance(msg, SnapshotRequest):
                    self._handle_snapshot_request(msg, sender)
                elif isinstance(msg, SnapshotResponse):
                    self._handle_snapshot_response(msg, sender)
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
