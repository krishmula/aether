# Instructions — Starting & Initializing the Pub-Sub System

## Prerequisites

- **Python 3.13+**
- **PyYAML** (`pip install pyyaml`) — required for config-based distributed runs
- Install the package: `pip install -e .` from the project root

---

## 1. Local Mode (Single-Process, In-Memory)

The simplest way to run the system. Everything runs in one process with no networking.

### Admin Utility

```bash
aether-admin <subscribers> [--publish-interval SECONDS] [--duration SECONDS] [--seed INT]
```

| Argument | Description |
|---|---|
| `subscribers` | Number of subscribers (1–255). The payload space (0–255) is partitioned evenly among them. |
| `--publish-interval` | Seconds between random publishes (default: `1.0`) |
| `--duration` | Optional runtime limit in seconds; omit to run until Ctrl+C |
| `--seed` | Optional random seed for deterministic payload generation |

**Example:**

```bash
aether-admin 4 --publish-interval 0.05 --duration 2 --seed 123
```

This starts a `Broker`, creates 4 `Subscriber` instances (each covering a slice of 0–255), and launches a background `Publisher` thread that emits random `Message(payload)` values at the given interval. Stats are printed continuously.

**What gets initialized:**
1. A single `Broker` with 256 subscriber buckets
2. N `Subscriber` objects, each registered with a `PayloadRange`
3. A `Publisher` bound to the broker
4. A daemon thread running the publish loop

---

## 2. Distributed Mode — Single-Machine (All-in-One)

Runs the full distributed stack (bootstrap, brokers, subscribers, publishers) inside a single process using `localhost` ports. No config file needed.

### Distributed Admin

```bash
aether-distributed <brokers> <subscribers_per_broker> <publishers> [options]
```

| Argument | Description |
|---|---|
| `brokers` | Number of `GossipBroker` nodes to create |
| `subscribers_per_broker` | Number of `NetworkSubscriber` instances per broker |
| `publishers` | Number of `NetworkPublisher` instances |
| `--base-port` | Starting port for brokers (default: `8000`) |
| `--publish-interval` | Seconds between publish rounds (default: `1.0`) |
| `--duration` | Runtime limit in seconds (default: `1.0`) |
| `--seed` | Random seed |

**Example:**

```bash
aether-distributed 3 2 2 --base-port 8000 --publish-interval 0.5 --duration 10 --seed 42
```

**Startup sequence (automated):**
1. **Bootstrap server** starts on `localhost:7000`
2. **Brokers** are created on ports `base-port` through `base-port + N - 1` (e.g. `8000`, `8001`, `8002`)
3. Each broker sends a `"JOIN"` message to the bootstrap server
4. Bootstrap responds with a `MembershipUpdate` containing all registered broker addresses
5. Each broker adds the returned peers via `add_peer()`
6. All brokers call `start()` — launching receive, heartbeat, heartbeat-check, and snapshot timer threads
7. **Subscribers** are created on ports starting at `10000`, each connects to a broker and sends a `SubscribeRequest`
8. **Publishers** are created on ports starting at `9000`, each gets the full list of broker addresses
9. The publish loop begins — each publisher picks a random payload and sends a `GossipMessage` to 2 random brokers per round

**Shutdown order:** Publishers → (drain pause) → Subscribers → Brokers → Bootstrap

---

## 3. Distributed Mode — Multi-Machine / Docker (Truly Distributed)

**Every component runs as its own process** — one per CLI invocation, one per Docker container. Each exposes a `/status` HTTP endpoint for observability.

### Step 0: Configure `config.yaml`

Edit `config.yaml` with the correct IPs/ports for your environment:

```yaml
bootstrap:
  host: "100.x.x.10"      # Machine running the bootstrap server
  port: 7000

brokers:
  - id: 1
    host: "100.66.53.34"   # Machine A
    port: 8000
  - id: 2
    host: "100.66.27.2"    # Machine B
    port: 8000
  # ... more brokers

subscribers:
  local_host: "100.104.217.78"  # This machine's IP
  base_port: 10000
  count_per_broker: 2

publishers:
  local_host: "100.104.217.78"  # This machine's IP
  base_port: 9000
  count: 2

gossip:
  fanout: 2
  ttl: 5
  heartbeat_interval: 5.0
  heartbeat_timeout: 15.0

snapshot:
  interval: 30.0
```

You can override the config path with the `AETHER_CONFIG` environment variable.

### Step 1: Start the Bootstrap Server

Run **once**, on the machine designated for bootstrap:

```bash
aether-bootstrap [--config config.yaml] [--host HOST] [--port PORT] [--status-port PORT]
```

### Step 2: Start Brokers

Run **one per broker machine**, specifying the broker's ID from the config:

```bash
aether-broker --broker-id 1 [--config config.yaml] [--host HOST] [--port PORT] [--status-port PORT]
```

Each broker:
1. Looks up its `id` in `config.yaml` to get its `host:port`
2. Creates a `GossipBroker` with the configured `fanout`, `ttl`, and `snapshot_interval`
3. Sends `"JOIN"` to the bootstrap address
4. Receives a `MembershipUpdate` and adds all peers
5. Calls `start()`, launching 4 daemon threads + status HTTP server

### Step 3: Start Subscribers (One Process Per Subscriber)

Each subscriber is a **separate process/container** selected by its numeric ID.

```bash
aether-subscriber --subscriber-id <N> [--config config.yaml] [--host HOST] [--port PORT] [--status-port PORT]
```

| Argument | Required | Description |
|---|---|---|
| `--subscriber-id` | Yes | 0-indexed subscriber ID (0, 1, 2, ...) |
| `--config` | No | Path to config file (default: `config.yaml`) |
| `--host` | No | Override host from config |
| `--port` | No | Override port from config (default: `base_port + id`) |
| `--status-port` | No | HTTP status port (default: `port + 10000`) |
| `--log-level` | No | DEBUG, INFO, WARNING, ERROR |
| `--log-file` | No | Path for rotating JSON log file |

**ID mapping (0-indexed):**
- Total subscribers = `len(brokers) * subscribers_per_broker`
- Subscriber `N` connects to broker at index `N // subscribers_per_broker`
- Port = `config.subscriber_base_port + N`
- Payload range = `partition_payload_space(total)[N % len(ranges)]`

**Example with 3 brokers and 1 subscriber per broker:**
```bash
# Terminal 1: connects to broker-1 (id=1), port 10000, range [0-85]
aether-subscriber --subscriber-id 0 --host sub-0

# Terminal 2: connects to broker-2 (id=2), port 10001, range [86-170]
aether-subscriber --subscriber-id 1 --host sub-1

# Terminal 3: connects to broker-3 (id=3), port 10002, range [171-255]
aether-subscriber --subscriber-id 2 --host sub-2
```

### Step 4: Start Publishers (One Process Per Publisher)

Each publisher is a **separate process/container** selected by its numeric ID.

```bash
aether-publisher --publisher-id <N> [--config config.yaml] [--host HOST] [--port PORT] [--status-port PORT] [--interval SECONDS] [--seed INT]
```

| Argument | Required | Description |
|---|---|---|
| `--publisher-id` | Yes | 0-indexed publisher ID (0, 1, 2, ...) |
| `--config` | No | Path to config file (default: `config.yaml`) |
| `--host` | No | Override host from config |
| `--port` | No | Override port (default: `base_port + id`) |
| `--status-port` | No | HTTP status port (default: `port + 10000`) |
| `--interval` | No | Seconds between publishes (default: `1.0`) |
| `--seed` | No | Random seed for reproducibility |
| `--log-level` | No | DEBUG, INFO, WARNING, ERROR |
| `--log-file` | No | Path for rotating JSON log file |

**Example with 2 publishers:**
```bash
# Terminal 1: port 9000, publishes every 1s
aether-publisher --publisher-id 0 --host pub-0 --interval 1.0

# Terminal 2: port 9001, publishes every 2s
aether-publisher --publisher-id 1 --host pub-1 --interval 2.0
```

### Docker Compose (Recommended)

The included `docker-compose.yml` automates the entire topology:

```bash
make demo    # Build, start, wait, query all status endpoints
make status  # Query /status from every running component
make logs    # Tail all container logs
make clean   # Tear down everything
```

The Docker Compose file creates individual containers for each component:
- `aether-bootstrap` — bootstrap server
- `aether-broker-1`, `aether-broker-2`, `aether-broker-3` — broker mesh
- `aether-subscriber-0`, `aether-subscriber-1`, `aether-subscriber-2` — one subscriber per broker
- `aether-publisher-0`, `aether-publisher-1` — two publishers targeting all brokers

Each container has:
- Its own hostname on the Docker bridge network
- Its own TCP port and status port exposed to the host
- A healthcheck polling `GET /status`
- Dependencies on all brokers being healthy before starting

---

## 4. Running Tests

### Unit Tests

```bash
pytest tests/unit/
```

The unit test suite includes 15 tests:
- **5 tests** for `StatusServer` (broker status endpoint)
- **3 tests** for `BootstrapStatusServer` (bootstrap status endpoint)
- **4 tests** for `SubscriberStatusServer` (subscriber status endpoint)
- **3 tests** for `PublisherStatusServer` (publisher status endpoint)

### Individual Integration/Scenario Tests

These are standalone scripts (not unittest-based) run directly:

| Script | What it tests | Approx. duration |
|---|---|---|
| `tests/integration/test_tcp_basic.py` | Raw TCP send/receive between two `NetworkNode`s | ~5s |
| `tests/integration/test_tcp_simple.py` | TCP connection, server accept, message round-trip | ~10s |
| `tests/integration/test_heartbeat.py` | Heartbeat failure detection (3 brokers, kill one, verify removal) | ~35s |
| `tests/integration/test_snapshot.py` | Local snapshot capture and independence from live state | ~2s |
| `tests/integration/test_snapshot_markers.py` | Chandy-Lamport marker propagation across 3 brokers | ~6s |
| `tests/integration/test_snapshot_replication.py` | Snapshot replication to peer brokers | ~25s |
| `tests/integration/test_snapshot_recovery.py` | Recovery protocol — retrieve dead broker's snapshot from peers | ~15s |
| `tests/integration/test_snapshot_full.py` | Full end-to-end: snapshot → crash → recover → subscriber reconnection | ~15s |
| `tests/integration/testnet.py` | Minimal 2-node TCP smoke test | ~2s |

**Example:**

```bash
python tests/integration/test_heartbeat.py
python tests/integration/test_snapshot_full.py
```

---

## 5. Port Allocation Summary

| Component | TCP Port | HTTP Status Port | Notes |
|---|---|---|---|
| Bootstrap | `7000` | `17000` | Single instance |
| Broker 1 | `8000` | `18000` | `--broker-id 1` |
| Broker 2 | `8000` | `18000` | `--broker-id 2` (same internal port, different container) |
| Broker 3 | `8000` | `18000` | `--broker-id 3` |
| Subscriber 0 | `10000` | `20000` | `--subscriber-id 0` → broker 1 |
| Subscriber 1 | `10001` | `20001` | `--subscriber-id 1` → broker 2 |
| Subscriber 2 | `10002` | `20002` | `--subscriber-id 2` → broker 3 |
| Publisher 0 | `9000` | `19000` | `--publisher-id 0` |
| Publisher 1 | `9001` | `19001` | `--publisher-id 1` |

**Docker port mappings** (host → container): Brokers use shifted mappings (`8001:8000`, `8002:8000`, etc.) since all broker containers use the same internal port. Subscribers and publishers use 1:1 mappings.

**Convention:** Status port = TCP port + 10000 (configurable via `--status-port`).

---

## 6. Environment Variables

| Variable | Description |
|---|---|
| `AETHER_CONFIG` | Override the config file path (default: `config.yaml`) |

---

## 7. Key Initialization Details

### Broker Initialization (`GossipBroker.__init__`)
- Creates a `NetworkNode` (TCP server socket, bound and listening)
- Creates an inner `Broker` for local subscriber dispatch
- Initializes 256-element lookup tables for remote subscriber routing
- Sets up gossip parameters: `fanout`, `ttl`, `seen_messages` dedup set
- Prepares Chandy-Lamport snapshot state (markers, channel recording, peer snapshots)
- Prepares recovery state (pending requests, received responses)
- Creates `StatusServer` if `http_port` is provided (started in `start()`)

### Network Node Initialization (`NetworkNode.__init__`)
- Binds a TCP socket with `SO_REUSEADDR` on the given `host:port`
- Starts a daemon thread to accept incoming connections
- Each accepted connection spawns its own handler thread
- Connections are persistent and bidirectional — identified via `_IdentificationMessage` on first connect
- Messages are length-prefixed (4-byte big-endian) and serialized with `pickle`

### Subscriber Initialization (`NetworkSubscriber.__init__`)
- Creates a `Subscriber` (inner 256-element counts array) and a `NetworkNode`
- Records `_start_time` for uptime tracking
- `_status_port` set by CLI before starting the status server
- Lifecycle: `__init__` → `connect_to_broker()` → `subscribe()` → `start()` → (blocking loop) → `stop()`

### Publisher Initialization (`NetworkPublisher.__init__`)
- Creates a `NetworkNode` with the list of all broker addresses
- Records `_start_time` for uptime tracking
- `_status_port` set by CLI before starting the status server
- Lifecycle: `__init__` → (publish loop) → `close()`

### Bootstrap Registration Flow
1. Broker sends any message (e.g., `"JOIN"`) to the bootstrap address
2. Bootstrap adds the sender to `registered_brokers`
3. Bootstrap sends a `MembershipUpdate(brokers=...)` to **all** registered brokers
4. Each broker calls `add_peer()` for every address in the update

### Subscriber Registration Flow
1. `NetworkSubscriber` calls `connect_to_broker(addr)` — stores the broker address
2. Calls `subscribe(PayloadRange)` — sends `SubscribeRequest` and waits for `SubscribeAck` (up to 3 retries, 2s timeout each)
3. Calls `start()` — launches receive loop thread that handles `PayloadMessageDelivery` and `BrokerRecoveryNotification`
4. `SubscriberStatusServer.start()` — launches HTTP daemon thread serving `GET /status`

### Publisher Publish Loop
1. `PublisherStatusServer.start()` — launches HTTP daemon thread serving `GET /status`
2. Main loop generates random `UInt8` payloads
3. `publish(Message(payload), redundancy=2)` creates a `GossipMessage` with UUID `msg_id`
4. Sends to 2 random brokers, tracking `total_sent`
5. Sleeps for `--interval` seconds, repeats until SIGINT/SIGTERM
