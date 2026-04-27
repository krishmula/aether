"""Bootstrap server client — centralized HTTP interactions with the bootstrap status server."""

import logging

import httpx

from aether.orchestrator.settings import OrchestratorSettings

logger = logging.getLogger(__name__)


async def deregister_from_bootstrap(
    host: str,
    port: int,
    settings: OrchestratorSettings,
) -> None:
    """Remove a broker address from the bootstrap server's registered_brokers set.

    Fire-and-forget: logs failures but never raises. The bootstrap status server
    listens on bootstrap_port + 10000 (convention established in recovery.py
    and docker_manager.py).

    Args:
        host: Broker hostname to deregister.
        port: Broker port to deregister.
        settings: Orchestrator settings containing bootstrap_host and bootstrap_port.
    """
    bootstrap_status_port = settings.bootstrap_port + 10000
    url = f"http://{settings.bootstrap_host}:{bootstrap_status_port}/deregister"
    try:
        async with httpx.AsyncClient() as client:
            await client.request(
                "DELETE",
                url,
                json={"host": host, "port": port},
                timeout=2.0,
            )
        logger.debug("deregistered %s:%d from bootstrap", host, port)
    except Exception:
        logger.debug("bootstrap deregister failed for %s:%d (non-critical)", host, port)
