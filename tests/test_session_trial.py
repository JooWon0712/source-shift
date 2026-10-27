"""공개 검사 종료 전 비공개 평가를 차단하는 세션 절차 검증."""

from pathlib import Path
import runpy
import tempfile
import unittest

from source_shift.runner import save_json
from source_shift.tasks import get_task

trial = runpy.run_path(str(Path(__file__).resolve().parents[1] / "scripts/session-trial.py"))


class SessionTrialTests(unittest.TestCase):
    def test_generation_prompt_contains_only_public_task_data(self):
        task = get_task("P08")
        prompt = trial["prompt_for"](task, "wat", Path("response.txt"))
        self.assertIn(task.specification, prompt)
        self.assertIn('"public_cases"', prompt)
        self.assertNotIn("private_cases", prompt)
        self.assertNotIn("6765", prompt)

    def test_missing_responses_prevent_private_finalization(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            save_json(directory / "manifest.json", {"jobs": [{"closed": False}], "finalized_at": None})
            export = directory / "export"
            with self.assertRaisesRegex(ValueError, "공개 생성"):
                trial["finalize"](directory, None, export)
            self.assertFalse(export.exists())
