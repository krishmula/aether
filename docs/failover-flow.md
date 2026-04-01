# Broker Failover Flow

End-to-end logical ordering of what happens when a broker dies.

---

## Steady State (before failure)

1. Brokers gossip with each other and periodically take Chandy-Lamport snapshots
2. Each broker replicates its snapshot to 2 random peers via `SnapshotReplica` (gossip TCP)
3. Peers store it in `_peer_snapshots[source_broker_address]`
4. HealthMonitor (orchestrator) polls every broker's `GET /status` every 5s

---

## Stage 1 — Detection

**Who:** `HealthMonitor` (orchestrator)

**What:** Polls `GET http://broker-2:18000/status` every 5s. After 3 consecutive
failures (network error or non-200), fires `on_broker_dead(broker_id=2, host="broker-2", port=8000)`.

**Implemented in:** `aether/orchestrator/health.py`

---

## Stage 2 — Decision

**Who:** `RecoveryManager` (orchestrator)

**What:**
1. Emits `BROKER_DECLARED_DEAD` WebSocket event
2. Queries **every surviving broker** concurrently for broker-2's snapshot:
   ```
   GET /snapshots/broker-2/8000  →  broker-1's StatusServer
   GET /snapshots/broker-2/8000  →  broker-3's StatusServer
   ```
3. Each surviving broker looks up `self.broker._peer_snapshots.get(NodeAddress("broker-2", 8000))`
   - Returns 200 + snapshot JSON (including `timestamp`) if it has one
   - Returns 404 if it never received broker-2's snapshot
4. RecoveryManager picks the freshest `timestamp` from all 200 responses
5. Freshness check: `time.time() - best_timestamp < 30s`
   - Fresh → **Path A**
   - Stale or no snapshot → **Path B**

**Implemented in:** `aether/orchestrator/recovery.py` (not yet written), `GET /snapshots` in `aether/gossip/status.py` (done)

---

## Stage 3A — Path A: Replacement (fresh snapshot)

**Who:** `RecoveryManager` (orchestrator) + new broker

1. `DELETE /deregister` on bootstrap — removes dead broker-2 from peer list so the replacement gets a clean start
2. Remove dead container: `DockerManager.remove_broker(2)`
3. Spin up replacement: `DockerManager.create_broker(broker_id=2)` — same hostname `broker-2`, same ports
4. Poll `GET http://broker-2:18000/status` every 500ms until 200 (broker process takes 2–5s to start)
5. Send `POST /recover` to the new broker:
   ```json
   {"dead_broker_host": "broker-2", "dead_broker_port": 8000}
   ```
6. New broker's `StatusServer` receives it and calls:
   - `broker.request_snapshot_from_peers(dead_addr)` — sends `SnapshotRequest` over gossip TCP to broker-1/broker-3, waits for `SnapshotReplica` response
   - `broker.recover_from_snapshot(snapshot)` — restores subscriber map, seen-message IDs, peer list
7. Emits `BROKER_RECOVERED(recovery_path="replacement")` WebSocket event

**Implemented in:** `POST /recover` in `aether/gossip/status.py` (done), `RecoveryManager` Path A in `aether/orchestrator/recovery.py` (not yet written)

---

## Stage 3B — Path B: Redistribution (stale/no snapshot)

**Who:** `RecoveryManager` (orchestrator)

1. Remove dead container: `DockerManager.remove_broker(2)`
2. Find orphaned subscribers: all entries in `_components` where `broker_id == 2`
3. Assign each to the surviving broker with the fewest current subscribers
4. Update `_components[subscriber].broker_id` for each reassignment
5. Emits `BROKER_RECOVERED(recovery_path="redistribution")` WebSocket event

No new broker is created. No `POST /recover` is called.

**Implemented in:** `RecoveryManager` Path B in `aether/orchestrator/recovery.py` (not yet written)

---

## Stage 4 — Subscriber Reconnect (Phase 1C)

**Who:** `NetworkSubscriber` (client side, independent of orchestrator)

1. Subscriber's `_broker_health_loop` detects missed Pong responses (15s timeout)
2. Calls `_start_reconnect(epoch)` — epoch guard ensures only one reconnect runs at a time
3. Queries `GET /api/assignment?subscriber_id=N` on the orchestrator
4. Orchestrator returns `{"broker_host": "broker-2", "broker_port": 8000}` (Path A) or a different broker (Path B)
5. Subscriber reconnects and re-sends `SubscribeRequest` for each of its subscriptions
6. Emits `SUBSCRIBER_RECONNECTED` WebSocket event

**Implemented in:** `aether/network/subscriber.py` (not yet written), `GET /api/assignment` in `aether/orchestrator/main.py` (not yet written)

---

## Summary: What's done vs. pending

| Stage | Component | Status |
|-------|-----------|--------|
| Steady state snapshots | `GossipBroker._replicate_snapshot()` | ✅ Pre-existing |
| Detection | `HealthMonitor` | ✅ Done (Phase 1B Task 2) |
| Snapshot query endpoint | `GET /snapshots` on broker | ✅ Done (Phase 1B Task 3) |
| Recovery trigger endpoint | `POST /recover` on broker | ✅ Done (Phase 1B Task 3) |
| Bootstrap deregister | `DELETE /deregister` on bootstrap | ✅ Done (Phase 1B Task 3) |
| Decision + Path A + Path B | `RecoveryManager` | ⬜ Phase 1B Task 5 |
| Assignment endpoint | `GET /api/assignment` | ⬜ Phase 1B Task 7 |
| Subscriber reconnect | `NetworkSubscriber` ping/pong + reconnect | ⬜ Phase 1C |
