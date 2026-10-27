"""작은 CPU 측정의 계약, 실패 보존, 교차 순서와 guest 검사를 검증한다."""

from hashlib import sha256
from importlib.util import find_spec
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import subprocess

from source_shift.benchmark import ENGINE_CONFIG, _run_runtime, benchmark_pair
from source_shift.runner import find_clang
from source_shift.tasks import get_task

ROOT = Path(__file__).resolve().parents[1]
CLANG = find_clang(os.environ.get("SOURCE_SHIFT_CLANG")) or find_clang(
    str(ROOT / ".tools/llvm-23.1.4/bin/clang.exe"))
WAT_SUM = "local.get $a local.get $b i32.add local.get $c i32.add"


class BenchmarkConfigurationTests(unittest.TestCase):
    def test_invalid_counts_rejected_before_artifacts(self):
        for key in ("build_rounds", "runtime_rounds", "cycles"):
            for value in (0, -1, True, 1.5):
                with tempfile.TemporaryDirectory() as temp:
                    output = Path(temp) / "benchmark"
                    with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                        benchmark_pair("P01", "return a+b+c;", WAT_SUM, output, "missing", **{key: value})
                    self.assertFalse(output.exists())

    def test_timeout_keeps_last_completed_runtime_record(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            (directory / "runtime-result.json").write_text(
                json.dumps({"status": "running", "records": [{"status": "passed", "round": 0}]}), encoding="utf-8")
            with patch("source_shift.benchmark.run_process", side_effect=subprocess.TimeoutExpired("worker", 60)):
                result = _run_runtime({}, directory)
            self.assertEqual(result["status"], "wall_timeout")
            self.assertEqual(len(result["records"]), 1)
            self.assertTrue((directory / "runtime-stderr.txt").exists())


@unittest.skipUnless(CLANG and find_spec("wasmtime"), "Clang/Wasmtime이 필요합니다.")
class BenchmarkIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.output = Path(self.temp.name) / "benchmark"

    def run_pair(self, c_body="return a+b+c;", wat_body=WAT_SUM, cycles=2):
        return benchmark_pair("P01", c_body, wat_body, self.output, CLANG,
                              build_rounds=2, runtime_rounds=2, cycles=cycles)

    def test_sum_records_rounds_checksums_and_artifact_hashes(self):
        report = self.run_pair()
        self.assertEqual(report["status"], "passed", report["runtime"])
        self.assertEqual(report["config"]["engine"], ENGINE_CONFIG)
        self.assertFalse(report["config"]["engine"]["consume_fuel"])
        self.assertEqual(report["environment"]["background_load"], "uncontrolled")
        self.assertEqual([r["route"] for r in report["build"]], ["c", "wat", "wat", "c", "c", "wat"])
        self.assertEqual(sum(r["warmup"] for r in report["build"]), 2)
        task = get_task("P01")
        count = len(task.public_cases) + len(task.private_cases)
        self.assertEqual(report["workload"]["function_calls_per_batch"], count * 2)
        self.assertEqual(report["workload"]["expected_xor"], 0)
        for phase in ("cold", "warm"):
            records = [r for r in report["runtime"]["records"] if r["phase"] == phase]
            self.assertEqual([r["route"] for r in records], ["c", "wat", "wat", "c", "c", "wat"])
            self.assertEqual(sum(r["warmup"] for r in records), 2)
        self.assertTrue(report["summary"]["wat"]["batch_seconds_median"] > 0)
        for artifact in report["artifacts"]:
            data = (self.output / artifact["path"]).read_bytes()
            self.assertEqual(artifact["bytes"], len(data))
            self.assertEqual(artifact["sha256"], sha256(data).hexdigest())
        saved = json.loads((self.output / "benchmark.json").read_text(encoding="utf-8"))
        self.assertEqual(saved, report)

    def test_even_cycles_wrong_results_cannot_hide_behind_zero_xor(self):
        report = self.run_pair(wat_body="i32.const 0")
        self.assertEqual(report["status"], "failed")
        self.assertIsNone(report["summary"])
        warm = [r for r in report["runtime"]["records"] if r["phase"] == "warm" and r["route"] == "wat"]
        self.assertTrue(all(r["status"] == "checksum_mismatch" for r in warm), warm)

    def test_target_trap_is_preserved_and_never_summarized(self):
        report = self.run_pair(wat_body="unreachable")
        self.assertEqual(report["status"], "failed")
        self.assertIsNone(report["summary"])
        warm = [r for r in report["runtime"]["records"] if r["phase"] == "warm" and r["route"] == "wat"]
        self.assertTrue(all(r["status"] == "trap" for r in warm))
        self.assertTrue(all("Trap" in r["error"] for r in warm))

    def test_compile_error_preserves_all_attempts_without_runtime(self):
        report = self.run_pair(c_body="return @;")
        self.assertEqual(report["status"], "failed")
        self.assertIsNone(report["runtime"])
        self.assertIsNone(report["summary"])
        builds = [r for r in report["build"] if r["route"] == "c"]
        self.assertEqual(len(builds), 3)
        self.assertTrue(all(r["status"] == "conversion_error" for r in builds))
        self.assertTrue((self.output / "build-00-c/conversion-stderr.txt").is_file())


if __name__ == "__main__":
    unittest.main()
