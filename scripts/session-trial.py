"""독립 세션 에이전트가 파일로 전달한 본문을 공개 검사하고 사후 평가한다.

생성기는 이 스크립트 밖에서 실행한다. 모델 ID/usage/요청 시간은 추정하지 않는다.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path

from source_shift.runner import RunConfig, evaluate_body, save_json, tool_versions
from source_shift.tasks import DATASET_VERSION, get_task, list_tasks
from source_shift.templates import C_FLAGS

ROOT = Path(__file__).resolve().parents[1]
LIMITS = RunConfig()


def now():
    return datetime.now(timezone.utc).isoformat()


def write_text(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def prompt_for(task, route, response, feedback=None, previous=None):
    interface = (
        "The runner supplies #include <stdint.h> and int32_t solve(int32_t a, int32_t b, int32_t c). "
        "Return only the C function body, with no signature, includes, or Markdown. Use C17 and int32_t."
        if route == "c" else
        'The runner supplies (module (func (export "solve") (param $a i32) (param $b i32) (param $c i32) (result i32) BODY)). '
        "Return only WAT function body instructions. Local declarations are allowed at the beginning. No module wrapper or Markdown."
    )
    prompt = (
        "You are generating one source-shift pilot artifact in an independent context. "
        "Use the inherited current cloud model; do not select another model. "
        "Do not read repository files, tests, other agent outputs, environment, or external resources. "
        "Do not run code or tools to inspect anything. The only permitted tool use is apply_patch to write the response file named below. "
        "Solve using only the public specification and examples in this message. "
        "No floating point, arrays, direct memory access, recursion, external functions, file/network access, or preprocessor directives. "
        "All arithmetic must respect signed i32 within the documented input domain. "
        "Maximum response artifact size is 16384 UTF-8 bytes. No extra explanation in the artifact.\n\n"
        + interface + "\n\nPublic task:\n"
        + json.dumps(task.public_view(), ensure_ascii=False, indent=2)
    )
    if feedback is not None:
        prompt += "\n\nPrevious body:\n" + previous + "\n\nPublic feedback only:\n" + json.dumps(feedback, ensure_ascii=False, indent=2)
    prompt += "\n\nWrite the exact generated body to this new UTF-8/LF file using apply_patch:\n" + str(response)
    prompt += "\nDo not modify any other file. In the final answer say only that the artifact was saved."
    return prompt


def prepare(directory, clang):
    directory.mkdir(parents=True, exist_ok=False)
    manifest = {
        "schema_version": 1, "kind": "session-integration", "created_at": now(),
        "dataset_version": DATASET_VERSION, "model_identifier": None,
        "model_selection": "inherited-current-session-model; no override",
        "usage": None, "cost": None, "model_request_seconds": None,
        "max_repair_calls": 2, "provider_output_token_limit": None,
        "response_artifact_byte_limit": 16384, "tools": tool_versions(clang),
        "clang_flags": list(C_FLAGS), "jobs": [], "finalized_at": None,
    }
    for index, task in enumerate(list_tasks()):
        for route in (("c", "wat") if index % 2 == 0 else ("wat", "c")):
            key = f"{task.id.lower()}-{route}"
            attempt_dir = directory / key / "attempt-0"
            attempt_dir.mkdir(parents=True)
            response = attempt_dir / "response.txt"
            prompt = prompt_for(task, route, response)
            write_text(attempt_dir / "prompt.txt", prompt)
            manifest["jobs"].append({"id": key, "task_id": task.id, "route": route,
                                      "closed": False, "attempts": [{"index": 0,
                                      "prompt_sha256": sha256(prompt.encode()).hexdigest(),
                                      "public_run": None, "posthoc_run": None}]})
    save_json(directory / "manifest.json", manifest)
    return manifest


def load(directory):
    return json.loads((directory / "manifest.json").read_text(encoding="utf-8"))


def dispatch_message(attempt_dir, job_id, index):
    # 이번 세션의 실제 전달 방식도 보존한다. 최초 3개는 본문을 직접 전달했고
    # 이후에는 이 파일 하나만 읽도록 허용한 뒤 같은 과제 프롬프트를 사용했다.
    if index == 0 and job_id in ("p01-c", "p01-wat", "p02-wat"):
        return (attempt_dir / "prompt.txt").read_text(encoding="utf-8")
    return (
        "Read only " + str(attempt_dir / "prompt.txt")
        + " as your one allowed initial read. Then follow its exact generation instructions. "
        "Its no-reading restrictions apply after this one prompt read. "
        "Do not inspect any other file, test, environment, or other outputs. "
        "Use only apply_patch afterward to create its specified response file. "
        "Do not select another model. Final answer only confirms the artifact was saved."
    )


def check(directory, clang):
    manifest = load(directory)
    if manifest["finalized_at"]:
        raise ValueError("이미 사후 평가가 끝난 세션입니다. 새 세션을 준비하세요.")
    for job in manifest["jobs"]:
        if job["closed"]:
            continue
        attempt = job["attempts"][-1]
        attempt_dir = directory / job["id"] / f"attempt-{attempt['index']}"
        response = attempt_dir / "response.txt"
        if not response.is_file():
            continue
        raw = response.read_bytes()
        if len(raw) > manifest["response_artifact_byte_limit"]:
            raise ValueError(f"{job['id']} 응답이 사전에 정한 바이트 상한을 넘었습니다. 원본은 보존합니다.")
        body = raw.decode("utf-8")
        report, run_dir = evaluate_body(job["task_id"], job["route"], body,
                                        directory / "public-runs", LIMITS, clang, include_private=False)
        attempt.update(response_sha256=sha256(raw).hexdigest(),
                       public_run=str(run_dir.relative_to(directory)),
                       public_status=report["status"], public_checked_at=now())
        if report["status"] == "passed" or attempt["index"] == manifest["max_repair_calls"]:
            job["closed"] = True
        else:
            next_index = attempt["index"] + 1
            next_dir = directory / job["id"] / f"attempt-{next_index}"
            next_dir.mkdir()
            feedback = json.loads((run_dir / "public-feedback.json").read_text(encoding="utf-8"))
            prompt = prompt_for(get_task(job["task_id"]), job["route"], next_dir / "response.txt", feedback, body)
            write_text(next_dir / "prompt.txt", prompt)
            job["attempts"].append({"index": next_index, "prompt_sha256": sha256(prompt.encode()).hexdigest(),
                                    "public_run": None, "posthoc_run": None})
        save_json(directory / "manifest.json", manifest)
        print(json.dumps({"job": job["id"], "attempt": attempt["index"],
                          "public_status": report["status"], "closed": job["closed"]}))
    return manifest


def summarize_suite(suite):
    if suite is None:
        return None
    failures = {}
    for case in suite.get("cases", []):
        if case["status"] != "passed":
            failures[case["status"]] = failures.get(case["status"], 0) + 1
    return {key: suite.get(key) for key in ("status", "passed", "total", "wall_seconds", "validation_seconds", "test_seconds")} | {"failure_types": failures}


def finalize(directory, clang, export):
    manifest = load(directory)
    if not all(job["closed"] for job in manifest["jobs"]):
        raise ValueError("모든 공개 생성·수정 과정이 끝난 뒤에만 비공개 채점할 수 있습니다.")
    if manifest["finalized_at"]:
        raise ValueError("사후 평가를 중복 실행하지 않습니다. 기존 결과를 확인하세요.")
    export.mkdir(parents=True, exist_ok=False)
    rows = []
    for job in manifest["jobs"]:
        attempts = []
        for attempt in job["attempts"]:
            attempt_dir = directory / job["id"] / f"attempt-{attempt['index']}"
            raw = (attempt_dir / "response.txt").read_bytes()
            if sha256(raw).hexdigest() != attempt["response_sha256"]:
                raise ValueError("공개 검사 후 본문이 바뀌었습니다. 원본을 복원하고 새 버전으로 기록하세요.")
            report, run_dir = evaluate_body(job["task_id"], job["route"], raw.decode("utf-8"),
                                            directory / "posthoc-runs", LIMITS, clang)
            attempt["posthoc_run"] = str(run_dir.relative_to(directory))
            public_report = json.loads((directory / attempt["public_run"] / "report.json").read_text(encoding="utf-8"))
            source = Path("sources") / job["id"] / f"attempt-{attempt['index']}.{job['route']}"
            write_text(export / source, raw.decode("utf-8"))
            prompt = (attempt_dir / "prompt.txt").read_text(encoding="utf-8")
            prompt = prompt.replace(str(ROOT), "<workspace>")
            prompt_path = source.with_suffix(".prompt.txt")
            write_text(export / prompt_path, prompt)
            dispatch = dispatch_message(attempt_dir, job["id"], attempt["index"])
            write_text(attempt_dir / "dispatch.txt", dispatch)
            dispatch_path = source.with_suffix(".dispatch.txt")
            write_text(export / dispatch_path, dispatch.replace(str(ROOT), "<workspace>"))
            attempts.append({"index": attempt["index"], "body_file": source.as_posix(),
                             "prompt_file": prompt_path.as_posix(),
                             "dispatch_file": dispatch_path.as_posix(),
                             "generator_context": "/root/trial_" + job["id"].replace("-", "_"),
                             "response_sha256": attempt["response_sha256"],
                             "public_status": public_report["status"], "final_status": report["status"],
                             "conversion_status": report["conversion"]["status"],
                             "public": summarize_suite(report["public"]), "private": summarize_suite(report["private"]),
                             "sizes": report["sizes"],
                             "public_check_wall_seconds": public_report["total_wall_seconds"],
                             "posthoc_wall_seconds": report["total_wall_seconds"],
                             "dataset_sha256": report["dataset_sha256"],
                             "implementation_sha256": report["implementation_sha256"]})
            save_json(directory / "manifest.json", manifest)
        rows.append({"task_id": job["task_id"], "route": job["route"],
                     "repairs": len(attempts) - 1, "attempts": attempts})
    result = {key: value for key, value in manifest.items() if key != "jobs"}
    result["finalized_at"] = now()
    result["config"] = report["config"]
    result["orchestrator_sha256"] = sha256(Path(__file__).read_bytes()).hexdigest()
    result["rows"] = rows
    result["limitations"] = [
        "세션 에이전트의 공개 응답 파일을 평가했다. API 원 응답/정확한 모델 ID/seed/출력 토큰 상한/usage/요청 지연은 관측하지 못했다.",
        "생성기는 문맥을 새로 시작하며 공개 명세와 예제만 전달받았다. 각 과제·경로 1회이고 공개 실패 시 최대 2회 수정한다.",
        "초기 공개 검사와 생성·수정 종료 후 사후 평가에서 각각 컴파일한다. 두 단계 시간은 별도로 기록한다.",
        "시간은 Python 시작/JIT/기록을 포함한 개발 검증 시간이며 실행 성능 벤치마크나 모델 생성 속도 비교가 아니다.",
        "파일 크기는 관측했지만 토큰 수나 비용으로 환산하지 않았다. 본 실행으로 경로의 일반적 우월성을 판단하지 않는다.",
        "원본 prompt는 로컬에 보존하고, 공개 복사본의 workspace 절대 경로만 <workspace>로 치환했다.",
        "최초 3개는 과제 프롬프트를 직접 전달했고 이후에는 지정한 과제 프롬프트 파일만 읽도록 했다. 추가 전달 지시문도 함께 보존했다.",
        "dispatch.txt는 실제 발송 지시문과 대조해 재구성한 기록이며 공급자 원 응답이나 전체 system/developer 문맥은 아니다.",
        "이번 20개 응답은 UTF-8/바이트 상한을 지켰다. 현재 보조 스크립트는 응답 인코딩·크기 위반 시 원본을 보존하고 중단하므로 해당 실패의 자동 집계는 후속 보완 사항이다.",
    ]
    save_json(export / "summary.json", result)
    manifest["finalized_at"] = result["finalized_at"]
    save_json(directory / "manifest.json", manifest)
    local_date = datetime.fromisoformat(manifest["created_at"]).astimezone(timezone(timedelta(hours=9))).date().isoformat()
    lines = ["# 세션 기반 파일럿 실행·검증", "", f"실행일: {local_date} (Asia/Seoul). 종류: `session-integration`.", "",
             "현재 세션의 모델 설정을 상속한 독립 에이전트가 과제 10개를 C/WAT로 생성했다. 공개 실패만 수정에 사용하고 모든 생성·수정이 끝난 뒤 각 시도를 비공개 입력으로 사후 채점했다.", "",
             "| 과제 | C 최초/최종 | WAT 최초/최종 | C/WAT 수정 횟수 | 비공개 입력 수 |", "| --- | --- | --- | --- | --- |"]
    for task in list_tasks():
        paired = {row["route"]: row for row in rows if row["task_id"] == task.id}
        def status(route):
            attempts = paired[route]["attempts"]
            return f"{attempts[0]['final_status']} / {attempts[-1]['final_status']}"
        lines.append(f"| {task.id} {task.title} | {status('c')} | {status('wat')} | {paired['c']['repairs']} / {paired['wat']['repairs']} | {len(task.private_cases)} |")
    lines += ["", "각 경로의 공개 입력은 30개, 비공개 입력은 684개다. 상세 판정·크기·단계별 시간·모든 시도의 본문과 공개 프롬프트는 [summary.json](summary.json)에 연결했다.", "",
              "## 해석 범위", ""] + ["- " + item for item in result["limitations"]]
    write_text(export / "report.md", "\n".join(lines) + "\n")
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("prepare", "check", "finalize"))
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--clang", default=str(ROOT / ".tools/llvm-23.1.4/bin/clang.exe"))
    parser.add_argument("--export", type=Path)
    args = parser.parse_args()
    directory = args.directory.resolve()
    if args.action == "prepare":
        manifest = prepare(directory, args.clang)
    elif args.action == "check":
        manifest = check(directory, args.clang)
    else:
        if args.export is None:
            parser.error("finalize에는 --export가 필요합니다.")
        finalize(directory, args.clang, args.export.resolve())
        manifest = load(directory)
    print(json.dumps({"closed": sum(job["closed"] for job in manifest["jobs"]),
                      "total": len(manifest["jobs"]), "finalized_at": manifest["finalized_at"]}))


if __name__ == "__main__":
    main()
