# Pub-Sub System: From Class Project to Portfolio Showpiece

## Complete Roadmap & Implementation Plan

---

## Current State

You have a working distributed pub-sub messaging system with:

- **Publishers** — each runs as its own process (`pubsub-publisher --publisher-id N`), owns its own TCP socket and HTTP `/status` endpoint
- **Brokers** (multiple instances) — each runs as its own process (`pubsub-broker --broker-id N`) with gossip protocol, heartbeats, and Chandy-Lamport snapshots
- **Subscribers** — each runs as its own process (`pubsub-subscriber --subscriber-id N`), owns its own TCP socket and HTTP `/status` endpoint
- **Bootstrap Server** — centralized peer discovery, its own `/status` endpoint
- **Socket-based communication** (TCP) between all components via `NetworkNode`
- **Chandy-Lamport snapshot algorithm** implemented across brokers for consistent global state capture
- **Acknowledgment-based reliable delivery**
- **Peer discovery** via the bootstrap server
- **One-process-per-component architecture** — ready for Docker containerization

Every component is independently launchable, independently observable via `GET /status`, and follows the same CLI pattern: one ID, one process, one container.

---

## Target State

A fully containerized, orchestratable distributed pub-sub system with:

- Docker containers for every component
- A FastAPI control plane that can spin up/down brokers, publishers, and subscribers on demand
- A React frontend dashboard with sliders, live topology visualization, real-time message flow animation, and Chandy-Lamport snapshot visualization
- Benchmarking data with real throughput/latency numbers
- A polished GitHub repo with architecture docs, diagrams, and a one-command demo
- Resume bullets that make distributed systems engineers stop and read twice

---

## Phase 0: Foundation & Cleanup (Days 1–3)

**Goal:** Get the existing codebase into a clean, testable, documented state before building anything new.

### 0.1 — Code Audit & Refactor ✅

**Status: Completed**

All components are independently launchable via CLI entry points registered in `pyproject.toml`. **Every component follows the same truly-distributed pattern** — one instance per CLI invocation, matching the Phase 1 containerization model:

| Command | Instance | Key Args |
|---|---|---|
| `pubsub-bootstrap` | One bootstrap server | `--host`, `--port`, `--status-port` |
| `pubsub-broker` | One broker | `--broker-id`, `--host`, `--port`, `--status-port` |
| `pubsub-subscriber` | One subscriber | `--subscriber-id`, `--host`, `--port`, `--status-port` |
| `pubsub-publisher` | One publisher | `--publisher-id`, `--host`, `--port`, `--status-port`, `--interval` |

All accept `--config`, `--log-level`, and `--log-file` args. Subscriber/publisher IDs are 0-indexed and determine the target broker, port, and payload range automatically.

```
# Each component launchable like this (one process = one container)
pubsub-broker --broker-id 1 --host broker-1 --port 8000 --status-port 18000
pubsub-subscriber --subscriber-id 0 --host sub-0
pubsub-publisher --publisher-id 0 --host pub-0 --interval 1.0
pubsub-bootstrap --host bootstrap --port 7000 --status-port 17000
```

This is the prerequisite for Docker containerization in Phase 1.

### 0.2 — Add Logging ✅

**Status: Completed**
Replaced all `print()` statements with Python's `logging` module. Uses a `QueueListener` for non-blocking logging, `ContextVar` for propagating correlation IDs (`msg_id`), and provides structured `JSONFormatter` output or a `ColoredFormatter` depending on the terminal environment. This perfectly sets up the data source for the real-time dashboard later.

### 0.3 — Add a Health/Status Endpoint ✅

**Status: Completed**

All four component types expose a `GET /status` HTTP endpoint via `ThreadingHTTPServer` (stdlib, zero new dependencies), each running as a daemon thread alongside the component's TCP socket:

| Component | Status Server Class | Default Port | Response Highlights |
|---|---|---|---|
| `GossipBroker` | `StatusServer` | `broker_port + 10000` | `peers`, `subscribers`, `messages_processed`, `snapshot_state`, `uptime_seconds` |
| `BootstrapServer` | `BootstrapStatusServer` | `bootstrap_port + 10000` | `registered_brokers`, `broker_count`, `uptime_seconds` |
| `NetworkSubscriber` | `SubscriberStatusServer` | `subscriber_port + 10000` | `broker`, `subscriptions`, `total_received`, `running`, `uptime_seconds` |
| `NetworkPublisher` | `PublisherStatusServer` | `publisher_port + 10000` | `brokers`, `broker_count`, `total_sent`, `uptime_seconds` |

All status servers share the same handler pattern: dynamic subclass with component reference injected via class attribute, `_send_json()` helper, 404 for unknown paths. The `--status-port` CLI arg allows overriding the default on all components.

Uptime tracking added to `NetworkSubscriber` and `NetworkPublisher` via `_start_time` attribute (set in `__init__`).

Covered by **15 unit tests** in `tests/unit/test_status.py`: 5 for broker, 3 for bootstrap, 4 for subscriber, 3 for publisher. Each test creates the component (no background threads except `NetworkNode`), starts the status server, hits `/status` via `urllib`, and asserts on the response shape and live state reflection.

### 0.4 — Write a Basic Test Script ✅

**Status: Completed**

`tests/integration/test_e2e.py` is a subprocess-based integration test that launches real OS processes via CLI entry points (`pubsub-bootstrap`, `pubsub-broker`, `pubsub-subscriber`, `pubsub-publisher`). It starts 1 bootstrap server, 3 brokers in a full mesh, individual subscriber processes, and individual publisher processes; writes a temp `config.yaml` with dynamically allocated ports; polls `/status` HTTP endpoints to verify peer discovery, mesh formation, subscriber registration, message flow, and Chandy-Lamport snapshot completion. All checks pass. Run with `python tests/integration/test_e2e.py`.

Create a script that programmatically starts the full system, publishes N messages, verifies they're received by subscribers, triggers a snapshot, and validates the snapshot output. This becomes your regression test as you add features.

```python
# test_system.py
# 1. Start bootstrap server
# 2. Start 3 brokers
# 3. Start 2 publishers, 2 subscribers
# 4. Publish 100 messages across 3 topics
# 5. Wait for delivery, verify subscriber received correct messages
# 6. Trigger Chandy-Lamport snapshot
# 7. Validate snapshot output (local states + channel states)
# 8. Print results: messages sent, received, lost, snapshot consistency check
```

**Deliverable:** Clean codebase where every component is independently launchable with CLI args, structured JSON logging, a /status HTTP endpoint on each broker, and a basic integration test.

---

## Phase 1: Containerization (Days 4–7)

**Goal:** Every component runs in its own Docker container. The entire system spins up with one command.

### 1.1 — Dockerfiles ✅

**Status: Completed**

Single Dockerfile works for all components (they're all Python). Uses `python:3.13-slim` base image with `pyyaml` dependency. Component is determined by CMD override in `docker-compose.yml`.

### 1.2 — Docker Compose (Truly Distributed Topology) ✅

**Status: Completed**

The `docker-compose.yml` implements the **truly distributed model** — each component runs in its own container:

| Service | Container Name | CLI Command |
|---|---|---|
| `bootstrap` | `pubsub-bootstrap` | `pubsub-bootstrap --host bootstrap --port 7000 --status-port 17000` |
| `broker-1` | `pubsub-broker-1` | `pubsub-broker --broker-id 1 --host broker-1 --port 8000 --status-port 18000` |
| `broker-2` | `pubsub-broker-2` | `pubsub-broker --broker-id 2 --host broker-2 --port 8000 --status-port 18000` |
| `broker-3` | `pubsub-broker-3` | `pubsub-broker --broker-id 3 --host broker-3 --port 8000 --status-port 18000` |
| `subscriber-0` | `pubsub-subscriber-0` | `pubsub-subscriber --subscriber-id 0 --host subscriber-0` |
| `subscriber-1` | `pubsub-subscriber-1` | `pubsub-subscriber --subscriber-id 1 --host subscriber-1` |
| `subscriber-2` | `pubsub-subscriber-2` | `pubsub-subscriber --subscriber-id 2 --host subscriber-2` |
| `publisher-0` | `pubsub-publisher-0` | `pubsub-publisher --publisher-id 0 --host publisher-0 --interval 1.0` |
| `publisher-1` | `pubsub-publisher-1` | `pubsub-publisher --publisher-id 1 --host publisher-1 --interval 1.0` |

Each container has:
- Unique hostname on the Docker bridge network (`pubsub-net`)
- Own TCP port and status port exposed to host
- Healthcheck polling `GET /status` (brokers and bootstrap)
- Dependencies on broker health before starting (subscribers and publishers)

### 1.3 — Verify Everything Works ✅

```bash
make demo     # Build, start, wait 20s, query all /status endpoints
make status   # Query /status from every component
make logs     # Tail all container logs
make clean    # Tear down everything
```

### 1.4 — Make Targets ✅

```makefile
# Current Makefile targets:
make demo     # Build images, start system, wait for stabilization, print status
make status   # Query /status from all 9 components (bootstrap + 3 brokers + 3 subscribers + 2 publishers)
make logs     # Tail logs from all containers
make clean    # Stop and remove all containers, networks, and volumes
make test     # Run integration test (no Docker)
```

**Deliverable:** `make demo` spins up the entire distributed system across 9 containers (1 bootstrap, 3 brokers, 3 subscribers, 2 publishers). Every component has its own `/status` endpoint. `make status` queries all 9 endpoints. Everything works across containers on a Docker bridge network.

---

## Phase 2: Orchestration API — The Control Plane (Days 8–14)

**Goal:** A FastAPI service that can dynamically create and destroy pub-sub components by managing Docker containers programmatically.

### 2.0 — Prerequisites (Before Building the Orchestrator)

These are changes to the existing pub-sub core that must land before the orchestrator can manage components dynamically. Without them, the orchestrator will fight the existing code.

#### 2.0.1 — Extract Hardcoded Heartbeat Timeout to Config

**Problem:** The heartbeat *interval* is configurable via `config.yaml` (`gossip.heartbeat_interval`), but the peer eviction *timeout* is hardcoded to `15.0` seconds in `_check_heartbeat_loop()` inside `gossip/broker.py`:

```python
# gossip/broker.py — _check_heartbeat_loop()
timeout_threshold = 15.0  # magic number, ignores config
```

Meanwhile, `config.yaml` already has `heartbeat_timeout: 15.0` — it's just never threaded through to the broker. The config field exists, the broker ignores it.

**Why this matters for Phase 2:** The orchestrator will start/stop brokers dynamically. When a broker is removed, its peers need to detect the failure and evict it. If someone tunes `heartbeat_interval` in config (say, to 1s for faster demos) but the timeout stays at 15s, eviction takes way too long. Conversely, if the interval is increased to 10s but the timeout is still 15s, healthy peers get falsely evicted. The interval and timeout must be configured together.

**Fix:** Thread the timeout through the `GossipBroker` constructor, the same way `fanout`, `ttl`, and `snapshot_interval` are already passed:

1. Add `heartbeat_timeout: float = 15.0` parameter to `GossipBroker.__init__()`
2. Store as `self.heartbeat_timeout`
3. In `_check_heartbeat_loop()`, replace `timeout_threshold = 15.0` with `self.heartbeat_timeout`
4. In `cli/run_broker.py`, pass `heartbeat_timeout=config.heartbeat_timeout` to the constructor

This is a 4-line change. The config field already exists — you're just wiring it up.

**Production context:** Every production system (Kafka, Consul, etcd) treats heartbeat interval and timeout as a pair of knobs that operators tune together. Hardcoding one while exposing the other is a classic source of "works on my machine, breaks in staging" bugs.

#### 2.0.2 — Allow Explicit Broker Address for Subscribers

**Problem:** Subscribers are statically assigned to brokers via arithmetic on the subscriber ID in `cli/run_subscribers.py`:

```python
# cli/run_subscribers.py
broker_idx = args.subscriber_id // config.subscribers_per_broker
broker_addr = config.brokers[broker_idx].to_address()
```

This means subscriber 0 always goes to broker 0, subscriber 1 always goes to broker 0 (if `subscribers_per_broker=2`), etc. The mapping is baked into the CLI and derived from the config file's static broker list.

**Why this matters for Phase 2:** The orchestrator needs to spin up a subscriber and tell it *which* broker to connect to — potentially a broker that was dynamically created and doesn't exist in the original `config.yaml`. The current CLI has no way to do this.

**Fix:** Add `--broker-host` and `--broker-port` CLI args to `run_subscribers.py`. When provided, they override the config-derived broker address:

```python
parser.add_argument("--broker-host", help="Override broker host (for dynamic orchestration)")
parser.add_argument("--broker-port", type=int, help="Override broker port (for dynamic orchestration)")

# Later in main():
if args.broker_host and args.broker_port:
    broker_addr = NodeAddress(args.broker_host, args.broker_port)
else:
    broker_idx = args.subscriber_id // config.subscribers_per_broker
    broker_addr = config.brokers[broker_idx].to_address()
```

This is backwards-compatible — existing `docker-compose.yml` commands don't pass these flags, so they keep working. But the orchestrator can now do:

```python
container = client.containers.run(
    image="pubsub-core",
    command=f"pubsub-subscriber --subscriber-id {sid} --host {hostname} "
            f"--broker-host {broker.host} --broker-port {broker.port}",
    ...
)
```

**Production context:** In Kafka, consumers discover brokers via a bootstrap server list, not via static assignment. In your system, the bootstrap server already exists for broker-to-broker discovery. Long-term, subscribers could use the same mechanism. But for Phase 2, explicit `--broker-host/--broker-port` flags are the pragmatic fix that unblocks the orchestrator without redesigning subscriber discovery.

### 2.1 — Project Structure

```
pubsub-system/
├── core/                    # Your existing pub-sub code
│   ├── broker.py
│   ├── publisher.py
│   ├── subscriber.py
│   ├── bootstrap_server.py
│   └── snapshot.py
├── orchestrator/            # New: Control plane
│   ├── main.py              # FastAPI app
│   ├── docker_manager.py    # Docker SDK wrapper
│   ├── state.py             # System state tracking
│   └── events.py            # WebSocket event broadcasting
├── dashboard/               # New: Frontend (Phase 3)
├── docker-compose.yml
├── Dockerfile
├── Makefile
└── README.md
```

### 2.2 — Docker Manager

This is the core of the orchestration layer. It uses the Docker SDK for Python to manage containers:

```python
# orchestrator/docker_manager.py
import docker
import uuid

class DockerManager:
    def __init__(self):
        # Mount the Docker socket so the orchestrator can control Docker
        self.client = docker.from_env()
        self.network_name = "pubsub-net"
        self.containers = {}  # id -> container info
        self._ensure_network()

    def _ensure_network(self):
        """Create the Docker network if it doesn't exist."""
        try:
            self.client.networks.get(self.network_name)
        except docker.errors.NotFound:
            self.client.networks.create(self.network_name, driver="bridge")

    def create_broker(self, broker_id: str = None) -> dict:
        """Spin up a new broker container."""
        broker_id = broker_id or f"broker-{uuid.uuid4().hex[:6]}"
        port = self._next_available_port("broker")

        container = self.client.containers.run(
            image="pubsub-core",
            command=f"python broker.py --id {broker_id} --host 0.0.0.0 --port {port} --bootstrap bootstrap:4000",
            name=broker_id,
            network=self.network_name,
            detach=True,
            ports={f"{port}/tcp": port, f"{port + 3000}/tcp": port + 3000},
            labels={"pubsub.role": "broker", "pubsub.id": broker_id}
        )

        self.containers[broker_id] = {
            "id": broker_id,
            "type": "broker",
            "container_id": container.id,
            "port": port,
            "status_port": port + 3000,
            "status": "running"
        }
        return self.containers[broker_id]

    def remove_broker(self, broker_id: str) -> dict:
        """Gracefully stop and remove a broker container."""
        if broker_id not in self.containers:
            raise ValueError(f"Unknown broker: {broker_id}")

        container = self.client.containers.get(self.containers[broker_id]["container_id"])
        container.stop(timeout=10)
        container.remove()
        info = self.containers.pop(broker_id)
        info["status"] = "removed"
        return info

    def create_publisher(self, publisher_id: str = None, topics: list = None, rate: int = 10) -> dict:
        """Spin up a new publisher container."""
        # Similar to create_broker, but runs publisher.py
        ...

    def create_subscriber(self, subscriber_id: str = None, topics: list = None) -> dict:
        """Spin up a new subscriber container."""
        ...

    def get_system_state(self) -> dict:
        """Return current state of all running components."""
        state = {"brokers": [], "publishers": [], "subscribers": []}
        for cid, info in self.containers.items():
            state[f"{info['type']}s"].append(info)
        return state

    def trigger_snapshot(self, initiator_broker_id: str) -> dict:
        """Trigger Chandy-Lamport snapshot on a specific broker."""
        # Send HTTP request to the broker's status endpoint
        # or exec into the container to trigger snapshot
        ...

    def _next_available_port(self, component_type: str) -> int:
        """Find next available port for a component type."""
        ...
```

### 2.3 — FastAPI Endpoints

```python
# orchestrator/main.py
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from docker_manager import DockerManager
from events import EventBroadcaster

app = FastAPI(title="Pub-Sub Control Plane")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

docker_mgr = DockerManager()
broadcaster = EventBroadcaster()

# --- Component Lifecycle ---

@app.post("/api/brokers")
async def add_broker():
    """Spin up a new broker."""
    broker = docker_mgr.create_broker()
    await broadcaster.emit("broker_added", broker)
    return broker

@app.delete("/api/brokers/{broker_id}")
async def remove_broker(broker_id: str):
    """Remove a broker."""
    result = docker_mgr.remove_broker(broker_id)
    await broadcaster.emit("broker_removed", result)
    return result

@app.post("/api/publishers")
async def add_publisher(topics: list[str] = ["default"], rate: int = 10):
    """Spin up a new publisher."""
    publisher = docker_mgr.create_publisher(topics=topics, rate=rate)
    await broadcaster.emit("publisher_added", publisher)
    return publisher

@app.delete("/api/publishers/{publisher_id}")
async def remove_publisher(publisher_id: str):
    result = docker_mgr.remove_publisher(publisher_id)
    await broadcaster.emit("publisher_removed", result)
    return result

@app.post("/api/subscribers")
async def add_subscriber(topics: list[str] = ["default"]):
    subscriber = docker_mgr.create_subscriber(topics=topics)
    await broadcaster.emit("subscriber_added", subscriber)
    return subscriber

@app.delete("/api/subscribers/{subscriber_id}")
async def remove_subscriber(subscriber_id: str):
    result = docker_mgr.remove_subscriber(subscriber_id)
    await broadcaster.emit("subscriber_removed", result)
    return result

# --- System State ---

@app.get("/api/state")
async def get_state():
    """Return full system topology and status."""
    return docker_mgr.get_system_state()

@app.get("/api/state/topology")
async def get_topology():
    """Return node connections for graph visualization."""
    # Query each broker's /status endpoint for peer lists
    # Return nodes and edges
    ...

# --- Snapshots ---

@app.post("/api/snapshots")
async def trigger_snapshot(initiator: str = None):
    """Trigger Chandy-Lamport snapshot."""
    result = docker_mgr.trigger_snapshot(initiator)
    await broadcaster.emit("snapshot_initiated", result)
    return result

@app.get("/api/snapshots/latest")
async def get_latest_snapshot():
    """Return the most recent snapshot result."""
    ...

# --- Metrics ---

@app.get("/api/metrics")
async def get_metrics():
    """Aggregate metrics from all brokers."""
    # Poll each broker's /status endpoint
    # Return aggregate: total messages, throughput, latency
    ...

# --- Real-Time Events (WebSocket) ---

@app.websocket("/ws/events")
async def websocket_events(websocket: WebSocket):
    """Stream real-time system events to the dashboard."""
    await websocket.accept()
    broadcaster.register(websocket)
    try:
        while True:
            # Keep connection alive, also accept commands from frontend
            data = await websocket.receive_text()
            # Handle frontend commands if needed
    except WebSocketDisconnect:
        broadcaster.unregister(websocket)
```

### 2.4 — Event Broadcasting

The orchestrator needs to push real-time events to the frontend. There are two types of events:

**Orchestration events** (from the control plane itself): broker_added, broker_removed, publisher_added, snapshot_initiated, etc.

**System events** (from the pub-sub components): message_published, message_delivered, snapshot_marker_sent, snapshot_marker_received, snapshot_complete, etc.

For system events, you have two options:

**Option A — Log aggregation:** Each container writes JSON logs. The orchestrator tails these logs via Docker SDK (`container.logs(stream=True)`) and forwards relevant events to the WebSocket.

**Option B — Direct reporting:** Each pub-sub component reports events to the orchestrator via HTTP POST to a `/api/events/ingest` endpoint. This is cleaner but requires modifying your core pub-sub code.

Recommendation: Start with Option A (log tailing). It's non-invasive — you don't have to modify your core pub-sub code, just make sure it logs in JSON format (which you set up in Phase 0).

```python
# orchestrator/events.py
import asyncio
import json

class EventBroadcaster:
    def __init__(self):
        self.connections = set()

    def register(self, ws):
        self.connections.add(ws)

    def unregister(self, ws):
        self.connections.discard(ws)

    async def emit(self, event_type: str, data: dict):
        message = json.dumps({"type": event_type, "data": data, "timestamp": time.time()})
        dead = set()
        for ws in self.connections:
            try:
                await ws.send_text(message)
            except:
                dead.add(ws)
        self.connections -= dead
```

### 2.5 — Orchestrator Docker Setup

The orchestrator itself runs as a container, but it needs access to the Docker socket to manage other containers:

```yaml
# Add to docker-compose.yml
orchestrator:
  build:
    context: .
    dockerfile: Dockerfile.orchestrator
  command: uvicorn orchestrator.main:app --host 0.0.0.0 --port 9000
  ports:
    - "9000:9000"
  volumes:
    - /var/run/docker.sock:/var/run/docker.sock # Docker-in-Docker access
  depends_on:
    bootstrap:
      condition: service_healthy
  networks:
    - pubsub-net
```

**Deliverable:** A running FastAPI service at `localhost:9000` with full CRUD for brokers, publishers, and subscribers. `POST /api/brokers` actually spins up a new Docker container. WebSocket at `/ws/events` streams real-time system events. Full Swagger docs at `/docs`.

---

## Phase 3: Frontend Dashboard (Days 15–25)

**Goal:** A React-based dashboard that visualizes the live system and provides interactive controls.

### 3.1 — Tech Stack

- **React** with hooks (you're familiar with React from your other projects)
- **D3.js** for the topology graph and message flow animation
- **Tailwind CSS** for styling
- **WebSocket** for real-time updates from the orchestrator

### 3.2 — Dashboard Layout

```
┌─────────────────────────────────────────────────────────┐
│  PubSub Control Plane                    [Trigger Snap] │
├───────────┬─────────────────────────────────────────────┤
│ CONTROLS  │                                             │
│           │         TOPOLOGY GRAPH                      │
│Brokers    │                                             │
│ [===3===] │    [B1] ←——→ [B2]                           │
│           │      ↕    ╲   ↕                             │
│Publishers │    [P1]    [B3]                             │
│ [===2===] │             ↕                               │
│           │           [S1] [S2]                         │
│Subscribers│                                             │
│ [===2===] │                                             │
│           │                                             │
│ Topics    ├─────────────────────────────────────────────┤
│ [weather] │  METRICS                    EVENT LOG       │
│ [sports ] │  Throughput: 2,341 msg/s    14:03:22 B1→B2  │
│ [finance] │  Latency p50: 2ms           14:03:22 B2→S1  │
│           │  Latency p99: 12ms          14:03:21 P1→B1  │
│           │  Active snaps: 0            14:03:21 MARKER │
└───────────┴─────────────────────────────────────────────┘
```

### 3.3 — Key Components

**Slider Controls (left panel):**
Each slider calls the orchestration API. Moving the broker slider from 3 to 5 triggers two `POST /api/brokers` calls. Moving it from 5 to 3 triggers two `DELETE /api/brokers/{id}` calls (removing the most recently added brokers). Add topic selector checkboxes that configure what publishers send and what subscribers listen to.

**Topology Graph (center, the star of the show):**
Use D3.js force-directed graph. Nodes are brokers (large circles), publishers (small squares), and subscribers (small triangles). Edges represent active connections. Color-code by type. When a new broker spins up, animate it appearing and edges forming. When a broker is removed, animate it fading and edges disappearing.

**Message Flow Animation:**
When a message travels from publisher → broker → subscriber, animate a small dot moving along the edge. Use the WebSocket event stream — each "message_forwarded" event triggers an animation. During a Chandy-Lamport snapshot, render the MARKER messages as a different color (red) so you can visually see them propagating through the graph. This is the moment that makes people go "oh wow."

**Metrics Panel (bottom-left):**
Poll `GET /api/metrics` every second. Display throughput (messages/second), latency (p50, p99), active brokers/publishers/subscribers counts, and snapshot status.

**Event Log (bottom-right):**
Scrolling log of WebSocket events. Timestamp, source, destination, event type. Color-coded by event type (message = blue, snapshot marker = red, broker join/leave = yellow).

### 3.4 — Snapshot Visualization (This Is Your Differentiator)

When the user clicks "Trigger Snapshot":

1. The initiating broker flashes/highlights
2. MARKER messages (red dots) animate outward from that broker to all peers
3. As each broker receives its first MARKER, it highlights (indicating local state captured)
4. Channel recording is shown with a colored border on edges being recorded
5. When a broker receives MARKERs from all channels, the recording stops (border returns to normal)
6. When all brokers have completed, a modal/panel shows the global snapshot: each broker's local state and each channel's recorded messages

This single visualization demonstrates that you understand the algorithm deeply enough to make it visible. That's a level of mastery most people can't demonstrate.

### 3.5 — Docker Setup for Frontend

```yaml
dashboard:
  build:
    context: ./dashboard
    dockerfile: Dockerfile
  ports:
    - "3000:3000"
  environment:
    - REACT_APP_API_URL=http://localhost:9000
    - REACT_APP_WS_URL=ws://localhost:9000/ws/events
  depends_on:
    - orchestrator
  networks:
    - pubsub-net
```

**Deliverable:** A live, interactive dashboard at `localhost:3000`. Sliders dynamically add/remove containers. The topology graph updates in real time. Message flow is visually animated. Chandy-Lamport snapshots visually propagate through the system.

---

## Phase 4: Benchmarking & Metrics (Days 26–30)

**Goal:** Generate real performance numbers you can put on your resume.

### 4.1 — Benchmark Script

```python
# benchmarks/throughput_test.py
"""
Measures:
- Messages per second (throughput) at varying broker/publisher counts
- End-to-end latency (publisher send → subscriber receive) at p50, p95, p99
- Snapshot completion time (initiation → all brokers finished)
- Message loss rate under load
"""

import time
import statistics

class BenchmarkRunner:
    def __init__(self, orchestrator_url="http://localhost:9000"):
        self.api = orchestrator_url

    def throughput_test(self, num_brokers=3, num_publishers=5, num_subscribers=5,
                        duration_seconds=60, messages_per_second=1000):
        """
        Ramp up system, publish at target rate, measure actual throughput.
        """
        # 1. Set up topology via orchestrator API
        # 2. Start publishers at target rate
        # 3. Measure subscriber receive rate over duration
        # 4. Calculate throughput, loss rate
        ...

    def latency_test(self, num_brokers=3, num_messages=10000):
        """
        Embed timestamps in messages, measure delivery latency.
        """
        # Publisher embeds send_timestamp in message payload
        # Subscriber records receive_timestamp
        # Calculate deltas, compute percentiles
        ...

    def snapshot_benchmark(self, num_brokers_range=[3, 5, 10, 20]):
        """
        Measure snapshot completion time as broker count scales.
        """
        # For each broker count:
        #   1. Set up topology
        #   2. Start message flow
        #   3. Trigger snapshot, measure time to completion
        ...

    def scaling_test(self):
        """
        Gradually increase publishers and measure throughput ceiling.
        """
        # Start with 1 publisher, increase to 50
        # Plot throughput vs publisher count
        # Find saturation point
        ...
```

### 4.2 — Timestamp Embedding for Latency

Modify your publisher to embed `send_timestamp` in the message payload. Modify your subscriber to compute `receive_timestamp - send_timestamp`. This gives you end-to-end latency. Important: use `time.monotonic_ns()` for same-machine tests, or embed UTC timestamps if testing across actual machines.

### 4.3 — Target Numbers to Aim For

These are rough targets that would look good on a resume. Actual results depend on your hardware and implementation:

| Metric                    | Target        | Resume-Worthy                            |
| ------------------------- | ------------- | ---------------------------------------- |
| Throughput (3 brokers)    | 5,000+ msg/s  | "Sustained 5K+ messages/second"          |
| Throughput (10 brokers)   | 10,000+ msg/s | "Scaled to 10K+ msg/s across 10 brokers" |
| Latency p50               | < 5ms         | "Sub-5ms median delivery latency"        |
| Latency p99               | < 50ms        | "< 50ms p99 latency"                     |
| Snapshot time (3 brokers) | < 100ms       | "Consistent snapshots in < 100ms"        |
| Message loss              | 0%            | "Zero-loss guaranteed delivery"          |

Even if your numbers are lower, having real numbers at all puts you ahead of 95% of applicants.

### 4.4 — Generate Charts

Use matplotlib to generate performance charts. Include them in your README:

- Throughput vs. number of brokers (line chart)
- Latency distribution (histogram with p50/p99 lines)
- Snapshot completion time vs. broker count (line chart)
- Throughput over time during a scaling test (line chart showing ramp-up)

**Deliverable:** A `benchmarks/` directory with runnable benchmark scripts and generated charts. Real, measurable performance numbers ready for your resume and README.

---

## Phase 5: Hardening & Extra Features (Days 31–40)

**Goal:** Add features that push this from "impressive class project" to "this could be production infrastructure."

### 5.1 — Fault Tolerance (High Priority)

**Heartbeat-based failure detection:**

Each broker sends periodic heartbeats to its peers (every 2 seconds). If a broker doesn't receive a heartbeat from a peer for 3 consecutive intervals (6 seconds), it marks that peer as dead and notifies the bootstrap server.

**Topic reassignment on broker failure:**

When a broker dies, its topics need to be redistributed to surviving brokers. The bootstrap server (or a leader broker) handles reassignment. Subscribers that were connected to the dead broker get redirected to the new owner of their topics.

This is essentially a simplified version of Kafka's partition reassignment. Implement it and you can say: "Implemented failure detection with heartbeats and automatic topic reassignment, achieving subscriber continuity during broker failures."

### 5.2 — Time-Windowed Message Deduplication (High Priority)

Replace the current bounded-deque dedup cache (`_seen_queue` + `_seen_set`) with a proper time-windowed deduplicator. This is how production messaging systems handle deduplication:

- **Google Cloud Pub/Sub**: 10-minute dedup window
- **AWS SQS**: 5-minute dedup window
- **Apache Kafka**: offset-based (different model, but consumer group dedup is time-windowed)

**Why time-based, not count-based:** A `deque(maxlen=N)` gives inconsistent guarantees. Under high throughput the window shrinks to seconds (IDs evicted before gossip can finish propagating), under low throughput you waste memory on stale IDs from hours ago. A time-based window gives consistent dedup guarantees regardless of message rate.

**Implementation — `MessageDeduplicator` class:**

```python
# pubsub/core/dedup.py
from collections import OrderedDict
import threading
import time

class MessageDeduplicator:
    """Time-windowed, bounded message dedup cache.

    Uses an OrderedDict keyed by msg_id with monotonic timestamps as
    values.  Entries are insertion-ordered, so expiry sweeps from the
    front in O(k) where k = number of expired entries.

    Thread-safe — all public methods acquire the internal lock.
    """

    def __init__(self, window_seconds: float = 300.0, max_entries: int = 100_000):
        self._window = window_seconds
        self._max_entries = max_entries
        self._entries: OrderedDict[str, float] = OrderedDict()
        self._lock = threading.Lock()

    def check_and_add(self, msg_id: str) -> bool:
        """Return True if msg_id is new (not seen within the window).

        Lazily sweeps expired entries on every call.  If the ID is new
        it is recorded; if it's a duplicate, returns False.
        """
        now = time.monotonic()
        with self._lock:
            # Sweep: pop from front while oldest entry is past the window
            cutoff = now - self._window
            while self._entries:
                oldest_id, oldest_ts = next(iter(self._entries.items()))
                if oldest_ts <= cutoff:
                    self._entries.popitem(last=False)
                else:
                    break

            if msg_id in self._entries:
                return False

            self._entries[msg_id] = now

            # Hard cap as safety valve against pathological bursts
            while len(self._entries) > self._max_entries:
                self._entries.popitem(last=False)

            return True

    def __contains__(self, msg_id: str) -> bool:
        with self._lock:
            return msg_id in self._entries

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)

    def snapshot_ids(self, limit: int = 10_000) -> set[str]:
        """Return up to limit most recent IDs for snapshot serialization."""
        with self._lock:
            return set(list(self._entries.keys())[-limit:])

    def restore(self, msg_ids: set[str]) -> None:
        """Bulk-load IDs during recovery. All get current timestamp."""
        now = time.monotonic()
        with self._lock:
            for mid in msg_ids:
                if mid not in self._entries:
                    self._entries[mid] = now
            while len(self._entries) > self._max_entries:
                self._entries.popitem(last=False)
```

**Key design decisions:**

1. **`time.monotonic()` not `time.time()`** — monotonic clocks aren't affected by NTP adjustments or wall-clock jumps. Production systems always use monotonic clocks for internal timing.
2. **Lazy sweep** — expired entries are cleaned up on every `check_and_add` call rather than a background thread. Simpler, no extra thread, and amortized O(1) per call.
3. **Hard cap (`max_entries`)** — safety valve. Even if messages arrive faster than they expire, memory stays bounded.
4. **`snapshot_ids()` returns the most recent** — when serializing for Chandy-Lamport, you want the newest IDs (most likely to still be in-flight).
5. **`restore()` uses current timestamp** — recovered IDs get a full window before expiry, preventing duplicate reprocessing after recovery.

**Integration:** Replace `_seen_queue`/`_seen_set` in `GossipBroker` with a single `MessageDeduplicator` instance. The dedup window should be configurable via `config.yaml` under the `gossip` section (e.g., `dedup_window_seconds: 300`).

### 5.3 — Write-Ahead Log (Medium Priority)

Add a simple append-only log per topic on each broker. Before acknowledging a message, write it to disk. On broker restart, replay the log to recover state.

```python
class WriteAheadLog:
    def __init__(self, topic: str, log_dir: str = "/data/wal"):
        self.filepath = f"{log_dir}/{topic}.log"
        self.file = open(self.filepath, "a")

    def append(self, message: dict):
        line = json.dumps(message) + "\n"
        self.file.write(line)
        self.file.flush()
        os.fsync(self.file.fileno())  # Ensure it hits disk

    def replay(self) -> list:
        messages = []
        with open(self.filepath, "r") as f:
            for line in f:
                messages.append(json.loads(line.strip()))
        return messages
```

This gives you durability. If a broker crashes and restarts, it replays the WAL and recovers. That's how Kafka, PostgreSQL, and every serious database works.

### 5.4 — Message Ordering Guarantees (Medium Priority)

Add per-topic sequence numbers. Publishers embed a monotonically increasing sequence number. Brokers track the latest sequence per topic. Subscribers can detect gaps and request retransmission. This lets you guarantee exactly-once or at-least-once delivery with ordering.

### 5.5 — Graceful Broker Drain (Lower Priority)

When a broker is being removed (slider goes down), instead of hard-killing it, implement a drain protocol: stop accepting new subscriptions, finish delivering in-flight messages, transfer topics to other brokers, then shut down. This is how real production systems handle rolling deployments.

**Deliverable:** Fault tolerance with heartbeats, automatic topic reassignment, write-ahead logging for durability, and optional message ordering guarantees.

---

## Phase 6: Documentation & Presentation (Days 41–45)

**Goal:** Make the GitHub repository itself a portfolio piece.

### 6.1 — README Structure

```markdown
# Distributed Pub-Sub Messaging System

A distributed publish-subscribe messaging system with multi-broker architecture,
Chandy-Lamport distributed snapshots, and a real-time orchestration dashboard.

[Screenshot/GIF of dashboard with messages flowing and snapshot happening]

## Architecture

[Mermaid diagram showing: Publishers → Brokers (mesh) → Subscribers,
Bootstrap Server in the center, Orchestrator + Dashboard on the side]

## Key Features

- Multi-broker architecture with dynamic scaling
- Chandy-Lamport distributed snapshot algorithm
- Acknowledgment-based reliable message delivery
- Write-ahead logging for durability
- Heartbeat-based failure detection with automatic topic reassignment
- Real-time orchestration dashboard with live topology visualization
- Docker-based deployment with programmatic container orchestration

## Quick Start

    docker-compose up
    # Dashboard: http://localhost:3000
    # API Docs: http://localhost:9000/docs

## Performance

[Embed throughput and latency charts here]

| Metric        | Value                   |
| ------------- | ----------------------- |
| Throughput    | X,XXX msg/s (3 brokers) |
| Latency (p99) | XXms                    |
| Snapshot time | XXms                    |

## Design Decisions

### Why Chandy-Lamport?

[Explain the consistency problem, why you can't just pause everything,
how FIFO channels + markers solve it. Draw the analogy to Apache Flink's
distributed checkpointing.]

### Why a centralized bootstrap server instead of gossip?

[Explain the tradeoff: simpler implementation, single point of failure,
but sufficient for the scale you're targeting. Note that gossip would be
the right choice at larger scale.]

### Why write-ahead logging?

[Explain durability guarantees, compare to how Kafka's log works.]

## How It Works

[Detailed explanation of message flow, snapshot algorithm, failure recovery]

## Analogies to Production Systems

| This Project             | Production Equivalent           |
| ------------------------ | ------------------------------- |
| Bootstrap Server         | ZooKeeper / etcd                |
| Broker mesh              | Kafka broker cluster            |
| Chandy-Lamport snapshots | Flink distributed checkpointing |
| Write-ahead log          | Kafka commit log                |
| Topic reassignment       | Kafka partition rebalancing     |
| Orchestrator API         | Kubernetes control plane        |
```

### 6.2 — Record a Demo Video/GIF

Use a screen recording tool to capture:

1. Dashboard loads with empty system
2. Slide broker count to 3 — watch containers appear in the topology
3. Add publishers and subscribers — messages start flowing visually
4. Click "Trigger Snapshot" — watch markers propagate through the system
5. Remove a broker — watch topic reassignment happen
6. Add it back — system rebalances

Convert to a GIF for the README. This 30-second GIF will sell the project more than any bullet point.

### 6.3 — Architecture Diagram

Create a clean Mermaid diagram for the README:

```mermaid
graph TB
    subgraph Dashboard
        UI[React Dashboard]
    end

    subgraph Control Plane
        API[FastAPI Orchestrator]
        DK[Docker SDK]
    end

    subgraph Pub-Sub Core
        BS[Bootstrap Server]
        B1[Broker 1]
        B2[Broker 2]
        B3[Broker 3]
        P1[Publisher 1]
        P2[Publisher 2]
        S1[Subscriber 1]
        S2[Subscriber 2]
    end

    UI <-->|WebSocket| API
    API --> DK
    DK -->|Manage Containers| BS
    DK -->|Manage Containers| B1
    DK -->|Manage Containers| B2
    DK -->|Manage Containers| B3

    P1 --> B1
    P2 --> B2
    B1 <-->|Peer Gossip| B2
    B2 <-->|Peer Gossip| B3
    B1 <-->|Peer Gossip| B3
    B1 --> S1
    B3 --> S2

    BS -.->|Discovery| B1
    BS -.->|Discovery| B2
    BS -.->|Discovery| B3
```

**Deliverable:** A polished GitHub repository with a README that serves as both documentation and a portfolio piece. Architecture diagrams, performance charts, demo GIF, and design decision explanations.

---

## Resume Bullets (Final Form)

After completing all phases, here's how the pub-sub project should read on your resume:

**Project Title:** Distributed Pub-Sub Messaging System | Python, FastAPI, Docker, React, D3.js, WebSocket

**Bullet 1 — Core System (distributed systems fundamentals):**
Designed multi-broker pub-sub messaging system with TCP socket communication, topic-based routing, and centralized service discovery, sustaining X,XXX messages/second across dynamically scaled broker clusters.

**Bullet 2 — Chandy-Lamport (the technical crown jewel):**
Implemented Chandy-Lamport distributed snapshot algorithm for consistent global state capture across broker processes, with real-time visualization of marker propagation over FIFO-ordered channels.

**Bullet 3 — Reliability & Fault Tolerance:**
Built fault-tolerant message delivery with write-ahead logging, acknowledgment protocols, and heartbeat-based failure detection with automatic topic reassignment, achieving zero message loss under broker failures.

**Bullet 4 — Orchestration & Observability:**
Developed Docker-based orchestration control plane with FastAPI, enabling dynamic container lifecycle management and real-time topology visualization via WebSocket-driven React dashboard.

Pick 2-3 of these depending on the role. For infrastructure roles, lead with bullets 1 and 2. For backend roles, lead with 1 and 3. For full-stack roles, lead with 1 and 4.

---

## Timeline Summary

| Phase                     | Days  | What You Build                                               | Key Signal                 |
| ------------------------- | ----- | ------------------------------------------------------------ | -------------------------- |
| **0: Foundation**         | 1–3   | Clean CLI args, JSON logging, /status endpoints, test script | Code quality               |
| **1: Containerization**   | 4–7   | Dockerfiles, docker-compose, one-command startup             | DevOps / containers        |
| **2: Orchestration API**  | 8–14  | FastAPI control plane, Docker SDK, WebSocket events          | Backend engineering        |
| **3: Frontend Dashboard** | 15–25 | React + D3.js live topology, message flow, snapshot viz      | Full-stack / observability |
| **4: Benchmarking**       | 26–30 | Throughput/latency tests, performance charts                 | Quantitative engineering   |
| **5: Hardening**          | 31–40 | Heartbeats, WAL, fault tolerance, message ordering           | Production thinking        |
| **6: Documentation**      | 41–45 | README, architecture docs, demo GIF, design decisions        | Communication              |

**Total estimated time: 6–7 weeks** working part-time alongside coursework.

**If you only have 2–3 weeks,** prioritize Phases 0, 1, 2, and 6. The containerization + orchestration API + good documentation alone will transform how this project reads. The dashboard (Phase 3) is the flashiest piece, but the orchestration API is what actually proves backend engineering skill.

---

## What This Project Becomes

When you're done, you don't have a class project. You have a **distributed systems platform** that demonstrates:

- You can design and implement distributed algorithms (Chandy-Lamport)
- You can build reliable, fault-tolerant systems (acknowledgments, WAL, heartbeats)
- You can containerize and orchestrate distributed services (Docker, control plane API)
- You can build observability tooling (real-time dashboard, metrics, event streaming)
- You can benchmark and quantify system performance (throughput, latency, scaling curves)
- You can communicate technical decisions clearly (README, architecture docs)

That's not a resume line. That's a conversation starter that could carry an entire 45-minute technical interview.
