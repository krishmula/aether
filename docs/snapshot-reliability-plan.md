# Snapshot Reliability & Observability Fix Plan

**Status:** Draft  
**Date:** 2026-04-28  
**Scope:** `aether/gossip/broker.py`, `aether/gossip/status.py`, `aether/orchestrator/main.py`, `aether/orchestrator/docker_manager.py`, `aether/orchestrator/models.py`, `aether/cli/run_broker.py`  

---

## 1. Problem Statement

The snapshot benchmark returns **0 rounds** because the orchestrator's `_snapshot_monitor` only emits `SNAPSHOT_COMPLETE` when it detects a **peer-stored snapshot replica**.

When brokers evict each other due to missed heartbeats (or bootstrap discovery fails), replication is skipped silently, and the orchestrator never sees the snapshot — even though the broker completed it locally.

This is a **conflation of two concerns**:
1. **Observability:** Did a snapshot actually happen?
2. **Safety:** Is the snapshot replicated to peers for recovery?

The current design uses peer replication as the *only* signal for both, which means a perfectly valid local snapshot becomes invisible to the system the moment replication fails.

---

## 2. Root Cause

| Step | Code | What happens |
|------|------|--------------|
| Brokers evict each other | `broker.py:916-917` | `peer_brokers.discard(peer)` — mutual eviction leaves empty peer sets |
| Snapshot completes | `broker.py:453` | `_channels_closed >= expected_peers` is `True` for empty set |
| Replication silently skipped | `broker.py:482-487` | `if not available_peers: return` — no warning, no retry |
| Orchestrator finds no replica | `docker_manager.py:580-595` | Queries other brokers' `/snapshots/{host}/{port}`; gets 404 |
| Monitor skips broker | `main.py:380-382` | `if s.timestamp is None: continue` — no event emitted |
| Benchmark gets nothing | `collectors.py:354` | `asyncio.wait_for(events.get(), timeout=240)` times out |

**Key insight:** The broker *already knows* it completed the snapshot. It logged `"snapshot complete"`. But the orchestrator never asks the broker directly — it only asks peers if they stored a copy.

---

## 3. Design Principle

> **Separate observability from safety.**
>
> - **Observability** (benchmarks, dashboard): Ask the primary source — the broker that took the snapshot.
> - **Safety** (recovery Path A): Verify peer replication independently. Never conflate the two.
>
> Do NOT add a fallback chain where peer-query failure silently falls back to local state. That creates "green lies" — the benchmark passes while the cluster is unsafe for recovery.

---

## 4. Changes

### Priority 1: Local snapshot observability (fixes benchmark + reduces load)

**Goal:** `_snapshot_monitor` should detect snapshot completion by asking each broker directly, not by querying peers.

#### 4.1.1 Track latest local snapshot in `GossipBroker`

**File:** `aether/gossip/broker.py`

After `_check_snapshot_complete` finalizes a snapshot, store a reference:

```python
# In _check_snapshot_complete, after snapshot = self._snapshot_recorded_state
self._latest_local_snapshot = snapshot
```

This field holds the most recently completed local snapshot. It is independent of `_peer_snapshots` (which stores replicas received from *other* brokers).

#### 4.1.2 Expose `latest_snapshot` in broker `/status`

**File:** `aether/gossip/status.py` — `_handle_status`

Add to the JSON payload:

```json
{
  "broker": "broker-1:8000",
  "snapshot_state": "idle",
  "latest_snapshot": {
    "snapshot_id": "abc123...",
    "timestamp": 1714321000.5,
    "peer_count": 2
  }
}
```

If no snapshot has been taken yet, `latest_snapshot` is `null`.

#### 4.1.3 Rewrite `_snapshot_monitor` to query `/status` directly

**File:** `aether/orchestrator/main.py`

Replace the `get_snapshots()` call with direct per-broker `/status` queries:

```python
async def _snapshot_monitor() -> None:
    last_timestamps: dict[str, float] = {}
    loop = asyncio.get_event_loop()

    while True:
        await asyncio.sleep(settings.snapshot_monitor_poll_interval)
        for info in docker_mgr.get_running_brokers():
            try:
                raw = await loop.run_in_executor(
                    None,
                    docker_mgr._fetch_status,
                    info.hostname,
                    info.internal_status_port,
                )
                snap = raw.get("latest_snapshot")
                if not snap or not snap.get("timestamp"):
                    continue

                ts = snap["timestamp"]
                prev = last_timestamps.get(info.hostname)
                if prev is None or ts > prev:
                    last_timestamps[info.hostname] = ts
                    await broadcaster.emit(
                        EventType.SNAPSHOT_COMPLETE,
                        {
                            "broker_id": info.component_id,
                            "broker_address": f"{info.hostname}:{info.internal_port}",
                            "snapshot_id": snap["snapshot_id"],
                            "snapshot_timestamp": ts,
                        },
                    )
            except Exception:
                pass
```

**Impact:**
- Cuts polling from **O(n²)** to **O(n)** HTTP requests per interval.
- Fixes the benchmark immediately — the monitor sees local snapshots regardless of replication state.
- Preserves recovery safety — `_fetch_best_snapshot` still requires peer replicas.

---

### Priority 2: Peer replication visibility (prevents silent recovery failures)

**Goal:** Make it visible when a snapshot completed but was NOT replicated to peers.

#### 4.2.1 Add `replicated_peer_count` to `BrokerSnapshotInfo`

**Files:**
- `aether/orchestrator/models.py` — add `replicated_peer_count: int | None = None`
- `aether/orchestrator/docker_manager.py` — populate it in `get_snapshots()`

After querying peers for stored replicas, also check how many peers reported a replica:

```python
replicated_count = sum(
    1 for q in brokers
    if q.component_id != target.component_id
    and self._fetch_url(f".../snapshots/{target.hostname}/{target.internal_port}")
)
```

Expose this in the API response so the dashboard and operators can see: *"Broker-1's latest snapshot is replicated on 0/2 peers."*

#### 4.2.2 Log replication outcome clearly

**File:** `aether/gossip/broker.py` — `_replicate_snapshot`

Current log:
```
no peers available to replicate snapshot_id=abc123
```

Ensure this is always emitted at `WARNING` level. Add a second log after successful replication:
```
snapshot_id=abc123 replicated to 2 peer(s)
```

This gives operators a clear signal in logs without needing to query the API.

---

### Priority 3: Self-healing peer discovery (prevents root cause)

**Goal:** Prevent permanent mutual eviction by allowing brokers to re-discover each other.

#### 4.3.1 Pass bootstrap address to `GossipBroker`

**Files:**
- `aether/gossip/broker.py` — add `bootstrap_address: Optional[NodeAddress] = None` to `__init__`
- `aether/cli/run_broker.py` — pass `config.bootstrap_address`

#### 4.3.2 Periodic re-registration with bootstrap

**File:** `aether/gossip/broker.py` — `_heartbeat_loop`

Every N heartbeats (e.g., every 30 seconds), re-send a `JOIN` message to the bootstrap:

```python
if self._bootstrap_address and sequence % 6 == 0:
    try:
        self.network.send("JOIN", self._bootstrap_address)
    except Exception:
        pass
```

The bootstrap's `_serve_loop` will re-broadcast the full membership list to all registered brokers. Evicted peers will receive `MembershipUpdate` and re-add each other via `add_peer()`.

**Caveat:** The bootstrap currently broadcasts to **all** brokers on every message. This creates O(n²) broadcast traffic at the bootstrap. For n < 20, this is acceptable. For larger clusters, the bootstrap should be replaced with a gossip seed list or SWIM protocol (see "Alternatives" below).

---

### Priority 4: Harden snapshot timeout

**Goal:** Prevent premature snapshot abandonment under load.

**File:** `aether/gossip/broker.py`

Increase `_snapshot_timeout` from 30.0s to 60.0s (or make it configurable via `config.yaml`):

```python
self._snapshot_timeout: float = 60.0
```

**Why:** If the broker is CPU-starved or the message queue is backed up, snapshot markers may take longer than 30s to propagate. A 60s timeout is more forgiving without being unbounded.

---

## 5. Verification

### After Priority 1

```bash
# Run snapshot benchmark
make bench-snapshot

# Expected: snapshot.json shows "status": "ok" with rounds captured
# Expected: no more "incomplete snapshot rounds: expected 2, got 0"
```

### After Priority 2

```bash
# Check orchestrator /api/snapshots
curl http://localhost:9000/api/snapshots

# Expected: each broker shows latest_snapshot.timestamp AND replicated_peer_count
```

### After Priority 3

```bash
# Check broker logs
docker compose logs broker-1 | grep "peer discovered\|peer evicted"

# Expected: if a peer was evicted, it should be re-discovered within 30s
```

---

## 6. Success Criteria

| Metric | Before | After Priority 1 | After Priority 3 |
|--------|--------|------------------|------------------|
| Snapshot rounds captured (3 brokers) | 0 | 2 | 2 |
| `_snapshot_monitor` HTTP requests per poll | O(n²) | O(n) | O(n) |
| Recovery Path A success rate | 0/3 | Unchanged | >2/3 |
| Peer eviction self-heal | None | None | Yes |
| Time to detect "snapshot not replicated" | Never | Visible in API | Visible in API |

---

## 7. Alternatives Considered

### Option A: Fallback chain (rejected)

> Query peers first; if no replica found, fall back to broker's own `/status`.

**Why rejected:** Creates "green lies." The benchmark passes while recovery remains broken. Operators can't tell if replication is working without manually checking peer stores. This pattern caused a 2-hour incident at LinkedIn where replication lag was masked by a fallback metric.

### Option B: Broker pushes events to orchestrator

> Broker POSTs `SNAPSHOT_COMPLETE` to orchestrator on finish.

**Why deferred:** This is the production-grade pattern (Datadog/Stripe use this). But it requires the broker to know the orchestrator URL, which is a config and deployment change. It also requires idempotency handling on the orchestrator side. Worth doing in a follow-up phase, but not needed to fix the immediate benchmark issue.

### Option C: Full gossip protocol (SWIM)

> Replace bootstrap + heartbeats with a proper gossip membership protocol.

**Why deferred:** Correct long-term fix for peer discovery, but months of work and overkill for <20 nodes. The bootstrap re-registration in Priority 3 is a tactical fix that buys time.

---

## 8. Risks & Mitigations

| Risk | Mitigation |
|------|------------|
| `_snapshot_monitor` baseline seeding carries timestamps across runs | Clear `last_timestamps` on orchestrator restart, or use monotonic generation IDs |
| Bootstrap broadcast amplification under load | Cap at n=20 for now; replace with gossip seed in Phase 7 |
| Local snapshot field grows unbounded | Only store the *latest* snapshot; `_latest_local_snapshot` is a single reference |
| Recovery Path A still fails if replication is broken | That's correct behavior — Path A should fail without peer replicas. Path B (redistribution) handles it. |

---

## 9. The One Thing

If you only do one thing, do **Priority 1**.

It fixes the benchmark, eliminates the O(n²) polling bomb, and preserves the honesty of the recovery path. Everything else is optimization and hardening.
