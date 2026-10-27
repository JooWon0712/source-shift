from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .runner import RunConfig, evaluate_body, tool_versions
from .tasks import list_tasks


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="source-shift", description="모델 호출 없이 C/WAT 본문을 검증합니다.")
    commands = parser.add_subparsers(dest="command", required=True)
    doctor = commands.add_parser("doctor", help="Python/Wasmtime/Clang/LLD 버전 확인")
    doctor.add_argument("--clang")
    commands.add_parser("tasks", help="파일럿의 공개 명세와 예제를 JSON으로 표시")
    run = commands.add_parser("evaluate", help="직접 작성한 본문 한 개를 변환·채점·기록")
    run.add_argument("--task", required=True, choices=[task.id for task in list_tasks()])
    run.add_argument("--route", required=True, choices=["c", "wat"])
    run.add_argument("--body", type=Path, required=True)
    run.add_argument("--output", type=Path, default=Path(".runs"))
    run.add_argument("--clang")
    run.add_argument("--wall-timeout", type=float, default=10.0)
    run.add_argument("--fuel", type=int, default=1_000_000)
    args = parser.parse_args(argv)
    if args.command == "doctor":
        versions = tool_versions(args.clang)
        print(json.dumps(versions, ensure_ascii=False, indent=2))
        return 0 if all(versions[key] for key in ("wasmtime", "clang", "wasm_ld")) else 1
    if args.command == "tasks":
        print(json.dumps([task.public_view() for task in list_tasks()], ensure_ascii=False, indent=2))
        return 0
    try:
        config = RunConfig(args.wall_timeout, args.fuel)
        body = args.body.read_bytes().decode("utf-8")
        report, directory = evaluate_body(args.task, args.route, body, args.output, config, args.clang)
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(json.dumps({"status": report["status"], "report": str(directory / "report.json")}, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "passed" else 1
