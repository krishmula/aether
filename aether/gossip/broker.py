import logging
import random
import threading
import time
import uuid
from collections import deque
from typing import Dict, List, Optional, Set

from aether.core.broker import Broker
from aether.core.message import Message
from aether.core.payload_range import PayloadRange
from aether.core.subscriber import Subscriber
from aether.gossip.protocol import (
    GossipMessage,
    Heartbeat,
    MembershipUpdate,
    PayloadMessageDelivery,
    SubscribeAck,
    SubscribeRequest,
    UnsubscribeAck,
    UnsubscribeRequest,
)
from aether.gossip.status import StatusServer
from aether.network.node import NetworkNode, NodeAddress
from aether.snapshot import (
    BrokerRecoveryNotification,
    BrokerSnapshot,
    Ping,
    Pong,
    SnapshotMarker,
    SnapshotReplica,
    SnapshotRequest,
    SnapshotResponse,
)
from aether.utils.log import BoundLogger, bind_msg_id, reset_msg_id

logger = logging.getLogger(__name__)


class GossipBroker:
    def __init__(
        self,
        address: NodeAddress,
        fanout: int = 3,
        ttl: int = 5,
        heartbeat_interval: float = 5.0,
        heartbeat_timeout: float = 15.0,
        snapshot_interval: float = 15.0,
        http_port: Optional[int] = None,
    ) -> None:
        self.address = address
        self.network = NetworkNode(address)
        self.log = BoundLogger(logger, {"broker": str(address)})

        self._local_broker = Broker()

        self._remote_subscribers: Dict[NodeAddress, Set[PayloadRange]] = {}
        self._payload_to_remotes: List[Set[NodeAddress]] = [set() for _ in range(256)]

        self.fanout = fanout
        self.ttl = ttl
        self.heartbeat_interval = heartbeat_interval
        self.heartbeat_timeout = heartbeat_timeout

        self.peer_brokers: Set[NodeAddress] = set()
        self.last_seen: Dict[NodeAddress, float] = {}

        self._seen_max = 50_000
        self._seen_queue: deque[str] = deque(maxlen=self._seen_max)
        self._seen_set: Set[str] = set()

        self._lock = threading.Lock()

        self.running = False
        self.recv_thread: Optional[threading.Thread] = None
        self.heartbeat_thread: Optional[threading.Thread] = None
        self.check_heartbeat_thread: Optional[threading.Thread] = None

        self.snapshot_interval = snapshot_interval
        self.snapshot_thread: Optional[threading.Thread] = None

        self._snapshot_in_progress: Optional[str] = None
        self._snapshot_recorded_state: Optional[BrokerSnapshot] = None
        self._channels_recording: Dict[NodeAddress, List[GossipMessage]] = {}
        self._channels_closed: Set[NodeAddress] = set()
        self._peer_snapshots: Dict[NodeAddress, BrokerSnapshot] = {}

        self._pending_recovery_request: Optional[NodeAddress] = None
        self._recovery_snapshot: Optional[BrokerSnapshot] = None
        self._recovery_responses_received: int = 0
        self._recovery_peers_asked: int = 0

        # Status / observability
        self._start_time: float = time.time()
        self._messages_processed: int = 0
        self._status_port: Optional[int] = http_port
        self._status_server: Optional[StatusServer] = (
            StatusServer(self, http_port) if http_port is not None else None
        )

    def _seen_add(self, msg_id: str) -> None:
        """Record a message ID. Must be called under self._lock.

        When the deque is full, the oldest ID is automatically evicted
        (via maxlen) and we remove it from the set to keep them in sync.
        """
        if len(self._seen_queue) == self._seen_max:
            evicted = self._seen_queue[0]  # will be popped by deque
            self._seen_set.discard(evicted)
        self._seen_queue.append(msg_id)
        self._seen_set.add(msg_id)

    def register(self, subscriber: Subscriber, payload_range: PayloadRange) -> None:
        self._local_broker.register(subscriber, payload_range)

    def unregister(self, subscriber: Subscriber) -> None:
        self._local_broker.unregister(subscriber)

    def add_peer(self, peer: NodeAddress) -> None:
        if peer != self.address:
            with self._lock:
                is_new = peer not in self.peer_brokers
                self.peer_brokers.add(peer)
                self.last_seen[peer] = time.time()
            if is_new:
                self.log.info(
                    "peer discovered: %s", peer,
                    extra={"event_type": "peer_joined"},
                )
            else:
                self.log.debug("peer heartbeat: %s", peer)

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

        if self._status_server is not None:
            self._status_server.start()

        self.log.info("started with %d peer(s)", len(self.peer_brokers))

    def stop(self) -> None:
        """Stop all background threads and close network connections."""
        self.running = False
        if self.recv_thread:
            self.recv_thread.join(timeout=2.0)
        if self.heartbeat_thread:
            self.heartbeat_thread.join(timeout=2.0)
        if self.check_heartbeat_thread:
            self.check_heartbeat_thread.join(timeout=2.0)
        if self.snapshot_thread:
            self.snapshot_thread.join(timeout=2.0)
        if self._status_server is not None:
            self._status_server.stop()
        self.network.close()

    def _handle_gossip_message(self, gossip_msg: GossipMessage) -> None:
        # Bind the gossip message ID to the current execution context.
        # Every log call made during this method and any function it calls
        # (deliver, gossip-to-peers) will automatically include msg_id.
        token = bind_msg_id(gossip_msg.msg_id)
        try:
            with self._lock:
                if gossip_msg.msg_id in self._seen_set:
                    self.log.debug("duplicate message, skipping")
                    return

                self._seen_add(gossip_msg.msg_id)
                self._messages_processed += 1

            self._local_broker.publish(gossip_msg.msg)
            self._deliver_to_remote_subscribers(gossip_msg.msg)

            if gossip_msg.ttl > 0:
                self._gossip_to_peers(gossip_msg)
        finally:
            reset_msg_id(token)

    def _gossip_to_peers(self, gossip_msg: GossipMessage) -> None:
        """Gossip the message payload to peer brokers."""
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
            except Exception:
                self.log.error("failed to gossip to %s", peer, exc_info=True)

    def _reconnect_subscribers(self, old_broker: NodeAddress) -> None:
        """Notify all subscribers from the recovered snapshot that this broker
        has taken over for the dead broker.

        Sends BrokerRecoveryNotification to each subscriber, allowing them
        to update their broker reference and resume normal operation.

        Args:
            old_broker: The address of the broker that died (which we're replacing)
        """
        with self._lock:
            subscribers = list(self._remote_subscribers.keys())

        if not subscribers:
            self.log.info("no subscribers to reconnect")
            return

        self.log.info(
            "sending recovery notifications to %d subscriber(s)", len(subscribers)
        )

        notification = BrokerRecoveryNotification(
            old_broker=old_broker, new_broker=self.address
        )

        for subscriber in subscribers:
            try:
                self.network.send(notification, subscriber)
                self.log.debug("recovery notification sent to %s", subscriber)
            except Exception:
                self.log.warning(
                    "failed to notify subscriber %s", subscriber, exc_info=True
                )

    def _record_local_state(self, snapshot_id: str) -> BrokerSnapshot:
        """Capture current broker state for a Chandy-Lamport snapshot.
        Must be called under self._lock to ensure consistency.
        """
        seen_subset = set(self._seen_queue)

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

        self.log.debug(
            "local state recorded snapshot_id=%s subscribers=%d peers=%d",
            snapshot_id[:8],
            len(snapshot.remote_subscribers),
            len(snapshot.peer_brokers),
        )

        return snapshot

    def take_snapshot(self, snapshot_id: Optional[str] = None) -> BrokerSnapshot:
        """Take an immediate local snapshot (for testing purposes).
        Simplified version — does not do full Chandy-Lamport coordination.
        """
        if snapshot_id is None:
            snapshot_id = str(uuid.uuid4())

        with self._lock:
            snapshot = self._record_local_state(snapshot_id)

        return snapshot

    def initiate_snapshot(self, snapshot_id: Optional[str] = None) -> Optional[str]:
        """Initiate a new distributed snapshot using the Chandy-Lamport algorithm.

        This broker becomes the initiator: it records its local state immediately
        and sends markers on all outgoing channels (to all peers).

        Returns the snapshot_id if initiated, or None if a snapshot
        is already in progress.
        """
        with self._lock:
            if self._snapshot_in_progress is not None:
                self.log.debug(
                    "snapshot already in progress snapshot_id=%s, skipping",
                    self._snapshot_in_progress[:8],
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

        self.log.info(
            "initiating snapshot snapshot_id=%s peers=%d",
            snapshot_id[:8],
            len(peers_to_notify),
            extra={"event_type": "snapshot_started", "snapshot_id": snapshot_id},
        )

        for peer in peers_to_notify:
            try:
                self.network.send(marker, peer)
                self.log.debug("snapshot marker sent to %s", peer)
            except Exception:
                self.log.error(
                    "failed to send snapshot marker to %s", peer, exc_info=True
                )

        self._check_snapshot_complete()

        return snapshot_id

    def _handle_snapshot_marker(
        self, marker: SnapshotMarker, sender: NodeAddress
    ) -> None:
        """Handle receiving a Chandy-Lamport snapshot marker.

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
                    self.log.warning(
                        "received marker for snapshot_id=%s "
                        "while %s in progress, ignoring",
                        marker.snapshot_id[:8],
                        self._snapshot_in_progress[:8],
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

            self.log.debug(
                "snapshot marker received from %s snapshot_id=%s first=%s closed=%d/%d",
                sender,
                marker.snapshot_id[:8],
                first_marker,
                len(self._channels_closed),
                len(self.peer_brokers),
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
                    self.log.debug("forwarded snapshot marker to %s", peer)
                except Exception:
                    self.log.error(
                        "failed to forward snapshot marker to %s", peer, exc_info=True
                    )

        self._check_snapshot_complete()

    def _check_snapshot_complete(self) -> None:
        """Check if all channels have been closed (markers received from all peers).
        If complete, finalize the snapshot and trigger replication.

        Uses the frozen peer set from _channels_recording (established at snapshot
        initiation) rather than the live peer_brokers set, which could change during
        the snapshot due to heartbeat timeouts or new connections.
        """
        expected_peers = set(self._channels_recording.keys())

        if self._channels_closed >= expected_peers:
            snapshot = self._snapshot_recorded_state
            if snapshot is None:
                return

            self.log.info(
                "snapshot complete snapshot_id=%s subscribers=%d peers=%d",
                snapshot.snapshot_id[:8],
                len(snapshot.remote_subscribers),
                len(snapshot.peer_brokers),
                extra={
                    "event_type": "snapshot_completed",
                    "snapshot_id": snapshot.snapshot_id,
                },
            )

            self._replicate_snapshot(snapshot)

            self._snapshot_in_progress = None
            self._snapshot_recorded_state = None
            self._channels_recording.clear()
            self._channels_closed.clear()

    def _replicate_snapshot(self, snapshot: BrokerSnapshot, k: int = 2) -> None:
        """Replicate this broker's snapshot to k random peers for redundant storage."""
        with self._lock:
            available_peers = list(self.peer_brokers)

        if not available_peers:
            self.log.warning(
                "no peers available to replicate snapshot_id=%s",
                snapshot.snapshot_id[:8],
            )
            return

        num_targets = min(k, len(available_peers))
        targets = random.sample(available_peers, num_targets)

        replica = SnapshotReplica(snapshot)

        self.log.info(
            "replicating snapshot_id=%s to %d peer(s)",
            snapshot.snapshot_id[:8],
            num_targets,
        )

        for peer in targets:
            try:
                self.network.send(replica, peer)
                self.log.debug("snapshot replica sent to %s", peer)
            except Exception:
                self.log.error(
                    "failed to send snapshot replica to %s", peer, exc_info=True
                )

    def _handle_snapshot_replica(
        self, replica: SnapshotReplica, sender: NodeAddress
    ) -> None:
        """Handle receiving a snapshot replica from another broker.

        Store the snapshot so we can provide it during recovery if the
        original broker fails.
        """
        snapshot = replica.snapshot
        source_broker = snapshot.broker_address

        with self._lock:
            existing = self._peer_snapshots.get(source_broker)

            if existing is not None and existing.timestamp >= snapshot.timestamp:
                self.log.debug(
                    "ignoring stale snapshot replica from %s "
                    "(have ts=%f, received ts=%f)",
                    source_broker,
                    existing.timestamp,
                    snapshot.timestamp,
                )
                return

            self._peer_snapshots[source_broker] = snapshot

        self.log.info(
            "stored snapshot replica for %s snapshot_id=%s subscribers=%d peers=%d",
            source_broker,
            snapshot.snapshot_id[:8],
            len(snapshot.remote_subscribers),
            len(snapshot.peer_brokers),
        )

    def _snapshot_timer_loop(self) -> None:
        """Background thread that periodically initiates snapshots.

        Uses a simple leader election heuristic: only the broker with the
        lowest address (lexicographically) initiates. Others participate
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
                self.log.debug("snapshot timer fired, initiating as leader")
                self.initiate_snapshot()
            else:
                self.log.debug("snapshot timer fired, not leader — awaiting marker")

    def _handle_snapshot_request(
        self, request: SnapshotRequest, sender: NodeAddress
    ) -> None:
        """Handle a request for a stored snapshot of a (presumably dead) broker.

        If we have a snapshot for the requested broker address, send it back.
        Otherwise, send a response with snapshot=None.
        """
        requested_broker = request.broker_address

        with self._lock:
            stored_snapshot = self._peer_snapshots.get(requested_broker)

        if stored_snapshot:
            self.log.info(
                "sending stored snapshot for %s to %s", requested_broker, sender
            )
        else:
            self.log.debug("no snapshot available for %s", requested_broker)

        response = SnapshotResponse(
            broker_address=requested_broker, snapshot=stored_snapshot
        )

        try:
            self.network.send(response, sender)
        except Exception:
            self.log.error(
                "failed to send snapshot response to %s", sender, exc_info=True
            )

    def _handle_snapshot_response(
        self, response: SnapshotResponse, sender: NodeAddress
    ) -> None:
        """Handle a response to our snapshot request during recovery.

        We may receive multiple responses (from different peers). We use the first
        valid snapshot received and ignore subsequent ones.
        """
        with self._lock:
            self._recovery_responses_received += 1

            if self._recovery_snapshot is not None:
                self.log.debug(
                    "already have recovery snapshot, ignoring response from %s", sender
                )
                return

            if response.snapshot is not None:
                self._recovery_snapshot = response.snapshot
                self.log.info(
                    "received recovery snapshot from %s for %s subscribers=%d peers=%d",
                    sender,
                    response.broker_address,
                    len(response.snapshot.remote_subscribers),
                    len(response.snapshot.peer_brokers),
                )
            else:
                self.log.debug(
                    "peer %s has no snapshot for %s", sender, response.broker_address
                )

    def request_snapshot_from_peers(
        self, dead_broker: NodeAddress, timeout: float = 5.0
    ) -> Optional[BrokerSnapshot]:
        """Request the snapshot of a dead broker from our peers.

        Sends SnapshotRequest to all known peers and waits for responses.
        Returns the first valid snapshot received, or None if no peer has it.
        """
        with self._lock:
            peers = list(self.peer_brokers)
            self._pending_recovery_request = dead_broker
            self._recovery_snapshot = None
            self._recovery_responses_received = 0
            self._recovery_peers_asked = len(peers)

        if not peers:
            self.log.warning(
                "no peers available to request snapshot for %s", dead_broker
            )
            return None

        self.log.info(
            "requesting snapshot for %s from %d peer(s)", dead_broker, len(peers)
        )

        request = SnapshotRequest(broker_address=dead_broker)
        for peer in peers:
            try:
                self.network.send(request, peer)
                self.log.debug("snapshot request sent to %s", peer)
            except Exception:
                self.log.error(
                    "failed to send snapshot request to %s", peer, exc_info=True
                )

        start_time = time.time()
        while time.time() - start_time < timeout:
            with self._lock:
                if self._recovery_snapshot is not None:
                    snapshot = self._recovery_snapshot
                    self._pending_recovery_request = None
                    return snapshot

                if self._recovery_responses_received >= self._recovery_peers_asked:
                    self.log.warning(
                        "all peers responded but none had snapshot for %s", dead_broker
                    )
                    self._pending_recovery_request = None
                    return None

            time.sleep(0.1)

        self.log.warning("timeout waiting for snapshot responses for %s", dead_broker)
        with self._lock:
            self._pending_recovery_request = None
        return None

    def recover_from_snapshot(self, snapshot: BrokerSnapshot) -> None:
        """Restore this broker's state from a snapshot."""
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
            self._seen_queue.clear()
            self._seen_set.clear()
            for mid in snapshot.seen_message_ids:
                self._seen_add(mid)

            self.log.info(
                "recovered from snapshot snapshot_id=%s "
                "subscribers=%d peers=%d seen_messages=%d",
                snapshot.snapshot_id[:8],
                len(self._remote_subscribers),
                len(self.peer_brokers),
                len(self._seen_set),
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

        self.log.info(
            "remote subscriber registered %s range=%s", subscriber, payload_range
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

        self.log.info(
            "remote subscriber unregistered %s range=%s", subscriber, payload_range
        )

    def _deliver_to_remote_subscribers(self, msg: Message) -> None:
        remote_subs = self._payload_to_remotes[msg.payload]
        for subscriber_addr in remote_subs:
            try:
                delivery = PayloadMessageDelivery(msg)
                self.network.send(delivery, subscriber_addr)
                self.log.debug(
                    "delivered payload=%d to remote subscriber %s",
                    msg.payload,
                    subscriber_addr,
                )
            except Exception:
                self.log.error(
                    "failed to deliver payload=%d to %s",
                    msg.payload,
                    subscriber_addr,
                    exc_info=True,
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
                    if sender is not None:
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
                    unsub_ack = UnsubscribeAck(payload_range, success=True)
                    self.network.send(unsub_ack, subscriber_addr)
                elif isinstance(msg, SnapshotMarker):
                    if sender is not None:
                        self._handle_snapshot_marker(msg, sender)
                elif isinstance(msg, SnapshotReplica):
                    if sender is not None:
                        self._handle_snapshot_replica(msg, sender)
                elif isinstance(msg, SnapshotRequest):
                    if sender is not None:
                        self._handle_snapshot_request(msg, sender)
                elif isinstance(msg, SnapshotResponse):
                    if sender is not None:
                        self._handle_snapshot_response(msg, sender)
                elif isinstance(msg, Ping):
                    if sender is not None:
                        self.network.send(
                            Pong(sender=self.address, sequence=msg.sequence), sender
                        )
                elif isinstance(msg, Message):
                    msg_id = str(uuid.uuid4())
                    gossip_msg = GossipMessage(
                        msg=msg, msg_id=msg_id, ttl=self.ttl, source=self.address
                    )
                    self._handle_gossip_message(gossip_msg)
                else:
                    self.log.error(
                        "unknown message type %s from %s", type(msg).__name__, sender
                    )

            except Exception:
                self.log.error("error handling message from %s", sender, exc_info=True)

    def _heartbeat_loop(self) -> None:
        sequence = 0
        n_steps = max(1, int(self.heartbeat_interval / 0.1))
        while self.running:
            for _ in range(n_steps):
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
                except Exception:
                    pass

    def _check_heartbeat_loop(self) -> None:
        n_steps = max(1, int(self.heartbeat_interval / 0.1))
        while self.running:
            for _ in range(n_steps):
                if not self.running:
                    return
                time.sleep(0.1)
            current_time = time.time()
            # Collect {peer: last_seen_time} for peers that timed out.
            dead_peers: Dict[NodeAddress, float] = {}

            with self._lock:
                for peer, last_time in list(self.last_seen.items()):
                    if current_time - last_time > self.heartbeat_timeout:
                        dead_peers[peer] = last_time

                for peer, last_time in dead_peers.items():
                    self.peer_brokers.discard(peer)
                    del self.last_seen[peer]
                    self.log.warning(
                        "peer evicted (no heartbeat for %.1fs): %s",
                        current_time - last_time,
                        peer,
                        extra={"event_type": "peer_evicted"},
                    )

    def get_count(self, sub: Subscriber, payload) -> int:
        return self._local_broker.get_count(sub, payload)
