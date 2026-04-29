# Aether — Engineering Learnings

A living document capturing the problems, bugs, architecture decisions, and design decisions encountered while building Aether, along with the reasoning and lessons from each.

---

## 1. NodeAddress Hostname-Based Identity

**Summary:** `NodeAddress` originally resolved hostnames to IPs via `socket.gethostbyname()`, causing stale identities after Docker container restarts. The fix stores raw hostname strings so identity remains stable across container lifecycle changes.

**Learning:** In containerized environments, IP addresses are ephemeral — hostnames are the stable identity primitive. Resolving to IPs at object construction time created a class of subtle routing bugs where a restarted container (with a new IP) could never be reached by peers holding stale `NodeAddress` references. The fix was a one-line change (`return host` instead of `return socket.gethostbyname(host)`), but it unblocked all recovery code because every peer list, snapshot, and subscriber mapping depends on stable identity.

**Reasoning:** Docker's internal DNS always resolves `broker-2` to whoever currently holds that hostname, making hostname-based identity the correct abstraction. This mirrors Kubernetes pods (addressed by service name, not pod IP). The change is backward-compatible because existing tests using `127.0.0.1` already pass raw IP strings.

---

## 2. One-Process-Per-Component Architecture

**Summary:** Every component (broker, subscriber, publisher, bootstrap) runs as an independent OS process with its own TCP socket and HTTP `/status` endpoint, making the system naturally containerizable.

**Learning:** Starting with a monolithic in-process design and then retrofitting distribution is painful. By designing each component as an independent process from day one, containerization became a trivial mapping (one process = one container). This decision also forced clean interfaces between components — there are no shared memory shortcuts, only network messages — which made the system more robust and testable. The tradeoff is higher baseline resource usage, but for a demo system proving distributed concepts, the clarity is worth it.

**Reasoning:** The project's goal was to demonstrate distributed systems concepts end-to-end, not to optimize for single-machine efficiency. Independent processes mean each component can be started, stopped, monitored, and debugged in isolation. It also makes the demo more impressive — killing a broker container and watching recovery is visceral in a way that thread death is not.

---

## 3. Chandy-Lamport Distributed Snapshots

**Summary:** Aether implements the classic Chandy-Lamport algorithm for consistent global state capture across brokers, using `SnapshotMarker` messages to trigger state recording and channel closure.

**Learning:** Textbook algorithms are easy to describe but hard to get right in practice. The marker-passing logic has edge cases around peer eviction, network partitions, and timer races that the original paper doesn't address. The implementation required careful lock management (`broker._lock` protecting all snapshot state), idempotent marker handling, and a timeout watchdog to prevent deadlocks. The most important realization was that snapshot *completion* and snapshot *replication* are separate concerns — conflating them caused the benchmark to report zero rounds even when snapshots completed successfully.

**Reasoning:** Chandy-Lamport was chosen because it is the canonical solution to the consistent snapshot problem and directly demonstrates mastery of a core distributed systems concept. For a portfolio project, implementing the "real" algorithm is more impressive than a simpler but less recognizable approach. The snapshot state (subscriber mappings, peer lists, dedup window) is small enough that capturing it in-memory is practical.

---

## 4. Snapshot Deadlock Prevention via Timeout Watchdog

**Summary:** Snapshot state could become permanently wedged if markers were lost or peers were evicted mid-snapshot, causing `_snapshot_in_progress` to block all future rounds. A timeout watchdog clears stuck state after a bounded interval.

**Learning:** Any distributed coordination protocol needs a safety valve. The original `_check_snapshot_timeout` was a no-op stub, and the first time a broker evicted a peer during snapshot recording, the snapshot state froze forever — no future snapshots could initiate. The fix added `_snapshot_started_at` timestamps to both initiator and first-marker-receiver paths, with a periodic check in the existing timer loop. The lesson is that distributed state machines must always have a local timeout path; waiting forever for a peer message is never correct.

**Reasoning:** The timeout is local to each broker and requires no cross-broker coordination, making it robust against the exact failure mode it protects against (peer unavailability). A 60-second timeout is forgiving enough for loaded systems while still bounding the stall. The warning log on timeout provides operational visibility without spam.

---

## 5. Separating Snapshot Observability from Safety

**Summary:** The orchestrator's `_snapshot_monitor` originally only detected snapshots by querying peers for stored replicas. When replication failed silently, snapshots became invisible. The fix queries each broker's `/status` directly for its latest local snapshot.

**Learning:** Using peer replication as the *only* observability signal created "green lies" — the benchmark would fail while the system appeared healthy from a recovery perspective. The critical insight was that observability (did a snapshot happen?) and safety (is it replicated for recovery?) must use independent detection paths. Direct `/status` queries cut polling from O(n²) to O(n) and made the benchmark honest about what was actually happening.

**Reasoning:** The broker already knows it completed a snapshot — it logs `"snapshot complete"`. Asking it directly is the most reliable signal. Peer replication remains the safety mechanism for Path A recovery, but it is no longer the bottleneck for visibility. This separation follows the principle that metrics and health checks should query primary sources, not derived state.

---

## 6. Recovery Path A vs Path B

**Summary:** When a broker dies, the orchestrator chooses between Path A (spin up replacement, restore from snapshot) and Path B (redistribute subscribers to surviving brokers) based on snapshot freshness.

**Learning:** There is no single "best" recovery strategy — it depends on the current system state. Path A preserves all state (subscribers, dedup window, peer list) but requires a fresh snapshot and a functioning replacement. Path B loses message history but keeps the system live when snapshots are stale or missing. The 30-second freshness threshold was chosen because it aligns with the snapshot interval and failure detection window, meaning Path A is available roughly 50% of the time in steady state. Having two paths made the demo more interesting and the system more resilient.

**Reasoning:** Path A demonstrates the full Chandy-Lamport recovery story — the snapshot was taken for this exact purpose. Path B is the graceful degradation when that story can't play out. The threshold is a policy knob, not a physical law; it was tuned for demo reliability rather than theoretical optimality.

---

## 7. Authoritative Snapshot Recovery (Orchestrator-Selected)

**Summary:** The original recovery flow had the orchestrator select a snapshot, then the replacement broker re-query peers and potentially select a different one. The authoritative recovery plan makes the orchestrator's selection binding by passing the snapshot directly in `POST /recover`.

**Learning:** Splitting a decision between two components creates a correctness hazard. The orchestrator and replacement broker used different selection logic (orchestrator: freshest by timestamp; broker: first non-null response), which could lead to restoring from a stale snapshot even when a fresh one was available. The fix centralizes the selection policy in one place — the orchestrator — and treats the replacement broker as an executor. This mirrors production control-plane design where policy and execution are separated.

**Reasoning:** Single decision authority is a prerequisite for auditable, deterministic recovery. It also simplifies debugging — the log shows exactly which snapshot was selected and that it was the one restored. The snapshot payload is small enough to inline in an HTTP request, avoiding the complexity of an object store or signed artifact fetch.

---

## 8. Bootstrap Server for Peer Discovery

**Summary:** A centralized bootstrap server maintains a `registered_brokers` set and broadcasts `MembershipUpdate` to all registered brokers on every join.

**Learning:** Centralized discovery is simple and correct for small clusters (n < 20), but it creates O(n²) broadcast traffic and a single point of partial failure. The bootstrap never removes dead brokers from its set — a `DELETE /deregister` endpoint had to be added later for the recovery path to work correctly. The lesson is that even "simple" centralized services need explicit removal semantics; otherwise, replacement brokers receive stale peer lists and waste cycles connecting to dead addresses.

**Reasoning:** For a demo system with 3–10 brokers, a full gossip membership protocol (like SWIM) would be overkill. The bootstrap is a pragmatic shortcut that gets peer discovery working in ~50 lines of code. The O(n²) broadcast is acceptable at this scale and can be replaced later without changing the broker protocol.

---

## 9. Heartbeat Interval and Timeout as Paired Config

**Summary:** The heartbeat *interval* was configurable via `config.yaml`, but the peer eviction *timeout* was hardcoded to 15 seconds, causing mismatched behavior when the interval was tuned.

**Learning:** Parameters that are logically coupled must be exposed together. When the heartbeat interval was reduced for faster demos, the hardcoded 15-second timeout meant peers were falsely evicted. Conversely, increasing the interval without adjusting the timeout caused slow failure detection. The fix threaded `heartbeat_timeout` through the `GossipBroker` constructor the same way as `fanout`, `ttl`, and `snapshot_interval`. Every production system (Kafka, Consul, etcd) treats these as paired knobs.

**Reasoning:** Operators tune interval and timeout together based on network conditions and SLOs. Hardcoding one while exposing the other is a classic source of "works on my machine, breaks in staging" bugs. The change was 4 lines but prevented an entire class of misconfiguration issues.

---

## 10. Publisher Dead-Broker Cooldown

**Summary:** Publishers track failed brokers in `_dead_brokers` and skip them for 30 seconds, preventing connection-error spam after a broker dies.

**Learning:** Client-side resilience can't depend on the orchestrator. Publishers have no knowledge of the control plane — they just need to route around failed connections. The cooldown pattern is simpler than querying the orchestrator, has no additional network dependency, and handles all failure modes (not just broker death, but also temporary network issues). This is how production message clients work (e.g., Kafka producer `reconnect.backoff.ms`).

**Reasoning:** The publisher's contract is "send messages to available brokers." Adding an orchestrator dependency would violate separation of concerns and create a new failure mode (what if the orchestrator is unreachable?). The 30-second cooldown aligns with the snapshot freshness threshold, so replacement brokers are definitely running by the time the cooldown expires.

---

## 11. Subscriber Epoch-Based Reconnect

**Summary:** Subscribers use a `_connection_epoch` counter and `threading.Lock` to serialize concurrent reconnect attempts, preventing race conditions between Ping/Pong timeout detection and `BrokerRecoveryNotification` push messages.

**Learning:** A boolean `is_reconnecting` flag has undefined behavior when two threads check it simultaneously. Kafka's consumer group rebalancing, LinkedIn Brooklin, and most production reconnect systems use an epoch/generation pattern for exactly this reason. The epoch makes one thread win cleanly — whoever increments the epoch owns the reconnect — while stale threads self-discard. The stop-reconnect-resume pattern (briefly stopping the receive loop to send `SubscribeRequest` and wait for `SubscribeAck`) is correct because the receive loop and subscribe call share the same socket receive queue.

**Reasoning:** Concurrent reconnect attempts are inevitable in a system with both push notifications and pull-based health checks. The epoch pattern is a well-established solution that costs almost nothing (one integer and one lock) while eliminating an entire class of race conditions.

---

## 12. Docker State Reconciliation

**Summary:** The orchestrator's in-memory `_components` dict could become stale when containers were killed externally (e.g., via `docker kill` or chaos scripts). A `_sync_component_status` method queries Docker for real container state on every read.

**Learning:** Any cache of external system state will diverge from reality. The orchestrator tracked component status based on its own lifecycle operations, but external events (container crashes, manual kills, Docker daemon restarts) bypassed that tracking. The fix was to reconcile status at read time by querying the Docker SDK for each container's actual state. This is the same pattern Kubernetes uses with its kubelet sync loop.

**Reasoning:** Eventual consistency between the orchestrator's view and Docker's reality is unavoidable. Polling on read is the simplest correct approach — it adds a small latency cost to API calls but guarantees the dashboard and recovery logic never act on stale state. Optimistic caching would require handling every possible Docker event, which is error-prone.

---

## 13. WebSocket Exception Handling

**Summary:** The `EventBroadcaster.emit()` method originally caught bare `Exception`, silently swallowing unrelated errors. The fix narrows the catch to specific connection-related exceptions and adds debug logging.

**Learning:** Overly broad exception handlers hide real bugs. The original code caught `Exception` around `ws.send_text()`, which meant that any bug in the event serialization logic (or even a typo in the message formatting) would be silently ignored. The fix narrowed the catch to `WebSocketDisconnect`, `RuntimeError`, and `ConnectionError` — the actual failures that occur when a client disconnects — and added debug logging for visibility.

**Reasoning:** WebSocket connections are inherently flaky; clients disconnect without warning. The broadcaster needs to handle that gracefully, but it shouldn't mask programming errors. Specific exception types plus logging achieve both goals: robust delivery to live clients and audible signals when something unexpected breaks.

---

## 14. Keeping Quiet Peer Sockets Alive

**Summary:** Persistent TCP connections between brokers were closed when no messages arrived within the socket timeout window, even though the peer was healthy. The fix treats post-handshake socket timeouts as idle polls rather than connection failures.

**Learning:** TCP socket timeouts are ambiguous — they can mean "the peer is dead" or "the peer is quiet." In a gossip protocol, brokers may legitimately have no traffic for a peer for seconds at a time (e.g., when message volume is low). Closing connections on every timeout created unnecessary reconnection churn and broke snapshot marker propagation. The fix distinguishes between the initial handshake (where a timeout is a real failure) and steady-state operation (where a timeout means "check again later").

**Reasoning:** Persistent connections are a performance optimization — establishing a new TCP connection per message would be prohibitively expensive. But persistence requires handling idle periods correctly. The change restores the broader snapshot freshness window that the recovery path expects, because stable peer sockets are a prerequisite for reliable marker propagation.

---

## 15. Structured Logging with Event Types

**Summary:** All logging uses a `JSONFormatter` with stable `event_type` fields, enabling LogQL queries like `{event_type="broker_declared_dead"}` instead of substring matching.

**Learning:** Free-text logs are fine for human reading but useless for automated querying, alerting, and dashboarding. The investment in a structured logging pipeline (with `QueueHandler`/`QueueListener` for non-blocking async delivery) paid off when building Grafana dashboards — every event type was already queryable without additional instrumentation. The key discipline was attaching `event_type` at the call site, not trying to infer it from the message string later.

**Reasoning:** The observability stack (OTel → Loki → Grafana) only works if the data plane produces structured data. Doing this from the start meant no retrofitting was needed when dashboards were built. The `BoundLogger` pattern (injecting per-instance context like `component_id` and `address`) eliminates repetitive boilerplate while ensuring every log line carries enough context for debugging.

---

## 16. QueueListener for Non-Blocking Logging

**Summary:** A `QueueHandler` queues log records on the calling thread, and a background `QueueListener` thread drains them to stdout/file/OTLP. Application code never blocks on I/O.

**Learning:** Synchronous logging on the hot path is a silent performance killer. The benchmark audit identified `self.log.info()` inside the subscriber receive loop as a likely contributor to the ~17-second latency — structured JSON formatting plus Docker log driver overhead can take ~150ms per message at volume. The QueueListener pattern decouples log emission from log delivery, ensuring that a slow sink (network, disk, or OTLP export) never stalls message processing.

**Reasoning:** Logging is a side effect, not a primary function. In a message broker, the primary function is routing messages. Blocking the receive loop on log I/O violates that priority. The QueueListener pattern is standard in production Python systems and requires only stdlib components.

---

## 17. Status Server Pattern (ThreadingHTTPServer)

**Summary:** Every component exposes `GET /status` via Python's stdlib `ThreadingHTTPServer` on a daemon thread, with zero external HTTP dependencies.

**Learning:** For simple HTTP surfaces (one endpoint, JSON responses), pulling in FastAPI/Flask/Starlette is unnecessary bloat. The `ThreadingHTTPServer` pattern with a dynamically generated handler subclass (injecting the component reference via a class attribute) provides everything needed in ~30 lines per component. The tradeoff is no auto-generated OpenAPI docs and manual path parsing, but for a single endpoint per component, that is trivial. The bigger lesson is that dependency minimization in the data plane reduces container image size, startup time, and attack surface.

**Reasoning:** The data plane (brokers, subscribers, publishers) needs to be lightweight and fast-starting. The orchestrator uses FastAPI because it has many endpoints and needs WebSocket support. Applying the same heavy framework to the data plane would be architectural overkill. Using stdlib HTTP also makes the data plane easier to reason about — there is no middleware stack, no routing table, just a single handler method.

---

## 18. Benchmark Metric Reliability

**Summary:** The benchmark suite underwent multiple rounds of hardening to ensure metrics were computed correctly: window classification, event ordering validation, generation stability checks, and retry-with-backoff for sample windows.

**Learning:** "Does the benchmark produce a number?" is a much lower bar than "Is the number correct?" Early versions of the benchmarks produced throughput and latency values that were shaped right but magnitude-wrong. The validation layer (rejecting windows with >50% low-rate samples, enforcing event ordering like `dead → recovery_started → recovered`, and gating chart generation on viable output) was added after discovering that the numbers didn't match physical intuition. The lesson is that benchmark infrastructure deserves the same rigor as production code — flaky or wrong benchmarks drive bad optimization decisions.

**Reasoning:** Benchmarks are the primary signal for whether the system is improving or regressing. If they are unreliable, engineering effort is wasted chasing phantom improvements or missing real regressions. The validation rules encode physical plausibility (e.g., delivery ratio > 0.7, monotonically non-decreasing latency samples indicate queue backlog) that serves as a sanity check on the raw data.

---

## 19. Latency Diagnosis: Queue Backlog vs Unit Bug

**Summary:** Latency measurements showed ~17 seconds P50, initially suspected to be a unit conversion bug. Investigation revealed it was genuine queue backlog — the subscriber drain rate was ~1/6 of the arrival rate.

**Learning:** When measurements are surprising, the first instinct is often "the measurement is wrong." The audit verified that `time.monotonic_ns()` was used consistently and the ns→µs conversion was correct, forcing the team to confront the harder truth: the system was actually that slow. The monotonically non-decreasing latency samples (7s → 26s across the measurement window) are the textbook signature of a queue draining slower than it fills. This shifted focus from "fix the metric" to "fix the bottleneck" — likely synchronous logging on the hot path.

**Reasoning:** Accurate metrics are uncomfortable but necessary. A unit bug would have been easy to fix; a real performance bottleneck requires architectural work. The diagnostic instrumentation (queue-depth gauge, per-stage timing) was added specifically to distinguish between "drain-rate-bound" and "arrival-anomaly" hypotheses.

---

## 20. Snapshot Benchmark: 0 Rounds Captured

**Summary:** The snapshot benchmark consistently reported 0 rounds across all broker counts because the orchestrator only detected snapshots via peer replication, which was silently skipped when peer lists were empty.

**Learning:** A benchmark that always returns zero is actually a gift — it reveals a systemic blind spot. The root cause was a chain of silent failures: brokers evict each other → peer list becomes empty → snapshot replication is skipped with no warning → orchestrator queries peers and gets 404 → no event emitted → benchmark times out. Each step was individually reasonable, but together they created a completely invisible failure mode. The fix required adding explicit visibility at every step: `WARNING` logs when replication is skipped, direct `/status` queries for observability, and a timeout watchdog for safety.

**Reasoning:** Distributed systems fail in chains, not in single events. The snapshot benchmark failure was a cascade, not a single bug. Fixing it required understanding the entire lifecycle from timer initiation through marker propagation to replication and detection. This reinforced the principle that every skip/early-return path in distributed code needs a log line — silent success is acceptable, silent failure is not.

---

## 21. CLI Args Bypassing Static Config

**Summary:** The original CLI scripts required component IDs to exist in `config.docker.yaml`, blocking dynamic orchestration. The fix allows `--host`, `--port`, `--broker-host`, `--range-low`, and `--range-high` to override config entirely.

**Learning:** Static configuration and dynamic orchestration are fundamentally in tension. A config file that lists all brokers assumes the topology is known at write time, but the orchestrator creates components on demand. The fix was to make CLI args authoritative when provided, falling back to config only when omitted. This is backward-compatible (existing docker-compose commands don't pass the new args) but unlocks dynamic creation.

**Reasoning:** The orchestrator is the single source of truth for topology in Phase 2. Components must trust the orchestrator's instructions over a static config file. The `--broker-host/--broker-port` flags are a pragmatic bridge that avoids redesigning subscriber discovery from scratch.

---

## 22. Pickle as Serialization Format

**Summary:** All network messages use Python `pickle` for serialization. A plan exists to replace it with `orjson` for a projected 3–5x throughput improvement.

**Learning:** `pickle` is the dominant CPU cost in the broker hot path — it runs ~10,000 times per second at load. While pickle is convenient (works with arbitrary Python objects), it is slow, Python-specific, and a security risk (arbitrary code execution on `loads`). The planned replacement with `orjson` requires building a type registry and custom encoders/decoders for `NodeAddress`, `PayloadRange`, and `Message`. The lesson is that serialization format decisions made early for convenience become expensive to change later.

**Reasoning:** Pickle was chosen for rapid prototyping — it requires zero schema definitions. For a demo system, that was the right call. But as the benchmark audit showed, it is now the bottleneck for hitting the 10-broker throughput SLO. The planned `orjson` migration is deferred because it is a large change (16 message types, 3 custom coders) with risk of subtle serialization bugs, but the diagnostic evidence makes it a priority for the next optimization pass.

---

## 23. FastAPI Orchestrator with Docker SDK

**Summary:** The orchestrator is a FastAPI service that manages containers via the Docker SDK for Python, exposing REST and WebSocket endpoints for the React dashboard.

**Learning:** Using the Docker SDK directly (rather than shelling out to `docker` CLI) provides type safety, better error handling, and programmatic access to container metadata. However, it requires mounting `/var/run/docker.sock` into the orchestrator container, which is a security consideration. The orchestrator's `_components` dict is an in-memory cache of container state that must be reconciled with Docker's actual state on every read to avoid stale data.

**Reasoning:** FastAPI was chosen because it provides auto-generated OpenAPI docs, WebSocket support, and async request handling out of the box. The Docker SDK integration keeps container management logic in Python rather than shell scripts, making it testable and auditable. The tradeoff is that the orchestrator itself is a single point of failure — there is no HA for the control plane in the current architecture.

---

## 24. D3 Force-Directed Topology Graph

**Summary:** The React dashboard uses D3.js for a force-directed graph visualization of the broker mesh, with animated particles representing message flow and color-coded nodes for component types.

**Learning:** Real-time graph visualization is harder than it looks. D3's force simulation is powerful but requires careful lifecycle management — adding or removing nodes while the simulation is running can cause jarring jumps. The edge breakage and rebuild animations (fading to red, dissolving, growing back green) were added because the topology graph is the centerpiece of the dashboard — it must communicate failure and recovery without requiring the viewer to read text. The lesson is that the most impactful UI investments are in making the system's behavior *visually obvious*.

**Reasoning:** The dashboard's job is to tell the resilience story in under 5 seconds. A text-based event log requires mental reconstruction; a graph with animated edges shows the story directly. D3 was chosen over Canvas or WebGL because the node count is small (< 20) and the force-directed layout communicates mesh topology intuitively.

---

## 25. Observability Stack Design

**Summary:** The full observability pipeline (structured logs → OTel Collector → Loki/Prometheus → Grafana) was designed from the start, with provisioned dashboards and low-cardinality stream labels.

**Learning:** Observability is not an afterthought — it shapes architecture decisions. The choice to use stable `event_type` fields, the decision to keep `recovery_id`/`snapshot_id` as log body fields (not stream labels), and the separation of metrics from logs all had downstream consequences for dashboard design and query performance. The Loki stream label cardinality rule (only `service_name`, `component_type`, `event_type` as labels) prevents index explosion while still enabling fast filtering.

**Reasoning:** A demo system without observability is just a black box. The goal was to show that the system is not only resilient but also *understandable* — operators can trace a failure from detection through recovery using correlated logs and metrics. Pre-provisioned dashboards eliminate setup friction, which is critical for a one-command demo.

---

## 26. Chaos Demo UI Enhancements

**Summary:** The "Create Chaos" workflow was enhanced with a visual recovery timeline, edge breakage/rebuild animations on the topology graph, and explicit display of the recovery path decision rationale.

**Learning:** A demo's impact depends on how quickly a viewer understands what happened. The original chaos UI showed a burst of text events that required mental parsing. The timeline component answers three questions in under 5 seconds: something broke, the system is handling it, it recovered. The recovery time number is the "hero metric" — it makes the system's resilience tangible. Adding the decision rationale ("Fresh snapshot found (2.1s old) → Path A") makes the system feel intelligent, not just reactive.

**Reasoning:** Every pixel in the chaos panel must serve the resilience story. Decorative chrome would distract; the timeline, edge animations, and decision text are all functional. The 8-second auto-clear after recovery gives viewers enough time to read the result without permanently cluttering the UI.

---

## 27. Makefile as Demo Orchestrator

**Summary:** The `Makefile` provides single-command targets (`make demo`, `make clean`, `make status`) that abstract Docker Compose operations, container health checks, and API seeding.

**Learning:** A project with a complex startup sequence needs a single entry point. Without `make demo`, the setup would require: build images, start compose, wait for healthchecks, call seed API, verify status. The Makefile encodes that sequence so it is reproducible and documented. The lesson is that developer experience is part of the product — if the demo is hard to start, people won't see it.

**Reasoning:** Make is a ubiquitous build tool; requiring a custom script or a specific shell would limit adoption. The Makefile also serves as executable documentation — reading the targets explains the startup sequence better than prose.

---

## 28. Test-Driven Development for Phase 1

**Summary:** Phase 1 failover infrastructure was built with tests written before or alongside each feature, resulting in 99 passing tests across 8 unit files and one integration suite.

**Learning:** Writing tests after the fact is tempting but produces weaker coverage. The Phase 1 plan explicitly required tests for every item (NodeAddress fix, HealthMonitor, RecoveryManager, subscriber reconnect, publisher cooldown, etc.) and the coverage target was 100% of new code paths. The discipline paid off — the e2e integration test (`test_failover_e2e.py`) caught bugs that unit tests missed, particularly around timing and cross-component interaction.

**Reasoning:** Distributed systems are hard to test manually — they require multiple processes, network timeouts, and failure injection. Automated tests are the only way to iterate quickly without regressing. The integration test that launches real `GossipBroker` instances via subprocess is expensive (~20s) but essential for validating the actual recovery flow.

---

## 29. Port Convention: TCP + 10000 for Status

**Summary:** Every component uses a consistent port convention: TCP port for data plane, `TCP port + 10000` for HTTP status.

**Learning:** Consistent conventions reduce cognitive load and configuration errors. The `+10000` offset makes it trivial to compute a component's status port from its data port without looking up a mapping table. This convention is applied universally (bootstrap, broker, subscriber, publisher) so that health checks, metrics polling, and manual debugging all follow the same rule.

**Reasoning:** Arbitrary port assignments create documentation burden and operational toil. A simple arithmetic rule is self-documenting and works at any scale. The `--status-port` CLI override exists for environments where the convention conflicts with existing services, but the default is always `port + 10000`.

---

## 30. UInt8 Payload Constraint

**Summary:** Messages carry a single `UInt8` payload (0–255), and subscribers subscribe to `PayloadRange` intervals within that space.

**Learning:** Constraining the domain to a single byte simplifies the system enormously. The broker uses a 256-element array of subscriber sets (`buckets[0..255]`), making routing O(1). Partitioning the payload space among subscribers is trivial arithmetic. The tradeoff is that Aether is not a general-purpose message broker — it can't carry arbitrary payloads. But for demonstrating distributed concepts, the simplicity is a feature, not a bug.

**Reasoning:** The project's goal is to demonstrate distributed systems mastery, not to compete with Kafka. A single-byte payload keeps the data plane simple while still supporting interesting topology problems (range-based subscription, payload partitioning, fanout). The `UInt8` type (a constrained `int` subclass) enforces the invariant at the language level, preventing invalid payloads from propagating.

---

## 31. Bounded `seen_messages` Deduplication Set

**Summary:** Each broker maintains a `seen_messages: Set[str]` of UUIDs to prevent gossip loops, capped at 10,000 entries.

**Learning:** Unbounded sets in long-running processes are memory leaks. The deduplication set is the most frequently accessed data structure in the broker hot path — every message triggers a membership check. Capping it at 10,000 entries (a snapshot captures a subset for recovery continuity) prevents unbounded growth while maintaining correctness for the gossip TTL window. The lesson is that even "simple" data structures need operational limits.

**Reasoning:** The TTL mechanism (decrementing by 1 per hop) already limits propagation depth, so old UUIDs will never reappear in practice. The 10,000-entry cap is generous enough that no legitimate message is affected, but strict enough that memory usage stays bounded. Snapshots include the current dedup window so recovery preserves continuity.

---

## 32. Retry with Backoff for Recovery Readiness

**Summary:** After creating a replacement broker container, the orchestrator polls `GET /status` every 500ms until it returns 200, rather than immediately sending `POST /recover`.

**Learning:** Container creation and process readiness are not the same event. Docker starts the container in milliseconds, but the broker process inside takes 2–5 seconds to bind its TCP socket and start the HTTP server. Sending `POST /recover` immediately results in `ConnectionRefused` and silent failure. The retry-with-backoff pattern is a correctness requirement, not an optimization. This is a universal lesson for any system that orchestrates processes inside containers.

**Reasoning:** The orchestrator cannot assume anything about the replacement broker's internal state. Polling is the only reliable signal. The 500ms interval and 10-second timeout are tuned for Docker startup latency on typical developer hardware. The same `httpx.AsyncClient` is reused for efficiency.

---

## 33. Bootstrap Deregister on Intentional Deletion

**Summary:** The `DELETE /deregister` endpoint was added to remove brokers from the bootstrap server's `registered_brokers` set when they are intentionally removed or recovered via redistribution.

**Learning:** The bootstrap server originally only added brokers — never removed them. After a broker died, its address stayed in the set forever. When a replacement broker started, it received the dead broker's address in its `MembershipUpdate` and wasted cycles trying to gossip to a dead IP. The fix required explicit deregistration in both the intentional deletion path and the redistribution recovery path. The lesson is that registration services need symmetric add/remove APIs; append-only semantics create operational debt.

**Reasoning:** The bootstrap is a simple in-memory set, not a distributed consensus store. Explicit deregistration is the minimal correct solution. A more robust system would use gossip-based membership (like SWIM) where failed nodes are detected and removed by the protocol itself, but that is out of scope for the current architecture.

---

*Document generated from project history, design docs, commit messages, and post-mortem analyses.*
