"""Verification helpers for strict benchmark execution."""

from __future__ import annotations

from typing import Any


def throughput_drift_reason(
    *,
    baseline_mean: float,
    observer_mean: float,
    max_drift_pct: float,
) -> str | None:
    """Return an explicit reason when the proof throughput runs drift too far."""
    if baseline_mean <= 0:
        return (
            "throughput drift proof invalid: baseline mean must be positive "
            f"(got {baseline_mean:.1f})"
        )

    drift_pct = abs(observer_mean - baseline_mean) / baseline_mean * 100
    if drift_pct <= max_drift_pct:
        return None

    return (
        "throughput drift proof failed: throughput drift "
        f"{drift_pct:.1f}% exceeds allowed {max_drift_pct:.1f}% "
        f"(baseline={baseline_mean:.1f}, observer={observer_mean:.1f})"
    )


def benchmark_has_chartable_output(
    name: str,
    result: dict[str, Any] | None,
) -> tuple[bool, str]:
    """Return whether a benchmark produced any trustworthy, chartable output."""
    if result is None:
        return False, f"{name} benchmark did not produce output"

    if name in {"throughput", "snapshot"}:
        ok_results = [
            row for row in result.get("results", []) if row.get("status") == "ok"
        ]
        if ok_results:
            return True, ""
        return False, f"no valid {name} rows"

    if name == "recovery":
        ok_trials = [
            trial for trial in result.get("trials", []) if trial.get("status") == "ok"
        ]
        if ok_trials:
            return True, ""
        return False, "no valid recovery trials"

    if name == "latency":
        sample_count = (
            result.get("results", {})
            .get("aggregate", {})
            .get("sample_count", 0)
        )
        if sample_count > 0:
            return True, ""
        return False, "no latency samples"

    if name == "scaling":
        if result.get("timeline"):
            return True, ""
        return False, "empty scaling timeline"

    return False, f"unknown benchmark type: {name}"
