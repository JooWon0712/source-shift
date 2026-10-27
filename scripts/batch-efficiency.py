"""추가 API 과금 없이 고정된 10과제 묶음의 생성·실행 비용을 비교한다."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import sys

from source_shift.runner import RunConfig, evaluate_body, save_json, tool_versions
from source_shift.tasks import DATASET_VERSION, list_tasks

ROOT = Path(__file__).resolve().parents[1]
TASKS = tuple(task.id for task in list_tasks())


def now():
    return datetime.now(timezone.utc).isoformat()


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8", newline="\n")


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def prompt(route, output, feedback=None, previous=None):
    contract = (
        "C17. Runner supplies #include <stdint.h> and int32_t solve(int32_t a,int32_t b,int32_t c); return only each function body."
        if route == "c" else
        'WAT. Runner supplies (module (func (export "solve") (param $a i32)(param $b i32)(param $c i32)(result i32) BODY)); return only each body. Local declarations may precede instructions.'
    )
    message = (
        "Generate the following 10 pure integer function bodies as one batch. "
        "Do not read files, tests, other agents, environment, browse, or execute code. "
        "Use exactly one apply_patch to write the specified output file as a UTF-8/LF JSON object mapping P01 through P10 to body strings. "
        "No other tools, no Markdown, no explanations in that file. Then final answer only Saved. "
        "No floats, arrays, direct memory access, recursion, external functions or preprocessor directives. "
        "Respect signed i32 and the input domains. Maximum JSON artifact 32768 bytes. "
        + contract + "\nPublic tasks:\n"
        + json.dumps([task.public_view() for task in list_tasks()], ensure_ascii=False, separators=(",", ":"))
    )
    if feedback is not None:
        message += "\nPrevious artifact:\n" + previous + "\nPublic feedback:\n" + json.dumps(feedback, ensure_ascii=False, separators=(",", ":"))
    return message + "\nWrite only: " + str(output)


def prepare(directory, clang):
    directory.mkdir(parents=True, exist_ok=False)
    protocol = {
        "experiment_id": "SS-002-BATCH-001", "kind": "session-batch-pilot",
        "frozen_at": now(), "scope": "10-task batch; pilot only; not unseen evaluation",
        "dataset_version": DATASET_VERSION, "replicates": 3,
        "unit_of_replication": "independent context for one 10-task batch, not individual function",
        "model_selection": "inherit current session; no override", "provider_output_token_cap": None,
        "max_repairs_per_batch": 2, "artifact_byte_cap": 32768,
        "generation_order": ["r1-c", "r1-wat", "r2-wat", "r2-c", "r3-c", "r3-wat"],
        "dispatch": "serial; exact saved prompt sent inline; one file write and final acknowledgement",
        "wall_timeout_seconds": 10.0, "fuel_per_case": 1000000,
        "private_evaluation": "after all six batch generation/repair loops close; all attempts scored",
        "primary_metrics": ["first/final correctness", "all generation/repair output tokens including reasoning", "cache-neutral API-price scenario"],
        "secondary_metrics": ["reported cached/uncached input", "whole generation-agent turn elapsed", "build", "JIT/instance cold", "guest harness warm", "body/full Wasm bytes"],
        "decision": "Report each paired replicate. A practical primary cost advantage requires same final correctness and >=5% median reduction with the same direction in all 3 pairs. Otherwise cost winner is inconclusive. No population-level claim from this pilot.",
        "api_price_scenario": {"actual_billing": False, "model_alias": "gpt-6.1-sol",
                               "as_of": "2026-10-27", "service": "hypothetical Standard short-context API",
                               "source": "https://developers.openai.com/api/docs/pricing",
                               "ordinary_input_per_million_usd": 2, "cached_input_per_million_usd": .1,
                               "output_per_million_usd": 10},
        "local_benchmark": {"selected_replicate": 1, "selection": "preselected first replicate; no best-case selection", "build_rounds": 5, "runtime_rounds": 5, "cycles": 100,
                            "fuel": False, "cranelift": "speed", "parallel_compilation": False,
                            "warm_scope": "guest harness + imported Wasm solve + per-call expected check; no Python bridge per solve",
                            "background_load": "uncontrolled", "no_GPU": True},
        "separate_api_calls": False, "separate_billed_cost": None,
        "tools": tool_versions(clang),
        "implementation_sha256": {path.name: sha256(path.read_bytes()).hexdigest()
                                   for path in sorted((ROOT / "src/source_shift").glob("*.py"))},
        "orchestrator_sha256": sha256(Path(__file__).read_bytes()).hexdigest(),
    }
    manifest = {"protocol": protocol, "jobs": [], "finalized_at": None}
    for key in protocol["generation_order"]:
        repeat, route = key.split("-")
        job_dir = directory / key / "attempt-0"
        job_dir.mkdir(parents=True)
        message = prompt(route, job_dir / "response.json")
        write(job_dir / "prompt.txt", message)
        manifest["jobs"].append({"id": key, "replicate": int(repeat[1:]), "route": route,
                                  "closed": False, "attempts": [{"index": 0, "prompt_sha256": sha256(message.encode()).hexdigest(), "public": None}]})
    save_json(directory / "manifest.json", manifest)
    save_json(directory / "protocol.json", protocol)
    return manifest


def check(directory, job_id, clang):
    manifest = read(directory / "manifest.json")
    if manifest["finalized_at"]:
        raise ValueError("이미 사후 평가를 끝낸 실험입니다.")
    job = next(job for job in manifest["jobs"] if job["id"] == job_id)
    if job["closed"]:
        return job
    attempt = job["attempts"][-1]
    attempt_dir = directory / job_id / f"attempt-{attempt['index']}"
    raw = (attempt_dir / "response.json").read_bytes()
    attempt["response_sha256"] = sha256(raw).hexdigest()
    feedback = {}
    bodies = None
    try:
        if len(raw) > manifest["protocol"]["artifact_byte_cap"]:
            raise ValueError("응답 JSON 바이트 상한 초과")
        bodies = json.loads(raw.decode("utf-8"))
        if not isinstance(bodies, dict) or set(bodies) != set(TASKS) or any(not isinstance(body, str) for body in bodies.values()):
            raise ValueError("P01..P10의 본문 문자열만 담은 JSON object가 필요합니다.")
    except (ValueError, UnicodeError) as exc:
        feedback = {"artifact_error": str(exc)}
    else:
        for task_id in TASKS:
            report, run_dir = evaluate_body(task_id, job["route"], bodies[task_id], attempt_dir / "public-runs", RunConfig(), clang, include_private=False)
            feedback[task_id] = read(run_dir / "public-feedback.json")
            attempt.setdefault("public_runs", {})[task_id] = str(run_dir.relative_to(directory))
    passed = bodies is not None and "artifact_error" not in feedback and all(
        value["conversion"]["status"] == "passed" and value["public"] is not None and value["public"]["status"] == "passed"
        for value in feedback.values())
    attempt["public"] = {"status": "passed" if passed else "failed", "checked_at": now()}
    save_json(attempt_dir / "public-feedback.json", feedback)
    if passed or attempt["index"] == manifest["protocol"]["max_repairs_per_batch"]:
        job["closed"] = True
    else:
        index = attempt["index"] + 1
        next_dir = directory / job_id / f"attempt-{index}"
        message = prompt(job["route"], next_dir / "response.json", feedback, raw.decode("utf-8", errors="replace"))
        write(next_dir / "prompt.txt", message)
        job["attempts"].append({"index": index, "prompt_sha256": sha256(message.encode()).hexdigest(), "public": None})
    save_json(directory / "manifest.json", manifest)
    return job


def finalize(directory, clang, export):
    manifest = read(directory / "manifest.json")
    if not all(job["closed"] for job in manifest["jobs"]):
        raise ValueError("전체 공개 생성·수정 종료 전 비공개 평가 금지")
    if manifest["finalized_at"]:
        raise ValueError("기존 사후 평가를 중복 실행하지 않습니다.")
    export.mkdir(parents=True, exist_ok=False)
    rows = []
    for job in manifest["jobs"]:
        attempts = []
        for attempt in job["attempts"]:
            attempt_dir = directory / job["id"] / f"attempt-{attempt['index']}"
            raw = (attempt_dir / "response.json").read_bytes()
            if sha256(raw).hexdigest() != attempt["response_sha256"]:
                raise ValueError("공개 검사 뒤 응답이 변경됨")
            destination = export / "sources" / job["id"] / f"attempt-{attempt['index']}"
            destination.mkdir(parents=True)
            # 유효하지 않은 응답도 원래 바이트 그대로 보존한다.
            (destination / "response.json").write_bytes(raw)
            write(destination / "prompt.txt", (attempt_dir / "prompt.txt").read_text(encoding="utf-8").replace(str(ROOT), "<workspace>"))
            reports = {}
            if "public_runs" in attempt:
                bodies = json.loads(raw.decode("utf-8"))
                for task_id in TASKS:
                    report, run_dir = evaluate_body(task_id, job["route"], bodies[task_id], directory / "posthoc-runs", RunConfig(), clang)
                    public_report = read(directory / attempt["public_runs"][task_id] / "report.json")
                    reports[task_id] = {
                        "status": report["status"], "conversion_status": report["conversion"]["status"],
                        "public": ({"status": report["public"]["status"], "passed": report["public"].get("passed"), "total": report["public"].get("total")} if report["public"] else None),
                        "private": ({"status": report["private"]["status"], "passed": report["private"].get("passed"), "total": report["private"].get("total")} if report["private"] else None),
                        "sizes": report["sizes"], "public_check_wall_seconds": public_report["total_wall_seconds"],
                        "posthoc_wall_seconds": report["total_wall_seconds"], "dataset_sha256": report["dataset_sha256"],
                        "body_sha256": report["body_sha256"], "raw_report": str(run_dir.relative_to(ROOT)),
                    }
            attempts.append({"index": attempt["index"], "response_sha256": attempt["response_sha256"], "reports": reports,
                             "public_status": attempt["public"]["status"], "artifact_file": (destination / "response.json").relative_to(export).as_posix()})
        rows.append({"job": job["id"], "replicate": job["replicate"], "route": job["route"], "repairs": len(attempts) - 1, "attempts": attempts})
    manifest["finalized_at"] = now()
    save_json(directory / "manifest.json", manifest)
    save_json(export / "evaluation.json", {"protocol": manifest["protocol"], "finalized_at": manifest["finalized_at"], "rows": rows})
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("prepare", "next", "check", "finalize"))
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--job")
    parser.add_argument("--export", type=Path)
    parser.add_argument("--clang", default=str(ROOT / ".tools/llvm-23.1.4/bin/clang.exe"))
    args = parser.parse_args()
    directory = args.directory.resolve()
    if args.action == "prepare":
        manifest = prepare(directory, args.clang)
        print(json.dumps({"jobs": len(manifest["jobs"]), "protocol": str(directory / "protocol.json")}))
    elif args.action == "next":
        manifest = read(directory / "manifest.json")
        job = next((job for job in manifest["jobs"] if not job["closed"]), None)
        if job:
            attempt = job["attempts"][-1]
            path = directory / job["id"] / f"attempt-{attempt['index']}" / "prompt.txt"
            print(json.dumps({"job": job["id"], "attempt": attempt["index"], "prompt": path.read_text(encoding="utf-8")}, ensure_ascii=False))
        else:
            print(json.dumps({"done": True}))
    elif args.action == "check":
        if args.job is None:
            parser.error("--job가 필요합니다.")
        job = check(directory, args.job, args.clang)
        print(json.dumps({"job": job["id"], "closed": job["closed"], "attempts": len(job["attempts"]), "latest_public": job["attempts"][-1]["public"]}))
    else:
        if args.export is None:
            parser.error("--export가 필요합니다.")
        rows = finalize(directory, args.clang, args.export.resolve())
        print(json.dumps({"jobs": len(rows), "passed_functions": sum(sum(report["status"] == "passed" for report in row["attempts"][-1]["reports"].values()) for row in rows)}))


if __name__ == "__main__":
    main()
