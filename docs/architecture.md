# System Architecture & Component Flow

## High-Level Overview

This is a distributed publish-subscribe system built on a gossip protocol with Chandy-Lamport distributed snapshots for fault tolerance. Messages carry a single `UInt8` payload (0–255) and are routed to subscribers based on `PayloadRange` subscriptions.

```
┌────────────┐         ┌────────────┐
│ Publisher 1 │         │ Publisher N │
└─────┬──────┘         └─────┬──────┘
      │ GossipMessage          │
      │ (redundancy=2)         │
      ▼                        ▼
┌──────────┐  gossip   ┌──────────┐  gossip   ┌──────────┐
│ Broker 1 │◄────────►│ Broker 2 │◄────────►│ Broker 3 │
│          │  heartbeat│          │  heartbeat│          │
│          │  snapshot │          │  snapshot │          │
└────┬─────┘  markers  └────┬─────┘  markers  └────┬─────┘
     │                      │                      │
     │ PayloadMessage       │ PayloadMessage       │ PayloadMessage
     │ Delivery             │ Delivery             │ Delivery
     ▼                      ▼                      ▼
┌─────────┐           ┌─────────┐           ┌─────────┐
│ Sub 1,2 │           │ Sub 3,4 │           │ Sub 5,6 │
└─────────┘           └─────────┘           └─────────┘

                  ┌───────────────┐
                  │   Bootstrap   │
                  │    Server     │
                  └───────────────┘
                  (peer discovery)
```

---

## Component Inventory

### Core Data Types

| Module | Class | Purpose |
|---|---|---|
| `pubsub.core.uint8` | `UInt8` | Integer constrained to 0–255. Subclass of `int`. |
| `pubsub.core.message` | `Message` | Immutable message carrying a single `UInt8` payload. |
| `pubsub.core.payload_range` | `PayloadRange` | Defines a `[low, high]` range within 0–255. Also has `partition_payload_space(n)` to split 0–255 into N even ranges. |

### Local (In-Process) Layer

| Module | Class | Purpose |
|---|---|---|
| `pubsub.core.subscriber` | `Subscriber` | Maintains a 256-element `counts` array. `handle_msg(msg)` increments `counts[msg.payload]`. |
| `pubsub.core.broker` | `Broker` | In-memory message router. Has 256 subscriber buckets. `register()` maps a subscriber to a `PayloadRange`. `publish(msg)` fans out to all subscribers in `buckets[msg.payload]`. |
| `pubsub.core.publisher` | `Publisher` | Thin wrapper — calls `broker.publish(msg)`. |

### Network Layer

| Module | Class | Purpose |
|---|---|---|
| `pubsub.network.node` | `NodeAddress` | `(host, port)` identity. Normalizes hostnames via DNS resolution for consistent hashing/equality. |
| `pubsub.network.node` | `NetworkNode` | TCP server + persistent connection manager. Handles accept, connect, send, receive. Messages are length-prefixed (4B big-endian) + pickled. Connections are identified via `_IdentificationMessage`. |

### Gossip Protocol Messages

All defined in `pubsub.gossip.protocol`:

| Dataclass | Direction | Purpose |
|---|---|---|
| `GossipMessage` | Publisher → Broker, Broker → Broker | Wraps a `Message` with `msg_id` (UUID), `ttl`, and `source` for deduplication and bounded propagation. |
| `Heartbeat` | Broker → Broker | Periodic liveness signal with a sequence number. |
| `MembershipUpdate` | Bootstrap → Broker | Set of all known broker addresses. |
| `SubscribeRequest` | Subscriber → Broker | Requests subscription for a `PayloadRange`. |
| `SubscribeAck` | Broker → Subscriber | Confirms/rejects subscription. |
| `UnsubscribeRequest` | Subscriber → Broker | Requests removal from a `PayloadRange`. |
| `UnsubscribeAck` | Broker → Subscriber | Confirms/rejects unsubscription. |
| `PayloadMessageDelivery` | Broker → Subscriber | Delivers a `Message` to a remote subscriber. |

### Snapshot & Recovery Messages

All defined in `pubsub.snapshot`:

| Dataclass | Direction | Purpose |
|---|---|---|
| `BrokerSnapshot` | (internal) | Captured state: `broker_address`, `peer_brokers`, `remote_subscribers`, `seen_message_ids`, `timestamp`. |
| `SnapshotMarker` | Broker → Broker | Chandy-Lamport marker that triggers state recording and channel closing. |
| `SnapshotReplica` | Broker → Broker | Sends a `BrokerSnapshot` to a peer for redundant storage. |
| `SnapshotRequest` | Broker → Broker | Asks a peer for a stored snapshot of a dead broker. |
| `SnapshotResponse` | Broker → Broker | Returns the requested snapshot (or `None`). |
| `BrokerRecoveryNotification` | Broker → Subscriber | Informs subscriber that a new broker has taken over for a dead one. |

### Distributed Components

| Module | Class | Purpose |
|---|---|---|
| `pubsub.gossip.bootstrap` | `BootstrapServer` | Peer discovery service. Brokers send any message to register; bootstrap broadcasts a `MembershipUpdate` to all registered brokers. |
| `pubsub.gossip.broker` | `GossipBroker` | Full-featured broker: gossip relay, heartbeat liveness, remote subscriber management, Chandy-Lamport snapshots, snapshot replication, and recovery. |
| `pubsub.network.publisher` | `NetworkPublisher` | Wraps a `NetworkNode`. Publishes by creating a `GossipMessage` and sending to N random brokers (configurable redundancy). |
| `pubsub.network.subscriber` | `NetworkSubscriber` | Wraps a `Subscriber` + `NetworkNode`. Sends `SubscribeRequest`, receives `PayloadMessageDelivery`, handles `BrokerRecoveryNotification`. |

### Configuration & Utilities

| Module | Purpose |
|---|---|
| `config.yaml` | YAML config for multi-machine deployments (IPs, ports, gossip params, snapshot interval). |
| `pubsub.config` | Loads `config.yaml` into a `Config` dataclass. Supports `PUBSUB_CONFIG` env override. Provides `get_config()` singleton. |
| `pubsub.utils.log` | Colored terminal logging: `log_info`, `log_success`, `log_warning`, `log_error`, `log_debug`, `log_network`, `log_system`, `log_separator`, `log_header`. |

### CLI Entry Points

| Command | Module | Purpose |
|---|---|---|
| `pubsub-admin` | `pubsub.cli.admin` | Single-process local mode (Broker + Subscribers + Publisher in one process). |
| `pubsub-distributed` | `pubsub.cli.distributed_admin` | All-in-one distributed mode (Bootstrap + Brokers + Subscribers + Publishers, all on localhost). |
| `pubsub-bootstrap` | `pubsub.cli.run_bootstrap` | Standalone bootstrap server process (for multi-machine deployment). |
| `pubsub-broker` | `pubsub.cli.run_broker` | Standalone broker process (one per machine). |
| `pubsub-subscribers` | `pubsub.cli.run_subscribers` | Standalone subscriber launcher (creates all subscribers on this machine). |
| `pubsub-publishers` | `pubsub.cli.run_publishers` | Standalone publisher launcher (creates all publishers on this machine). |

---

## Detailed Flows

### Flow 1: Broker Peer Discovery

```
Broker                     Bootstrap Server              Other Brokers
  │                              │                              │
  │──── "JOIN" (any msg) ───────►│                              │
  │                              │── stores sender address ──►  │
  │                              │                              │
  │◄── MembershipUpdate ────────│──── MembershipUpdate ───────►│
  │   {broker1, broker2, ...}    │   {broker1, broker2, ...}    │
  │                              │                              │
  │── add_peer() for each ──►   │                              │
```

- The bootstrap server is stateless beyond an in-memory `registered_brokers` set.
- Every time a new broker joins, **all** registered brokers receive an updated membership list.
- Brokers only need to contact bootstrap once at startup.

---

### Flow 2: Message Publishing (Network Mode)

```
Publisher                  Broker A                   Broker B                  Broker C
  │                           │                          │                         │
  │ 1. Create GossipMessage   │                          │                         │
  │    (msg_id=UUID, ttl=5)   │                          │                         │
  │                           │                          │                         │
  │── GossipMessage ─────────►│                          │                         │
  │── GossipMessage ──────────┼─────────────────────────►│  (redundancy — same msg_id)
  │                           │                          │                         │
  │                           │ 2. Check seen_messages   │                         │
  │                           │    (deduplicate by       │                         │
  │                           │     msg_id)              │                         │
  │                           │                          │                         │
  │                           │ 3. Deliver locally:      │                         │
  │                           │    broker.publish(msg)   │                         │
  │                           │    + deliver to remote   │                         │
  │                           │      subscribers         │                         │
  │                           │                          │                         │
  │                           │ 4. Gossip (ttl-1) to     │                         │
  │                           │    random peers          │                         │
  │                           │──── GossipMessage ──────►│                         │
  │                           │──── GossipMessage ───────┼────────────────────────►│
  │                           │    (fanout=2)            │                         │
  │                           │                          │                         │
  │                           │                          │ 5. Broker B also:       │
  │                           │                          │    - dedup check        │
  │                           │                          │    - local delivery     │
  │                           │                          │    - remote delivery    │
  │                           │                          │    - gossip to peers    │
```

**Key details:**
- `NetworkPublisher.publish()` generates one `msg_id` and sends the **same** `GossipMessage` to N brokers (default redundancy=2).
- Each broker deduplicates by `msg_id` in a `seen_messages` set — so even if a broker receives the same message from both the publisher and a gossip peer, it processes it only once.
- Gossip propagation decreases `ttl` by 1 each hop. When `ttl` reaches 0, no further forwarding.
- `fanout` controls how many random peers receive each gossip forward.

---

### Flow 3: Subscriber Registration & Message Delivery

```
NetworkSubscriber              GossipBroker
  │                                │
  │ 1. connect_to_broker(addr)     │
  │    (stores addr locally)       │
  │                                │
  │── SubscribeRequest ───────────►│
  │   {subscriber_addr,            │ 2. _register_remote()
  │    payload_range}              │    - adds to _remote_subscribers dict
  │                                │    - updates _payload_to_remotes[0..255]
  │◄── SubscribeAck ──────────────│
  │   {payload_range, success}     │
  │                                │
  │ 3. start() → recv loop        │
  │                                │
  │     ... time passes ...        │
  │                                │
  │                                │ 4. Broker receives a GossipMessage
  │                                │    with payload in subscriber's range
  │                                │
  │◄── PayloadMessageDelivery ────│
  │   {msg: Message}               │ 5. _deliver_to_remote_subscribers()
  │                                │    looks up _payload_to_remotes[payload]
  │ 6. subscriber.handle_msg(msg)  │
  │    counts[payload] += 1        │
```

---

### Flow 4: Heartbeat & Failure Detection

```
Broker A                    Broker B                    Broker C
  │                            │                            │
  │── Heartbeat(seq=1) ───────►│                            │
  │── Heartbeat(seq=1) ────────┼───────────────────────────►│
  │                            │                            │
  │◄── Heartbeat(seq=1) ──────│                            │
  │                            │── Heartbeat(seq=1) ───────►│
  │                            │                            │
  │◄─────────────────────────── │◄── Heartbeat(seq=1) ─────│
  │                            │                            │
  │     ... every ~5 seconds ...                            │
  │                            │                            │
  │                            │         ╳ CRASH            │
  │                            │                            │
  │── Heartbeat(seq=N) ───────►│                            │
  │── Heartbeat(seq=N) ────────┼──────────── ╳ (no recv)   │
  │                            │                            │
  │    ... 15s passes without   │                            │
  │    hearing from Broker C ...│                            │
  │                            │                            │
  │ _check_heartbeat_loop():   │                            │
  │   last_seen[C] is stale →  │                            │
  │   peer_brokers.discard(C)  │                            │
  │   del last_seen[C]         │                            │
```

**Timing:**
- Heartbeats sent every **~5 seconds** (50 × 0.1s sleep loop).
- Heartbeat check runs every **~5 seconds**.
- Timeout threshold: **15 seconds** without a heartbeat → peer removed.
- On receiving any `Heartbeat`, the broker calls `add_peer(sender)` and updates `last_seen[sender]`.

---

### Flow 5: Chandy-Lamport Distributed Snapshot

```
Broker A (leader)          Broker B                     Broker C
  │                           │                            │
  │ 1. _snapshot_timer_loop   │                            │
  │    triggers (leader =     │                            │
  │    lowest address)        │                            │
  │                           │                            │
  │ 2. Record local state     │                            │
  │    (_record_local_state)  │                            │
  │    Begin recording on     │                            │
  │    all incoming channels  │                            │
  │                           │                            │
  │── SnapshotMarker ────────►│                            │
  │── SnapshotMarker ─────────┼───────────────────────────►│
  │                           │                            │
  │                           │ 3. First marker received:  │
  │                           │    - Record local state    │
  │                           │    - Close sender channel  │
  │                           │    - Forward marker to     │
  │                           │      all other peers       │
  │                           │                            │
  │                           │── SnapshotMarker ─────────►│
  │◄── SnapshotMarker ───────│                            │
  │                           │                            │
  │ 4. Marker from B:         │                            │ 3. First marker received
  │    Close channel B        │                            │    (from A): record state
  │                           │                            │── SnapshotMarker ───────►│  → to B
  │◄──────────────────────────┼──── SnapshotMarker ───────│  (back to A)
  │                           │                            │
  │ 5. Marker from C:         │ 5. Marker from C:          │
  │    Close channel C        │    Close channel C         │
  │    All channels closed →  │    All channels closed →   │
  │    Snapshot complete!     │    Snapshot complete!      │
  │                           │                            │
  │ 6. _replicate_snapshot()  │ 6. _replicate_snapshot()   │ 6. _replicate_snapshot()
  │── SnapshotReplica ───────►│                            │
  │── SnapshotReplica ────────┼───────────────────────────►│
```

**Leader election:** The broker with the lexicographically lowest `(host, port)` initiates periodic snapshots. Others participate when they receive markers.

**Snapshot content (`BrokerSnapshot`):**
- `broker_address` — who took this snapshot
- `peer_brokers` — known peers at the time
- `remote_subscribers` — `Dict[NodeAddress, Set[PayloadRange]]`
- `seen_message_ids` — for deduplication continuity (capped at 10,000)
- `timestamp`

**Replication:** After completion, each broker sends a `SnapshotReplica` to `k=2` random peers for redundant storage. Peers store the latest snapshot per source broker in `_peer_snapshots`.

---

### Flow 6: Broker Recovery

```
Dead Broker B              Replacement Broker B'         Surviving Peers (A, C)
  │                              │                              │
  │     ╳ CRASH                  │                              │
  │                              │                              │
  │                              │ 1. Created on same address   │
  │                              │    as dead broker            │
  │                              │                              │
  │                              │ 2. add_peer(A), add_peer(C)  │
  │                              │    start()                   │
  │                              │                              │
  │                              │── SnapshotRequest(B) ───────►│
  │                              │   "do you have B's snapshot?"│
  │                              │                              │
  │                              │◄── SnapshotResponse ────────│
  │                              │   {snapshot: BrokerSnapshot} │
  │                              │                              │
  │                              │ 3. recover_from_snapshot():  │
  │                              │    - Restore _remote_subs    │
  │                              │    - Rebuild payload lookup  │
  │                              │    - Restore peer_brokers    │
  │                              │    - Restore seen_messages   │
  │                              │                              │
  │                              │ 4. _reconnect_subscribers(): │
  │                              │── BrokerRecoveryNotification►│ (to each subscriber)
  │                              │   {old_broker: B,            │
  │                              │    new_broker: B'}           │
  │                              │                              │
                                                Subscriber:
                                                if broker == old_broker:
                                                    broker = new_broker
```

---

### Flow 7: Local Mode (In-Process)

```
pubsub-admin
  │
  ├── Broker()
  │     └── 256 subscriber buckets (list of sets)
  │
  ├── Subscriber × N
  │     └── each registered with a PayloadRange
  │
  ├── Publisher(broker)
  │     └── publish(msg) → broker.publish(msg)
  │
  └── Thread: publish-loop
        └── while not stopped:
              payload = random UInt8
              publisher.publish(Message(payload))
              sleep(interval)
```

No networking. `Broker.publish(msg)` directly iterates `buckets[msg.payload]` and calls `subscriber.handle_msg(msg)` in-process.

---

## Threading Model

Each `GossipBroker.start()` spawns **4 daemon threads**:

| Thread | Name Pattern | Purpose |
|---|---|---|
| Receive loop | `broker-{port}-recv` | Dispatches all incoming messages by type |
| Heartbeat sender | `broker-{port}-hb` | Sends `Heartbeat` to all peers every ~5s |
| Heartbeat checker | `broker-{port}-check-hb` | Evicts stale peers every ~5s |
| Snapshot timer | `broker-{port}-snapshot` | Leader initiates periodic snapshots |

Each `NetworkNode` additionally spawns:

| Thread | Name Pattern | Purpose |
|---|---|---|
| Accept loop | `tcp-server-{port}` | Accepts incoming TCP connections |
| Per-connection handler | `tcp-handler-{port}-{remote}` | Reads from an accepted connection |
| Per-outbound handler | (unnamed, daemon) | Reads from an outbound connection |

Each `NetworkSubscriber.start()` spawns **1 daemon thread** for its receive loop.

---

## Serialization & Wire Format

- All messages serialized with Python `pickle`
- Wire format: `[4 bytes length (big-endian)] [pickled payload]`
- First message on any new TCP connection is an `_IdentificationMessage(sender: NodeAddress)` — allows the receiver to map the socket to a logical peer
- Connections are persistent and bidirectional — once established, both sides can send and receive
- Send has 3 retries with exponential backoff (0.5s, 1.0s)

---

## Deduplication

- Each `GossipMessage` carries a `msg_id` (UUID string)
- Every broker maintains a `seen_messages: Set[str]` 
- On receiving a `GossipMessage`, the broker checks `msg_id in seen_messages` — if seen, the message is dropped
- This prevents infinite loops in the gossip overlay even when `ttl > 0`
- Snapshots capture a subset of `seen_messages` (capped at 10,000) for continuity after recovery
