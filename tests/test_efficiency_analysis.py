"""부분집합 토큰을 중복 청구하지 않는 비용 시나리오 검증."""

from pathlib import Path
import runpy
import unittest

analysis = runpy.run_path(str(Path(__file__).resolve().parents[1] / "scripts/analyze-efficiency.py"))


class CostScenarioTests(unittest.TestCase):
    def test_cache_and_reasoning_are_not_added_twice(self):
        usage = {"input_tokens": 1000, "cached_input_tokens": 800, "cache_write_input_tokens": 0,
                 "output_tokens": 100, "reasoning_output_tokens": 50, "total_tokens": 1100}
        rates = {"ordinary_input_per_million_usd": 2, "cached_input_per_million_usd": .1,
                 "output_per_million_usd": 10}
        self.assertAlmostEqual(analysis["cost"](usage, rates, False), .003)
        self.assertAlmostEqual(analysis["cost"](usage, rates, True), .00148)

    def test_missing_or_unsupported_costs_stay_unobserved(self):
        self.assertIsNone(analysis["cost"](None, {}, False))
        self.assertIsNone(analysis["cost"]({"cache_write_input_tokens": 1}, {}, False))
