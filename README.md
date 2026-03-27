# Aether

A distributed publish-subscribe messaging system built from scratch in Python. Aether implements gossip-based message propagation across a broker mesh, Chandy-Lamport distributed snapshots for fault-tolerant state capture, heartbeat-based failure detection with automatic broker recovery, and a FastAPI orchestration control plane that creates and destroys Docker containers on the fly — all wrapped in a real-time React dashboard.

```
Publisher ──→ Broker ←─ gossip ─→ Broker ──→ Subscriber
                │                    │
                └──── Chandy-Lamport snapshot ────┘
                         (replicated to k peers)
```

**No external message queue. No Kafka. No Redis. Built from TCP sockets up.**

---

## What Makes This Interesting

- **Gossip Protocol** — Messages propagate through the broker mesh via randomized gossip with configurable fanout and TTL. UUID deduplication prevents loops without a central coordinator.
- **Chandy-Lamport Snapshots** — Consistent global state capture across all brokers. The classic distributed systems algorithm, running for real, every 30 seconds. Snapshots are replicated to k=2 peers.
- **Automatic Broker Recovery** — When a broker dies, its replacement requests the last snapshot from peers, restores subscriber registrations and message state, and broadcasts a recovery notification so clients reconnect transparently.
- **Heartbeat Failure Detection** — Brokers exchange heartbeats every 5 seconds. Dead peers are detected within 15 seconds without any external health-check service.
- **Dynamic Orchestration** — A FastAPI control plane manages broker/publisher/subscriber containers via the Docker SDK. Spin up or tear down any component with a single API call while the system keeps running.
- **Fully Observable** — Every component (broker, publisher, subscriber, bootstrap) exposes a live JSON `/status` endpoint over HTTP using only the Python standard library.
- **Three Deployment Modes** — Single process in-memory, distributed across localhost, or fully containerized with Docker Compose and dynamic container management.

---

## Prerequisites

| Requirement | Version | Notes |
|---|---|---|
| Python | 3.13+ | Required for the local and distributed modes |
| Docker | 24+ | Required for the containerized mode |
| Docker Compose | v2 (`docker compose`) | Included with Docker Desktop |
| `make` | any | Optional but recommended; all commands are documented below |

Check your versions:

```bash
python --version   # need 3.13+
docker --version
docker compose version
```

---

## Quickest Path: Docker Demo

The fastest way to see Aether running with a full topology and live dashboard:

```bash
git clone https://github.com/krishmula/aether.git
cd aether
make demo
```

This builds both Docker images (Python backend + React dashboard), starts bootstrap, orchestrator, and dashboard containers, waits for them to become healthy, then seeds a default topology of **3 brokers, 2 publishers, and 3 subscribers** — all as dynamically managed containers.

Once ready:

| Service | URL | What you'll find |
|---|---|---|
| **Dashboard** | http://localhost:3000 | Live topology graph, metrics, component controls |
| **API Docs** | http://localhost:9000/docs | Interactive Swagger UI for the orchestrator |
| **System State** | http://localhost:9000/api/state | Full JSON state of every component |
| **Bootstrap Status** | http://localhost:17100/status | Registered brokers and uptime |

When you're done:

```bash
make clean   # stops and removes all containers (compose-managed + dynamic)
```

---

## All Setup Modes

### Mode 1 — Local (single process, no networking)

Everything runs in memory within a single Python process. No sockets, no Docker. Great for testing the core logic.

```bash
pip install -e ".[dev]"
aether-admin 4 --publish-interval 0.05 --duration 2 --seed 123
```

**Arguments:**
- `4` — number of subscribers (payload space is partitioned evenly across them)
- `--publish-interval 0.05` — seconds between published messages
- `--duration 2` — how long to run (seconds)
- `--seed 123` — deterministic random seed for reproducibility

**Expected output:** Message counts per subscriber showing the payload distribution across the 256-value space.

---

### Mode 2 — Distributed (all-in-one on localhost, real TCP)

All components run as separate threads with real TCP sockets on localhost. Tests the full network stack — gossip propagation, heartbeats, snapshot protocol — without Docker.

```bash
pip install -e ".[dev]"
aether-distributed 3 2 2 --publish-interval 0.5 --duration 10 --seed 42
#                  │ │ │
#                  │ │ └── 2 publishers
#                  │ └──── 2 subscribers per broker
#                  └────── 3 brokers
```

The bootstrap server starts first, brokers register and form the mesh, publishers connect and begin gossip propagation, and subscribers receive delivery notifications. Chandy-Lamport snapshots fire every 30 seconds (configurable).

Or use the Makefile shortcut:

```bash
make distributed-demo
```

---

### Mode 3 — Docker (fully containerized, orchestrated)

The full production-style setup. Bootstrap and orchestrator are compose-managed; all pub/sub components are created dynamically by the orchestrator via the Docker SDK (Docker-out-of-Docker).

#### Step 1 — Build

```bash
make build
# or manually:
docker compose build
```

This builds two images:
- `aether:latest` — Python 3.13-slim with all backend dependencies
- `aether-dashboard:latest` — Node build stage → nginx serve

#### Step 2 — Start infrastructure

```bash
make up
# or:
docker compose up -d
```

This starts three containers with health checks:
1. **aether-bootstrap** — peer discovery server, waits for `/status` to respond
2. **aether-orchestrator** — FastAPI control plane, waits for bootstrap to be healthy
3. **aether-dashboard** — nginx serving the React app, waits for orchestrator to be healthy

Check everything is healthy:

```bash
make ps
# or:
docker compose ps
```

#### Step 3 — Create topology

Seed the default topology (3 brokers, 2 publishers, 3 subscribers):

```bash
curl -X POST http://localhost:9000/api/seed
```

Or build your own manually:

```bash
# Add brokers
curl -X POST http://localhost:9000/api/brokers
curl -X POST http://localhost:9000/api/brokers
curl -X POST http://localhost:9000/api/brokers

# Add publishers
curl -X POST http://localhost:9000/api/publishers \
  -H 'Content-Type: application/json' \
  -d '{"interval": 0.5}'

# Add a subscriber attached to broker 0
curl -X POST http://localhost:9000/api/subscribers \
  -H 'Content-Type: application/json' \
  -d '{"broker_id": 0}'
```

#### Step 4 — Explore

```bash
# Full system state
curl http://localhost:9000/api/state | python3 -m json.tool

# Graph topology (nodes + edges for visualization)
curl http://localhost:9000/api/state/topology | python3 -m json.tool

# Aggregated metrics
curl http://localhost:9000/api/metrics | python3 -m json.tool

# Live event stream (WebSocket)
# Connect to ws://localhost:9000/ws/events to receive real-time component events

# Dashboard
open http://localhost:3000

# Interactive API docs
open http://localhost:9000/docs
```

#### Remove components dynamically

```bash
# Remove a broker (its subscribers get notified for failover)
curl -X DELETE http://localhost:9000/api/brokers/0

# Remove a publisher
curl -X DELETE http://localhost:9000/api/publishers/0
```

#### Tear down

```bash
make clean
# This stops both compose-managed services AND all dynamically created containers
```

---

### Mode 4 — Multi-machine (Tailscale / LAN)

For running across real physical or virtual machines. Set Tailscale or LAN IPs in `config.yaml`:

```yaml
bootstrap:
  host: "100.x.x.10"
  port: 7000

brokers:
  - id: 1
    host: "100.x.x.34"
    port: 8000
  - id: 2
    host: "100.x.x.35"
    port: 8000

gossip:
  fanout: 2             # relay to 2 random peers per hop
  ttl: 5                # discard after 5 hops
  heartbeat_interval: 5.0
  heartbeat_timeout: 15.0

snapshot:
  interval: 30.0
```

Point each process at the config:

```bash
export AETHER_CONFIG=/path/to/config.yaml
aether-bootstrap --host 100.x.x.10 --port 7000 --status-port 17000
aether-broker    --host 100.x.x.34 --port 8000 --status-port 18000 --id 1
```

---

## Architecture

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│  Publisher   │     │  Publisher   │     │  Publisher   │
└──────┬──────┘     └──────┬──────┘     └──────┬──────┘
       │                   │                   │
       ▼                   ▼                   ▼
┌─────────────┐◄──gossip──►┌─────────────┐◄──gossip──►┌─────────────┐
│   Broker 0  │            │   Broker 1  │            │   Broker 2  │
│             │◄──heartbeat►│             │◄──heartbeat►│             │
│  snapshot   │            │  snapshot   │            │  snapshot   │
└──────┬──────┘            └──────┬──────┘            └──────┬──────┘
       │                          │                          │
       ▼                          ▼                          ▼
┌───────────┐             ┌───────────┐             ┌───────────┐
│Subscriber │             │Subscriber │             │Subscriber │
└───────────┘             └───────────┘             └───────────┘

                    ┌─────────────┐
                    │  Bootstrap  │  (peer discovery)
                    └─────────────┘

                    ┌─────────────┐
                    │Orchestrator │  (FastAPI + Docker SDK)
                    └─────────────┘
```

### Components

| Component | Role | Default Ports |
|---|---|---|
| **Bootstrap** | Peer discovery — brokers register here, receive membership updates | TCP `7000`, HTTP `/status` on `17000` |
| **Broker** | Message routing, gossip relay, snapshot coordination, failure detection | TCP `8000+`, HTTP `/status` on `18000+` |
| **Publisher** | Generates `UInt8` messages, sends to N random brokers | TCP `9000+`, HTTP `/status` on `19000+` |
| **Subscriber** | Receives messages matching its `[low, high]` payload range | TCP `10000+`, HTTP `/status` on `20000+` |
| **Orchestrator** | FastAPI control plane — dynamic container lifecycle management | HTTP `9000` |
| **Dashboard** | React + D3 real-time visualization | HTTP `3000` |

Each component is an **independent process** with its own TCP socket and HTTP status endpoint, mapping 1:1 to a Docker container.

### Message Flow

1. **Publisher** creates a `GossipMessage` with a UUID, TTL=5, and random `UInt8` payload; sends to N random brokers.
2. **Broker** deduplicates by UUID, delivers to local subscribers whose `PayloadRange` includes the payload, gossips to random peers (fanout=2, TTL decremented).
3. **Subscriber** receives `PayloadMessageDelivery`, increments `counts[payload]` in its 256-element array. The payload space `[0, 255]` is partitioned evenly across all subscribers.

### Fault Tolerance

1. **Heartbeat** — Brokers exchange heartbeats every 5 seconds over TCP. No response within 15 seconds marks the peer as dead.
2. **Snapshot** — The leader broker (lowest TCP address) initiates a Chandy-Lamport consistent global snapshot every 30 seconds. All brokers close their incoming channels on marker receipt and record local state.
3. **Replication** — Each broker replicates its snapshot to k=2 random peers.
4. **Recovery** — A replacement broker requests the last snapshot from known peers, restores subscriber registrations and message state, sends `BrokerRecoveryNotification` so subscribers transparently reconnect.

---

## Dashboard

The React dashboard connects to the orchestrator's WebSocket and REST API to show the live system:

- **Topology Graph** — D3 force-directed graph of brokers, publishers, and subscribers with animated data flow edges
- **Metrics Panel** — Messages processed, active components, uptime, message rates
- **Control Panel** — Add and remove brokers, publishers, and subscribers without leaving the browser

Open http://localhost:3000 after `make demo` or `make up`.

---

## Orchestrator API

Full interactive docs at http://localhost:9000/docs.

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/api/brokers` | Create a new broker container |
| `DELETE` | `/api/brokers/{id}` | Stop and remove a broker |
| `POST` | `/api/publishers` | Create a new publisher (`{"interval": 0.5}`) |
| `DELETE` | `/api/publishers/{id}` | Stop and remove a publisher |
| `POST` | `/api/subscribers` | Create a new subscriber (`{"broker_id": 0}`) |
| `DELETE` | `/api/subscribers/{id}` | Stop and remove a subscriber |
| `GET` | `/api/state` | Full system state (all components, live status) |
| `GET` | `/api/state/topology` | Node/edge graph for visualization |
| `GET` | `/api/metrics` | Aggregated metrics across all brokers |
| `POST` | `/api/seed` | Idempotent seed: 3 brokers + 2 publishers + 3 subscribers |
| `WS` | `/ws/events` | Real-time event stream (component added, removed, state changes) |

---

## Component Status Endpoints

Every running component exposes a `/status` endpoint that returns live metrics. These use Python's built-in `ThreadingHTTPServer` — no framework required.

```bash
# Bootstrap
curl http://localhost:17100/status

# Broker (status port = TCP port + 10000)
curl http://localhost:18001/status

# Publisher
curl http://localhost:19001/status

# Subscriber
curl http://localhost:20001/status
```

Example broker status response:

```json
{
  "broker": "broker-1",
  "host": "broker-1",
  "port": 8001,
  "peers": ["broker-2", "broker-3"],
  "peer_count": 2,
  "subscribers": 1,
  "messages_processed": 4821,
  "seen_message_ids": 1203,
  "uptime_seconds": 142.3,
  "snapshot_state": "idle"
}
```

---

## Makefile Reference

```bash
make demo             # Build, start, seed 3+2+3 topology (fastest path)
make status           # Query system state via API
make logs             # Tail all container logs (Ctrl+C to stop)
make clean            # Stop all containers — compose + dynamic
make build            # Build Docker images
make build-dashboard  # Rebuild only the React dashboard image
make up               # Start compose services in background
make down             # Stop compose services (keeps dynamic containers)
make restart          # Restart compose services
make ps               # Show container status
make check-ports      # Verify ports 17100, 9000, 3000 are free
make install          # pip install -e ".[dev]"
make local-demo       # Run single-process local mode
make distributed-demo # Run all-in-one distributed mode
make test             # Run unit tests
make lint             # ruff + mypy
```

---

## CLI Reference

Each component can run as a standalone process for manual or multi-machine deployments:

| Command | Description |
|---|---|
| `aether-admin` | Single-process local mode (all in-memory, no networking) |
| `aether-distributed` | All-in-one distributed mode on localhost |
| `aether-bootstrap` | Standalone bootstrap server |
| `aether-broker` | Standalone broker process |
| `aether-publisher` | Standalone publisher process |
| `aether-subscriber` | Standalone subscriber process |

All standalone commands accept `--host`, `--port`, `--status-port`, and `--log-level`.

---

## Running Tests

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Unit tests (fast, no networking)
pytest tests/unit/ --tb=short

# Integration tests (real TCP sockets, ~30s each)
python tests/integration/test_tcp_basic.py          # NetworkNode connectivity
python tests/integration/test_heartbeat.py          # failure detection & timeout
python tests/integration/test_snapshot_full.py      # end-to-end snapshot & recovery
python tests/integration/test_e2e.py                # full system as subprocesses

# Linting
ruff check .
mypy aether/
```

The integration tests spawn real processes and exercise the full network stack — gossip propagation, Chandy-Lamport markers, snapshot replication, and broker recovery — without Docker.

---

## Project Structure

```
aether/
├── core/                   # Pure in-process data types
│   ├── uint8.py            # UInt8 constrained integer (0–255)
│   ├── message.py          # Immutable message with UInt8 payload
│   ├── payload_range.py    # PayloadRange & partition_payload_space()
│   ├── subscriber.py       # Local subscriber (256-element counts array)
│   ├── broker.py           # In-memory message router (256 buckets)
│   └── publisher.py        # Local publisher
├── network/                # TCP networking layer
│   ├── node.py             # NodeAddress, NetworkNode, persistent connections
│   ├── publisher.py        # NetworkPublisher (gossip-aware, sends to N brokers)
│   └── subscriber.py       # NetworkSubscriber (remote delivery over TCP)
├── gossip/                 # Gossip protocol implementation
│   ├── protocol.py         # All protocol message types (dataclasses + pickle)
│   ├── broker.py           # GossipBroker — routing, gossip, heartbeat, snapshot
│   ├── bootstrap.py        # Bootstrap peer discovery server
│   └── status.py           # HTTP /status server (stdlib ThreadingHTTPServer)
├── orchestrator/           # FastAPI control plane
│   ├── main.py             # REST endpoints + WebSocket event stream
│   ├── docker_manager.py   # Docker SDK — container lifecycle management
│   ├── models.py           # Pydantic request/response models
│   ├── events.py           # EventBroadcaster for real-time WebSocket push
│   └── settings.py         # Environment-based configuration
├── snapshot.py             # Chandy-Lamport snapshot types
├── config.py               # YAML config loader
└── cli/                    # CLI entry points (registered in pyproject.toml)
dashboard/
├── src/
│   ├── components/         # React components (Topology, Metrics, Controls)
│   ├── api/                # Fetch wrapper + TypeScript types
│   └── store/              # Zustand state management
├── Dockerfile              # Node build → nginx serve
└── nginx.conf              # Reverse proxy to orchestrator API
tests/
├── unit/                   # 15 tests — data types and status endpoints
└── integration/            # TCP, heartbeat, snapshot, recovery, e2e
docs/
├── architecture.md         # Detailed system design and message flows
├── roadmap.md              # Phase-by-phase development plan
└── instructions.md         # Extended setup and usage guide
```

---

## Tech Stack

**Backend:** Python 3.13, FastAPI, uvicorn, Pydantic, Docker SDK, standard library TCP/HTTP

**Frontend:** React 19, TypeScript, Vite, D3 (force-directed graphs), Zustand, Tailwind CSS

**Infrastructure:** Docker, Docker Compose, nginx (dashboard reverse proxy)

---

## Documentation

- [Architecture & Flows](docs/architecture.md) — Threading model, protocol sequences, gossip internals, snapshot algorithm walkthrough
- [Setup & Usage Guide](docs/instructions.md) — Extended installation, deployment modes, configuration reference
- [Development Roadmap](docs/roadmap.md) — Phase-by-phase plan and progress
