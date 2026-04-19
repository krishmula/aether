"""Unit tests for Phase 1 cached orchestrator metrics behavior.

These tests lock in the intended Phase 1 contract:

1. ``DockerManager.get_metrics()`` is a pure cache read and must not trigger
   live broker/subscriber polling.
2. One authoritative refresh path samples live status, updates cache metadata,
   and computes throughput from sampler tick deltas rather than per-request
   mutable globals.
"""

from __future__ import annotations

import asyncio
import importlib
import sys
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

from aether.orchestrator.docker_manager import DockerManager
from aether.orchestrator.models import (
    ComponentInfo,
    ComponentStatus,
    ComponentType,
    CreateBrokerRequest,
    MetricsResponse,
)


def _make_broker(broker_id: int) -> ComponentInfo:
    return ComponentInfo(
        component_type=ComponentType.BROKER,
        component_id=broker_id,
        container_name=f"aether-broker-{broker_id}",
        hostname=f"broker-{broker_id}",
        internal_port=8000,
        internal_status_port=18000,
        status=ComponentStatus.RUNNING,
        created_at=datetime.now(UTC),
    )


def _make_manager() -> DockerManager:
    fake_client = MagicMock()
    fake_client.networks.get.return_value = MagicMock()
    with patch(
        "aether.orchestrator.docker_manager.docker.from_env",
        return_value=fake_client,
    ):
        return DockerManager()


class TestMetricsCacheReadPath:
    def test_get_metrics_returns_cached_snapshot_without_live_polling(self) -> None:
        manager = _make_manager()
        cached = MetricsResponse(
            total_messages_processed=123,
            total_brokers=1,
            total_publishers=2,
            total_subscribers=3,
            throughput_msgs_per_sec=45.6,
            topology_generation=7,
        )

        manager._metrics_cache = cached  # type: ignore[attr-defined]
        manager._sync_component_status = MagicMock(  # type: ignore[method-assign]
            side_effect=AssertionError("get_metrics() must not sync component state")
        )
        manager._fetch_status = MagicMock(  # type: ignore[method-assign]
            side_effect=AssertionError(
                "get_metrics() must not poll live status endpoints"
            )
        )

        result = manager.get_metrics()

        assert result == cached


class TestMetricsCacheRefresh:
    def test_refresh_metrics_cache_uses_sampler_deltas_and_updates_metadata(
        self,
    ) -> None:
        manager = _make_manager()
        broker = _make_broker(1)
        manager._components = {broker.container_name: broker}
        manager._sync_component_status = MagicMock()  # type: ignore[method-assign]

        manager._fetch_status = MagicMock(  # type: ignore[method-assign]
            return_value={
                "host": "broker-1",
                "port": 8000,
                "peer_count": 0,
                "subscribers": {"count": 0},
                "messages_processed": 100,
                "uptime_seconds": 5.0,
                "snapshot_state": "idle",
            }
        )

        first = manager.refresh_metrics_cache(
            now=100.0,
            fetched_at=1_700_000_100.0,
        )
        assert first.total_messages_processed == 100
        assert first.throughput_msgs_per_sec == 0
        assert hasattr(first, "fetched_at")
        assert first.fetched_at == 1_700_000_100.0
        assert hasattr(first, "sample_interval_seconds")
        assert first.sample_interval_seconds == 0.0
        assert first.topology_generation == 0

        manager._fetch_status = MagicMock(  # type: ignore[method-assign]
            return_value={
                "host": "broker-1",
                "port": 8000,
                "peer_count": 0,
                "subscribers": {"count": 0},
                "messages_processed": 160,
                "uptime_seconds": 6.0,
                "snapshot_state": "idle",
            }
        )

        second = manager.refresh_metrics_cache(
            now=101.0,
            fetched_at=1_700_000_101.0,
        )
        assert second.total_messages_processed == 160
        assert second.sample_interval_seconds == 1.0
        assert second.throughput_msgs_per_sec == 60.0
        assert second.fetched_at == 1_700_000_101.0

    def test_topology_mutation_advances_generation_and_resets_cached_sample(self) -> None:
        manager = _make_manager()
        manager._metrics_cache = MetricsResponse(
            total_messages_processed=75,
            total_brokers=1,
            throughput_msgs_per_sec=12.5,
            fetched_at=1_700_000_000.0,
            sample_interval_seconds=1.0,
            topology_generation=0,
        )
        manager._last_total_messages = 75  # type: ignore[attr-defined]
        manager._last_metrics_time = 50.0  # type: ignore[attr-defined]

        manager.client.containers.run.return_value = MagicMock(id="broker-1-container")
        broker = manager.create_broker(CreateBrokerRequest())

        cached_after_create = manager.get_metrics()
        assert broker.component_id == 1
        assert cached_after_create.topology_generation == 1
        assert cached_after_create.total_messages_processed == 0
        assert cached_after_create.throughput_msgs_per_sec == 0
        assert cached_after_create.fetched_at == 0
        assert cached_after_create.sample_interval_seconds == 0
        assert manager._last_total_messages == 0  # type: ignore[attr-defined]
        assert manager._last_metrics_time == 0.0  # type: ignore[attr-defined]

        removed_container = MagicMock()
        manager.client.containers.get.return_value = removed_container
        manager.remove_broker(broker.component_id)

        cached_after_remove = manager.get_metrics()
        assert cached_after_remove.topology_generation == 2
        removed_container.stop.assert_called_once_with(timeout=10)
        removed_container.remove.assert_called_once()


class TestMetricsEndpointReadPath:
    def test_metrics_route_reads_cache_without_refreshing(self) -> None:
        fake_client = MagicMock()
        fake_client.networks.get.return_value = MagicMock()

        with patch(
            "aether.orchestrator.docker_manager.docker.from_env",
            return_value=fake_client,
        ):
            sys.modules.pop("aether.orchestrator.main", None)
            main = importlib.import_module("aether.orchestrator.main")

        cached = MetricsResponse(
            total_messages_processed=321,
            throughput_msgs_per_sec=12.3,
            fetched_at=50.0,
            sample_interval_seconds=1.0,
            topology_generation=4,
        )
        main.docker_mgr.get_metrics = MagicMock(return_value=cached)
        main.docker_mgr.refresh_metrics_cache = MagicMock(
            side_effect=AssertionError("/api/metrics must not refresh live metrics")
        )

        result = asyncio.run(main.get_metrics())

        assert result == cached
