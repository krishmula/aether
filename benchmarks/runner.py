"""Benchmark runner CLI — entry point for `python -m benchmarks.runner`."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time

from benchmarks.config import BenchmarkConfig

logger = logging.getLogger("benchmarks")

BENCHMARKS = ("throughput", "latency", "snapshot", "recovery", "scaling")


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

    # Determine which benchmarks to run.
    if not args.benchmarks or "all" in args.benchmarks:
        to_run = list(BENCHMARKS)
    else:
        to_run = [b for b in args.benchmarks if b in BENCHMARKS]

    # Run each benchmark sequentially.
    summary: dict[str, str] = {}
    overall_start = time.monotonic()

    for name in to_run:
        result = await _run_benchmark(name, cfg)
        summary[name] = "OK" if result is not None else "FAILED"

    # Generate charts from whatever results we have.
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
            "  python -m benchmarks.runner                  # run all benchmarks\n"
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
