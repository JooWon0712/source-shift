"""로컬 Codex 로그에서 지정한 생성기의 사용량만 복원한다.

원시 로그, 파일 경로, 응답 ID와 본문은 반환하지 않는다. 이 값은 세션
사용량이며 API 청구액이나 순수 모델 요청 지연시간을 측정하지 않는다.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from typing import Any


TOKEN_FIELDS = (
    "input_tokens",
    "cached_input_tokens",
    "cache_write_input_tokens",
    "output_tokens",
    "reasoning_output_tokens",
    "total_tokens",
)
_SETTING = re.compile(r"[A-Za-z0-9_.-]{1,128}\Z")
_TOOL_NAME = re.compile(r"[A-Za-z0-9_.:-]{1,128}\Z")


def _object(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _usage(value: Any) -> dict[str, int] | None:
    if not isinstance(value, dict):
        return None
    result = {key: value.get(key) for key in TOKEN_FIELDS}
    if any(type(number) is not int or number < 0 for number in result.values()):
        return None
    if result["input_tokens"] + result["output_tokens"] != result["total_tokens"]:
        return None
    if result["cached_input_tokens"] + result["cache_write_input_tokens"] > result["input_tokens"]:
        return None
    if result["reasoning_output_tokens"] > result["output_tokens"]:
        return None
    return result


def _timestamp(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return None
        return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    except (ValueError, OverflowError):
        return None


def _setting(value: Any) -> str | None:
    return value if isinstance(value, str) and _SETTING.fullmatch(value) else None


def _metadata_path(metadata: Any, parent_thread_id: str) -> str | None:
    metadata = _object(metadata)
    if metadata.get("type") != "session_meta":
        return None
    source = _object(_object(metadata.get("payload")).get("source"))
    spawn = _object(_object(source.get("subagent")).get("thread_spawn"))
    if spawn.get("parent_thread_id") != parent_thread_id:
        return None
    agent_path = spawn.get("agent_path")
    return agent_path if isinstance(agent_path, str) else None


def _read_agent(path: Path, parent_thread_id: str, agent_path: str) -> dict:
    """첫 메타데이터가 이미 선택된 파일에서 허용 필드만 추출한다."""
    errors: set[str] = set()
    records: dict[tuple[str, str], tuple[dict[str, int], str | None]] = {}
    final_thread_usage = None
    settings: list[dict] = []
    user_hashes: list[str] = []
    user_part_hashes: list[str] = []
    tools: Counter[str] = Counter()
    starts: list[tuple[Any, str | None]] = []
    completions: list[tuple[Any, str | None]] = []
    digest = hashlib.sha256()
    duplicate_count = 0
    try:
        with path.open("rb") as stream:
            first = stream.readline()
            digest.update(first)
            metadata = _object(json.loads(first))
            if _metadata_path(metadata, parent_thread_id) != agent_path:
                raise ValueError("Selected session metadata changed")
            session_id = _object(metadata.get("payload")).get("id")
            for line in stream:
                digest.update(line)
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except (ValueError, UnicodeError):
                    errors.add("invalid_jsonl")
                    continue
                if not isinstance(row, dict):
                    errors.add("invalid_jsonl")
                    continue
                payload = _object(row.get("payload"))
                kind = row.get("type")
                if kind == "token_usage_record":
                    usage = _usage(payload.get("usage"))
                    thread_usage = _usage(payload.get("thread_token_usage"))
                    # 대조 대상은 마지막 레코드의 누적값이다. 중간 누적값을 합산하지 않는다.
                    final_thread_usage = thread_usage
                    thread_id, response_id = payload.get("thread_id"), payload.get("response_id")
                    if not isinstance(thread_id, str) or not thread_id or not isinstance(response_id, str) or not response_id:
                        errors.add("missing_response_identity")
                        continue
                    if isinstance(session_id, str) and thread_id != session_id:
                        errors.add("thread_identity_mismatch")
                    if usage is None:
                        errors.add("invalid_usage")
                        continue
                    identity = (thread_id, response_id)
                    timestamp = _timestamp(row.get("timestamp"))
                    if identity in records:
                        duplicate_count += 1
                        if records[identity][0] != usage:
                            errors.add("conflicting_duplicate_response")
                    else:
                        records[identity] = (usage, timestamp)
                elif kind == "turn_context":
                    setting = {key: _setting(payload.get(key)) for key in ("model", "effort")}
                    if setting not in settings:
                        settings.append(setting)
                elif kind == "event_msg":
                    event = payload.get("type")
                    if event == "task_started":
                        starts.append((payload.get("turn_id"), _timestamp(row.get("timestamp"))))
                    elif event == "task_complete":
                        completions.append((payload.get("turn_id"), _timestamp(row.get("timestamp"))))
                elif kind == "response_item":
                    content = payload.get("content")
                    if payload.get("type") == "message" and payload.get("role") == "user" and isinstance(content, list):
                        for part in content:
                            part = _object(part)
                            if part.get("type") in ("input_text", "text") and isinstance(part.get("text"), str):
                                user_part_hashes.append(hashlib.sha256(part["text"].encode("utf-8")).hexdigest())
                    if payload.get("type") == "message" and payload.get("role") == "user" and isinstance(content, list) and len(content) == 1:
                        part = _object(content[0])
                        value = part.get("text")
                        if part.get("type") in ("input_text", "text") and isinstance(value, str):
                            user_hashes.append(hashlib.sha256(value.encode("utf-8")).hexdigest())
                    if payload.get("type") in ("function_call", "custom_tool_call"):
                        name = payload.get("name")
                        if isinstance(name, str) and _TOOL_NAME.fullmatch(name):
                            tools[name] += 1
    except (OSError, ValueError, UnicodeError):
        errors.add("unreadable_log")

    totals = None
    if records:
        totals = {field: sum(record[0][field] for record in records.values()) for field in TOKEN_FIELDS}
        if final_thread_usage is None:
            errors.add("invalid_thread_token_usage")
        if totals != final_thread_usage:
            errors.add("thread_usage_mismatch")
    else:
        errors.add("missing_usage")

    elapsed = None
    if len(starts) == len(completions) == 1:
        turn_id, start = starts[0]
        complete_turn_id, end = completions[0]
        if turn_id == complete_turn_id and start is not None and end is not None:
            difference = (datetime.fromisoformat(end.replace("Z", "+00:00")) - datetime.fromisoformat(start.replace("Z", "+00:00"))).total_seconds()
            if difference >= 0:
                elapsed = difference

    def unique_setting(key: str) -> str | None:
        values = {setting[key] for setting in settings}
        return next(iter(values)) if len(values) == 1 else None

    return {
        "status": "unreconciled" if errors else "ok",
        "usage": totals if not errors else None,
        "thread_token_usage": final_thread_usage,
        "responses": [{"timestamp": timestamp, "usage": usage} for usage, timestamp in records.values()],
        "response_count": len(records),
        "duplicate_response_count": duplicate_count,
        "model": unique_setting("model"),
        "effort": unique_setting("effort"),
        "model_settings": settings,
        "agent_turn_elapsed_seconds": elapsed,
        "elapsed_kind": "agent_turn_wall_clock",
        "file_sha256": digest.hexdigest(),
        "user_message_sha256": user_hashes,
        "user_text_part_sha256": user_part_hashes,
        "tool_call_counts": dict(sorted(tools.items())),
        "actual_billed_cost": None,
        "errors": sorted(errors),
    }


def collect_usage(log_directory: Path, parent_thread_id: str, expected_agents: dict[str, str]) -> dict:
    """정확한 부모 ID와 에이전트 경로가 일치하는 생성기만 집계한다.

    각 JSONL의 첫 session_meta만 읽어 대상 여부를 결정한다. 누락·중복
    에이전트와 대조 실패는 작업별 상태로 남기며 전체 합계를 null로 둔다.
    """
    if not isinstance(parent_thread_id, str) or not parent_thread_id or not expected_agents:
        raise ValueError("A parent thread ID and nonempty expected agents are required")
    if any(not isinstance(job, str) or not job or not isinstance(path, str) or not path.startswith("/root/") for job, path in expected_agents.items()):
        raise ValueError("Expected agents must map job IDs to exact /root/ agent paths")
    paths: dict[str, list[Path]] = {agent: [] for agent in expected_agents.values()}
    scan_errors = 0
    try:
        candidates = sorted(Path(log_directory).rglob("*.jsonl"))
    except OSError:
        candidates = []
        scan_errors += 1
    for path in candidates:
        try:
            with path.open("rb") as stream:
                metadata = json.loads(stream.readline())
        except (OSError, ValueError, UnicodeError):
            scan_errors += 1
            continue
        agent = _metadata_path(metadata, parent_thread_id)
        if agent in paths:
            paths[agent].append(path)

    jobs = {}
    expected_counts = Counter(expected_agents.values())
    for job, agent in expected_agents.items():
        matches = paths[agent]
        if len(matches) > 1 or expected_counts[agent] > 1:
            jobs[job] = {"status": "duplicate_agent", "usage": None, "actual_billed_cost": None}
        elif not matches:
            jobs[job] = {"status": "missing", "usage": None, "actual_billed_cost": None}
        else:
            jobs[job] = _read_agent(matches[0], parent_thread_id, agent)
    ok = scan_errors == 0 and all(job["status"] == "ok" for job in jobs.values())
    aggregate = {field: sum(job["usage"][field] for job in jobs.values()) for field in TOKEN_FIELDS} if ok else None
    return {"status": "ok" if ok else "failed", "jobs": jobs, "usage": aggregate, "actual_billed_cost": None, "scan_errors": scan_errors}
