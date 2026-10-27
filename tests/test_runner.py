"""실제 Wasmtime으로 정상·실패·채점 정보 분리를 검증한다."""

from importlib.util import find_spec
import json
import os
from pathlib import Path
import tempfile
import unittest

from source_shift.runner import RunConfig, evaluate_body, find_clang, run_worker
from source_shift.tasks import get_task
from source_shift.templates import wrap_body

HAS_WASMTIME = find_spec("wasmtime") is not None
CLANG = os.environ.get("SOURCE_SHIFT_CLANG") or find_clang()
SUM_BODY = "local.get $a\nlocal.get $b\ni32.add\nlocal.get $c\ni32.add"


class ConfigurationTests(unittest.TestCase):
    def test_rejects_unusable_limits(self):
        for timeout in (0, -1, float("inf"), float("nan")):
            with self.subTest(timeout=timeout), self.assertRaises(ValueError):
                RunConfig(wall_timeout_seconds=timeout)
        for fuel in (0, -1, 2**64):
            with self.subTest(fuel=fuel), self.assertRaises(ValueError):
                RunConfig(fuel_per_case=fuel)

    def test_unknown_route_rejected_before_artifact_creation(self):
        with self.assertRaises(ValueError):
            wrap_body("raw-bytes", "00")

    def test_missing_clang_is_a_recorded_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            report, directory = evaluate_body("P01", "c", "return a+b+c;", Path(temp), clang="missing-source-shift-compiler")
            self.assertEqual(report["status"], "tool_unavailable")
            self.assertIsNone(report["private"])
            self.assertIsNone(report["sizes"]["wasm_bytes"])
            self.assertTrue((directory / "body.txt").exists())
            self.assertTrue((directory / "report.json").exists())


@unittest.skipUnless(HAS_WASMTIME, "wasmtime이 설치되지 않았습니다.")
class WasmtimeIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def run_body(self, body, config=None):
        return evaluate_body("P01", "wat", body, self.root, config)

    def test_sum_passes_and_records_reproducible_artifacts(self):
        report, directory = self.run_body(SUM_BODY)
        self.assertEqual(report["status"], "passed")
        self.assertEqual(report["private"]["passed"], len(get_task("P01").private_cases))
        self.assertEqual((directory / "body.txt").read_text(encoding="utf-8"), SUM_BODY)
        self.assertEqual(report["kind"], "manual-smoke")
        for key in ("model", "usage", "cost", "model_request_seconds", "runtime_benchmark"):
            self.assertIsNone(report[key])
        self.assertTrue((directory / "module.wasm").read_bytes().startswith(b"\x00asm"))
        again, another_directory = self.run_body(SUM_BODY)
        self.assertEqual(report["dataset_sha256"], again["dataset_sha256"])
        self.assertNotEqual(directory, another_directory)

    def test_public_feedback_never_contains_private_scores(self):
        # 공개 예제만 맞추는 본문: 공개 성공 후 비공개 실패가 모델 피드백에 새지 않아야 한다.
        body = "local.get $a\ni32.const 1\ni32.eq\nif (result i32)\ni32.const 6\nelse\ni32.const 0\nend"
        report, directory = self.run_body(body)
        self.assertEqual(report["public"]["status"], "passed")
        self.assertEqual(report["private"]["status"], "failed")
        feedback = json.loads((directory / "public-feedback.json").read_text(encoding="utf-8"))
        self.assertEqual(set(feedback), {"task_id", "route", "conversion", "public"})
        self.assertNotIn("private", json.dumps(feedback))
        self.assertEqual(len(feedback["public"]["cases"]), 3)

    def test_public_only_run_does_not_evaluate_or_snapshot_private_inputs(self):
        report, directory = evaluate_body("P01", "wat", SUM_BODY, self.root, include_private=False)
        self.assertEqual(report["status"], "passed")
        self.assertEqual(report["evaluation_scope"], "public-only")
        self.assertIsNone(report["private"])
        dataset = json.loads((directory / "dataset.json").read_text(encoding="utf-8"))
        self.assertEqual(set(dataset), {"public"})
        self.assertFalse((directory / "private-request.json").exists())

    def test_wrong_answer_and_conversion_failure_are_distinct(self):
        report, _ = self.run_body("i32.const 0")
        self.assertEqual(report["conversion"]["status"], "passed")
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["public"]["cases"][0]["status"], "wrong_answer")
        broken, directory = self.run_body("i32.not_an_instruction")
        self.assertEqual(broken["status"], "conversion_error")
        self.assertIsNone(broken["public"])
        self.assertIsNone(broken["private"])
        self.assertTrue((directory / "public-feedback.json").exists())

    def test_template_breakout_is_a_preserved_failed_attempt(self):
        body = SUM_BODY + ") (memory 1) (func"
        report, directory = self.run_body(body)
        self.assertEqual(report["status"], "body_contract_error")
        self.assertEqual((directory / "body.txt").read_bytes(), body.encode("utf-8"))
        self.assertIsNone(report["private"])

    def test_body_bytes_and_engine_settings_are_preserved(self):
        body = SUM_BODY.replace("\n", "\r\n")
        report, directory = self.run_body(body)
        self.assertEqual(report["status"], "passed")
        self.assertEqual((directory / "body.txt").read_bytes(), body.encode("utf-8"))
        self.assertEqual(report["config"]["engine"]["cranelift_opt_level"], "speed")
        self.assertEqual(report["config"]["store_limits"]["memory_size"], 2097152)

    def test_invalid_stack_is_validation_failure(self):
        report, _ = self.run_body("local.get $a\nlocal.get $b")
        self.assertEqual(report["conversion"]["status"], "passed")
        self.assertEqual(report["public"]["status"], "validation_error")
        self.assertEqual(report["private"]["status"], "validation_error")

    def test_trap_and_fuel_exhaustion_are_distinct(self):
        trap, _ = self.run_body("unreachable")
        self.assertEqual(trap["public"]["cases"][0]["status"], "trap")
        loop, _ = self.run_body("(loop $forever br $forever) unreachable", RunConfig(fuel_per_case=1000))
        self.assertEqual(loop["public"]["cases"][0]["status"], "fuel_exhausted")
        self.assertEqual(loop["private"]["passed"], 0)

    def test_wall_timeout_is_recorded_without_a_success_score(self):
        report, directory = self.run_body(SUM_BODY, RunConfig(wall_timeout_seconds=0.001))
        self.assertEqual(report["status"], "wall_timeout")
        self.assertIsNone(report["public"])
        self.assertIsNone(report["private"])
        self.assertTrue((directory / "report.json").exists())

    def test_imports_and_wrong_abi_are_rejected(self):
        import wasmtime
        for text in (
            '(module (import "env" "foo" (func)) (func (export "solve") (param i32 i32 i32) (result i32) i32.const 0))',
            '(module (func (export "solve") (param i64) (result i32) i32.const 0))',
        ):
            wasm = self.root / "contract.wasm"
            wasm.write_bytes(wasmtime.wat2wasm(text))
            result = run_worker("evaluate", {"wasm": str(wasm), "cases": [{"inputs": [0, 0, 0], "expected": 0}],
                                            "fuel_per_case": 1000}, self.root, "contract", 10)
            self.assertEqual(result["status"], "contract_error")
            self.assertEqual(result["cases"], [])


@unittest.skipUnless(CLANG and HAS_WASMTIME, "C 통합 검증은 Clang/Wasmtime이 필요합니다.")
class ClangIntegrationTests(unittest.TestCase):
    def test_c_sum_and_compile_error(self):
        with tempfile.TemporaryDirectory() as temp:
            report, _ = evaluate_body("P01", "c", "return a+b+c;", Path(temp), clang=CLANG)
            self.assertEqual(report["status"], "passed", report["conversion"])
            self.assertIn("-O2", report["conversion"]["command"])
            broken, _ = evaluate_body("P01", "c", "return @;", Path(temp), clang=CLANG)
            self.assertEqual(broken["status"], "conversion_error")
            self.assertIsNone(broken["private"])
