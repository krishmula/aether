"""Unit tests for DELETE /api/brokers/{broker_id} endpoint.

Uses FastAPI TestClient (synchronous) with mocked dependencies.
Follows the same pattern as test_assignment_endpoint.py.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from aether.orchestrator.main import app


@pytest.fixture()
def client():
    return TestClient(app, raise_server_exceptions=True)


class TestRemoveBroker:
    """Tests for DELETE /api/brokers/{broker_id} endpoint."""

    def test_remove_broker_deregisters_from_bootstrap(self, client):
        """Intentional broker deletion calls deregister_from_bootstrap."""
        from aether.orchestrator import main
        from aether.orchestrator.models import (
            ComponentInfo,
            ComponentStatus,
            ComponentType,
        )
        from datetime import datetime

        broker_info = ComponentInfo(
            component_type=ComponentType.BROKER,
            component_id=2,
            container_name="aether-broker-2",
            hostname="broker-2",
            internal_port=8000,
            internal_status_port=18000,
            status=ComponentStatus.RUNNING,
            created_at=datetime.utcnow(),
        )

        with patch.object(main.docker_mgr, "remove_broker", return_value=broker_info):
            with patch.object(main.broadcaster, "emit", new_callable=AsyncMock):
                with patch.object(
                    main.recovery_mgr, "reassign_orphans", new_callable=AsyncMock
                ):
                    with patch(
                        "aether.orchestrator.main.deregister_from_bootstrap",
                        new_callable=AsyncMock,
                    ) as mock_deregister:
                        response = client.delete("/api/brokers/2")

        assert response.status_code == 200
        mock_deregister.assert_awaited_once()
        call_args = mock_deregister.await_args.args
        assert call_args[0] == "broker-2"
        assert call_args[1] == 8000

    def test_remove_broker_404_on_unknown_broker(self, client):
        """Deleting a non-existent broker returns 404."""
        from aether.orchestrator import main

        with patch.object(
            main.docker_mgr,
            "remove_broker",
            side_effect=ValueError("broker 999 not found"),
        ):
            with patch(
                "aether.orchestrator.main.deregister_from_bootstrap",
                new_callable=AsyncMock,
            ) as mock_deregister:
                response = client.delete("/api/brokers/999")

        assert response.status_code == 404
        mock_deregister.assert_not_awaited()
