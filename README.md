# Aether

A distributed publish-subscribe messaging system built from scratch in Python. Features gossip-based message propagation, Chandy-Lamport distributed snapshots for fault tolerance, and a FastAPI orchestration control plane with Docker container management.

```
Publisher ──→ Broker ──gossip──→ Broker ──→ Subscriber
                │                   │
                └──── snapshot ──────┘
                     (Chandy-Lamport)
```

## Highlights

- **Gossip Protocol** — Messages propagate through the broker mesh via randomized gossip with configurable fanout and TTL-bounded relay. UUID-based deduplication prevents infinite loops.
- **Chandy-Lamport Snapshots** — Consistent global state capture across all brokers. Snapshots are replicated to peers and used for automatic broker recovery after failures.
- **Heartbeat Failure Detection** — Brokers exchange periodic heartbeats. Dead peers are detected within 15s and their subscribers are notified for failover.
- **Dynamic Orchestration** — FastAPI control plane creates and destroys broker/publisher/subscriber containers on the fly via Docker SDK. Real-time event stream over WebSocket.
- **Observable** — Every component (broker, publisher, subscriber, bootstrap) exposes a live JSON `/status` endpoint over HTTP.
- **Flexible Deployment** — Run everything in a single process, distributed on localhost, across multiple machines via config, or fully containerized with Docker Compose + orchestrator.

## Quick Start

### Local Mode (single process, no networking)

```bash
pip install -e ".[dev]"
aether-admin 4 --publish-interval 0.05 --duration 2 --seed 123
```

### Distributed Mode (all-in-one on localhost)

```bash
aether-distributed 3 2 2 --publish-interval 0.5 --duration 10 --seed 42
#                  │ │ │
#                  │ │ └── 2 publishers
#                  │ └──── 2 subscribers per broker
#                  └────── 3 brokers
```

### Docker Mode (orchestrated containers)

```bash
docker build -t aether:latest .
docker compose up -d          # starts bootstrap + orchestrator

# Seed a default topology (3 brokers, 2 publishers, 3 subscribers)
curl -X POST http://localhost:9000/api/seed

# Or build your own topology
curl -X POST http://localhost:9000/api/brokers
curl -X POST http://localhost:9000/api/publishers -H 'Content-Type: application/json' -d '{"interval": 0.5}'
curl -X POST http://localhost:9000/api/subscribers -H 'Content-Type: application/json' -d '{"broker_id": 0}'

# View system state
curl http://localhost:9000/api/state
curl http://localhost:9000/api/state/topology
curl http://localhost:9000/api/metrics
```

## Architecture

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│  Publisher   │     │  Publisher   │     │  Publisher   │
└──────┬──────┘     └──────┬──────┘     └──────┬──────┘
       │                   │                   │
       ▼                   ▼                   ▼
┌─────────────┐◄──gossip──►┌─────────────┐◄──gossip──►┌─────────────┐
│   Broker 0  │            │   Broker 1  │            │   Broker 2  │
│             │◄──heart────►│             │◄──heart────►│             │
│  snapshot   │◄──beat─────►│  snapshot   │◄──beat─────►│  snapshot   │
└──────┬──────┘            └──────┬──────┘            └──────┬──────┘
       │         ▲                │                          │
       ▼         │                ▼                          ▼
┌───────────┐    │         ┌───────────┐             ┌───────────┐
│Subscriber │    │         │Subscriber │             │Subscriber │
└───────────┘    │         └───────────┘             └───────────┘
                 │
          ┌──────┴──────┐
          │  Bootstrap  │  (peer discovery)
          └─────────────┘
```

### Component Model

Each component is an independent process with its own TCP socket and HTTP status endpoint, mapping 1:1 to Docker containers:

| Component | Role | Status Port |
|---|---|---|
| **Bootstrap** | Peer discovery — brokers register here, receive membership updates | `port + 10000` |
| **Broker** | Message routing, gossip relay, snapshot coordination, failure detection | `port + 10000` |
| **Publisher** | Generates messages, sends to N random brokers (configurable redundancy) | `port + 10000` |
| **Subscriber** | Receives messages matching its payload range, maintains counts array | `port + 10000` |
| **Orchestrator** | FastAPI control plane for dynamic container lifecycle management | `9000` |

### Message Flow

1. **Publisher** creates a `GossipMessage` with UUID, TTL=5, and sends to N random brokers
2. **Broker** deduplicates by UUID, delivers to matching local subscribers, gossips to random peers (fanout=2, TTL decremented)
3. **Subscriber** receives `PayloadMessageDelivery`, increments `counts[payload]` in its 256-element array

### Fault Tolerance

1. **Snapshot** — Leader broker (lowest address) initiates Chandy-Lamport. All brokers record state and close channels on marker receipt.
2. **Replication** — Each broker replicates its snapshot to k=2 random peers.
3. **Recovery** — Replacement broker requests snapshot from peers, restores subscriber registrations and message state, notifies subscribers to reconnect.

## Orchestrator API

The FastAPI orchestrator (`localhost:9000`) provides dynamic topology management:

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/api/brokers` | Create a new broker container |
| `DELETE` | `/api/brokers/{id}` | Stop and remove a broker |
| `POST` | `/api/publishers` | Create a new publisher |
| `DELETE` | `/api/publishers/{id}` | Stop and remove a publisher |
| `POST` | `/api/subscribers` | Create a new subscriber |
| `DELETE` | `/api/subscribers/{id}` | Stop and remove a subscriber |
| `GET` | `/api/state` | Full system state (all components) |
| `GET` | `/api/state/topology` | Node/edge graph for visualization |
| `GET` | `/api/metrics` | Aggregated metrics from all brokers |
| `POST` | `/api/seed` | Idempotent seed: creates 3 brokers, 2 publishers, 3 subscribers |
| `WS` | `/ws/events` | Real-time event stream (component added/removed, etc.) |

## CLI Reference

| Command | Description |
|---|---|
| `aether-admin` | Single-process local mode (all in-memory) |
| `aether-distributed` | All-in-one distributed mode on localhost |
| `aether-bootstrap` | Standalone bootstrap server |
| `aether-broker` | Standalone broker process |
| `aether-publisher` | Standalone publisher process |
| `aether-subscriber` | Standalone subscriber process |

Each standalone command accepts `--host`, `--port`, and `--status-port` for deployment flexibility.

## Configuration

Aether uses YAML configuration for multi-machine deployments. Set `AETHER_CONFIG` to override the default path.

```yaml
bootstrap:
  host: "100.x.x.10"    # Tailscale / LAN IP
  port: 7000

brokers:
  - id: 1
    host: "100.x.x.34"
    port: 8000

gossip:
  fanout: 2
  ttl: 5
  heartbeat_interval: 5.0
  heartbeat_timeout: 15.0

snapshot:
  interval: 30.0
```

## Project Structure

```
aether/
├── core/                   # In-process data types & routing
│   ├── uint8.py            # UInt8 constrained integer (0-255)
│   ├── message.py          # Immutable message with UInt8 payload
│   ├── payload_range.py    # Subscription ranges & partitioning
│   ├── subscriber.py       # Local subscriber (256-element counts array)
│   ├── broker.py           # In-memory message router
│   └── publisher.py        # Local publisher
├── network/                # TCP networking layer
│   ├── node.py             # NodeAddress, NetworkNode, connection mgmt
│   ├── publisher.py        # NetworkPublisher (gossip-aware)
│   └── subscriber.py       # NetworkSubscriber (remote delivery)
├── gossip/                 # Gossip protocol
│   ├── protocol.py         # All protocol message types
│   ├── broker.py           # GossipBroker (routing, gossip, heartbeat, snapshot)
│   ├── bootstrap.py        # Bootstrap peer discovery server
│   └── status.py           # HTTP /status endpoints
├── orchestrator/           # FastAPI control plane
│   ├── main.py             # REST + WebSocket endpoints
│   ├── models.py           # Pydantic request/response models
│   ├── docker_manager.py   # Docker SDK container lifecycle
│   ├── events.py           # Event types & broadcasting
│   └── settings.py         # Environment-based config
├── snapshot.py             # Chandy-Lamport snapshot types
├── config.py               # YAML config loader
├── cli/                    # CLI entry points
│   ├── admin.py            # Local single-process mode
│   ├── distributed_admin.py # All-in-one distributed mode
│   ├── run_bootstrap.py    # Standalone bootstrap
│   ├── run_broker.py       # Standalone broker
│   ├── run_publishers.py   # Standalone publisher
│   └── run_subscribers.py  # Standalone subscriber
└── utils/
    └── log.py              # Structured logging with correlation IDs
tests/
├── unit/                   # 15 unit tests (status endpoints)
└── integration/            # TCP, heartbeat, snapshot, recovery, e2e
docs/
├── architecture.md         # Detailed system design & message flows
├── instructions.md         # Setup & usage guide
├── roadmap.md              # Phase-by-phase development plan
└── TODO.md                 # Feature backlog
```

## Requirements

- Python 3.13+
- Docker (for containerized mode)

## Running Tests

```bash
# Unit tests
pytest tests/unit/

# Integration tests
python tests/integration/test_snapshot_full.py   # end-to-end snapshot & recovery
python tests/integration/test_heartbeat.py       # failure detection
python tests/integration/test_e2e.py             # full system subprocess test
```

## Documentation

- [Architecture & Flows](docs/architecture.md) — Detailed system design, threading model, protocol sequences
- [Setup & Usage Guide](docs/instructions.md) — Installation, deployment modes, configuration
- [Development Roadmap](docs/roadmap.md) — Phase-by-phase plan and progress
