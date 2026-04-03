"""Prometheus metrics registry for the Aether orchestrator.

All metric objects are defined at module level as singletons. This means:
  - They are created once when this module is first imported.
  - Any module that imports from here (main.py, recovery.py, etc.) shares
    the exact same metric objects and writes to the same in-process registry.
  - The /metrics endpoint in main.py reads from this same registry and
    serialises everything into Prometheus text format for Prometheus to scrape.

Why a separate module instead of defining metrics inline in main.py or recovery.py?
  - main.py and recovery.py both need to update the same counters. If the metrics
    were defined in main.py, recovery.py would have to import from main.py, which
    creates a circular import (main.py already imports RecoveryManager).
  - Keeping metrics here gives both modules a shared, neutral import target.

Metric types used:
  Counter  — monotonically increasing. Use for events that accumulate (recoveries,
             reconnects). Never decreases, even across process restarts of the
             _instrumented code_ — though the in-process counter resets to 0 if
             the orchestrator itself restarts.
  Gauge    — can go up or down. Use for current state (component_up, peer_count).
             Set it to the observed value on each poll.
  Histogram — records a distribution of observations (e.g. durations). Prometheus
              stores bucket counts so you can query percentiles with
              histogram_quantile(0.95, ...).

Label strategy:
  Labels let you slice a metric by dimension (e.g. per broker, per recovery path).
  Keep label cardinality low — each unique label combination is a separate time
  series. Never use high-cardinality values like UUIDs or raw addresses as labels.
  Good: component_type, broker_id (small finite set), recovery_path, result.
  Bad:  recovery_id, snapshot_id, msg_id.
"""

from prometheus_client import Counter, Gauge, Histogram

# ---------------------------------------------------------------------------
# Recovery metrics
# ---------------------------------------------------------------------------

broker_recoveries_total = Counter(
    "aether_broker_recoveries_total",
    # What it counts: every completed recovery attempt, whether it succeeded or
    # failed. The recovery_path label distinguishes the two recovery strategies
    # (replacement = spin up a new broker from snapshot; redistribution = move
    # orphaned subscribers to surviving brokers). The result label tells you
    # whether it worked.
    "Total broker recovery attempts, labelled by path and outcome.",
    ["recovery_path", "result"],  # e.g. replacement/success, redistribution/failure
)

recovery_duration_seconds = Histogram(
    "aether_recovery_duration_seconds",
    # What it measures: wall-clock time from when handle_broker_dead() is entered
    # (i.e. the moment the health monitor fires) to when the recovery path
    # completes (success or failure). This is the most important SLO metric —
    # it tells you how long subscribers were without a broker.
    "End-to-end duration of a broker recovery attempt in seconds.",
    buckets=[1, 2, 5, 10, 15, 30, 60],
    # Bucket rationale:
    #   1s  — replacement with a warm snapshot should finish here
    #   5s  — reasonable upper bound for healthy replacement
    #   15s — health_timeout default; replacement gives up here
    #   30s — recovery_timeout default for POST /recover
    #   60s — anything above this is a very bad day
)

# ---------------------------------------------------------------------------
# Subscriber metrics
# ---------------------------------------------------------------------------

subscriber_reconnects_total = Counter(
    "aether_subscriber_reconnects_total",
    # What it counts: subscriber reassignments triggered by the orchestrator
    # during redistribution recovery. Each subscriber moved to a new broker
    # increments this by 1. This is a control-plane view — it only counts
    # reconnects the orchestrator orchestrated, not self-healed reconnects
    # that subscribers negotiated directly (those are visible in Loki via
    # event_type="subscriber_reconnected").
    "Total subscriber reassignments performed by the orchestrator during recovery.",
)

# ---------------------------------------------------------------------------
# Component health metrics
# ---------------------------------------------------------------------------

component_up = Gauge(
    "aether_component_up",
    # What it tracks: 1 if the component container is in RUNNING state according
    # to the orchestrator's DockerManager, 0 otherwise. Updated every 15 seconds
    # by the background metrics updater in main.py.
    # Use this to build alerts: alert when sum(aether_component_up{component_type="broker"}) < 2.
    "Whether a managed component is currently running (1 = up, 0 = down).",
    ["component_type", "component_id"],  # e.g. broker/1, subscriber/3
)

broker_peer_count = Gauge(
    "aether_broker_peer_count",
    # What it tracks: number of peer brokers visible in a broker's gossip mesh,
    # as reported by its /status endpoint. In a healthy 3-broker cluster this
    # should be 2 for each broker. Dropping to 0 is a strong signal that a
    # broker is isolated or dead. Polled from docker_mgr.get_metrics() in main.py.
    "Current number of peer brokers visible to each broker in the gossip mesh.",
    ["broker_id"],
)

broker_subscriber_count = Gauge(
    "aether_broker_subscriber_count",
    # What it tracks: number of active subscribers connected to each broker,
    # as reported by the broker's /status endpoint. Useful for load distribution
    # analysis — after redistribution recovery you should see this rebalance
    # across surviving brokers.
    "Current number of subscribers connected to each broker.",
    ["broker_id"],
)

# ---------------------------------------------------------------------------
# Message throughput metrics
# ---------------------------------------------------------------------------

messages_published_total = Counter(
    "aether_messages_published_total",
    # What it counts: cumulative messages processed by all brokers combined,
    # derived from broker /status endpoint polling in the orchestrator's background
    # updater. This is an _approximation_ — the orchestrator only increments this
    # counter by the positive delta between consecutive polls, so brief counter
    # resets (e.g. broker container restart) won't inflate the count, but may
    # cause a small gap. For precise per-message accounting, use Loki with
    # event_type="message_published" instead.
    "Cumulative messages processed across all brokers (approximated from /status polling).",
)

