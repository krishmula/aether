"""Unit tests for GET /api/assignment.

Uses FastAPI TestClient (synchronous) — no real Docker calls.
DockerManager._components is monkey-patched with in-memory ComponentInfo dicts.
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from aether.orchestrator.models import (
    ComponentInfo,
    ComponentStatus,
    ComponentType,
)


def _make_broker(broker_id: int, status: ComponentStatus = ComponentStatus.RUNNING) -> ComponentInfo:
    return ComponentInfo(
        component_type=ComponentType.BROKER,
        component_id=broker_id,
        container_name=f"aether-broker-{broker_id}",
        hostname=f"broker-{broker_id}",
        internal_port=8000,
        internal_status_port=18000,
        status=status,
        created_at=datetime.utcnow(),
    )


def _make_subscriber(subscriber_id: int, broker_id: int | None) -> ComponentInfo:
    return ComponentInfo(
        component_type=ComponentType.SUBSCRIBER,
        component_id=subscriber_id,
        container_name=f"aether-subscriber-{subscriber_id}",
        hostname=f"subscriber-{subscriber_id}",
        internal_port=9100,
        internal_status_port=19100,
        broker_id=broker_id,
        created_at=datetime.utcnow(),
    )


def _components(*items: ComponentInfo) -> dict[str, ComponentInfo]:
    return {item.container_name: item for item in items}


@pytest.fixture()
def client():
    from aether.orchestrator.main import app
    return TestClient(app, raise_server_exceptions=True)


def _patch_components(components: dict):
    return patch("aether.orchestrator.main.docker_mgr._components", components)


class TestGetAssignmentFound:
    def test_returns_broker_host_and_port(self, client):
        broker = _make_broker(1)
        subscriber = _make_subscriber(3, broker_id=1)
        with _patch_components(_components(broker, subscriber)):
            resp = client.get("/api/assignment?subscriber_id=3")
        assert resp.status_code == 200
        body = resp.json()
        assert body["subscriber_id"] == 3
        assert body["broker_host"] == "broker-1"
        assert body["broker_port"] == 8000

    def test_starting_broker_is_accepted(self, client):
        """STARTING status is acceptable — broker is coming up after replacement."""
        broker = _make_broker(2, status=ComponentStatus.STARTING)
        subscriber = _make_subscriber(5, broker_id=2)
        with _patch_components(_components(broker, subscriber)):
            resp = client.get("/api/assignment?subscriber_id=5")
        assert resp.status_code == 200


class TestGetAssignmentNotFound:
    def test_unknown_subscriber_returns_404(self, client):
        with _patch_components({}):
            resp = client.get("/api/assignment?subscriber_id=99")
        assert resp.status_code == 404

    def test_subscriber_with_no_broker_id_returns_404(self, client):
        subscriber = _make_subscriber(1, broker_id=None)
        with _patch_components(_components(subscriber)):
            resp = client.get("/api/assignment?subscriber_id=1")
        assert resp.status_code == 404

    def test_broker_stopped_returns_404(self, client):
        """Subscriber's assigned broker is dead — don't hand out a stale address."""
        broker = _make_broker(1, status=ComponentStatus.STOPPED)
        subscriber = _make_subscriber(2, broker_id=1)
        with _patch_components(_components(broker, subscriber)):
            resp = client.get("/api/assignment?subscriber_id=2")
        assert resp.status_code == 404

    def test_broker_error_returns_404(self, client):
        broker = _make_broker(1, status=ComponentStatus.ERROR)
        subscriber = _make_subscriber(2, broker_id=1)
        with _patch_components(_components(broker, subscriber)):
            resp = client.get("/api/assignment?subscriber_id=2")
        assert resp.status_code == 404

    def test_broker_missing_from_components_returns_404(self, client):
        """broker_id set but no matching broker entry (e.g. removed mid-recovery)."""
        subscriber = _make_subscriber(2, broker_id=7)
        with _patch_components(_components(subscriber)):
            resp = client.get("/api/assignment?subscriber_id=2")
        assert resp.status_code == 404
