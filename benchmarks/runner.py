"""Benchmark runner CLI — entry point for `python -m benchmarks.runner`."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import shutil
import sys
import time
from dataclasses import replace

from benchmarks.config import BenchmarkConfig
from benchmarks.verification import (
    benchmark_has_chartable_output,
    throughput_drift_reason,
)

logger = logging.getLogger("benchmarks")

BENCHMARKS = ("throughput", "latency", "snapshot", "recovery", "scaling")
VERIFICATION_ORDER = ("throughput", "scaling", "latency", "snapshot", "recovery")


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )


async def _preflight(cfg: BenchmarkConfig) -> bool:
    """Check that the orchestrator is reachable."""
    from benchmarks.client import AetherClient

    client = AetherClient(cfg)
    try:
        await client.wait_ready(timeout=10)
        state = await client.get_state()
        n = (
            len(state.get("brokers", []))
            + len(state.get("publishers", []))
            + len(state.get("subscribers", []))
        )
        logger.info(
            "preflight OK — orchestrator reachable, %d components active", n
        )
        return True
    except RuntimeError as exc:
        logger.error("preflight FAILED: %s", exc)
        return False
    finally:
        await client.close()


async def _run_benchmark(name: str, cfg: BenchmarkConfig) -> dict | None:
    """Import and run a single benchmark module by name."""
    logger.info("=" * 60)
    logger.info("Starting benchmark: %s", name)
    logger.info("=" * 60)
    start = time.monotonic()

    try:
        if name == "throughput":
            from benchmarks.throughput import run
        elif name == "latency":
            from benchmarks.latency import run  # type: ignore[no-redef]
        elif name == "snapshot":
            from benchmarks.snapshot import run  # type: ignore[no-redef]
        elif name == "recovery":
            from benchmarks.recovery import run  # type: ignore[no-redef]
        elif name == "scaling":
            from benchmarks.scaling import run  # type: ignore[no-redef]
        else:
            logger.error("Unknown benchmark: %s", name)
            return None

        result = await run(cfg)
        elapsed = time.monotonic() - start
        logger.info("benchmark '%s' completed in %.1fs", name, elapsed)
        return result

    except Exception:
        elapsed = time.monotonic() - start
        logger.error(
            "benchmark '%s' FAILED after %.1fs", name, elapsed, exc_info=True
        )
        return None


async def _generate_charts(cfg: BenchmarkConfig) -> None:
    """Generate charts from existing JSON results."""
    try:
        from benchmarks.charts import generate_all_charts

        generate_all_charts(cfg)
    except ImportError:
        logger.error(
            "matplotlib not installed. Run: pip install -e '.[benchmark]'"
        )
    except Exception:
        logger.error("chart generation failed", exc_info=True)


def _clear_previous_results(cfg: BenchmarkConfig) -> None:
    """Delete stale result artifacts so charts only reflect the current run."""
    if not cfg.results_dir.exists():
        return

    for path in cfg.results_dir.iterdir():
        if path.name == ".gitkeep":
            continue
        if path.is_dir():
            shutil.rmtree(path)
            continue
        if path.is_file() and path.suffix in {".json", ".png"}:
            path.unlink()


def _extract_single_throughput_mean(result: dict) -> float:
    """Extract the p50 throughput from a one-row proof run.

    p50 is used instead of mean because the 1s cache-refresh background thread
    and 1s poll interval can occasionally coincide, producing a 0-rate sample
    when two consecutive polls read the same cached value. p50 is robust to a
    single such outlier; mean is not.
    """
    ok_rows = [row for row in result.get("results", []) if row.get("status") == "ok"]
    if not ok_rows:
        raise RuntimeError("proof throughput run produced no valid rows")
    if len(ok_rows) != 1:
        raise RuntimeError(
            f"proof throughput run expected 1 valid row, got {len(ok_rows)}"
        )
    return float(ok_rows[0]["stats"]["p50"])


async def _run_metrics_reader(
    cfg: BenchmarkConfig,
    *,
    stop_event: asyncio.Event,
) -> None:
    """Continuously poll /api/metrics during the proof run."""
    from benchmarks.client import AetherClient

    client = AetherClient(cfg)
    try:
        while not stop_event.is_set():
            try:
                await client.get_metrics()
            except Exception:
                logger.debug(
                    "proof metrics reader poll failed",
                    exc_info=True,
                )
            await asyncio.sleep(cfg.verification_metrics_reader_poll_interval)
    finally:
        await client.close()


async def _run_proof_throughput_check(cfg: BenchmarkConfig) -> None:
    """Prove that an extra /api/metrics reader does not perturb throughput."""
    from benchmarks.throughput import run as run_throughput

    proof_cfg = replace(
        cfg,
        broker_counts=[cfg.verification_proof_brokers],
        publisher_counts=[cfg.verification_proof_publishers],
        warmup_seconds=cfg.verification_proof_warmup_seconds,
        measurement_seconds=cfg.verification_proof_measurement_seconds,
        results_dir=cfg.results_dir / "proof-alone",
    )
    proof_cfg.results_dir.mkdir(parents=True, exist_ok=True)
    baseline_result = await run_throughput(proof_cfg)
    baseline_mean = _extract_single_throughput_mean(baseline_result)

    observed_cfg = replace(
        proof_cfg,
        results_dir=cfg.results_dir / "proof-with-reader",
    )
    observed_cfg.results_dir.mkdir(parents=True, exist_ok=True)
    stop_event = asyncio.Event()
    reader_task = asyncio.create_task(
        _run_metrics_reader(observed_cfg, stop_event=stop_event)
    )
    try:
        observed_result = await run_throughput(observed_cfg)
    finally:
        stop_event.set()
        await reader_task

    observer_mean = _extract_single_throughput_mean(observed_result)
    reason = throughput_drift_reason(
        baseline_mean=baseline_mean,
        observer_mean=observer_mean,
        max_drift_pct=cfg.verification_max_throughput_drift_pct,
    )
    proof_output = {
        "benchmark": "verification",
        "timestamp": time.time(),
        "proof": {
            "baseline_mean_msgs_per_sec": round(baseline_mean, 1),
            "observer_mean_msgs_per_sec": round(observer_mean, 1),
            "max_allowed_drift_pct": cfg.verification_max_throughput_drift_pct,
            "status": "ok" if reason is None else "failed",
            "failure_reason": reason,
        },
    }
    out_path = cfg.results_dir / "verification.json"
    out_path.write_text(json.dumps(proof_output, indent=2))

    if reason is not None:
        raise RuntimeError(reason)

    logger.info(
        "proof check passed: baseline=%.1f msg/s, with_reader=%.1f msg/s",
        baseline_mean,
        observer_mean,
    )


async def _run_verification_suite(cfg: BenchmarkConfig) -> dict[str, str]:
    """Run the strict proof check followed by the ordered benchmark suite."""
    summary: dict[str, str] = {}

    await _run_proof_throughput_check(cfg)

    for name in VERIFICATION_ORDER:
        result = await _run_benchmark(name, cfg)
        chartable, reason = benchmark_has_chartable_output(name, result)
        if result is not None and chartable:
            summary[name] = "OK"
        else:
            summary[name] = "FAILED"
            logger.error(
                "benchmark '%s' failed verification: %s",
                name,
                reason,
            )

    await _generate_charts(cfg)
    return summary


async def _main(args: argparse.Namespace) -> int:
    cfg = BenchmarkConfig(orchestrator_url=args.orchestrator_url)
    cfg.results_dir.mkdir(parents=True, exist_ok=True)

    if args.charts_only:
        await _generate_charts(cfg)
        return 0

    if not await _preflight(cfg):
        return 1

    if args.dry_run:
        logger.info("dry run complete — system is reachable")
        return 0

    _clear_previous_results(cfg)
    overall_start = time.monotonic()
    if not args.benchmarks or "all" in args.benchmarks:
        summary = await _run_verification_suite(cfg)
    else:
        to_run = [b for b in args.benchmarks if b in BENCHMARKS]
        summary: dict[str, str] = {}
        for name in to_run:
            result = await _run_benchmark(name, cfg)
            chartable, reason = benchmark_has_chartable_output(name, result)
            if result is not None and chartable:
                summary[name] = "OK"
            else:
                summary[name] = "FAILED"
                logger.error(
                    "benchmark '%s' failed verification: %s",
                    name,
                    reason,
                )
        await _generate_charts(cfg)

    elapsed = time.monotonic() - overall_start
    logger.info("")
    logger.info("=" * 60)
    logger.info("Benchmark suite complete in %.1fs", elapsed)
    logger.info("-" * 60)
    for name, status in summary.items():
        icon = "+" if status == "OK" else "x"
        logger.info("  [%s] %s", icon, name)
    logger.info("=" * 60)

    return 0 if all(s == "OK" for s in summary.values()) else 1


def main() -> None:
    _setup_logging()

    parser = argparse.ArgumentParser(
        description="Aether Benchmark Suite",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  python -m benchmarks.runner                  # run proof + "
            "strict full suite\n"
            "  python -m benchmarks.runner throughput       # run single benchmark\n"
            "  python -m benchmarks.runner --charts-only    # regenerate charts\n"
            "  python -m benchmarks.runner --dry-run        # check connectivity\n"
        ),
    )
    parser.add_argument(
        "benchmarks",
        nargs="*",
        choices=["all", *BENCHMARKS],
        metavar=f"{{{','.join(['all', *BENCHMARKS])}}}",
        help="which benchmarks to run (default: all)",
    )
    parser.add_argument(
        "--charts-only",
        action="store_true",
        help="regenerate charts from existing JSON results",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="validate connectivity only — do not run benchmarks",
    )
    parser.add_argument(
        "--orchestrator-url",
        default="http://localhost:9000",
        help="orchestrator base URL (default: http://localhost:9000)",
    )

    args = parser.parse_args()
    rc = asyncio.run(_main(args))
    sys.exit(rc)


if __name__ == "__main__":
    main()
