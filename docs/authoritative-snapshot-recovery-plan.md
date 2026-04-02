# Authoritative Snapshot Recovery Plan

## Goal

Make broker replacement recovery deterministic and production-grade by ensuring the orchestrator selects the recovery snapshot exactly once, and the replacement broker restores exactly that snapshot.

Today, the control plane and the replacement broker do not use the same selection decision:

1. The orchestrator queries surviving brokers over HTTP and picks the freshest snapshot
2. The orchestrator decides that replacement recovery is viable
3. The replacement broker then queries peers again over the gossip network
4. The replacement broker restores the first non-null snapshot response

That means the system can decide based on snapshot `A` and recover using snapshot `B`.

This plan removes that inconsistency.

---

## Desired End State

The recovery contract should become:

1. The orchestrator gathers candidate snapshots from surviving brokers
2. The orchestrator selects the winning snapshot using a single freshness policy
3. The orchestrator creates the replacement broker
4. The orchestrator sends the selected snapshot, or an exact reference to it, to the replacement broker
5. The replacement broker restores only that selected snapshot
6. The replacement broker reports recovery status explicitly
7. The orchestrator emits success/failure events based on the actual outcome

The key production property is:

**One recovery decision, one recovery artifact, one execution path**

---

## Production Design Principles

This plan follows the design patterns used in mature control-plane/data-plane systems:

- **Single decision authority**
  The orchestrator is the authority for choosing the recovery snapshot.

- **Deterministic execution**
  The broker does not re-run snapshot selection logic during recovery.

- **Idempotent commands**
  Retrying the same recovery request should not corrupt broker state or produce different results.

- **Auditable recovery**
  Every recovery attempt should be traceable by `broker_id`, `dead_broker`, `snapshot_id`, and `timestamp`.

- **Explicit state transitions**
  Recovery should move through well-defined states such as `pending`, `restoring`, `recovered`, `failed`.

- **Policy/transport separation**
  Snapshot selection is policy. Snapshot transport is plumbing. Do not mix them.

---

## Recommended Approach

### Chosen Pattern

Use an **orchestrator-selected snapshot payload** for replacement recovery.

That means:

- the orchestrator fetches the candidate snapshots
- the orchestrator selects the winner
- the orchestrator sends that exact snapshot to the replacement broker in `POST /recover`
- the replacement broker restores that snapshot directly

This is the recommended design because the snapshots in this system are relatively small and contain recovery metadata rather than large message logs.

### Why This Is Better Than Broker-Side Re-selection

An alternative would be to keep the replacement broker pull model, but have it choose the freshest peer response rather than the first one.

That is an improvement over the current implementation, but it still has structural weaknesses:

- it duplicates selection policy in two places
- it creates two code paths that can drift over time
- it complicates debugging because the orchestrator's selected snapshot is not necessarily the recovered snapshot
- it makes recovery correctness depend on network timing again

For a production-quality system, selection should happen once.

---

## Proposed API Contract

## `POST /recover`

Replace the current request body:

```json
{
  "dead_broker_host": "broker-2",
  "dead_broker_port": 8000
}
```

With an authoritative recovery request:

```json
{
  "dead_broker_host": "broker-2",
  "dead_broker_port": 8000,
  "snapshot": {
    "snapshot_id": "6eb4c8f2-...",
    "broker_address": "broker-2:8000",
    "peer_brokers": ["broker-1:8000", "broker-3:8000"],
    "remote_subscribers": {
      "subscriber-1:9100": [{"low": 0, "high": 84}]
    },
    "seen_message_ids": ["..."],
    "timestamp": 1710000000.0
  }
}
```

### Broker behavior

The replacement broker should:

1. validate the body shape
2. validate that the snapshot belongs to the declared dead broker
3. reject recovery if the broker is already recovered from a newer or equal snapshot
4. restore state from the provided snapshot
5. record recovery metadata locally
6. expose the resulting recovery state in `/status`

### Response body

Return a structured result:

```json
{
  "status": "recovered",
  "snapshot_id": "6eb4c8f2-...",
  "dead_broker": "broker-2:8000",
  "recovered_at": 1710000005.2
}
```

On failure:

```json
{
  "status": "failed",
  "error": "snapshot_validation_failed"
}
```

---

## Data Model Changes

### 1. Recovery request model

Add a typed recovery payload model in the orchestrator/broker boundary layer.

Suggested fields:

- `dead_broker_host`
- `dead_broker_port`
- `snapshot`

### 2. Recovery status on broker

Add broker-local recovery state, for example:

- `idle`
- `pending`
- `restoring`
- `recovered`
- `failed`

Suggested fields on the broker:

- `_recovery_state`
- `_recovery_snapshot_id`
- `_recovery_dead_broker`
- `_recovery_error`
- `_recovered_at`

### 3. Snapshot identity metadata

The snapshot already has:

- `snapshot_id`
- `timestamp`
- `broker_address`

That is enough for now. If this system grows, consider adding:

- `snapshot_version`
- `checksum`
- `schema_version`

---

## Implementation Plan

## Phase 1: Make the Orchestrator's Decision Authoritative

### Objective

Ensure replacement recovery uses the exact snapshot that passed the freshness check.

### Changes

#### `aether/orchestrator/recovery.py`

- Keep `_fetch_best_snapshot()` as the single selector
- After selecting a winner, pass that exact snapshot into `_recover_replacement()`
- In `_recover_replacement()`, include the serialized snapshot in `POST /recover`
- Emit logs containing:
  - `broker_id`
  - `dead_broker`
  - `selected_snapshot_id`
  - `selected_snapshot_timestamp`
  - `snapshot_age_seconds`

### Acceptance criteria

- Path A is chosen from the freshest snapshot
- The replacement broker restores that exact snapshot
- Logs show the exact snapshot used

---

## Phase 2: Remove Broker-Side Re-selection During Recovery

### Objective

Prevent the replacement broker from querying peers again during authoritative recovery.

### Changes

#### `aether/gossip/status.py`

- Update `POST /recover` to accept a snapshot payload
- Stop calling `request_snapshot_from_peers()` in this path
- Deserialize the provided snapshot and pass it to `recover_from_snapshot()`

#### `aether/gossip/broker.py`

- Keep `request_snapshot_from_peers()` for other testing or manual workflows if desired
- Do not use it in the orchestrated replacement path

### Acceptance criteria

- The replacement path contains no second snapshot selection step
- Recovery correctness no longer depends on peer response order

---

## Phase 3: Add Recovery State Machine

### Objective

Make recovery progress visible and explicit.

### Changes

#### Broker

Before restore:

- state = `pending`

While applying snapshot:

- state = `restoring`

On success:

- state = `recovered`

On failure:

- state = `failed`

#### `GET /status`

Extend broker status payload with:

- `recovery_state`
- `recovery_snapshot_id`
- `recovery_dead_broker`
- `recovery_error`
- `recovered_at`

#### Orchestrator

- Poll broker `/status` after `POST /recover` if necessary
- Emit `BROKER_RECOVERED` only after broker reports success
- Emit `BROKER_RECOVERY_FAILED` with structured reason on failure

### Acceptance criteria

- Recovery progress is externally visible
- Dashboard and logs can distinguish "created replacement" from "restored replacement"

---

## Phase 4: Add Idempotency and Monotonic Guards

### Objective

Make retries safe and avoid stale restores.

### Changes

#### Broker

If the broker receives the same recovery request twice:

- return success without reapplying if the same `snapshot_id` already succeeded

If the broker is already recovered from a newer snapshot:

- reject an older snapshot

Suggested logic:

- same snapshot ID + same dead broker: idempotent success
- older timestamp than current recovered snapshot: reject
- different dead broker while already recovered: reject unless explicitly reset

### Acceptance criteria

- Retried `POST /recover` is safe
- Late/stale recovery requests cannot regress state

---

## Phase 5: Validation and Integrity Checks

### Objective

Prevent malformed or inconsistent snapshots from being applied.

### Validation rules

At minimum:

- snapshot must be present
- snapshot `broker_address` must equal `dead_broker_host:dead_broker_port`
- `snapshot_id` must be non-empty
- `timestamp` must be numeric
- subscriber ranges must deserialize cleanly
- peer addresses must deserialize cleanly

Future hardening:

- add payload checksum
- add snapshot schema version

### Acceptance criteria

- Invalid snapshots fail closed
- Errors are explicit and diagnosable

---

## Testing Plan

## Unit Tests

### Recovery manager

Add tests proving:

- the freshest snapshot selected by `_fetch_best_snapshot()` is the same snapshot sent to `POST /recover`
- stale snapshots never trigger Path A
- missing snapshots trigger Path B
- recovery logs/events include the selected `snapshot_id`

### Broker status server

Add tests proving:

- `POST /recover` succeeds with a valid provided snapshot
- `POST /recover` rejects malformed snapshot payloads
- `POST /recover` rejects snapshot/dead-broker mismatches
- repeated `POST /recover` with the same snapshot is idempotent
- older snapshots are rejected after a newer one is applied

### Broker recovery state

Add tests proving:

- state transitions move through `pending/restoring/recovered` on success
- failures land in `failed`
- `/status` exposes the recovery state fields

---

## Integration Tests

Add tests for:

- peer A has snapshot `T1`, peer B has newer snapshot `T2`
- orchestrator chooses `T2`
- replacement broker recovers from `T2`
- final broker state matches `T2`, not `T1`

Add idempotency test:

- send the same authoritative recovery request twice
- verify state remains correct and no duplicate side effects occur

Add failure test:

- replacement comes up
- `POST /recover` contains mismatched snapshot metadata
- broker reports `failed`
- orchestrator falls back or surfaces a structured error

---

## Operational Visibility

## Logging

Every replacement recovery should log:

- `broker_id`
- `dead_broker`
- `recovery_path`
- `selected_snapshot_id`
- `selected_snapshot_timestamp`
- `selected_snapshot_age_seconds`
- `replacement_broker_host`
- `result`
- `error_reason`

## Metrics

Recommended metrics:

- `recovery_attempts_total{path=replacement|redistribution}`
- `recovery_success_total{path=...}`
- `recovery_failure_total{path=...,reason=...}`
- `recovery_duration_seconds`
- `recovery_snapshot_age_seconds`
- `recovery_idempotent_replays_total`
- `recovery_stale_snapshot_rejections_total`

---

## Rollout Strategy

## Step 1

Add the new authoritative `POST /recover` format while temporarily keeping backward compatibility for the old format.

## Step 2

Update `RecoveryManager` to send the selected snapshot payload.

## Step 3

Add broker recovery-state reporting and new tests.

## Step 4

Remove replacement-path use of peer-side re-selection.

## Step 5

Once tests and observability are stable, remove the old recovery-body format if it is no longer needed.

---

## Risks and Tradeoffs

### Snapshot payload size

Passing the full snapshot in `POST /recover` increases request size.

Why acceptable here:

- snapshots contain recovery metadata, not full message logs
- replacement recovery is rare
- correctness is more important than shaving a small request body

### Broker/orchestrator coupling

The orchestrator now knows more about snapshot structure.

Why acceptable here:

- the orchestrator already reasons about snapshot freshness and content
- this coupling is lower risk than duplicated selection policy

### Future scaling

If snapshots grow much larger later, the system can evolve toward:

- object-store backed snapshots
- exact snapshot references
- signed artifact fetches

That is a future optimization, not the right first fix.

---

## Non-Goals

This plan does not change:

- intentional broker deletion semantics
- redistribution balancing policy
- the simplified coordinated-snapshot scope around in-flight gossip payloads
- subscriber reconnect policy, except insofar as replacement recovery becomes more deterministic

---

## Summary

The production-grade fix is not "pick a fresher snapshot inside the broker."

The production-grade fix is:

- orchestrator selects the winning snapshot once
- replacement broker restores exactly that snapshot
- recovery is idempotent, explicit, and observable

That is the cleanest path to a system whose failover behavior is deterministic, debuggable, and safe to evolve.
