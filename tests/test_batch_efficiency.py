"""묶음 생성의 프롬프트·종료 전 비공개 평가 차단을 검증한다."""

from pathlib import Path
import runpy
import tempfile
import unittest

from source_shift.runner import save_json

study = runpy.run_path(str(Path(__file__).resolve().parents[1] / "scripts/batch-efficiency.py"))


class BatchEfficiencyTests(unittest.TestCase):
    def test_both_routes_share_the_public_problem_set_and_no_private_cases(self):
        for route in ("c", "wat"):
            message = study["prompt"](route, Path("response.json"))
            for number in range(1, 11):
                self.assertIn(f'"id":"P{number:02d}"', message)
            self.assertNotIn("private_cases", message)
            self.assertIn("exactly one apply_patch", message)

    def test_no_private_scoring_until_every_public_loop_has_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            save_json(directory / "manifest.json", {"jobs": [{"closed": False}], "finalized_at": None})
            with self.assertRaisesRegex(ValueError, "전체 공개"):
                study["finalize"](directory, "clang", directory / "export")
            self.assertFalse((directory / "export").exists())
