"""HealthMonitor — asyncio task that detects dead brokers.

Polls each running broker's /status endpoint every `poll_interval` seconds.
After `failure_threshold` consecutive failures (network error or non-200),
calls the `on_broker_dead` callback with (broker_id, host, port).

The failure counter resets on any successful poll, so a broker that recovers
on its own (e.g. temporary network blip) won't trigger a false failover.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime

import httpx

from aether.utils.log import BoundLogger

from .models import ComponentInfo, ComponentStatus, ComponentType

logger = BoundLogger(
    logging.getLogger(__name__),
    {"service_name": "aether-orchestrator", "component_type": "orchestrator"},
)


class HealthMonitor:
    """Asyncio background task that polls brokers and fires on_broker_dead."""

    def __init__(
        self,
        components: dict[str, ComponentInfo],
        on_broker_dead: Callable[[int, str, int], Awaitable[None]],
        poll_interval: float = 5.0,
        failure_threshold: int = 3,
        startup_grace_seconds: float = 30.0,
        request_timeout: float = 5.0,
    ) -> None:
        self._components = components
        self._on_broker_dead = on_broker_dead
        self.poll_interval = poll_interval
        self.failure_threshold = failure_threshold
        self.startup_grace_seconds = startup_grace_seconds
        self.request_timeout = request_timeout
        self._failure_counts: dict[int, int] = {}  # broker_id → consecutive failures

    async def run(self) -> None:
        """Main polling loop. Runs until the task is cancelled."""
        async with httpx.AsyncClient() as client:
            while True:
                await self._poll_all(client)
                await asyncio.sleep(self.poll_interval)

    async def _poll_all(self, client: httpx.AsyncClient) -> None:
        now = datetime.utcnow()
        all_brokers = [
            c
            for c in self._components.values()
            if c.component_type == ComponentType.BROKER
            and c.status == ComponentStatus.RUNNING
        ]
        brokers = []
        for c in all_brokers:
            age = (now - c.created_at).total_seconds()
            if age < self.startup_grace_seconds:
                logger.debug(
                    "broker %d in grace period (%.1fs old, need %.1fs)",
                    c.component_id, age, self.startup_grace_seconds,
                )
            else:
                brokers.append(c)
        if brokers:
            await asyncio.gather(*(self._poll_one(client, b) for b in brokers))

    async def _poll_one(self, client: httpx.AsyncClient, broker: ComponentInfo) -> None:
        url = f"http://{broker.hostname}:{broker.internal_status_port}/status"
        try:
            resp = await client.get(url, timeout=self.request_timeout)
            success = resp.status_code == 200
        except Exception:
            success = False

        if success:
            if self._failure_counts.get(broker.component_id, 0) > 0:
                logger.debug(
                    "broker %d recovered (poll succeeded)", broker.component_id
                )
            self._failure_counts[broker.component_id] = 0
            return

        count = self._failure_counts.get(broker.component_id, 0) + 1
        self._failure_counts[broker.component_id] = count
        logger.warning(
            "broker %d unhealthy (%d/%d consecutive failures)",
            broker.component_id,
            count,
            self.failure_threshold,
            extra={
                "event_type": "broker_poll_failed",
                "broker_id": str(broker.component_id),
            },
        )

        if count >= self.failure_threshold:
            self._failure_counts[broker.component_id] = 0  # reset — don't re-fire
            logger.error(
                "broker %d declared dead after %d failures — triggering recovery",
                broker.component_id,
                self.failure_threshold,
                extra={
                    "event_type": "broker_declared_dead",
                    "broker_id": str(broker.component_id),
                },
            )
            asyncio.create_task(
                self._on_broker_dead(
                    broker.component_id,
                    broker.hostname,
                    broker.internal_port,
                )
            )
