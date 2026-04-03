"""Docker SDK wrapper for managing Aether pub-sub containers."""

from __future__ import annotations

import json
import logging
import urllib.request
from urllib.error import URLError

import docker
import docker.errors

from .models import (
    BootstrapInfo,
    BrokerMetrics,
    ComponentInfo,
    ComponentStatus,
    ComponentType,
    CreateBrokerRequest,
    CreatePublisherRequest,
    CreateSubscriberRequest,
    MetricsResponse,
    SnapshotStatusResponse,
    SystemState,
    TopologyEdge,
    TopologyNode,
    TopologyResponse,
)
from .settings import settings

logger = logging.getLogger(__name__)

_DOCKER_TO_COMPONENT_STATUS: dict[str, ComponentStatus] = {
    "running": ComponentStatus.RUNNING,
    "created": ComponentStatus.STARTING,
    "restarting": ComponentStatus.STARTING,
    "removing": ComponentStatus.STOPPING,
    "paused": ComponentStatus.STOPPED,
    "exited": ComponentStatus.STOPPED,
    "dead": ComponentStatus.ERROR,
}


class DockerManager:
    def __init__(self) -> None:
        self.client = docker.from_env()
        self._components: dict[str, ComponentInfo] = (
            {}
        )  # container_name -> ComponentInfo
        self._ensure_network()

    def _ensure_network(self) -> None:
        """Create the Docker network if it doesn't already exist."""
        try:
            self.client.networks.get(settings.docker_network)
        except docker.errors.NotFound:
            self.client.networks.create(settings.docker_network, driver="bridge")
            logger.info("Created Docker network %s", settings.docker_network)

    # --- Broker Lifecycle ---

    def create_broker(self, req: CreateBrokerRequest) -> ComponentInfo:
        """Spin up a new broker container and register it."""
        broker_id = req.broker_id or self._next_id(ComponentType.BROKER)
        hostname = f"broker-{broker_id}"
        container_name = f"aether-broker-{broker_id}"

        # All brokers use the same internal port — they're isolated per container.
        # Host ports vary so the orchestrator (on the host) can reach each one.
        internal_port = 8000
        internal_status_port = 18000
        host_port = 8000 + broker_id * 10
        host_status_port = 18000 + broker_id * 10

        container = self.client.containers.run(
            settings.aether_image,
            command=(
                f"aether-broker --broker-id {broker_id} --host {hostname} "
                f"--port {internal_port} --status-port {internal_status_port}"
            ),
            name=container_name,
            hostname=hostname,
            network=settings.docker_network,
            environment={"AETHER_CONFIG": "/app/config.docker.yaml"},
            detach=True,
            ports={
                f"{internal_port}/tcp": host_port,
                f"{internal_status_port}/tcp": host_status_port,
            },
            labels={"component_type": "broker", "component_id": str(broker_id)},
        )

        info = ComponentInfo(
            component_type=ComponentType.BROKER,
            component_id=broker_id,
            container_name=container_name,
            container_id=container.id,
            hostname=hostname,
            internal_port=internal_port,
            internal_status_port=internal_status_port,
            host_port=host_port,
            host_status_port=host_status_port,
        )
        self._components[container_name] = info
        info.status = ComponentStatus.RUNNING
        logger.info("Started broker %d (container %s)", broker_id, container.id[:12])
        return info

    def remove_broker(self, broker_id: int) -> ComponentInfo:
        """Stop and remove a broker container."""
        remove_container_info = self._get_component(ComponentType.BROKER, broker_id)
        container = self.client.containers.get(remove_container_info.container_id)
        container.stop(timeout=10)
        container.remove()
        remove_container_info.status = ComponentStatus.STOPPED
        del self._components[remove_container_info.container_name]
        logger.info(
            "Removed broker %d (container %s)",
            broker_id,
            remove_container_info.container_id[:12],
        )
        return remove_container_info

    def force_kill_broker(self, broker_id: int) -> ComponentInfo:
        """SIGKILL a broker container without removing it from tracking.

        Simulates an unplanned crash: the container dies but the orchestrator's
        _components entry stays intact and status remains RUNNING. The health
        monitor (or the chaos endpoint directly) will detect the failure and
        trigger the recovery pipeline.
        """
        kill_container_info = self._get_component(ComponentType.BROKER, broker_id)
        container = self.client.containers.get(kill_container_info.container_id)
        container.kill()
        logger.warning(
            "Force-killed broker %d (container %s) — simulating crash",
            broker_id,
            kill_container_info.container_id[:12],
        )
        return kill_container_info

    # --- Publisher Lifecycle ---

    def create_publisher(self, req: CreatePublisherRequest) -> ComponentInfo:
        """Spin up a new publisher container."""
        publisher_id = req.publisher_id or self._next_id(ComponentType.PUBLISHER)
        hostname = f"publisher-{publisher_id}"
        container_name = f"aether-publisher-{publisher_id}"

        internal_port = 9000
        internal_status_port = 19000
        host_port = 9000 + publisher_id * 10
        host_status_port = 19000 + publisher_id * 10

        broker_ids = req.broker_ids or self._running_broker_ids()
        broker_hosts = " ".join(f"--broker-host broker-{bid}" for bid in broker_ids)

        container = self.client.containers.run(
            settings.aether_image,
            command=(
                f"aether-publisher --publisher-id {publisher_id} --host {hostname} "
                f"--port {internal_port} --status-port {internal_status_port} "
                f"{broker_hosts} --broker-port 8000 "
                f"--interval {req.interval}"
            ),
            name=container_name,
            hostname=hostname,
            network=settings.docker_network,
            environment={"AETHER_CONFIG": "/app/config.docker.yaml"},
            detach=True,
            ports={
                f"{internal_port}/tcp": host_port,
                f"{internal_status_port}/tcp": host_status_port,
            },
            labels={"component_type": "publisher", "component_id": str(publisher_id)},
        )

        info = ComponentInfo(
            component_type=ComponentType.PUBLISHER,
            component_id=publisher_id,
            container_name=container_name,
            container_id=container.id,
            hostname=hostname,
            internal_port=internal_port,
            internal_status_port=internal_status_port,
            host_port=host_port,
            host_status_port=host_status_port,
            broker_ids=broker_ids,
            publish_interval=req.interval,
        )
        self._components[container_name] = info
        info.status = ComponentStatus.RUNNING
        logger.info(
            "Started publisher %d (container %s)", publisher_id, container.id[:12]
        )
        return info

    def remove_publisher(self, publisher_id: int) -> ComponentInfo:
        """Stop and remove a publisher container."""
        info = self._get_component(ComponentType.PUBLISHER, publisher_id)
        container = self.client.containers.get(info.container_id)
        container.stop(timeout=10)
        container.remove()
        info.status = ComponentStatus.STOPPED
        del self._components[info.container_name]
        logger.info(
            "Removed publisher %d (container %s)", publisher_id, info.container_id[:12]
        )
        return info

    # --- Subscriber Lifecycle ---

    def create_subscriber(self, req: CreateSubscriberRequest) -> ComponentInfo:
        """Spin up a new subscriber container."""
        subscriber_id = req.subscriber_id or self._next_id(ComponentType.SUBSCRIBER)
        hostname = f"subscriber-{subscriber_id}"
        container_name = f"aether-subscriber-{subscriber_id}"

        internal_port = 9100
        internal_status_port = 19100
        host_port = 9100 + subscriber_id * 10
        host_status_port = 19100 + subscriber_id * 10

        range_args = f"--range-low {req.range_low} --range-high {req.range_high}"

        container = self.client.containers.run(
            settings.aether_image,
            command=(
                f"aether-subscriber --subscriber-id {subscriber_id} --host {hostname} "
                f"--port {internal_port} --status-port {internal_status_port} "
                f"--broker-host broker-{req.broker_id} --broker-port 8000 "
                f"{range_args}"
            ),
            name=container_name,
            hostname=hostname,
            network=settings.docker_network,
            environment={
                "AETHER_CONFIG": "/app/config.docker.yaml",
                "AETHER_ORCHESTRATOR_URL": settings.orchestrator_url,
            },
            detach=True,
            ports={
                f"{internal_port}/tcp": host_port,
                f"{internal_status_port}/tcp": host_status_port,
            },
            labels={"component_type": "subscriber", "component_id": str(subscriber_id)},
        )

        info = ComponentInfo(
            component_type=ComponentType.SUBSCRIBER,
            component_id=subscriber_id,
            container_name=container_name,
            container_id=container.id,
            hostname=hostname,
            internal_port=internal_port,
            internal_status_port=internal_status_port,
            host_port=host_port,
            host_status_port=host_status_port,
            broker_id=req.broker_id,
            range_low=req.range_low,
            range_high=req.range_high,
        )
        self._components[container_name] = info
        info.status = ComponentStatus.RUNNING
        logger.info(
            "Started subscriber %d (container %s)", subscriber_id, container.id[:12]
        )
        return info

    def remove_subscriber(self, subscriber_id: int) -> ComponentInfo:
        """Stop and remove a subscriber container."""
        info = self._get_component(ComponentType.SUBSCRIBER, subscriber_id)
        container = self.client.containers.get(info.container_id)
        container.stop(timeout=10)
        container.remove()
        info.status = ComponentStatus.STOPPED
        del self._components[info.container_name]
        logger.info(
            "Removed subscriber %d (container %s)",
            subscriber_id,
            info.container_id[:12],
        )
        return info

    # --- System State ---

    def get_system_state(self) -> SystemState:
        """Return current state of all managed components."""
        self._sync_component_status()
        brokers, publishers, subscribers = [], [], []
        for info in self._components.values():
            if info.component_type == ComponentType.BROKER:
                brokers.append(info)
            elif info.component_type == ComponentType.PUBLISHER:
                publishers.append(info)
            elif info.component_type == ComponentType.SUBSCRIBER:
                subscribers.append(info)

        bootstrap_status_port = settings.bootstrap_port + 10000
        raw = self._fetch_status(settings.bootstrap_host, bootstrap_status_port)
        bootstrap = (
            BootstrapInfo(
                host=raw["host"],
                port=raw["port"],
                status_port=raw["status_port"],
                registered_brokers=raw.get("registered_brokers", []),
                broker_count=raw.get("broker_count", 0),
                uptime_seconds=raw.get("uptime_seconds"),
            )
            if raw
            else None
        )

        return SystemState(
            brokers=brokers,
            publishers=publishers,
            subscribers=subscribers,
            bootstrap=bootstrap,
        )

    def get_topology(self) -> TopologyResponse:
        """Build a node/edge graph by querying each broker's /status endpoint."""
        self._sync_component_status()
        node_ids: set[str] = set()
        nodes: list[TopologyNode] = []
        for info in self._components.values():
            nodes.append(
                TopologyNode(
                    id=info.hostname,
                    component_type=info.component_type,
                    component_id=info.component_id,
                    status=info.status,
                )
            )
            node_ids.add(info.hostname)

        edges: list[TopologyEdge] = []
        seen_peer_pairs: set[frozenset[str]] = set()

        for info in self._components.values():
            if info.component_type == ComponentType.BROKER:
                raw = self._fetch_status(info.hostname, info.internal_status_port)
                for peer_str in raw.get("peers", []):
                    peer_host = peer_str.split(":")[0]
                    if peer_host not in node_ids:
                        continue
                    pair = frozenset({info.hostname, peer_host})
                    if pair in seen_peer_pairs:
                        continue
                    seen_peer_pairs.add(pair)
                    edges.append(
                        TopologyEdge(
                            source=info.hostname, target=peer_host, edge_type="peer"
                        )
                    )

            elif info.component_type == ComponentType.PUBLISHER:
                for bid in info.broker_ids or []:
                    edges.append(
                        TopologyEdge(
                            source=info.hostname,
                            target=f"broker-{bid}",
                            edge_type="publish",
                        )
                    )

            elif info.component_type == ComponentType.SUBSCRIBER:
                if info.broker_id is not None:
                    edges.append(
                        TopologyEdge(
                            source=info.hostname,
                            target=f"broker-{info.broker_id}",
                            edge_type="subscribe",
                        )
                    )

        return TopologyResponse(nodes=nodes, edges=edges)

    def get_metrics(self) -> MetricsResponse:
        """Aggregate metrics by polling each broker's /status endpoint."""
        self._sync_component_status()
        brokers, publishers, subscribers = [], [], []
        for info in self._components.values():
            if info.component_type == ComponentType.BROKER:
                brokers.append(info)
            elif info.component_type == ComponentType.PUBLISHER:
                publishers.append(info)
            elif info.component_type == ComponentType.SUBSCRIBER:
                subscribers.append(info)

        broker_metrics = []
        total_messages = 0
        for info in brokers:
            raw = self._fetch_status(info.hostname, info.internal_status_port)
            messages = raw.get("messages_processed", 0)
            total_messages += messages
            broker_metrics.append(
                BrokerMetrics(
                    broker_id=info.component_id,
                    host=raw.get("host", info.hostname),
                    port=raw.get("port", info.internal_port),
                    peer_count=raw.get("peer_count", 0),
                    subscriber_count=raw.get("subscribers", {}).get("count", 0),
                    messages_processed=messages,
                    uptime_seconds=raw.get("uptime_seconds", 0.0),
                    snapshot_state=raw.get("snapshot_state", "idle"),
                )
            )

        return MetricsResponse(
            brokers=broker_metrics,
            total_messages_processed=total_messages,
            total_brokers=len(brokers),
            total_publishers=len(publishers),
            total_subscribers=len(subscribers),
        )

    def trigger_snapshot(
        self, initiator_broker_id: int | None
    ) -> SnapshotStatusResponse:
        """Trigger Chandy-Lamport snapshot on the specified (or first running) broker."""
        raise NotImplementedError

    # --- Internal Helpers ---

    def _next_id(self, component_type: ComponentType, start: int = 1) -> int:
        """Return the lowest unused integer ID for the given component type."""
        existing = {
            c.component_id
            for c in self._components.values()
            if c.component_type == component_type
        }
        n = start
        while n in existing:
            n += 1
        return n

    def _get_component(
        self, component_type: ComponentType, component_id: int
    ) -> ComponentInfo:
        """Look up a managed component by type and ID, raising ValueError if not found."""
        for info in self._components.values():
            if (
                info.component_type == component_type
                and info.component_id == component_id
            ):
                return info
        raise ValueError(f"{component_type} {component_id} not found")

    def _sync_component_status(self) -> None:
        """Update each component's status to match actual Docker container state."""
        for info in self._components.values():
            if info.container_id is None:
                continue
            try:
                container = self.client.containers.get(info.container_id)
                info.status = _DOCKER_TO_COMPONENT_STATUS.get(
                    container.status, ComponentStatus.ERROR
                )
            except docker.errors.NotFound:
                info.status = ComponentStatus.ERROR

    def _running_broker_ids(self) -> list[int]:
        """Return IDs of all brokers currently in RUNNING state."""
        return [
            c.component_id
            for c in self._components.values()
            if c.component_type == ComponentType.BROKER
            and c.status == ComponentStatus.RUNNING
        ]

    def _fetch_status(self, hostname: str, internal_status_port: int) -> dict:
        """GET /status from a component via its internal Docker hostname. Returns {} on failure."""
        url = f"http://{hostname}:{internal_status_port}/status"
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                return json.loads(resp.read())
        except (URLError, TimeoutError) as exc:
            logger.warning("Failed to fetch status from %s: %s", url, exc)
            return {}

    def _fetch_url(self, url: str) -> dict:
        """GET an arbitrary URL. Returns {} on failure."""
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                return json.loads(resp.read())
        except Exception:
            return {}

    def get_snapshots(self) -> "SnapshotsResponse":
        """Return per-broker snapshot metadata by querying peer status servers."""
        import time
        from .models import BrokerSnapshotInfo, SnapshotsResponse

        brokers = [
            info for info in self._components.values()
            if info.component_type == ComponentType.BROKER
            and info.status == ComponentStatus.RUNNING
        ]
        fetched_at = time.time()
        results: list[BrokerSnapshotInfo] = []

        for target in brokers:
            best: dict | None = None
            for querier in brokers:
                if querier.component_id == target.component_id:
                    continue
                url = (
                    f"http://{querier.hostname}:{querier.internal_status_port}"
                    f"/snapshots/{target.hostname}/{target.internal_port}"
                )
                raw = self._fetch_url(url)
                if raw and raw.get("timestamp") and (
                    best is None or raw["timestamp"] > best["timestamp"]
                ):
                    best = raw

            own = self._fetch_status(target.hostname, target.internal_status_port)
            snap_state = own.get("snapshot_state", "idle")

            if best:
                results.append(BrokerSnapshotInfo(
                    broker_id=target.component_id,
                    broker_address=f"{target.hostname}:{target.internal_port}",
                    snapshot_id=best.get("snapshot_id"),
                    timestamp=best["timestamp"],
                    age_seconds=fetched_at - best["timestamp"],
                    peer_count=len(best.get("peer_brokers", [])),
                    subscriber_count=len(best.get("remote_subscribers", {})),
                    seen_message_count=len(best.get("seen_message_ids", [])),
                    snapshot_state=snap_state,
                ))
            else:
                results.append(BrokerSnapshotInfo(
                    broker_id=target.component_id,
                    broker_address=f"{target.hostname}:{target.internal_port}",
                    snapshot_state=snap_state,
                ))

        return SnapshotsResponse(brokers=results, fetched_at=fetched_at)
