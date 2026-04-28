"""Unit tests for benchmark verification gates."""

from __future__ import annotations

import unittest

from benchmarks.verification import (
    benchmark_has_chartable_output,
    throughput_drift_reason,
)


class TestThroughputDriftReason(unittest.TestCase):
    def test_allows_small_proof_run_drift(self) -> None:
        reason = throughput_drift_reason(
            baseline_mean=100.0,
            observer_mean=103.0,
            max_drift_pct=5.0,
        )

        self.assertIsNone(reason)

    def test_rejects_large_proof_run_drift(self) -> None:
        reason = throughput_drift_reason(
            baseline_mean=100.0,
            observer_mean=112.0,
            max_drift_pct=5.0,
        )

        self.assertIn("throughput drift", reason)
        self.assertIn("12.0%", reason)


class TestBenchmarkHasChartableOutput(unittest.TestCase):
    def test_throughput_requires_at_least_one_ok_result(self) -> None:
        ok, reason = benchmark_has_chartable_output(
            "throughput",
            {"results": [{"status": "invalid"}]},
        )

        self.assertFalse(ok)
        self.assertIn("no valid throughput rows", reason)

    def test_snapshot_accepts_ok_rows(self) -> None:
        ok, reason = benchmark_has_chartable_output(
            "snapshot",
            {"results": [{"status": "ok"}, {"status": "invalid"}]},
        )

        self.assertTrue(ok)
        self.assertEqual(reason, "")

    def test_recovery_requires_at_least_one_ok_trial(self) -> None:
        ok, reason = benchmark_has_chartable_output(
            "recovery",
            {"trials": [{"status": "invalid"}]},
        )

        self.assertFalse(ok)
        self.assertIn("no valid recovery trials", reason)

    def test_latency_requires_samples(self) -> None:
        ok, reason = benchmark_has_chartable_output(
            "latency",
            {"results": {"aggregate": {"sample_count": 0}}},
        )

        self.assertFalse(ok)
        self.assertIn("no latency samples", reason)

    def test_scaling_requires_timeline(self) -> None:
        ok, reason = benchmark_has_chartable_output("scaling", {"timeline": []})

        self.assertFalse(ok)
        self.assertIn("empty scaling timeline", reason)
