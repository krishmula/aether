"""Recovery benchmark (crown jewel) — measures failover detection and recovery times."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from benchmarks.client import AetherClient, event_stream
from benchmarks.collectors import collect_recovery_events
from benchmarks.config import BenchmarkConfig

logger = logging.getLogger(__name__)


async def _wait_for_snapshot(
    events: asyncio.Queue[dict[str, Any]],
    timeout: float = 45.0,
) -> float | None:
    """Wait for the next SNAPSHOT_COMPLETE event and return its timestamp."""
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        try:
            event = await asyncio.wait_for(events.get(), timeout=2.0)
        except asyncio.TimeoutError:
            continue
        if event.get("type") == "snapshot_complete":
            return event.get("timestamp", time.time())
    return None


async def _wait_for_stale_snapshots(
    client: AetherClient,
    stale_seconds: float,
    timeout: float,
    poll_interval: float = 3.0,
) -> bool:
    """Poll /api/snapshots until every broker snapshot is older than stale_seconds.

    Returns True when confirmed stale, False if the timeout expires first.
    With snapshot_interval=90s and stale_seconds=50s, this resolves within
    ~50s and the next snapshot cycle won't fire for another 40s — giving
    a clean window in which the orchestrator's recovery manager will choose
    Path B (redistribution).
    """
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        try:
            data = await client.get_snapshots()
            broker_snaps = data.get("brokers", [])
            if broker_snaps:
                ages = [
                    s["age_seconds"]
                    for s in broker_snaps
                    if s.get("age_seconds") is not None
                ]
                if ages and all(age > stale_seconds for age in ages):
                    return True
        except Exception as exc:
            logger.debug("snapshot staleness poll failed: %s", exc)
        await asyncio.sleep(poll_interval)
    return False


async def _run_trial(
    client: AetherClient,
    cfg: BenchmarkConfig,
    events: asyncio.Queue[dict[str, Any]],
    trial: int,
    path_a: bool,
) -> dict[str, Any]:
    """Run a single recovery trial.

    For Path A: trigger chaos shortly after a snapshot (fresh snapshot available).
    For Path B: wait past the configured snapshot freshness threshold so it is stale.
    """
    target_path = "A" if path_a else "B"
    expected_path = "replacement" if path_a else "redistribution"
    logger.info(
        "  trial %d (target: Path %s) — waiting for snapshot...",
        trial, target_path,
    )

    if path_a:
        # Wait for a fresh snapshot via WS event, then trigger chaos quickly.
        snap_ts = await _wait_for_snapshot(events, timeout=cfg.recovery_path_a_snapshot_wait_seconds)
        if snap_ts is not None:
            logger.info(
                "  trial %d — snapshot seen, waiting %.0fs before chaos...",
                trial,
                cfg.recovery_path_a_delay_seconds,
            )
            await asyncio.sleep(cfg.recovery_path_a_delay_seconds)
        else:
            logger.warning(
                "  trial %d — no snapshot observed, triggering chaos anyway",
                trial,
            )
    else:
        # Poll /api/snapshots directly until all broker snapshots are confirmed
        # stale. With snapshot_interval=90s and stale_threshold=50s, this
        # settles cleanly — the next snapshot cycle is still 40s away.
        stale_threshold = (
            cfg.recovery_snapshot_max_age_seconds + cfg.recovery_stale_margin_seconds
        )
        logger.info(
            "  trial %d — waiting for all snapshots to be >%.0fs old...",
            trial,
            stale_threshold,
        )
        achieved = await _wait_for_stale_snapshots(
            client,
            stale_threshold,
            timeout=stale_threshold * 3,
            poll_interval=cfg.recovery_snapshot_poll_interval,
        )
        if not achieved:
            logger.warning(
                "  trial %d — snapshot staleness not confirmed within timeout, "
                "triggering anyway",
                trial,
            )

    # Drain any queued events before triggering chaos.
    while not events.empty():
        try:
            events.get_nowait()
        except asyncio.QueueEmpty:
            break

    logger.info("  trial %d — triggering chaos...", trial)
    try:
        chaos_result = await client.trigger_chaos()
        chaos_target = chaos_result.get("chaos_target")
        logger.info("  trial %d — killed broker %s", trial, chaos_target)
    except Exception as exc:
        logger.error("  trial %d — chaos failed: %s", trial, exc)
        return {
            "trial": trial,
            "target_path": target_path,
            "status": "chaos_failed",
            "error": str(exc),
        }

    # Collect recovery events.
    try:
        recovery = await collect_recovery_events(events, timeout=60.0)
    except RuntimeError as exc:
        return {
            "trial": trial,
            "target_path": target_path,
            "expected_path": expected_path,
            "status": "invalid",
            "chaos_target": chaos_target,
            "invalid_reason": str(exc),
        }

    actual_path = recovery.get("recovery_path")
    if actual_path != expected_path:
        return {
            "trial": trial,
            "target_path": target_path,
            "expected_path": expected_path,
            "actual_path": actual_path,
            "status": "invalid",
            "chaos_target": chaos_target,
            "invalid_reason": (
                "recovery path mismatch: expected "
                f"{expected_path}, observed {actual_path}"
            ),
            **recovery,
        }

    result = {
        "trial": trial,
        "target_path": target_path,
        "expected_path": expected_path,
        "actual_path": actual_path,
        "status": "ok",
        "chaos_target": chaos_target,
        **recovery,
    }
    logger.info(
        "  trial %d — path=%s, recovery=%.3fs",
        trial,
        recovery.get("recovery_path", "?"),
        recovery.get("recovery_time_s", 0),
    )

    # Wait for system to stabilize before next trial.
    await asyncio.sleep(5)

    return result


async def run(cfg: BenchmarkConfig) -> dict[str, Any]:
    """Run the recovery benchmark.

    Runs recovery_trials trials: the first recovery_path_a_trials target Path A,
    the rest target Path B.
    """
    client = AetherClient(cfg)
    trials: list[dict[str, Any]] = []

    try:
        # Seed a standard topology.
        await client.cleanup()
        await asyncio.sleep(2)

        logger.info(
            "recovery: seeding topology (3 brokers, 2 pubs, 3 subs)"
        )
        await client.seed_topology(3, 2, 3)
        await client.wait_all_running(timeout=cfg.recovery_startup_timeout)

        logger.info("recovery: warmup (%ds)...", cfg.warmup_seconds)
        await asyncio.sleep(cfg.warmup_seconds)

        async with event_stream(cfg) as events:
            for i in range(cfg.recovery_trials):
                path_a = i < cfg.recovery_path_a_trials

                # Ensure we have enough brokers for chaos (need >= 2 running).
                state = await client.get_state()
                running_brokers = [
                    b for b in state.get("brokers", [])
                    if b.get("status") == "running"
                ]
                if len(running_brokers) < 2:
                    logger.info("  fewer than 2 brokers running — re-seeding...")
                    await client.seed_topology(3, 2, 3)
                    await client.wait_all_running(timeout=60)
                    await asyncio.sleep(cfg.warmup_seconds)

                result = await _run_trial(client, cfg, events, i + 1, path_a)
                trials.append(result)

    finally:
        await client.cleanup()
        await client.close()

    # Summarize by path.
    path_a_trials = [
        t
        for t in trials
        if t.get("status") == "ok" and t.get("actual_path") == "replacement"
    ]
    path_b_trials = [
        t
        for t in trials
        if t.get("status") == "ok" and t.get("actual_path") == "redistribution"
    ]

    def _summarize(group: list[dict]) -> dict[str, Any]:
        recovery_times = [t["recovery_time_s"] for t in group if "recovery_time_s" in t]
        if not recovery_times:
            return {"count": len(group), "mean_s": 0, "min_s": 0, "max_s": 0}
        return {
            "count": len(group),
            "mean_s": round(sum(recovery_times) / len(recovery_times), 3),
            "min_s": round(min(recovery_times), 3),
            "max_s": round(max(recovery_times), 3),
        }

    output = {
        "benchmark": "recovery",
        "timestamp": time.time(),
        "config": {
            "recovery_trials": cfg.recovery_trials,
            "recovery_path_a_trials": cfg.recovery_path_a_trials,
            "path_a_delay_seconds": cfg.recovery_path_a_delay_seconds,
            "snapshot_max_age_seconds": cfg.recovery_snapshot_max_age_seconds,
            "stale_margin_seconds": cfg.recovery_stale_margin_seconds,
        },
        "trials": trials,
        "summary": {
            "path_a_replacement": _summarize(path_a_trials),
            "path_b_redistribution": _summarize(path_b_trials),
            "total_trials": len(trials),
            "valid_trials": len([t for t in trials if t.get("status") == "ok"]),
            "invalid_trials": len(
                [t for t in trials if t.get("status") == "invalid"]
            ),
        },
    }

    out_path = cfg.results_dir / "recovery.json"
    out_path.write_text(json.dumps(output, indent=2))
    logger.info("recovery results written to %s", out_path)

    return output
