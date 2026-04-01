# Broker Failover & Recovery System

## Context

When a broker goes down, its subscribers become orphaned — they still reference the dead broker's address and receive no messages. The system currently detects peer failures via heartbeat timeouts but takes no recovery action. All Chandy-Lamport snapshot primitives exist (`recover_from_snapshot`, `_reconnect_subscribers`, `BrokerRecoveryNotification`), but nothing triggers them end-to-end.

This plan introduces production-grade broker failover with a **hybrid recovery strategy**: the system checks snapshot freshness and chooses the optimal recovery path automatically.

- **Fresh snapshot** — spin up a replacement broker, recover full state (subscriber mappings, dedup history, peer list)
- **Stale or missing snapshot** — redistribute orphaned subscribers across surviving brokers

Subscribers are **active participants** in recovery: they detect broker failure independently, query a central assignment API for their new broker, and reconnect autonomously. This is the same pull-based model used by Kafka consumers, LinkedIn's messaging infrastructure, and Stripe's event systems.

---

## Architecture Overview

```
                    DETECTION                         DECISION                        EXECUTION
              ┌──────────────────┐            ┌─────────────────────┐         ┌─────────────────────┐
              │                  │            │                     │         │                     │
  Orchestrator│  Poll broker     │  Broker    │  Query peers for    │  Fresh  │  Replacement Path   │
  Health      │  /status every   │  declared  │  dead broker's      │  snap?  │  - Spin up new      │
  Monitor     │  5s. 3 consec.   ├──dead────► │  snapshot replicas  ├──YES──► │    broker (same ID) │
              │  failures =      │            │  Check freshness    │         │  - POST /recover    │
              │  dead            │            │  vs threshold       │         │  - Update registry  │
              │                  │            │                     │         │                     │
              └──────────────────┘            └─────────┬───────────┘         └─────────────────────┘
                                                        │
              ┌──────────────────┐                      │ NO                 ┌─────────────────────┐
              │                  │                      │                    │                     │
  Subscriber  │  Ping broker     │                      └──────────────────► │  Redistribution     │
  Health      │  every 5s.       │                                           │  - Lookup orphaned  │
  Check       │  No pong for     │                                           │    subs from state  │
              │  3 attempts =    │                                           │  - Assign to least- │
              │  enter reconnect │                                           │    loaded brokers   │
              │                  │                                           │  - Update registry  │
              └───────┬──────────┘                                           │                     │
                      │                                                      └─────────────────────┘
                      │ reconnect
                      ▼                                                      ┌─────────────────────┐
              ┌──────────────────┐                                           │                     │
              │  Query           │                                           │  Subscriber         │
              │  GET /api/       │◄──────────── assignment ─────────────────►│  Reconnection       │
              │  assignment?     │              registry                     │  - Connect to new   │
              │  subscriber_id=X │              (source of                   │    broker           │
              │                  │               truth)                      │  - SubscribeRequest │
              │  Exponential     │                                           │  - Resume delivery  │
              │  backoff + jitter│                                           │                     │
              └──────────────────┘                                           └─────────────────────┘
```

---

## Design Principles

1. **Pull over push.** Subscribers query for their assignment rather than waiting for notifications. If a notification is missed, the subscriber retries naturally. No single coordination moment that must succeed.

2. **Orchestrator as assignment authority.** Single source of truth for "which subscriber belongs to which broker." All recovery paths converge here. Subscribers, brokers, and the dashboard all query the same registry.

3. **Snapshot freshness determines recovery quality.** Fresh snapshots enable full state recovery (dedup history, subscriber mappings, peer list). Stale snapshots trigger graceful degradation to redistribution. This makes snapshots genuinely load-bearing — their quality directly affects recovery quality.

4. **Grace period before recovery.** Three consecutive health check failures before declaring a broker dead. Prevents false positives from network blips, GC pauses, or container restarts. Avoids split-brain scenarios.

5. **Subscriber autonomy.** Subscribers detect failure independently and reconnect at their own pace. No thundering herd — exponential backoff with full jitter spreads reconnection attempts.

---

## Recovery Flows

### Path A: Fresh Snapshot — Replacement Broker

This is the premium recovery path. The dead broker's full state is restored on a new broker. Subscribers are seamlessly redirected. Message deduplication history is preserved.

```
Time
 │
 │  T+0s     Broker-2 crashes
 │
 │  T+5s     Orchestrator health check #1 fails
 │  T+10s    Orchestrator health check #2 fails
 │  T+15s    Orchestrator health check #3 fails → broker-2 declared DEAD
 │           ┌──────────────────────────────────────────────────┐
 │           │ Orchestrator queries surviving brokers:          │
 │           │   GET /snapshots/broker-2 → broker-1, broker-3  │
 │           │ Freshest snapshot: 8 seconds old (< threshold)  │
 │           │ Decision: REPLACEMENT PATH                      │
 │           └──────────────────────────────────────────────────┘
 │
 │  T+16s    Orchestrator removes dead broker-2 container
 │           Orchestrator creates replacement broker-2 (same ID, same hostname)
 │
 │  T+18s    New broker-2 starts, registers with bootstrap, discovers peers
 │           Orchestrator polls new broker-2 /status until healthy
 │
 │  T+20s    Orchestrator sends POST /recover to new broker-2:
 │             { "dead_broker_host": "broker-2", "dead_broker_port": 8000 }
 │
 │           New broker-2 executes:
 │             1. request_snapshot_from_peers(dead_broker_addr)
 │             2. recover_from_snapshot(snapshot)
 │                - Restores _remote_subscribers mapping
 │                - Rebuilds _payload_to_remotes index
 │                - Restores peer_brokers
 │                - Restores seen_message dedup state
 │             3. _reconnect_subscribers(old_broker_addr)
 │                - Sends BrokerRecoveryNotification to all subscribers (fast-path)
 │
 │  T+21s    Orchestrator updates assignment registry:
 │             subscriber-1 → broker-2 (unchanged, same hostname)
 │
 │  T+15-25s Subscribers independently detect broker-2 was dead:
 │             - Ping timeout after 3 attempts
 │             - Enter reconnection state
 │             - Query GET /api/assignment?subscriber_id=X
 │             - Receive: { broker_host: "broker-2", broker_port: 8000 }
 │             - Connect, send SubscribeRequest, resume
 │
 │  T+25s    Recovery complete. Message delivery resumes.
 │
 ▼
```

**Why same broker_id:** The replacement gets hostname `broker-2`, so Docker DNS resolves to the new container. The assignment registry entry stays the same. Subscribers that received the fast-path `BrokerRecoveryNotification` already reconnected. Subscribers that missed it query the assignment API and get the same answer.

### Path B: Stale/Missing Snapshot — Redistribution

When snapshots are stale or missing, the system can't reliably restore the dead broker's state. Instead, it redistributes orphaned subscribers across surviving brokers. This is a degraded but functional recovery — dedup state is lost, but subscribers resume receiving messages.

```
Time
 │
 │  T+0s     Broker-2 crashes
 │
 │  T+15s    Broker-2 declared DEAD (3 consecutive failures)
 │           ┌──────────────────────────────────────────────────┐
 │           │ Orchestrator queries surviving brokers:          │
 │           │   GET /snapshots/broker-2 → broker-1, broker-3  │
 │           │ Freshest snapshot: 90 seconds old (> threshold)  │
 │           │ Decision: REDISTRIBUTION PATH                   │
 │           └──────────────────────────────────────────────────┘
 │
 │  T+16s    Orchestrator looks up orphaned subscribers from _components:
 │             subscriber-3 (range 60-90) → was on broker-2
 │             subscriber-4 (range 91-120) → was on broker-2
 │
 │           Orchestrator assigns to least-loaded surviving brokers:
 │             broker-1 has 2 subscribers, broker-3 has 1 subscriber
 │             subscriber-3 → broker-3 (now has 2)
 │             subscriber-4 → broker-1 (now has 3)
 │
 │           Orchestrator updates assignment registry:
 │             subscriber-3 → broker-3
 │             subscriber-4 → broker-1
 │
 │           Orchestrator removes dead broker-2 container
 │
 │  T+15-25s Subscribers detect broker-2 is dead:
 │             - Ping timeout
 │             - Query GET /api/assignment?subscriber_id=3
 │             - Receive: { broker_host: "broker-3", broker_port: 8000 }
 │             - Connect to broker-3, send SubscribeRequest, resume
 │
 │  T+25s    Recovery complete. All subscribers redistributed.
 │
 ▼
```

**What's lost in redistribution:** Message deduplication state. Subscribers may receive duplicate messages for a brief window. The broker's peer list is rebuilt naturally through bootstrap discovery. This is acceptable — the system prioritizes availability over exactly-once delivery during failure recovery.

---

## Component Changes

### 1. Subscriber Health Monitoring

**Files:** `aether/network/subscriber.py`, `aether/snapshot.py`

Subscribers currently have no way to detect their broker is dead. Add a `Ping`/`Pong` health check protocol.

**New message types** in `aether/snapshot.py`:

```python
@dataclass
class Ping:
    sender: NodeAddress
    sequence: int

@dataclass
class Pong:
    sender: NodeAddress
    sequence: int
```

**Subscriber changes** in `aether/network/subscriber.py`:

- Add configurable `broker_ping_interval` (default 5s) and `broker_ping_timeout` (default 15s — 3 missed pings)
- Add `_broker_health_loop` background thread:
  - Send `Ping` to `self.broker` every `broker_ping_interval`
  - Track last `Pong` received
  - After `broker_ping_timeout` with no `Pong` → set `self._broker_alive = False`
- Add `_reconnection_loop` background thread:
  - Activated when `_broker_alive` becomes `False`
  - Query `GET {orchestrator_url}/api/assignment?subscriber_id={self.subscriber_id}`
  - Exponential backoff with full jitter: `delay = random(0, min(cap, base * 2^attempt))`
    - Base: 1s, cap: 30s
  - On success: update `self.broker`, send fresh `SubscribeRequest`, set `_broker_alive = True`
- Handle `Pong` in `_receive_loop`
- Accept `orchestrator_url` and `subscriber_id` in constructor

**Broker changes** in `aether/gossip/broker.py`:

- Handle incoming `Ping` in `_receive_loop` → respond with `Pong`

### 2. Orchestrator Health Monitoring

**Files:** `aether/orchestrator/main.py`, `aether/orchestrator/health.py` (new)

The orchestrator needs its own view of broker health, independent of peer gossip.

**New file** `aether/orchestrator/health.py`:

```python
class HealthMonitor:
    """Periodically polls broker /status endpoints.
    Declares broker dead after N consecutive failures."""

    def __init__(self, docker_mgr, on_broker_dead_callback, check_interval=5.0, failure_threshold=3):
        self._docker_mgr = docker_mgr
        self._on_broker_dead = on_broker_dead_callback
        self._check_interval = check_interval          # seconds between checks
        self._failure_threshold = failure_threshold      # consecutive failures before dead
        self._failure_counts: dict[int, int] = {}        # broker_id → consecutive failures
        self._running = False
```

- `async _monitor_loop()`: runs as asyncio background task
  - Every `check_interval`, iterate over all broker components in `_components`
  - HTTP GET each broker's `/status` endpoint (via `host_status_port`)
  - On success: reset failure count to 0
  - On failure: increment failure count
  - If failure count >= `failure_threshold`: invoke `on_broker_dead_callback(broker_id)`
  - Remove broker from monitoring after declaring dead (prevent duplicate callbacks)

**Wire up in `main.py`:**

- Create `HealthMonitor` instance at startup
- Start monitoring loop as background task via `@app.on_event("startup")`
- Callback triggers recovery manager

### 3. Snapshot Freshness API

**Files:** `aether/gossip/status.py`, `aether/gossip/broker.py`

The orchestrator needs to query surviving brokers for their stored snapshot replicas of the dead broker.

**Extend `/status` response** or add **`GET /snapshots/{broker_host}/{broker_port}`** to the broker's HTTP status server:

Response:

```json
{
  "broker_address": ["broker-2", 8000],
  "snapshot_id": "abc123",
  "timestamp": 1711612800.0,
  "age_seconds": 8.3,
  "subscriber_count": 4,
  "peer_count": 2
}
```

Returns `404` if no snapshot stored for that broker.

**Broker changes:** expose `_peer_snapshots` data through the status handler (read-only, under lock).

### 4. Recovery Manager

**Files:** `aether/orchestrator/recovery.py` (new), `aether/orchestrator/models.py`

Central recovery orchestration logic. Receives dead broker notifications from `HealthMonitor`, decides recovery strategy, executes it.

**New file** `aether/orchestrator/recovery.py`:

```python
class RecoveryManager:
    def __init__(self, docker_mgr, broadcaster, snapshot_max_age=30.0):
        self._docker_mgr = docker_mgr
        self._broadcaster = broadcaster
        self._snapshot_max_age = snapshot_max_age      # configurable freshness threshold
        self._active_recoveries: dict[int, float] = {} # broker_id → start_time (debounce)
        self._assignments: dict[int, AssignmentInfo] = {} # subscriber_id → broker assignment
```

**Key methods:**

`async handle_broker_death(broker_id: int)`:

1. Debounce: if `broker_id` in `_active_recoveries` and started < 60s ago, skip
2. Mark recovery in progress
3. Emit `BROKER_FAILURE_DETECTED` event
4. Query surviving brokers for dead broker's snapshot (parallel HTTP requests)
5. Evaluate freshness of best snapshot vs `_snapshot_max_age`
6. If fresh → `_execute_replacement_recovery(broker_id, snapshot_info)`
7. If stale/missing → `_execute_redistribution_recovery(broker_id)`
8. Clean up `_active_recoveries` after completion

`async _execute_replacement_recovery(broker_id, snapshot_info)`:

1. Remove dead broker container (try/except — may already be gone)
2. Create replacement broker with same `broker_id` (same hostname/ports)
3. Wait for new broker to be healthy (poll `/status`, exponential backoff, 15s timeout)
4. `POST /recover` to new broker with dead broker's address
5. Poll `/status` until `recovery_state == "complete"` (30s timeout)
6. Update assignment registry (subscribers stay mapped to same broker)
7. Emit `BROKER_RECOVERY_COMPLETE` event

`async _execute_redistribution_recovery(broker_id)`:

1. Look up orphaned subscribers from `_docker_mgr._components` (filter by `broker_id`)
2. Get list of surviving healthy brokers
3. Assign each orphaned subscriber to the least-loaded broker (by subscriber count)
4. Update assignment registry with new mappings
5. Remove dead broker container
6. Emit `BROKER_RECOVERY_COMPLETE` event (with `strategy: "redistribution"`)

### 5. Assignment Registry & API

**Files:** `aether/orchestrator/main.py`, `aether/orchestrator/recovery.py`

The assignment registry is the source of truth for "which subscriber belongs to which broker." Subscribers query it during reconnection.

**Registry lives in `RecoveryManager`:** `_assignments: dict[int, AssignmentInfo]`

```python
@dataclass
class AssignmentInfo:
    subscriber_id: int
    broker_host: str
    broker_port: int
    assigned_at: float
    reason: str          # "initial", "replacement_recovery", "redistribution"
```

**Initialization:** When the orchestrator creates a subscriber via `POST /api/subscribers`, it also registers the assignment in the registry.

**API endpoint** in `main.py`:

```
GET /api/assignment?subscriber_id=5

Response:
{
  "subscriber_id": 5,
  "broker_host": "broker-1",
  "broker_port": 8000,
  "assigned_at": 1711612800.0,
  "reason": "redistribution"
}
```

Returns `404` if subscriber not found (subscriber should retry with backoff).

**Why not use `_components` directly:** The `_components` dict tracks container metadata. The assignment registry tracks the _desired_ broker mapping, which can change during recovery before any container is restarted. Separation of concerns.

### 6. Broker Recovery Endpoint

**Files:** `aether/gossip/status.py`, `aether/gossip/broker.py`

The orchestrator needs to tell a replacement broker to recover from a dead broker's snapshot.

**Add `POST /recover` to broker status server** (`status.py`):

- Parse JSON body: `{ "dead_broker_host": str, "dead_broker_port": int }`
- Validate no recovery already in progress
- Run recovery in background thread:
  1. Set `_recovery_state = "recovering"`
  2. `request_snapshot_from_peers(dead_broker_addr, timeout=10.0)`
  3. If snapshot found: `recover_from_snapshot(snapshot)` → `_recovery_state = "complete"`
  4. If not found: `_recovery_state = "failed"`
- Respond `200 { "status": "recovery_initiated" }`

**Add `_recovery_state` field to `GossipBroker`:** `"none"` | `"recovering"` | `"complete"` | `"failed"`

**Include `recovery_state` in `/status` response** so the orchestrator can poll for completion.

### 7. Configuration

**File:** `aether/config.py`, `config.docker.yaml`

All thresholds should be configurable:

```yaml
orchestrator:
  url: "http://orchestrator:8080" # for subscribers to query assignment API

health:
  check_interval: 5.0 # orchestrator polls brokers every N seconds
  failure_threshold: 3 # consecutive failures before declaring dead

recovery:
  snapshot_max_age: 30.0 # seconds — snapshots older than this trigger redistribution
  replacement_health_timeout: 15.0 # seconds to wait for replacement broker to become healthy
  recovery_completion_timeout: 30.0 # seconds to wait for snapshot recovery to complete
  debounce_window: 60.0 # seconds — ignore duplicate failure reports within window

subscriber:
  broker_ping_interval: 5.0 # subscriber pings broker every N seconds
  broker_ping_timeout: 15.0 # declare broker dead after N seconds with no pong
  reconnect_backoff_base: 1.0 # initial reconnection delay (seconds)
  reconnect_backoff_cap: 30.0 # maximum reconnection delay (seconds)
```

### 8. Event Types for Dashboard

**File:** `aether/orchestrator/models.py`

New `EventType` values emitted via the WebSocket event stream (`/ws/events`):

```python
BROKER_FAILURE_DETECTED = "broker_failure_detected"    # broker declared dead
BROKER_RECOVERY_STARTED = "broker_recovery_started"    # replacement or redistribution begun
BROKER_RECOVERY_COMPLETE = "broker_recovery_complete"  # recovery finished successfully
BROKER_RECOVERY_FAILED = "broker_recovery_failed"      # recovery failed (timeout, no snapshot, etc.)
SUBSCRIBER_RECONNECTING = "subscriber_reconnecting"    # subscriber detected dead broker
SUBSCRIBER_RECONNECTED = "subscriber_reconnected"      # subscriber connected to new broker
```

Event payloads include: `broker_id`, `strategy` ("replacement" | "redistribution"), `snapshot_age`, `affected_subscriber_count`, `timestamp`.

### 9. CLI Changes

**File:** `aether/cli/run_broker.py`

- Add `--orchestrator-url` argument, pass to `GossipBroker` constructor

**File:** `aether/cli/run_subscriber.py`

- Add `--orchestrator-url` and `--subscriber-id` arguments
- Pass to `NetworkSubscriber` constructor for reconnection support

**File:** `aether/orchestrator/docker_manager.py`

- In `create_broker()`: add `--orchestrator-url http://orchestrator:8080` to container command
- In `create_subscriber()`: add `--orchestrator-url http://orchestrator:8080 --subscriber-id {id}` to container command

### 10. Bootstrap Cleanup

**File:** `aether/gossip/bootstrap.py`

The bootstrap server currently never removes registered brokers. When a replacement broker registers with the same hostname but a new IP, the old (stale) entry remains.

- Track `last_seen: dict[NodeAddress, float]` — update on each successful `MembershipUpdate` send
- Evict brokers that haven't been seen for `30s` (configurable)
- This prevents stale entries from polluting `MembershipUpdate` messages sent to new brokers

---

## Edge Cases & Failure Modes

### False Positive: Broker Temporarily Unreachable

**Scenario:** Network blip causes 3 consecutive health check failures, but broker is actually alive.
**Mitigation:** Grace period (3 failures over 15s). If the orchestrator starts replacement and the old broker comes back, the old container will have been removed by then (orchestrator removes it before creating replacement). If somehow both exist, the old broker won't receive traffic because its Docker container/network identity is gone.

### Split-Brain: Two Brokers With Same Subscribers

**Scenario:** Old broker somehow survives while replacement is created.
**Mitigation:** The orchestrator `remove_broker()` force-stops the old container before creating the replacement. Docker container lifecycle guarantees only one container with a given name exists.

### Orchestrator Down During Recovery

**Scenario:** Orchestrator crashes mid-recovery.
**Mitigation:** Subscribers keep retrying the assignment API with exponential backoff. When the orchestrator restarts, it rebuilds `_components` from Docker state (`docker ps`), re-evaluates broker health, and resumes normal operation. Subscriber assignment queries that failed will succeed on retry.

### No Surviving Brokers

**Scenario:** All brokers die simultaneously.
**Mitigation:** No peers to query for snapshots. Orchestrator detects all brokers dead, creates fresh brokers. Subscribers query assignment API, get new broker assignments, re-register from scratch. Message history and dedup state are lost.

### Snapshot Replication Failed

**Scenario:** Snapshot was taken but never replicated (no peers available at replication time).
**Mitigation:** Falls through to redistribution path. System degrades gracefully.

### Subscriber Can't Reach Orchestrator

**Scenario:** Network partition between subscriber and orchestrator.
**Mitigation:** Subscriber retries indefinitely with capped exponential backoff (max 30s). No data corruption — subscriber is just idle until connectivity is restored.

### Multiple Brokers Die In Sequence

**Scenario:** Broker-1 dies, recovery starts, then broker-2 dies during recovery.
**Mitigation:** Each recovery is independent, keyed by `broker_id` in the debounce map. The orchestrator can run multiple recoveries concurrently. The redistribution path only assigns to brokers that are currently healthy.

---

## Files Summary

| File                                    | Action  | Description                                                                    |
| --------------------------------------- | ------- | ------------------------------------------------------------------------------ |
| `aether/network/subscriber.py`          | Modify  | Add Ping/Pong health check, reconnection loop, orchestrator URL                |
| `aether/gossip/broker.py`               | Modify  | Handle Ping/Pong, add `_recovery_state`, accept `orchestrator_url`             |
| `aether/gossip/status.py`               | Modify  | Add `POST /recover`, `GET /snapshots/{host}/{port}`, recovery_state in /status |
| `aether/gossip/bootstrap.py`            | Modify  | Add TTL-based eviction of stale broker registrations                           |
| `aether/orchestrator/health.py`         | **New** | `HealthMonitor` — polls broker /status, declares dead after N failures         |
| `aether/orchestrator/recovery.py`       | **New** | `RecoveryManager` — decides strategy, executes recovery, manages assignments   |
| `aether/orchestrator/main.py`           | Modify  | Wire up HealthMonitor + RecoveryManager, add `GET /api/assignment`             |
| `aether/orchestrator/models.py`         | Modify  | Add new event types, `AssignmentInfo`, recovery-related models                 |
| `aether/orchestrator/docker_manager.py` | Modify  | Pass `--orchestrator-url` to broker/subscriber containers                      |
| `aether/snapshot.py`                    | Modify  | Add `Ping`/`Pong` message types                                                |
| `aether/config.py`                      | Modify  | Add orchestrator, health, recovery, subscriber config sections                 |
| `config.docker.yaml`                    | Modify  | Add new configuration values                                                   |
| `aether/cli/run_broker.py`              | Modify  | Add `--orchestrator-url` CLI arg                                               |
| `aether/cli/run_subscriber.py`          | Modify  | Add `--orchestrator-url`, `--subscriber-id` CLI args                           |
| `tests/integration/test_failover.py`    | **New** | End-to-end failover tests                                                      |

---

## Testing Strategy

### Unit Tests

- `RecoveryManager` debounce logic (duplicate failure reports within window)
- `RecoveryManager` strategy selection (fresh snapshot → replacement, stale → redistribution)
- `RecoveryManager` redistribution assignment (least-loaded broker selection)
- `HealthMonitor` failure counting and threshold logic
- Subscriber reconnection backoff timing (exponential with jitter)
- Assignment registry CRUD operations

### Integration Tests (`tests/integration/test_failover.py`)

**Test 1: Replacement recovery with fresh snapshot**

1. Start 3 brokers, register subscriber on broker-2
2. Wait for snapshot cycle (snapshot replicated to peers)
3. Kill broker-2
4. Verify orchestrator detects failure (3 health check failures)
5. Verify replacement broker-2 is created
6. Verify snapshot recovery executes
7. Verify subscriber receives `BrokerRecoveryNotification` OR reconnects via assignment API
8. Verify message delivery resumes through replacement broker

**Test 2: Redistribution with stale snapshot**

1. Start 3 brokers, register subscribers on broker-2
2. Set `snapshot_max_age` to 0 (force all snapshots to be "stale")
3. Kill broker-2
4. Verify orchestrator chooses redistribution path
5. Verify assignment registry updated with new broker assignments
6. Verify subscribers query assignment API and reconnect to surviving brokers
7. Verify subscribers re-register with new brokers (`SubscribeRequest` sent)
8. Verify message delivery resumes

**Test 3: Subscriber autonomous reconnection**

1. Start broker and subscriber
2. Kill broker without orchestrator involvement
3. Verify subscriber detects failure (Ping timeout)
4. Verify subscriber enters reconnection state
5. Verify subscriber queries assignment API with exponential backoff
6. Start replacement broker, update assignment
7. Verify subscriber reconnects and resumes

**Test 4: Concurrent broker failures**

1. Start 4 brokers with subscribers distributed across them
2. Kill broker-2 and broker-3 simultaneously
3. Verify both recoveries execute independently
4. Verify no subscriber is assigned to a dead broker

**Test 5: False positive protection**

1. Start broker, simulate single health check failure (not 3)
2. Verify no recovery triggered
3. Verify broker resumes normal operation

### Manual Docker Verification

1. `docker compose up` — start full system
2. Create brokers and subscribers via dashboard
3. `docker kill aether-broker-2` — simulate crash
4. Observe in dashboard: failure detection event, recovery strategy decision, subscriber reconnection
5. Verify message delivery resumes in dashboard metrics

---

## Implementation Order

1. **Ping/Pong + subscriber health monitoring** — foundation for subscriber-side detection
2. **Assignment registry + API endpoint** — foundation for pull-based reconnection
3. **Subscriber reconnection loop** — subscriber can now detect failure and reconnect
4. **Orchestrator health monitor** — orchestrator detects dead brokers
5. **Snapshot freshness API** — orchestrator can query snapshot age from peers
6. **Recovery manager (replacement path)** — full state recovery from snapshots
7. **Recovery manager (redistribution path)** — fallback when snapshots are stale
8. **Broker `/recover` endpoint** — orchestrator can trigger recovery on replacement broker
9. **Configuration + CLI args** — wire everything together
10. **Bootstrap cleanup** — TTL-based eviction of stale registrations
11. **Event types + dashboard integration** — observability
12. **Integration tests** — validate end-to-end
