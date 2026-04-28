"""Unit tests for benchmark runner verification mode."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from benchmarks.config import BenchmarkConfig


class TestRunVerificationSuite(unittest.IsolatedAsyncioTestCase):
    async def test_runs_proof_then_ordered_suite_and_generates_charts(self) -> None:
        from benchmarks import runner

        cfg = BenchmarkConfig()

        with (
            patch(
                "benchmarks.runner._run_proof_throughput_check",
                new=AsyncMock(),
            ) as proof_mock,
            patch(
                "benchmarks.runner._run_benchmark",
                new=AsyncMock(
                    side_effect=[
                        {"results": [{"status": "ok"}]},
                        {"timeline": [{"publishers": 1}]},
                        {"results": {"aggregate": {"sample_count": 10}}},
                        {"results": [{"status": "ok"}]},
                        {"trials": [{"status": "ok"}]},
                    ]
                ),
            ) as run_mock,
            patch(
                "benchmarks.runner._generate_charts",
                new=AsyncMock(),
            ) as charts_mock,
        ):
            summary = await runner._run_verification_suite(cfg)

        proof_mock.assert_awaited_once_with(cfg)
        self.assertEqual(
            [call.args[0] for call in run_mock.await_args_list],
            ["throughput", "scaling", "latency", "snapshot", "recovery"],
        )
        charts_mock.assert_awaited_once_with(cfg)
        self.assertEqual(summary["throughput"], "OK")
        self.assertEqual(summary["recovery"], "OK")

    async def test_marks_benchmark_failed_when_output_is_not_chartable(self) -> None:
        from benchmarks import runner

        cfg = BenchmarkConfig()

        with (
            patch(
                "benchmarks.runner._run_proof_throughput_check",
                new=AsyncMock(),
            ),
            patch(
                "benchmarks.runner._run_benchmark",
                new=AsyncMock(
                    side_effect=[
                        {"results": [{"status": "invalid"}]},
                        {"timeline": [{"publishers": 1}]},
                        {"results": {"aggregate": {"sample_count": 10}}},
                        {"results": [{"status": "ok"}]},
                        {"trials": [{"status": "ok"}]},
                    ]
                ),
            ),
            patch(
                "benchmarks.runner._generate_charts",
                new=AsyncMock(),
            ),
        ):
            summary = await runner._run_verification_suite(cfg)

        self.assertEqual(summary["throughput"], "FAILED")
