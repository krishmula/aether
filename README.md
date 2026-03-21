# aether

Distributed publish-subscribe system with gossip protocol and Chandy-Lamport distributed snapshots for fault tolerance.

## Project Structure

```
aether/
├── aether/                     # Main package
│   ├── core/                   # Data types & in-process aether
│   │   ├── uint8.py            # UInt8 constrained integer
│   │   ├── message.py          # Message with UInt8 payload
│   │   ├── payload_range.py    # PayloadRange & partitioning
│   │   ├── subscriber.py       # Local subscriber (counts array)
│   │   ├── broker.py           # In-memory message router
│   │   └── publisher.py        # Local publisher
│   ├── network/                # TCP networking
│   │   ├── node.py             # NodeAddress, NetworkNode, TCP layer
│   │   ├── publisher.py        # NetworkPublisher (gossip-aware)
│   │   └── subscriber.py       # NetworkSubscriber (remote)
│   ├── gossip/                 # Gossip protocol
│   │   ├── protocol.py         # Protocol dataclasses
│   │   ├── broker.py           # GossipBroker (full distributed broker)
│   │   └── bootstrap.py        # Bootstrap peer discovery server
│   ├── snapshot.py             # Chandy-Lamport snapshot dataclasses
│   ├── config.py               # YAML config loader
│   ├── utils/
│   │   └── log.py              # Colored terminal logging
│   └── cli/                    # CLI entry points
│       ├── admin.py             # Local single-process mode
│       ├── distributed_admin.py # All-in-one distributed mode
│       ├── run_bootstrap.py     # Standalone bootstrap server
│       ├── run_broker.py        # Standalone broker process
│       ├── run_publishers.py    # Standalone publisher launcher
│       └── run_subscribers.py   # Standalone subscriber launcher
├── tests/
│   ├── unit/                   # Fast unit tests
│   └── integration/            # Network integration tests
├── docs/                       # Documentation
│   ├── architecture.md         # System architecture & flows
│   ├── instructions.md         # Setup & usage guide
│   └── TODO.md                 # Feature backlog
├── config.yaml                 # Deployment configuration
└── pyproject.toml              # Package configuration
```

## Quick Start

```bash
# Install in development mode
pip install -e ".[dev]"

# Run local mode (single-process, no networking)
aether-admin 4 --publish-interval 0.05 --duration 2 --seed 123

# Run distributed mode (all-in-one on localhost)
aether-distributed 3 2 2 --publish-interval 0.5 --duration 10 --seed 42
```

## Requirements

- Python 3.13+
- PyYAML

## Running Tests

```bash
# Unit tests
pytest tests/unit/

# Integration tests (standalone scripts)
python tests/integration/test_snapshot_full.py
```

## CLI Commands

| Command | Description |
|---|---|
| `aether-admin` | Single-process local mode |
| `aether-distributed` | All-in-one distributed mode on localhost |
| `aether-bootstrap` | Standalone bootstrap server |
| `aether-broker` | Standalone broker process |
| `aether-publishers` | Standalone publisher launcher |
| `aether-subscribers` | Standalone subscriber launcher |

See [docs/instructions.md](docs/instructions.md) for detailed usage and [docs/architecture.md](docs/architecture.md) for system design.
