# Subscriber Reconnect Readiness Plan

## Goal

Make subscriber reconnection deterministic and production-safe by ensuring a subscriber is only considered reconnected after the target broker is actually ready to accept subscriptions.

Today, a subscriber can mark itself healthy too early:

1. The subscriber detects broker failure
2. It queries `GET /api/assignment`
3. The orchestrator may return a broker in `STARTING` state
4. The subscriber sets `_broker_alive = True` immediately
5. The subscriber sends `SubscribeRequest` without waiting for confirmation

If the broker is still starting, or has not finished recovery, the subscriber can exit the reconnect loop even though no subscription was actually re-established.

This plan fixes that by introducing an explicit readiness contract between:

- the orchestrator assignment API
- broker recovery/readiness state
- the subscriber reconnect state machine

---

## Problem Statement

The current reconnect flow conflates three separate concepts:

- **assigned**
  The orchestrator says this subscriber should use broker X

- **reachable**
  The broker process is listening and responding to health checks

- **ready**
  The broker can accept subscriptions and resume message delivery

In production systems, those are not the same state.

A broker can be:

- assigned but not reachable
- reachable but not recovered
- recovered but not yet fully ready to serve all traffic

The reconnect path should not treat assignment as success.

---

## Desired End State

The subscriber reconnect contract should become:

1. The subscriber detects broker failure
2. It queries the orchestrator for its assignment
3. The orchestrator returns only brokers that are ready for reconnect traffic
4. The subscriber attempts to re-subscribe
5. The subscriber waits for positive confirmation
6. Only then does it mark the broker as healthy and exit reconnect mode

The key production property is:

**A subscriber is not "recovered" until reconnection is confirmed, not merely attempted**

---

## Production Design Principles

- **Readiness is explicit**
  Do not infer readiness from container creation or assignment alone.

- **Health and readiness are different**
  `GET /status == 200` is not sufficient if the broker is still recovering.

- **Positive acknowledgment wins**
  A reconnect attempt is only successful after a real acknowledgment from the broker.

- **Retry loops must be safe**
  Repeating reconnect attempts should not corrupt state or create inconsistent duplicate registrations.

- **Control plane advertises only ready targets**
  Assignment APIs should not hand out destinations that are not actually ready for traffic.

---

## Current Failure Mode

The problematic sequence is:

1. Broker A dies
2. Replacement broker B is created
3. Subscriber queries `/api/assignment`
4. Assignment endpoint returns broker B because its `ComponentStatus` is `STARTING`
5. Subscriber updates `self.broker = broker B`
6. Subscriber sets `_broker_alive = True`
7. Subscriber sends `SubscribeRequest`
8. Broker B is not ready yet, so the request can be lost or rejected
9. Subscriber exits reconnect loop anyway

This leaves a silent bad state:

- the subscriber thinks it is healthy
- the orchestrator thinks the subscriber has an assignment
- the broker may not have the subscriber registered

This is the kind of bug that looks fine in dashboards but causes intermittent message loss.

---

## Recommended Approach

Use a **two-sided readiness contract**:

1. the orchestrator only hands out brokers that are ready
2. the subscriber only exits reconnect mode after broker acknowledgment

Both sides matter.

If only the orchestrator changes, stale races still exist.
If only the subscriber changes, the reconnect loop may waste time on brokers that should never have been advertised yet.

---

## Proposed Readiness Model

## Broker states

Split readiness into explicit broker-level states.

Suggested model:

- `starting`
  Broker process/container is coming up

- `recovering`
  Broker is applying snapshot recovery or completing replacement initialization

- `ready`
  Broker can accept subscriptions and serve traffic

- `failed`
  Broker recovery or startup failed

For non-replacement brokers:

- a newly started broker may move from `starting` to `ready`

For replacement brokers:

- it should move from `starting` to `recovering` to `ready`

### Important rule

The assignment API should only return brokers in `ready`.

Not `starting`.
Not `recovering`.

---

## API and Status Contract

## Broker `/status`

Extend broker status with readiness fields:

```json
{
  "broker": "broker-2:8000",
  "recovery_state": "recovering",
  "readiness_state": "not_ready",
  "accepting_subscriptions": false
}
```

Suggested fields:

- `recovery_state`
  `idle | recovering | recovered | failed`

- `readiness_state`
  `starting | not_ready | ready | failed`

- `accepting_subscriptions`
  boolean

### Readiness semantics

`accepting_subscriptions = true` only when:

- TCP server is live
- status server is live
- recovery is complete if recovery was required
- the broker can safely process `SubscribeRequest`

---

## Assignment API

## `GET /api/assignment`

Change semantics from:

- return assigned broker if status is `RUNNING` or `STARTING`

To:

- return assigned broker only if it is `ready`
- otherwise return `404` or `409` so the subscriber retries

### Recommended response behavior

If assigned broker exists but is not ready:

```json
{
  "error": "broker_not_ready"
}
```

Status code options:

- `404`
  simple retry contract, consistent with current subscriber behavior

- `409`
  more semantically precise: assignment exists but is not yet usable

For now, `404` is acceptable if you want minimal subscriber changes.
For production polish, `409` is better.

---

## Subscriber State Machine

## Current problem

The subscriber currently transitions to "healthy" too early.

## Proposed states

Suggested subscriber broker-connection states:

- `healthy`
- `suspect`
- `reconnecting`
- `awaiting_ack`
- `failed`

### State transitions

#### On broker ping timeout

- `healthy -> reconnecting`

#### On assignment received

- update target broker
- send subscription requests
- transition to `awaiting_ack`

#### On `SubscribeAck` for all subscriptions

- set `_broker_alive = True`
- transition to `healthy`

#### On ack timeout or send failure

- transition back to `reconnecting`

### Key rule

Do not set `_broker_alive = True` when assignment is fetched.
Set it only after successful broker acknowledgment.

---

## Subscription Reconnect Handshake

The reconnect path needs a real handshake.

### Current behavior

- send `SubscribeRequest`
- do not wait for `SubscribeAck`
- treat reconnect as complete

### Recommended behavior

For each known subscription:

1. send `SubscribeRequest`
2. wait for matching `SubscribeAck`
3. retry on timeout

When all active subscriptions are acknowledged:

- mark reconnect successful

### Edge case: zero subscriptions

If the subscriber has zero subscriptions:

- either do a broker ping/health probe
- or allow reconnect success once broker health is confirmed

---

## Message Correlation

To make reconnect confirmation robust, the subscriber should be able to match acknowledgments to outstanding requests.

If current `SubscribeAck` semantics are sufficient, use them directly.

If not, consider adding:

- request correlation ID
- reconnect epoch or session token

This is especially useful if:

- delayed acks from the dead/old broker are possible
- reconnect attempts can overlap

### Suggested minimal approach

Track reconnect attempts with a local epoch:

- increment reconnect epoch on every reconnect cycle
- ignore stale acknowledgments from previous epochs

This is the same style of guard used in production reconnect loops to avoid races.

---

## Broker-Side Subscription Idempotency

Retrying `SubscribeRequest` must be safe.

Broker requirements:

- repeated subscribe for same subscriber + payload range should succeed
- duplicate subscribe should not produce inconsistent internal state
- ack should be returned even if the subscription already exists

This lets the subscriber retry safely during transient readiness windows.

---

## Coordinating with Replacement Recovery

This reconnect plan should align with the authoritative snapshot recovery plan.

### Replacement lifecycle

1. orchestrator creates replacement broker
2. broker starts
3. broker receives authoritative snapshot via `POST /recover`
4. broker restores state
5. broker transitions to `ready`
6. assignment API begins returning it
7. subscribers reconnect and wait for ack

This prevents subscribers from racing ahead of broker recovery.

---

## Recommended Implementation

## Phase 1: Introduce Broker Readiness Fields

### Objective

Expose a clear signal for whether a broker can accept reconnect traffic.

### Changes

#### `aether/gossip/broker.py`

Add readiness fields:

- `_recovery_state`
- `_readiness_state`
- `_accepting_subscriptions`

Suggested transitions:

- initial startup: `starting`, `false`
- normal startup complete: `ready`, `true`
- replacement recovery in progress: `recovering`, `false`
- recovery complete: `ready`, `true`
- recovery failure: `failed`, `false`

#### `aether/gossip/status.py`

Expose readiness in `/status`.

### Acceptance criteria

- `/status` tells the truth about subscription readiness
- readiness changes are externally visible

---

## Phase 2: Tighten Assignment API Semantics

### Objective

Stop advertising brokers that are not ready.

### Changes

#### `aether/orchestrator/main.py`

Update `/api/assignment` so it returns only brokers that are:

- present
- alive
- ready for reconnect traffic

That means removing the current acceptance of `STARTING` unless readiness is separately true.

### Acceptance criteria

- subscribers never receive a broker assignment that is known to be not ready
- assignment retries naturally while replacement recovery is in progress

---

## Phase 3: Make Subscriber Reconnect Ack-Driven

### Objective

Only mark reconnect success after a positive confirmation from the broker.

### Changes

#### `aether/network/subscriber.py`

Modify `_reconnect()` so it:

- fetches assignment
- updates the target broker
- sends subscribe requests
- waits for `SubscribeAck`
- only then sets `_broker_alive = True`

Do not set `_broker_alive = True` just because assignment lookup succeeded.

### Design details

Possible implementation pattern:

- collect active subscriptions
- for each subscription:
  - send `SubscribeRequest`
  - wait for ack with timeout
  - retry if timeout occurs
- if all acks succeed:
  - mark healthy
- otherwise:
  - sleep with backoff and retry whole reconnect cycle

### Acceptance criteria

- reconnect success means the broker actually accepted subscriptions
- temporary readiness gaps do not produce false healthy state

---

## Phase 4: Add Epoch Guarding

### Objective

Prevent stale acknowledgments from completing the wrong reconnect attempt.

### Changes

Add a reconnect epoch or reconnect attempt counter.

Suggested behavior:

- increment epoch when entering reconnect
- attach or track current epoch locally
- ignore stale acks from older reconnect attempts

### Acceptance criteria

- delayed or out-of-order responses cannot complete a stale reconnect

---

## Phase 5: Optional Fast-Path Optimization

### Objective

Keep broker-pushed recovery notifications as an optimization without relying on them for correctness.

### Behavior

If a subscriber receives `BrokerRecoveryNotification`:

- update preferred target broker
- do not treat that alone as reconnect success
- still require either:
  - successful health probe and subscribe ack
  - or another positive readiness/registration signal

This preserves the fast path while keeping the pull-and-ack model authoritative.

---

## Testing Plan

## Unit Tests

### Assignment endpoint

Add tests proving:

- ready broker returns success
- starting broker without readiness returns retryable error
- recovering broker returns retryable error
- failed broker returns retryable or unavailable error

### Subscriber reconnect

Add tests proving:

- assignment success alone does not set `_broker_alive = True`
- `SubscribeAck` is required before reconnect completes
- ack timeout causes retry
- send failure causes retry
- stale ack from previous epoch is ignored

### Broker status

Add tests proving:

- readiness fields appear in `/status`
- readiness transitions happen correctly during replacement recovery

---

## Integration Tests

Add integration tests for:

- replacement broker exists but is not yet ready
- `/api/assignment` does not hand it out yet
- once ready, assignment starts returning it
- subscriber reconnects only after actual `SubscribeAck`

Add a race test:

- subscriber gets assignment to replacement broker
- broker is reachable but not yet ready to process subscriptions
- initial subscribe attempt fails
- subscriber remains in reconnect mode
- later retry succeeds after readiness flips

Add fast-path test:

- subscriber receives `BrokerRecoveryNotification`
- still waits for actual registration confirmation before leaving reconnect mode

---

## Operational Visibility

## Logs

Log reconnect attempts with:

- `subscriber_id`
- `old_broker`
- `target_broker`
- `reconnect_epoch`
- `attempt`
- `assignment_result`
- `subscribe_ack_result`
- `final_state`

## Metrics

Recommended metrics:

- `subscriber_reconnect_attempts_total`
- `subscriber_reconnect_success_total`
- `subscriber_reconnect_failures_total{reason=...}`
- `subscriber_reconnect_duration_seconds`
- `assignment_not_ready_responses_total`
- `subscribe_ack_timeouts_total`
- `stale_reconnect_ack_ignored_total`

---

## Rollout Strategy

## Step 1

Add readiness fields to broker `/status`.

## Step 2

Update assignment API to stop returning brokers that are not ready.

## Step 3

Update subscriber reconnect logic to require ack before declaring success.

## Step 4

Add epoch guarding and extended tests.

## Step 5

Refine HTTP response semantics for assignment-not-ready if desired.

---

## Risks and Tradeoffs

### Slower reconnect in exchange for correctness

Subscribers may remain in reconnect mode slightly longer.

Why acceptable:

- correctness matters more than optimistic but false recovery
- retries with ack-based confirmation remove silent failure windows

### More explicit states

The code becomes more stateful.

Why acceptable:

- reconnect and readiness are already stateful in reality
- explicit state is easier to reason about than implicit races

### Additional tests and plumbing

This adds integration complexity.

Why acceptable:

- reconnect bugs are exactly the kind that require stronger state-machine coverage

---

## Non-Goals

This plan does not change:

- the least-loaded redistribution policy
- intentional broker deletion semantics
- the coordinated-snapshot scope around in-flight gossip payloads
- publisher-side behavior

---

## Summary

The production-grade fix is:

- readiness becomes explicit
- assignment only returns ready brokers
- subscriber reconnect is ack-driven, not assignment-driven
- reconnect success means confirmed broker registration

That is the right model for a system where recovery correctness matters more than optimistic state transitions.
