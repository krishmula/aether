"""Chart generation — 6 matplotlib charts with dark theme."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from benchmarks.config import BenchmarkConfig

logger = logging.getLogger(__name__)

# Theme constants
BG_COLOR = "#0a0a0f"
PANEL_COLOR = "#12121a"
TEXT_COLOR = "#e0e0e0"
GRID_COLOR = "#2a2a3a"
CYAN = "#00d4ff"
GREEN = "#00ff88"
AMBER = "#ffaa00"
RED = "#ff4466"
PURPLE = "#aa66ff"

ACCENT_COLORS = [CYAN, GREEN, AMBER, RED, PURPLE, "#66ffcc", "#ff66aa", "#66aaff"]


def _apply_theme(ax, fig) -> None:  # type: ignore[no-untyped-def]
    """Apply the dark theme to a matplotlib axes and figure."""
    fig.patch.set_facecolor(BG_COLOR)
    ax.set_facecolor(PANEL_COLOR)
    ax.tick_params(colors=TEXT_COLOR, which="both")
    ax.xaxis.label.set_color(TEXT_COLOR)
    ax.yaxis.label.set_color(TEXT_COLOR)
    ax.title.set_color(TEXT_COLOR)
    ax.grid(True, color=GRID_COLOR, alpha=0.5, linestyle="--", linewidth=0.5)
    for spine in ax.spines.values():
        spine.set_color(GRID_COLOR)


def _load_json(path: Path) -> dict | None:
    if not path.exists():
        logger.warning("results file not found: %s", path)
        return None
    try:
        return json.loads(path.read_text())
    except Exception:
        logger.warning("failed to parse %s", path, exc_info=True)
        return None


# -------------------------------------------------------------------------
# Chart 1: Throughput vs. Brokers
# -------------------------------------------------------------------------


def chart_throughput_vs_brokers(cfg: BenchmarkConfig) -> None:
    import matplotlib.pyplot as plt

    data = _load_json(cfg.results_dir / "throughput.json")
    if data is None:
        return

    results = [r for r in data.get("results", []) if r.get("status") == "ok"]
    if not results:
        return

    # Group by publisher count.
    by_pubs: dict[int, list[tuple[int, float]]] = {}
    for r in results:
        pubs = r["publishers"]
        brokers = r["brokers"]
        mean = r["stats"]["mean"]
        by_pubs.setdefault(pubs, []).append((brokers, mean))

    fig, ax = plt.subplots(figsize=(12, 7), dpi=150)
    _apply_theme(ax, fig)

    for i, (pubs, points) in enumerate(sorted(by_pubs.items())):
        points.sort()
        xs = [p[0] for p in points]
        ys = [p[1] for p in points]
        color = ACCENT_COLORS[i % len(ACCENT_COLORS)]
        ax.plot(xs, ys, marker="o", color=color, linewidth=2, label=f"{pubs} pub(s)")

    ax.set_xlabel("Broker Count", fontsize=12)
    ax.set_ylabel("Throughput (msg/s)", fontsize=12)
    ax.set_title("Throughput vs. Broker Count", fontsize=14, fontweight="bold")
    ax.legend(facecolor=PANEL_COLOR, edgecolor=GRID_COLOR, labelcolor=TEXT_COLOR)

    out = cfg.results_dir / "throughput_vs_brokers.png"
    fig.savefig(out, bbox_inches="tight", facecolor=BG_COLOR)
    plt.close(fig)
    logger.info("chart saved: %s", out)


# -------------------------------------------------------------------------
# Chart 2: Throughput vs. Publishers (scaling curve)
# -------------------------------------------------------------------------


def chart_throughput_vs_publishers(cfg: BenchmarkConfig) -> None:
    import matplotlib.pyplot as plt

    data = _load_json(cfg.results_dir / "throughput.json")
    if data is None:
        return

    results = [r for r in data.get("results", []) if r.get("status") == "ok"]
    if not results:
        return

    # Group by broker count.
    by_brokers: dict[int, list[tuple[int, float]]] = {}
    for r in results:
        brokers = r["brokers"]
        pubs = r["publishers"]
        mean = r["stats"]["mean"]
        by_brokers.setdefault(brokers, []).append((pubs, mean))

    fig, ax = plt.subplots(figsize=(12, 7), dpi=150)
    _apply_theme(ax, fig)

    for i, (brokers, points) in enumerate(sorted(by_brokers.items())):
        points.sort()
        xs = [p[0] for p in points]
        ys = [p[1] for p in points]
        color = ACCENT_COLORS[i % len(ACCENT_COLORS)]
        ax.plot(
            xs, ys, marker="s", color=color, linewidth=2,
            label=f"{brokers} broker(s)",
        )

    ax.set_xlabel("Publisher Count", fontsize=12)
    ax.set_ylabel("Throughput (msg/s)", fontsize=12)
    ax.set_title("Throughput vs. Publisher Count", fontsize=14, fontweight="bold")
    ax.legend(facecolor=PANEL_COLOR, edgecolor=GRID_COLOR, labelcolor=TEXT_COLOR)

    out = cfg.results_dir / "throughput_vs_publishers.png"
    fig.savefig(out, bbox_inches="tight", facecolor=BG_COLOR)
    plt.close(fig)
    logger.info("chart saved: %s", out)


# -------------------------------------------------------------------------
# Chart 3: Latency Distribution
# -------------------------------------------------------------------------


def chart_latency_distribution(cfg: BenchmarkConfig) -> None:
    import matplotlib.pyplot as plt

    data = _load_json(cfg.results_dir / "latency.json")
    if data is None:
        return

    agg = data.get("results", {}).get("aggregate", {})
    if agg.get("sample_count", 0) == 0:
        logger.warning("no latency samples — skipping chart")
        return

    # We only have aggregate percentiles, not raw samples.
    # Build a synthetic visualization from the percentile values.
    p50 = agg.get("p50", 0)
    p95 = agg.get("p95", 0)
    p99 = agg.get("p99", 0)
    sample_count = agg.get("sample_count", 0)

    fig, ax = plt.subplots(figsize=(12, 7), dpi=150)
    _apply_theme(ax, fig)

    # Create a bar chart of percentiles.
    labels = ["p50", "p95", "p99"]
    values = [p50, p95, p99]
    colors = [GREEN, AMBER, RED]
    bars = ax.bar(labels, values, color=colors, width=0.5, edgecolor=GRID_COLOR)

    for bar, val in zip(bars, values, strict=True):
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            bar.get_height() + max(values) * 0.02,
            f"{val:.0f} \u00b5s",
            ha="center",
            va="bottom",
            color=TEXT_COLOR,
            fontsize=12,
            fontweight="bold",
        )

    ax.set_ylabel("Latency (\u00b5s)", fontsize=12)
    ax.set_title(
        f"End-to-End Delivery Latency  (n={sample_count:,})",
        fontsize=14,
        fontweight="bold",
    )

    out = cfg.results_dir / "latency_distribution.png"
    fig.savefig(out, bbox_inches="tight", facecolor=BG_COLOR)
    plt.close(fig)
    logger.info("chart saved: %s", out)


# -------------------------------------------------------------------------
# Chart 4: Snapshot Time vs. Brokers
# -------------------------------------------------------------------------


def chart_snapshot_vs_brokers(cfg: BenchmarkConfig) -> None:
    import matplotlib.pyplot as plt

    data = _load_json(cfg.results_dir / "snapshot.json")
    if data is None:
        return

    results = [r for r in data.get("results", []) if r.get("status") == "ok"]
    if not results:
        return

    brokers = [r["brokers"] for r in results]
    means = [r["summary"]["mean_ms"] for r in results]
    mins = [r["summary"]["min_ms"] for r in results]
    maxes = [r["summary"]["max_ms"] for r in results]

    fig, ax = plt.subplots(figsize=(12, 7), dpi=150)
    _apply_theme(ax, fig)

    ax.plot(brokers, means, marker="o", color=CYAN, linewidth=2, label="Mean")
    ax.fill_between(brokers, mins, maxes, alpha=0.2, color=CYAN, label="Min–Max range")

    ax.set_xlabel("Broker Count", fontsize=12)
    ax.set_ylabel("Snapshot Coordination Time (ms)", fontsize=12)
    ax.set_title(
        "Chandy-Lamport Snapshot Coordination Overhead",
        fontsize=14,
        fontweight="bold",
    )
    ax.legend(facecolor=PANEL_COLOR, edgecolor=GRID_COLOR, labelcolor=TEXT_COLOR)

    out = cfg.results_dir / "snapshot_vs_brokers.png"
    fig.savefig(out, bbox_inches="tight", facecolor=BG_COLOR)
    plt.close(fig)
    logger.info("chart saved: %s", out)


# -------------------------------------------------------------------------
# Chart 5: Recovery Timeline
# -------------------------------------------------------------------------


def chart_recovery_timeline(cfg: BenchmarkConfig) -> None:
    import matplotlib.pyplot as plt

    data = _load_json(cfg.results_dir / "recovery.json")
    if data is None:
        return

    trials = [t for t in data.get("trials", []) if t.get("status") == "ok"]
    if not trials:
        return

    fig, ax = plt.subplots(figsize=(12, 7), dpi=150)
    _apply_theme(ax, fig)

    labels: list[str] = []
    detection_bars: list[float] = []
    recovery_bars: list[float] = []
    reconnect_bars: list[float] = []

    for t in trials:
        path = t.get("actual_path", "?")
        trial_num = t.get("trial", "?")
        labels.append(f"Trial {trial_num}\n({path})")

        detection = t.get("detection_time_s", 0)
        total_recovery = t.get("recovery_time_s", 0)
        reconnect = t.get("subscriber_reconnect_time_s", 0)

        # Stacked: detection | recovery (minus detection) | reconnect (beyond recovery)
        recovery_only = max(0, total_recovery - detection)
        reconnect_only = max(0, reconnect - total_recovery)

        detection_bars.append(detection)
        recovery_bars.append(recovery_only)
        reconnect_bars.append(reconnect_only)

    y_pos = range(len(labels))

    ax.barh(
        y_pos,
        detection_bars,
        color=AMBER,
        edgecolor=GRID_COLOR,
        label="Detection",
        height=0.6,
    )
    ax.barh(
        y_pos,
        recovery_bars,
        left=detection_bars,
        color=CYAN,
        edgecolor=GRID_COLOR,
        label="Recovery",
        height=0.6,
    )
    lefts = [
        d + r for d, r in zip(detection_bars, recovery_bars, strict=True)
    ]
    ax.barh(
        y_pos,
        reconnect_bars,
        left=lefts,
        color=GREEN,
        edgecolor=GRID_COLOR,
        label="Reconnect",
        height=0.6,
    )

    ax.set_yticks(list(y_pos))
    ax.set_yticklabels(labels)
    ax.set_xlabel("Time (seconds)", fontsize=12)
    ax.set_title("Recovery Timeline by Trial", fontsize=14, fontweight="bold")
    ax.legend(
        facecolor=PANEL_COLOR,
        edgecolor=GRID_COLOR,
        labelcolor=TEXT_COLOR,
        loc="lower right",
    )
    ax.invert_yaxis()

    out = cfg.results_dir / "recovery_timeline.png"
    fig.savefig(out, bbox_inches="tight", facecolor=BG_COLOR)
    plt.close(fig)
    logger.info("chart saved: %s", out)


# -------------------------------------------------------------------------
# Chart 6: Scaling Curve
# -------------------------------------------------------------------------


def chart_scaling_curve(cfg: BenchmarkConfig) -> None:
    import matplotlib.pyplot as plt

    data = _load_json(cfg.results_dir / "scaling.json")
    if data is None:
        return

    timeline = data.get("timeline", [])
    if not timeline:
        return

    fig, ax = plt.subplots(figsize=(12, 7), dpi=150)
    _apply_theme(ax, fig)

    pubs = [t["publishers"] for t in timeline]
    rates = [t["mean_msgs_per_sec"] for t in timeline]

    ax.plot(pubs, rates, marker="o", color=CYAN, linewidth=2.5)
    ax.fill_between(pubs, 0, rates, alpha=0.1, color=CYAN)

    # Annotate saturation point if found.
    sat = data.get("saturation_point")
    if sat:
        ax.axvline(
            x=sat["publishers"],
            color=RED,
            linestyle="--",
            linewidth=1.5,
            alpha=0.8,
        )
        ax.annotate(
            f"Saturation\n({sat['publishers']} pubs, {sat['throughput']:.0f} msg/s)",
            xy=(sat["publishers"], sat["throughput"]),
            xytext=(sat["publishers"] + 0.5, sat["throughput"] * 0.85),
            color=RED,
            fontsize=10,
            fontweight="bold",
            arrowprops=dict(arrowstyle="->", color=RED, lw=1.5),
        )

    ax.set_xlabel("Publisher Count", fontsize=12)
    ax.set_ylabel("Throughput (msg/s)", fontsize=12)
    ax.set_title(
        "Throughput Scaling Curve (Publisher Ramp)",
        fontsize=14,
        fontweight="bold",
    )

    out = cfg.results_dir / "scaling_curve.png"
    fig.savefig(out, bbox_inches="tight", facecolor=BG_COLOR)
    plt.close(fig)
    logger.info("chart saved: %s", out)


# -------------------------------------------------------------------------
# Generate all
# -------------------------------------------------------------------------


def generate_all_charts(cfg: BenchmarkConfig) -> None:
    """Generate all available charts from existing JSON results."""
    logger.info("generating charts from %s", cfg.results_dir)

    chart_throughput_vs_brokers(cfg)
    chart_throughput_vs_publishers(cfg)
    chart_latency_distribution(cfg)
    chart_snapshot_vs_brokers(cfg)
    chart_recovery_timeline(cfg)
    chart_scaling_curve(cfg)

    logger.info("chart generation complete")
