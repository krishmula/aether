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
        logger.info("Started broker %d (container %s)", broker_id, container.id[:12])
        return info

    def remove_broker(self, broker_id: int) -> ComponentInfo:
        """Stop and remove a broker container."""
        remove_container_info = self._get_component(ComponentType.BROKER, broker_id)
        container = self.client.containers.get(remove_container_info.container_id)
        container.stop(timeout=10)
        container.remove()
        del self._components[remove_container_info.container_name]
        logger.info(
            "Removed broker %d (container %s)",
            broker_id,
            remove_container_info.container_id[:12],
        )
        return remove_container_info

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
            environment={"AETHER_CONFIG": "/app/config.docker.yaml"},
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
