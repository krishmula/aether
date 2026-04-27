"""Unit tests for aether.orchestrator.bootstrap_client."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from aether.orchestrator.bootstrap_client import deregister_from_bootstrap


def _mock_settings(**overrides):
    defaults = {
        "bootstrap_host": "bootstrap",
        "bootstrap_port": 7000,
    }
    defaults.update(overrides)
    settings = MagicMock()
    for k, v in defaults.items():
        setattr(settings, k, v)
    return settings


class TestDeregisterFromBootstrap(unittest.IsolatedAsyncioTestCase):
    """Tests for the centralized deregister function."""

    async def test_deregister_success(self):
        """Successful deregister makes the correct HTTP call."""
        settings = _mock_settings()

        with patch(
            "aether.orchestrator.bootstrap_client.httpx.AsyncClient"
        ) as MockClient:
            mock_client = AsyncMock()
            mock_client.request = AsyncMock(return_value=MagicMock(status_code=200))
            MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

            await deregister_from_bootstrap("broker-1", 8000, settings)

            mock_client.request.assert_called_once_with(
                "DELETE",
                "http://bootstrap:17000/deregister",
                json={"host": "broker-1", "port": 8000},
                timeout=2.0,
            )

    async def test_deregister_failure_is_silent(self):
        """Network failure is caught and logged — no exception raised."""
        settings = _mock_settings()

        with patch(
            "aether.orchestrator.bootstrap_client.httpx.AsyncClient"
        ) as MockClient:
            mock_client = AsyncMock()
            mock_client.request = AsyncMock(side_effect=ConnectionError("refused"))
            MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

            # Should not raise
            await deregister_from_bootstrap("broker-1", 8000, settings)

    async def test_deregister_uses_custom_bootstrap_host(self):
        """Custom bootstrap_host from settings is used in the URL."""
        settings = _mock_settings(
            bootstrap_host="aether-bootstrap", bootstrap_port=9000
        )

        with patch(
            "aether.orchestrator.bootstrap_client.httpx.AsyncClient"
        ) as MockClient:
            mock_client = AsyncMock()
            mock_client.request = AsyncMock(return_value=MagicMock(status_code=200))
            MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

            await deregister_from_bootstrap("broker-2", 8001, settings)

            mock_client.request.assert_called_once_with(
                "DELETE",
                "http://aether-bootstrap:19000/deregister",
                json={"host": "broker-2", "port": 8001},
                timeout=2.0,
            )


if __name__ == "__main__":
    unittest.main()
