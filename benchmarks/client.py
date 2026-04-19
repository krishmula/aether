"""Async client wrapper around the Aether orchestrator REST API + WebSocket."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

import httpx
import websockets
import websockets.asyncio.client

from benchmarks.config import BenchmarkConfig

logger = logging.getLogger(__name__)


class AetherClient:
    """Async HTTP client for the orchestrator API."""

    def __init__(self, cfg: BenchmarkConfig) -> None:
        self.cfg = cfg
        self.base = cfg.orchestrator_url
        self._http = httpx.AsyncClient(base_url=self.base, timeout=30.0)

    async def close(self) -> None:
        await self._http.aclose()

    # ------------------------------------------------------------------
    # Health / preflight
    # ------------------------------------------------------------------

    async def wait_ready(self, timeout: float = 60.0) -> None:
        """Poll /api/state until orchestrator responds."""
        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            try:
                resp = await self._http.get("/api/state")
                if resp.status_code == 200:
                    return
            except httpx.ConnectError:
                pass
            await asyncio.sleep(1.0)
        raise RuntimeError(
            f"Orchestrator not reachable at {self.base} after {timeout}s. "
            "Is the system running?  Run 'make demo' first."
        )

    # ------------------------------------------------------------------
    # Topology management
    # ------------------------------------------------------------------

    async def seed_topology(
        self,
        brokers: int = 3,
        publishers: int = 2,
        subscribers: int = 3,
        publish_interval: float | None = None,
        publishers_last: bool = False,
    ) -> dict[str, Any]:
        """Seed a topology by building it piece by piece.

        We never use /api/seed here — that endpoint hardcodes publish_interval=1.0
        (intentional for the UI demo) which makes benchmark throughput numbers
        useless. Always create publishers explicitly with cfg.publish_interval.

        publish_interval overrides cfg.publish_interval when provided, allowing
        latency benchmarks to use a slower rate that keeps the broker unsaturated.
        """
        interval = (
            publish_interval
            if publish_interval is not None
            else self.cfg.publish_interval
        )
        created: list[dict] = []

        state = await self.get_state()
        existing_brokers = [b["component_id"] for b in state.get("brokers", [])]

        # Create brokers up to target count.
        while len(existing_brokers) < brokers:
            info = await self._create_broker()
            existing_brokers.append(info["component"]["component_id"])
            created.append(info)

        state = await self.get_state()
        existing_brokers = [b["component_id"] for b in state.get("brokers", [])]

        async def _create_publishers() -> None:
            current_state = await self.get_state()
            existing_pubs = len(current_state.get("publishers", []))
            for _ in range(max(0, publishers - existing_pubs)):
                info = await self._create_publisher(
                    broker_ids=existing_brokers,
                    interval=interval,
                )
                created.append(info)

        # Create subscribers — one per broker, round-robin.
        step = max(1, 256 // subscribers) if subscribers > 0 else 256
        async def _create_subscribers() -> None:
            current_state = await self.get_state()
            existing_subs = len(current_state.get("subscribers", []))
            subs_needed = max(0, subscribers - existing_subs)
            for i in range(subs_needed):
                broker_id = existing_brokers[i % len(existing_brokers)]
                low = i * step
                high = min((i + 1) * step - 1, 255)
                info = await self._create_subscriber(broker_id, low, high)
                created.append(info)

        if publishers_last:
            await _create_subscribers()
            await _create_publishers()
        else:
            await _create_publishers()
            await _create_subscribers()

        return {"seeded": len(created), "components": created}

    async def _create_broker(self) -> dict[str, Any]:
        resp = await self._http.post("/api/brokers", json={})
        resp.raise_for_status()
        return resp.json()

    async def _create_publisher(
        self, broker_ids: list[int] | None = None, interval: float = 1.0
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"interval": interval}
        if broker_ids is not None:
            body["broker_ids"] = broker_ids
        resp = await self._http.post("/api/publishers", json=body)
        resp.raise_for_status()
        return resp.json()

    async def _create_subscriber(
        self, broker_id: int, range_low: int, range_high: int
    ) -> dict[str, Any]:
        resp = await self._http.post(
            "/api/subscribers",
            json={
                "broker_id": broker_id,
                "range_low": range_low,
                "range_high": range_high,
            },
        )
        resp.raise_for_status()
        return resp.json()

    async def add_publisher(
        self, broker_ids: list[int] | None = None, interval: float | None = None
    ) -> dict[str, Any]:
        if interval is None:
            interval = self.cfg.publish_interval
        return await self._create_publisher(broker_ids, interval)

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    async def get_state(self) -> dict[str, Any]:
        resp = await self._http.get("/api/state")
        resp.raise_for_status()
        return resp.json()

    async def get_metrics(self) -> dict[str, Any]:
        resp = await self._http.get("/api/metrics")
        resp.raise_for_status()
        return resp.json()

    def assert_valid_metrics_snapshot(
        self,
        metrics: dict[str, Any],
        *,
        stage: str,
        expected_generation: int | None = None,
    ) -> None:
        """Validate cached metrics contract before a benchmark trusts a sample."""
        actual_generation = metrics.get("topology_generation")
        if actual_generation is None:
            raise RuntimeError(f"missing metrics generation during {stage}")
        if expected_generation is not None and actual_generation != expected_generation:
            raise RuntimeError(
                f"metrics generation changed during {stage}: "
                f"expected={expected_generation}, actual={actual_generation}"
            )

        fetched_at = metrics.get("fetched_at", 0)
        if fetched_at <= 0:
            raise RuntimeError(f"missing metrics fetch timestamp during {stage}")

        sample_interval = metrics.get("sample_interval_seconds", 0)
        if sample_interval <= 0:
            raise RuntimeError(f"invalid metrics sample interval during {stage}")

        allowed_staleness = max(
            self.cfg.metrics_max_staleness_seconds,
            float(sample_interval) * 2,
        )
        age_seconds = time.time() - float(fetched_at)
        if age_seconds > allowed_staleness:
            raise RuntimeError(
                f"stale metrics snapshot during {stage}: "
                f"age={age_seconds:.3f}s, limit={allowed_staleness:.3f}s"
            )

    async def wait_for_metrics_generation(
        self,
        expected_generation: int,
        *,
        timeout: float = 10.0,
        poll_interval: float = 0.2,
    ) -> dict[str, Any]:
        """Wait for the sampler to publish a fresh metrics snapshot for a generation."""
        deadline = asyncio.get_event_loop().time() + timeout
        last_metrics: dict[str, Any] | None = None

        while asyncio.get_event_loop().time() < deadline:
            metrics = await self.get_metrics()
            last_metrics = metrics
            if (
                metrics.get("topology_generation") == expected_generation
                and metrics.get("fetched_at", 0) > 0
            ):
                return metrics
            await asyncio.sleep(poll_interval)

        raise RuntimeError(
            "timed out waiting for fresh metrics sample for topology generation "
            f"{expected_generation}; last_sample={last_metrics}"
        )

    async def assert_metrics_generation(
        self,
        expected_generation: int,
        *,
        stage: str,
    ) -> None:
        """Fail fast if a measurement window crossed a topology boundary."""
        metrics = await self.get_metrics()
        actual_generation = metrics.get("topology_generation")
        if actual_generation != expected_generation:
            raise RuntimeError(
                f"metrics generation changed during {stage}: "
                f"expected={expected_generation}, actual={actual_generation}"
            )

    async def get_topology_fingerprint(self) -> dict[str, Any]:
        """Capture the current dynamic topology in a comparison-friendly form."""
        state = await self.get_state()
        brokers = {
            comp["component_id"]: comp.get("status")
            for comp in state.get("brokers", [])
        }
        publishers = {
            comp["component_id"]: {
                "status": comp.get("status"),
                "publish_interval": comp.get("publish_interval"),
            }
            for comp in state.get("publishers", [])
        }
        subscribers = {
            comp["component_id"]: {
                "status": comp.get("status"),
                "broker_id": comp.get("broker_id"),
                "range_low": comp.get("range_low"),
                "range_high": comp.get("range_high"),
            }
            for comp in state.get("subscribers", [])
        }
        return {
            "brokers": brokers,
            "publishers": publishers,
            "subscribers": subscribers,
        }

    async def assert_topology_matches(
        self,
        expected: dict[str, Any],
        *,
        stage: str,
    ) -> None:
        """Fail if the dynamic topology differs from the expected benchmark state."""
        actual = await self.get_topology_fingerprint()
        if actual != expected:
            raise RuntimeError(
                f"benchmark topology changed during {stage}: "
                f"expected={expected}, actual={actual}"
            )

    async def get_subscriber_status(self, host: str, port: int) -> dict[str, Any]:
        """Query a subscriber's /status endpoint directly (outside Docker network)."""
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"http://{host}:{port}/status")
            resp.raise_for_status()
            return resp.json()

    async def reset_subscriber_latency(self, host: str, port: int) -> dict[str, Any]:
        """Clear a subscriber's in-memory latency sample buffer."""
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(f"http://{host}:{port}/latency/reset")
            resp.raise_for_status()
            return resp.json()

    # ------------------------------------------------------------------
    # Chaos
    # ------------------------------------------------------------------

    async def trigger_chaos(self) -> dict[str, Any]:
        resp = await self._http.post("/api/chaos")
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    async def cleanup(self, timeout: float = 60.0) -> None:
        """Remove all dynamic components and verify the system returns to empty."""
        state = await self.get_state()
        failures: list[str] = []

        async def _delete(path: str, label: str) -> None:
            try:
                resp = await self._http.delete(path)
                resp.raise_for_status()
            except Exception as exc:
                failures.append(f"{label}: {exc}")

        for sub in state.get("subscribers", []):
            await _delete(
                f"/api/subscribers/{sub['component_id']}",
                f"subscriber {sub['component_id']}",
            )
        for pub in state.get("publishers", []):
            await _delete(
                f"/api/publishers/{pub['component_id']}",
                f"publisher {pub['component_id']}",
            )
        for broker in state.get("brokers", []):
            await _delete(
                f"/api/brokers/{broker['component_id']}",
                f"broker {broker['component_id']}",
            )

        deadline = asyncio.get_event_loop().time() + timeout
        final_state: dict[str, Any] | None = None
        while asyncio.get_event_loop().time() < deadline:
            final_state = await self.get_state()
            if (
                not final_state.get("brokers")
                and not final_state.get("publishers")
                and not final_state.get("subscribers")
            ):
                break
            await asyncio.sleep(1.0)
        else:
            if final_state is None:
                final_state = await self.get_state()
            failures.append(
                "cleanup did not converge to an empty topology within "
                f"{timeout}s (remaining: "
                f"brokers={len(final_state.get('brokers', []))}, "
                f"publishers={len(final_state.get('publishers', []))}, "
                f"subscribers={len(final_state.get('subscribers', []))})"
            )

        if failures:
            raise RuntimeError("benchmark cleanup failed: " + "; ".join(failures))

    # ------------------------------------------------------------------
    # Wait for components to be running
    # ------------------------------------------------------------------

    async def wait_all_running(self, timeout: float = 60.0) -> None:
        """Poll /api/state until every component has status RUNNING."""
        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            state = await self.get_state()
            all_components = (
                state.get("brokers", [])
                + state.get("publishers", [])
                + state.get("subscribers", [])
            )
            if all_components and all(
                c.get("status") == "running" for c in all_components
            ):
                return
            await asyncio.sleep(1.0)
        raise RuntimeError(f"Not all components reached RUNNING within {timeout}s")

    async def wait_latency_ready(
        self,
        *,
        expected_subscribers: int,
        min_samples_per_subscriber: int,
        stable_polls: int,
        timeout: float,
        poll_interval: float,
    ) -> None:
        """Wait until latency measurement prerequisites are stably true.

        Readiness requires:
          - the expected subscriber count exists
          - total broker message count is still increasing
          - every subscriber has received traffic
          - every subscriber has accumulated enough latency samples
          - these conditions hold for stable_polls consecutive polls
        """
        deadline = asyncio.get_event_loop().time() + timeout
        prev_total_messages: int | None = None
        expected_generation: int | None = None
        ready_polls = 0
        last_reason = "latency readiness has not started"

        while asyncio.get_event_loop().time() < deadline:
            try:
                state = await self.get_state()
                metrics = await self.get_metrics()
            except Exception as exc:
                ready_polls = 0
                last_reason = f"failed to query readiness metrics: {exc}"
                await asyncio.sleep(poll_interval)
                continue

            self.assert_valid_metrics_snapshot(
                metrics,
                stage="latency readiness",
                expected_generation=expected_generation,
            )
            expected_generation = metrics["topology_generation"]

            state_subscribers = state.get("subscribers", [])
            if len(state_subscribers) != expected_subscribers:
                ready_polls = 0
                last_reason = (
                    f"expected {expected_subscribers} subscribers, "
                    f"found {len(state_subscribers)}"
                )
                prev_total_messages = metrics.get("total_messages_processed", 0)
                await asyncio.sleep(poll_interval)
                continue

            subscriber_metrics = {
                sub.get("subscriber_id"): sub for sub in metrics.get("subscribers", [])
            }
            missing_ids: list[int] = []
            idle_ids: list[int] = []
            undersampled: list[str] = []

            for sub in state_subscribers:
                subscriber_id = sub.get("component_id")
                metric = subscriber_metrics.get(subscriber_id)
                if metric is None:
                    missing_ids.append(subscriber_id)
                    continue

                if metric.get("total_received", 0) <= 0:
                    idle_ids.append(subscriber_id)

                sample_count = metric.get("latency_us", {}).get("sample_count", 0)
                if sample_count < min_samples_per_subscriber:
                    undersampled.append(f"{subscriber_id}:{sample_count}")

            total_messages = metrics.get("total_messages_processed", 0)
            traffic_increasing = (
                prev_total_messages is not None
                and total_messages > prev_total_messages
            )
            prev_total_messages = total_messages

            if missing_ids:
                ready_polls = 0
                last_reason = f"missing subscriber metrics for IDs {missing_ids}"
            elif idle_ids:
                ready_polls = 0
                last_reason = f"subscribers with zero deliveries: {idle_ids}"
            elif undersampled:
                ready_polls = 0
                last_reason = (
                    "subscribers below minimum latency samples "
                    f"({min_samples_per_subscriber}): {undersampled}"
                )
            elif not traffic_increasing:
                ready_polls = 0
                last_reason = "broker message count is not increasing between polls"
            else:
                ready_polls += 1
                last_reason = (
                    f"ready poll {ready_polls}/{stable_polls} "
                    f"(messages_total={total_messages})"
                )
                if ready_polls >= stable_polls:
                    return

            await asyncio.sleep(poll_interval)

        raise RuntimeError(
            "Latency benchmark did not reach measurement readiness within "
            f"{timeout}s: {last_reason}"
        )

    async def reset_all_subscriber_latency_samples(
        self,
        *,
        expected_subscribers: int,
    ) -> None:
        """Reset latency sample buffers for all expected subscribers."""
        state = await self.get_state()
        subscribers = state.get("subscribers", [])
        if len(subscribers) != expected_subscribers:
            raise RuntimeError(
                f"cannot reset latency samples: expected {expected_subscribers} "
                f"subscribers, found {len(subscribers)}"
            )

        reset_calls = []
        for sub in subscribers:
            host_port = sub.get("host_status_port")
            subscriber_id = sub.get("component_id")
            if host_port is None:
                raise RuntimeError(
                    f"subscriber {subscriber_id} missing host_status_port"
                )
            reset_calls.append(self.reset_subscriber_latency("localhost", host_port))

        results = await asyncio.gather(*reset_calls, return_exceptions=True)
        failures = [
            f"subscriber #{idx + 1}: {result}"
            for idx, result in enumerate(results)
            if isinstance(result, Exception)
        ]
        if failures:
            raise RuntimeError(
                "failed to reset subscriber latency samples: " + ", ".join(failures)
            )


# --------------------------------------------------------------------------
# WebSocket event stream
# --------------------------------------------------------------------------


@asynccontextmanager
async def event_stream(
    cfg: BenchmarkConfig,
) -> AsyncIterator[asyncio.Queue[dict[str, Any]]]:
    """Connect to the orchestrator WebSocket and yield a queue of parsed events.

    Usage::

        async with event_stream(cfg) as events:
            event = await asyncio.wait_for(events.get(), timeout=30)
    """
    ws_url = cfg.orchestrator_url.replace("http://", "ws://") + "/ws/events"
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

    async def _reader(ws: websockets.asyncio.client.ClientConnection) -> None:
        try:
            async for raw in ws:
                try:
                    queue.put_nowait(json.loads(raw))
                except json.JSONDecodeError:
                    pass
        except websockets.exceptions.ConnectionClosed:
            pass

    async with websockets.asyncio.client.connect(ws_url) as ws:
        task = asyncio.create_task(_reader(ws))
        try:
            yield queue
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
