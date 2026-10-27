"""개인 로그 없이 synthetic JSONL로 사용량 복원 계약을 확인한다."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from source_shift.usage import TOKEN_FIELDS, collect_usage


PARENT = "parent-thread"
AGENT = "/root/trial_p01_c"
TEMP_ROOT = Path(__file__).resolve().parents[1] / ".runs" / "test-temp"


def usage(input_tokens=10, output_tokens=3, cached=4, write=1, reasoning=2):
    return dict(zip(TOKEN_FIELDS, (input_tokens, cached, write, output_tokens, reasoning, input_tokens + output_tokens)))


def metadata(parent=PARENT, agent=AGENT):
    return {"type": "session_meta", "payload": {"id": "generator-thread", "cwd": "PRIVATE-PATH", "creator_user_id": "PRIVATE-ACCOUNT", "source": {"subagent": {"thread_spawn": {"parent_thread_id": parent, "agent_path": agent}}}}}


def record(response_id="response-secret", amount=None, total=None):
    amount = usage() if amount is None else amount
    return {"type": "token_usage_record", "timestamp": "2026-10-27T00:00:01Z", "payload": {"thread_id": "generator-thread", "response_id": response_id, "usage": amount, "thread_token_usage": amount if total is None else total}}


def event(kind, timestamp):
    return {"type": "event_msg", "timestamp": timestamp, "payload": {"type": kind, "turn_id": "turn-secret", "last_agent_message": "PRIVATE-TEXT"}}


class UsageTests(unittest.TestCase):
    def setUp(self):
        TEMP_ROOT.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=TEMP_ROOT)
        self.directory = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)

    def write(self, name, rows):
        path = self.directory / name
        path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8", newline="\n")
        return path

    def collect(self):
        return collect_usage(self.directory, PARENT, {"P01-c": AGENT})

    def test_reconciles_usage_and_exposes_only_audit_fields(self):
        prompt = "A synthetic generation request"
        rows = [metadata(), event("task_started", "2026-10-27T00:00:00Z"),
                {"type": "turn_context", "payload": {"model": "example-model", "effort": "high", "cwd": "PRIVATE-PATH", "summary": "PRIVATE-TEXT"}},
                {"type": "response_item", "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": prompt}]}},
                {"type": "response_item", "payload": {"type": "function_call", "name": "functions.apply_patch", "arguments": "PRIVATE-TEXT"}},
                record(), event("task_complete", "2026-10-27T00:00:02.500Z")]
        path = self.write("agent.jsonl", rows)
        result = self.collect()
        job = result["jobs"]["P01-c"]
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["usage"], usage())
        self.assertEqual(job["agent_turn_elapsed_seconds"], 2.5)
        self.assertEqual((job["model"], job["effort"]), ("example-model", "high"))
        self.assertEqual(job["file_sha256"], hashlib.sha256(path.read_bytes()).hexdigest())
        self.assertEqual(job["user_message_sha256"], [hashlib.sha256(prompt.encode()).hexdigest()])
        self.assertEqual(job["tool_call_counts"], {"functions.apply_patch": 1})
        self.assertIsNone(job["actual_billed_cost"])
        self.assertEqual(set(job["responses"][0]), {"timestamp", "usage"})
        serialized = json.dumps(result)
        for private in ("PRIVATE", "response-secret", "generator-thread", "turn-secret", prompt, str(path)):
            self.assertNotIn(private, serialized)

    def test_unrelated_bodies_are_never_parsed_or_hashed(self):
        self.write("agent.jsonl", [metadata(), record()])
        for name, meta in (("other-parent.jsonl", metadata(parent="other")), ("other-agent.jsonl", metadata(agent="/root/another"))):
            path = self.write(name, [meta])
            with path.open("a", encoding="utf-8") as stream:
                stream.write("THIS IS NOT JSON AND MAY CONTAIN PRIVATE BODY DATA\n")
        self.assertEqual(self.collect()["status"], "ok")

    def test_missing_and_duplicate_agents_do_not_become_zero_usage(self):
        result = self.collect()
        self.assertEqual(result["jobs"]["P01-c"]["status"], "missing")
        self.assertIsNone(result["usage"])
        self.write("one.jsonl", [metadata(), record()])
        self.write("two.jsonl", [metadata(), record()])
        result = self.collect()
        self.assertEqual(result["jobs"]["P01-c"]["status"], "duplicate_agent")
        self.assertIsNone(result["jobs"]["P01-c"]["usage"])
        self.assertIsNone(result["usage"])

    def test_response_identity_deduplicates_without_summing_cumulative_usage(self):
        first = record()
        second_usage = usage(input_tokens=20, output_tokens=5)
        total = {key: usage()[key] + second_usage[key] for key in TOKEN_FIELDS}
        self.write("agent.jsonl", [metadata(), first, deepcopy(first), record("second-secret", second_usage, total)])
        result = self.collect()
        job = result["jobs"]["P01-c"]
        self.assertEqual(result["status"], "ok")
        self.assertEqual(job["usage"], total)
        self.assertEqual(job["response_count"], 2)
        self.assertEqual(job["duplicate_response_count"], 1)
        self.assertEqual(len(job["responses"]), 2)
        self.assertIsNone(job["agent_turn_elapsed_seconds"])

    def test_conflicting_duplicate_fails_without_exposing_identity(self):
        self.write("agent.jsonl", [metadata(), record(), record(amount=usage(output_tokens=4))])
        result = self.collect()
        job = result["jobs"]["P01-c"]
        self.assertEqual(job["status"], "unreconciled")
        self.assertIn("conflicting_duplicate_response", job["errors"])
        self.assertIsNone(result["usage"])

    def test_invalid_token_relationships_and_missing_fields_fail(self):
        cases = [{"total_tokens": 99}, {"cached_input_tokens": 10, "cache_write_input_tokens": 1},
                 {"reasoning_output_tokens": 4}, {"input_tokens": True}, {"output_tokens": -1}, {"cached_input_tokens": None}]
        for changes in cases:
            with self.subTest(changes=changes):
                invalid = usage() | changes
                self.write("agent.jsonl", [metadata(), record(amount=invalid)])
                job = self.collect()["jobs"]["P01-c"]
                self.assertEqual(job["status"], "unreconciled")
                self.assertIn("invalid_usage", job["errors"])
                self.assertIsNone(job["usage"])

    def test_last_thread_usage_must_equal_sum_exactly(self):
        self.write("agent.jsonl", [metadata(), record(total=usage(input_tokens=11))])
        job = self.collect()["jobs"]["P01-c"]
        self.assertEqual(job["status"], "unreconciled")
        self.assertIn("thread_usage_mismatch", job["errors"])
        self.write("agent.jsonl", [metadata(), {"type": "token_usage_record", "payload": {}}])
        job = self.collect()["jobs"]["P01-c"]
        self.assertEqual(job["status"], "unreconciled")
        self.assertIn("missing_usage", job["errors"])

    def test_aggregate_keeps_all_jobs_and_requires_every_job_to_reconcile(self):
        self.write("one.jsonl", [metadata(), record()])
        self.write("two.jsonl", [metadata(agent="/root/trial_p01_wat"), record()])
        expected = {"P01-c": AGENT, "P01-wat": "/root/trial_p01_wat"}
        result = collect_usage(self.directory, PARENT, expected)
        self.assertEqual(result["usage"], {field: 2 * usage()[field] for field in TOKEN_FIELDS})
        expected["P02-c"] = "/root/trial_p02_c"
        result = collect_usage(self.directory, PARENT, expected)
        self.assertEqual(len(result["jobs"]), 3)
        self.assertEqual(result["status"], "failed")
        self.assertIsNone(result["usage"])


if __name__ == "__main__":
    unittest.main()
