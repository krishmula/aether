import asyncio
import logging
import time

import httpx

from aether.orchestrator.models import (
    ComponentInfo,
    ComponentStatus,
    ComponentType,
    CreateBrokerRequest,
    EventType,
)

logger = logging.getLogger(__name__)


class RecoveryManager:
    def __init__(self, docker_mgr, broadcaster, settings) -> None:
        self._docker_mgr = docker_mgr
        self._broadcaster = broadcaster
        self._settings = settings
        self._recent_recoveries: dict[
            int, float
        ] = {}  # broker_id → timestamp (debounce)
        self._lock = asyncio.Lock()

    async def handle_broker_dead(self, broker_id: int, host: str, port: int) -> None:
        now = time.time()
        last_recovery = self._recent_recoveries.get(broker_id)
        if (
            last_recovery
            and (now - last_recovery) < self._settings.recovery_debounce_window
        ):
            logger.info(
                "Debounce: skipping recovery for broker %d (last recovery %.1fs ago)",
                broker_id,
                now - last_recovery,
            )
            return

        async with self._lock:
            try:
                self._recent_recoveries[broker_id] = now
                await self._broadcaster.emit(
                    EventType.BROKER_DECLARED_DEAD,
                    {"broker_id": broker_id, "host": host, "port": port},
                )
                logger.info("Broker %d declared dead — fetching snapshots", broker_id)

                # get surviving brokers (exclude the dead one)
                alive_brokers = [
                    info
                    for info in self._docker_mgr._components.values()
                    if info.component_type == ComponentType.BROKER
                    and info.status == ComponentStatus.RUNNING
                    and info.component_id != broker_id
                ]

                snapshot = await self._fetch_best_snapshot(alive_brokers, host, port)

                if (
                    snapshot
                    and (now - snapshot["timestamp"]) < self._settings.snapshot_max_age
                ):
                    logger.info(
                        "Fresh snapshot found (%.1fs old) — attempting replacement recovery",
                        now - snapshot["timestamp"],
                    )
                    try:
                        await self._recover_replacement(broker_id, host, port, snapshot)
                        return
                    except Exception:
                        logger.exception(
                            "Replacement recovery failed for broker %d — falling back to redistribution",
                            broker_id,
                        )
                else:
                    reason = "stale" if snapshot else "missing"
                    logger.info("Snapshot %s — using redistribution recovery", reason)

                await self._recover_redistribution(broker_id)

            except Exception as e:
                logger.exception("Recovery failed for broker %d", broker_id)
                await self._broadcaster.emit(
                    EventType.BROKER_RECOVERY_FAILED,
                    {"broker_id": broker_id, "reason": str(e)},
                )

    async def _fetch_best_snapshot(
        self,
        alive_brokers: list[ComponentInfo],
        dead_host: str,
        dead_port: int,
    ) -> dict | None:
        """Query all surviving brokers for snapshots of the dead broker, return the freshest."""
        if not alive_brokers:
            return None

        async def _query_one(
            client: httpx.AsyncClient, broker: ComponentInfo
        ) -> dict | None:
            url = f"http://{broker.hostname}:{broker.internal_status_port}/snapshots/{dead_host}/{dead_port}"
            try:
                resp = await client.get(url, timeout=2.0)
                if resp.status_code == 200:
                    return resp.json()
            except Exception:
                logger.debug(
                    "Failed to fetch snapshot from broker %d", broker.component_id
                )
            return None

        async with httpx.AsyncClient() as client:
            results = await asyncio.gather(
                *(_query_one(client, b) for b in alive_brokers)
            )

        best = None
        for snapshot in results:
            if snapshot and (best is None or snapshot["timestamp"] > best["timestamp"]):
                best = snapshot

        return best

    async def _recover_replacement(
        self,
        broker_id: int,
        host: str,
        port: int,
        snapshot: dict,
    ) -> None:
        """Path A: spin up a replacement broker with the same ID and restore from snapshot."""
        await self._broadcaster.emit(
            EventType.BROKER_RECOVERY_STARTED,
            {"broker_id": broker_id, "recovery_path": "replacement"},
        )

        # 1. Remove the dead broker container (may already be gone)
        try:
            self._docker_mgr.remove_broker(broker_id)
        except (ValueError, Exception):
            logger.debug("Dead broker %d container already removed", broker_id)

        # 2. Deregister from bootstrap so peer lists stay clean
        bootstrap_status_port = self._settings.bootstrap_port + 10000
        try:
            async with httpx.AsyncClient() as client:
                await client.request(
                    "DELETE",
                    f"http://{self._settings.bootstrap_host}:{bootstrap_status_port}/deregister",
                    json={"host": host, "port": port},
                    timeout=2.0,
                )
        except Exception:
            logger.debug("Bootstrap deregister failed (non-critical)")

        # 3. Spin up replacement with the same broker_id
        info = self._docker_mgr.create_broker(CreateBrokerRequest(broker_id=broker_id))
        await self._broadcaster.emit(
            EventType.BROKER_ADDED, info.model_dump(mode="json")
        )

        # 4. Poll replacement's /status until healthy
        url = f"http://{info.hostname}:{info.internal_status_port}/status"
        deadline = time.time() + self._settings.health_timeout
        healthy = False
        async with httpx.AsyncClient() as client:
            while time.time() < deadline:
                try:
                    resp = await client.get(url, timeout=2.0)
                    if resp.status_code == 200:
                        healthy = True
                        break
                except Exception:
                    pass
                await asyncio.sleep(self._settings.health_poll_interval)

        if not healthy:
            raise RuntimeError(
                f"Replacement broker {broker_id} did not become healthy within {self._settings.health_timeout}s"
            )

        # 5. POST /recover to restore state from the dead broker's snapshot
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"http://{info.hostname}:{info.internal_status_port}/recover",
                json={"dead_broker_host": host, "dead_broker_port": port},
                timeout=self._settings.recovery_timeout,
            )
            if resp.status_code != 200:
                raise RuntimeError(
                    f"POST /recover returned {resp.status_code}: {resp.text}"
                )

        logger.info("Broker %d replacement recovery complete", broker_id)
        await self._broadcaster.emit(
            EventType.BROKER_RECOVERED,
            {"broker_id": broker_id, "recovery_path": "replacement"},
        )

    async def _recover_redistribution(self, broker_id: int) -> None:
        """Path B: redistribute orphaned subscribers across surviving brokers."""
        await self._broadcaster.emit(
            EventType.BROKER_RECOVERY_STARTED,
            {"broker_id": broker_id, "recovery_path": "redistribution"},
        )

        # Re-read _components fresh — state may have changed if Path A failed
        orphaned_subscribers = [
            info
            for info in self._docker_mgr._components.values()
            if info.component_type == ComponentType.SUBSCRIBER
            and info.broker_id == broker_id
        ]

        surviving_brokers = [
            info
            for info in self._docker_mgr._components.values()
            if info.component_type == ComponentType.BROKER
            and info.status == ComponentStatus.RUNNING
            and info.component_id != broker_id
        ]

        # Remove the dead broker container
        try:
            self._docker_mgr.remove_broker(broker_id)
        except (ValueError, Exception):
            logger.debug("Dead broker %d container already removed", broker_id)

        if not surviving_brokers:
            logger.critical(
                "No surviving brokers — cannot redistribute %d subscribers",
                len(orphaned_subscribers),
            )
            await self._broadcaster.emit(
                EventType.BROKER_RECOVERY_FAILED,
                {"broker_id": broker_id, "reason": "no_surviving_brokers"},
            )
            return

        # Count current subscribers per surviving broker
        subscriber_counts: dict[int, int] = {
            b.component_id: 0 for b in surviving_brokers
        }
        for info in self._docker_mgr._components.values():
            if (
                info.component_type == ComponentType.SUBSCRIBER
                and info.broker_id in subscriber_counts
            ):
                subscriber_counts[info.broker_id] += 1

        # Assign each orphan to the least-loaded surviving broker
        for subscriber in orphaned_subscribers:
            least_loaded_id = min(subscriber_counts, key=subscriber_counts.get)
            old_broker_id = subscriber.broker_id
            subscriber.broker_id = least_loaded_id
            subscriber_counts[least_loaded_id] += 1

            logger.info(
                "Reassigned subscriber %d: broker %d → broker %d",
                subscriber.component_id,
                old_broker_id,
                least_loaded_id,
            )
            await self._broadcaster.emit(
                EventType.SUBSCRIBER_RECONNECTED,
                {
                    "subscriber_id": subscriber.component_id,
                    "old_broker_id": old_broker_id,
                    "new_broker_id": least_loaded_id,
                },
            )

        logger.info(
            "Broker %d redistribution complete — %d subscribers reassigned",
            broker_id,
            len(orphaned_subscribers),
        )
        await self._broadcaster.emit(
            EventType.BROKER_RECOVERED,
            {
                "broker_id": broker_id,
                "recovery_path": "redistribution",
                "affected_subscribers": len(orphaned_subscribers),
            },
        )

    async def reassign_orphans(self, broker_id: int) -> int:
        """Reassign subscribers orphaned by an intentional broker deletion.

        Called after the broker container has already been removed. Finds all
        subscribers that were assigned to ``broker_id``, then distributes them
        across surviving running brokers using a least-loaded strategy.

        Returns the number of subscribers reassigned.
        """
        orphaned_subscribers = [
            info
            for info in self._docker_mgr._components.values()
            if info.component_type == ComponentType.SUBSCRIBER
            and info.broker_id == broker_id
        ]

        if not orphaned_subscribers:
            return 0

        surviving_brokers = [
            info
            for info in self._docker_mgr._components.values()
            if info.component_type == ComponentType.BROKER
            and info.status == ComponentStatus.RUNNING
            and info.component_id != broker_id
        ]

        if not surviving_brokers:
            logger.warning(
                "No surviving brokers — %d subscribers remain orphaned",
                len(orphaned_subscribers),
            )
            return 0

        subscriber_counts: dict[int, int] = {
            b.component_id: 0 for b in surviving_brokers
        }
        for info in self._docker_mgr._components.values():
            if (
                info.component_type == ComponentType.SUBSCRIBER
                and info.broker_id in subscriber_counts
            ):
                subscriber_counts[info.broker_id] += 1

        for subscriber in orphaned_subscribers:
            least_loaded_id = min(subscriber_counts, key=subscriber_counts.get)
            old_broker_id = subscriber.broker_id
            subscriber.broker_id = least_loaded_id
            subscriber_counts[least_loaded_id] += 1

            logger.info(
                "Reassigned subscriber %d: broker %d → broker %d",
                subscriber.component_id,
                old_broker_id,
                least_loaded_id,
            )
            await self._broadcaster.emit(
                EventType.SUBSCRIBER_RECONNECTED,
                {
                    "subscriber_id": subscriber.component_id,
                    "old_broker_id": old_broker_id,
                    "new_broker_id": least_loaded_id,
                },
            )

        logger.info(
            "Broker %d intentional delete — %d subscribers reassigned",
            broker_id,
            len(orphaned_subscribers),
        )
        return len(orphaned_subscribers)
