"""수동 본문 한 개를 변환하고 공개/비공개 입력으로 사후 채점한다."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from hashlib import sha256
from importlib import metadata
import json
import math
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
from time import perf_counter
from uuid import uuid4

from . import __version__
from .body_contract import validate_body
from .processes import run_process
from .runtime_config import ENGINE_CONFIG, STORE_LIMITS
from .tasks import DATASET_VERSION, get_task
from .templates import C_FLAGS, TEMPLATE_VERSION, wrap_body


@dataclass(frozen=True)
class RunConfig:
    # 개발용 기본값이며, 모델 비교 실험 전에 적합성을 검토·고정해야 한다.
    wall_timeout_seconds: float = 10.0
    fuel_per_case: int = 1_000_000

    def __post_init__(self) -> None:
        if not math.isfinite(self.wall_timeout_seconds) or self.wall_timeout_seconds <= 0:
            raise ValueError("wall timeout은 유한한 양수여야 합니다.")
        if not 0 < self.fuel_per_case < 2**64:
            raise ValueError("fuel은 1 이상 2**64 미만이어야 합니다.")


def save_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def find_clang(explicit: str | None = None) -> str | None:
    if explicit:
        found = shutil.which(explicit)
        return str(Path(found).resolve()) if found else (str(Path(explicit).resolve()) if Path(explicit).is_file() else None)
    found = shutil.which("clang")
    return str(Path(found).resolve()) if found else None


def tool_versions(clang: str | None = None) -> dict:
    try:
        wasmtime_version = metadata.version("wasmtime")
    except metadata.PackageNotFoundError:
        wasmtime_version = None
    executable = find_clang(clang)
    compiler = None
    linker = None
    for name, command in (("clang", executable), ("linker", str(Path(executable).with_name("wasm-ld.exe" if os.name == "nt" else "wasm-ld")) if executable else None)):
        if command:
            try:
                result = run_process([command, "--version"], timeout=5)
                version = result.stdout.decode("utf-8", errors="replace").splitlines()[0] if result.returncode == 0 and result.stdout else None
                if name == "clang":
                    compiler = version
                else:
                    linker = version
            except (OSError, subprocess.TimeoutExpired):
                pass
    return {"python": platform.python_version(), "platform": platform.platform(),
            "wasmtime": wasmtime_version, "clang": compiler, "wasm_ld": linker}


def run_worker(mode: str, request: dict, directory: Path, name: str, timeout: float) -> dict:
    request_path = directory / f"{name}-request.json"
    result_path = directory / f"{name}-result.json"
    save_json(request_path, request)
    env = os.environ.copy()
    package_root = str(Path(__file__).resolve().parents[1])
    env["PYTHONPATH"] = package_root + os.pathsep + env.get("PYTHONPATH", "")
    started = perf_counter()
    try:
        process = run_process(
            [sys.executable, "-m", "source_shift._worker", mode, str(request_path), str(result_path)],
            cwd=directory, env=env, timeout=timeout,
        )
        (directory / f"{name}-stderr.txt").write_bytes(process.stderr)
        if process.returncode != 0 or not result_path.exists():
            result = {"status": "worker_error", "error": f"worker exit code: {process.returncode}"}
        else:
            result = json.loads(result_path.read_text(encoding="utf-8"))
    except subprocess.TimeoutExpired as exc:
        result = json.loads(result_path.read_text(encoding="utf-8")) if result_path.exists() else {}
        result.update(status="wall_timeout", error=f"stage exceeded {timeout} seconds")
        (directory / f"{name}-stderr.txt").write_bytes(exc.stderr or b"")
    except OSError as exc:
        result = {"status": "worker_error", "error": str(exc)}
    result["wall_seconds"] = perf_counter() - started
    save_json(result_path, result)
    return result


def compile_c(executable: str | None, directory: Path, timeout: float) -> dict:
    if executable is None:
        return {"status": "tool_unavailable", "error": "Clang이 없습니다. --clang으로 경로를 지정하세요.", "wall_seconds": None, "command": None}
    command = [executable, *C_FLAGS, "source.c", "-o", "module.wasm"]
    started = perf_counter()
    try:
        process = run_process(command, cwd=directory, timeout=timeout)
        (directory / "conversion-stdout.txt").write_bytes(process.stdout)
        (directory / "conversion-stderr.txt").write_bytes(process.stderr)
        result = {"status": "passed" if process.returncode == 0 else "conversion_error",
                  "error": process.stderr.decode("utf-8", errors="replace") if process.returncode else None,
                  "exit_code": process.returncode}
    except subprocess.TimeoutExpired as exc:
        (directory / "conversion-stdout.txt").write_bytes(exc.stdout or b"")
        (directory / "conversion-stderr.txt").write_bytes(exc.stderr or b"")
        result = {"status": "wall_timeout", "error": f"stage exceeded {timeout} seconds"}
    except OSError as exc:
        result = {"status": "tool_unavailable", "error": str(exc)}
    return {**result, "wall_seconds": perf_counter() - started, "command": command}


def evaluate_body(task_id: str, route: str, body: str, output_root: Path,
                  config: RunConfig | None = None, clang: str | None = None,
                  *, include_private: bool = True) -> tuple[dict, Path]:
    config = config or RunConfig()
    task = get_task(task_id)
    source = wrap_body(route, body)
    started = perf_counter()
    timestamp = datetime.now(timezone.utc)
    run_id = timestamp.strftime("%Y%m%dT%H%M%S") + "-" + uuid4().hex[:12]
    directory = output_root.resolve() / run_id
    directory.mkdir(parents=True, exist_ok=False)
    (directory / "body.txt").write_text(body, encoding="utf-8", newline="\n")
    source_path = directory / ("source.c" if route == "c" else "source.wat")
    source_path.write_text(source, encoding="utf-8", newline="\n")
    dataset = {"public": [case.to_dict() for case in task.public_cases]}
    if include_private:
        dataset["private"] = [case.to_dict() for case in task.private_cases]
    suites = tuple(dataset)
    dataset_bytes = json.dumps(dataset, sort_keys=True, separators=(",", ":")).encode()
    save_json(directory / "dataset.json", dataset)
    report = {
        "schema_version": 1, "run_id": run_id, "kind": "manual-smoke",
        "task_id": task.id, "route": route, "started_at": timestamp.isoformat(),
        "source_shift_version": __version__, "dataset_version": DATASET_VERSION,
        "evaluation_scope": "public-and-private" if include_private else "public-only",
        "dataset_sha256": sha256(dataset_bytes).hexdigest(), "template_version": TEMPLATE_VERSION,
        "body_sha256": sha256(body.encode("utf-8")).hexdigest(),
        "config": {**asdict(config), "engine": ENGINE_CONFIG, "store_limits": STORE_LIMITS},
        "tools": tool_versions(clang), "task": task.public_view(),
        "implementation_sha256": {path.name: sha256(path.read_bytes()).hexdigest()
                                  for path in sorted(Path(__file__).parent.glob("*.py"))},
        "model": None, "usage": None, "cost": None, "model_request_seconds": None,
        "runtime_benchmark": None, "conversion": None, "public": None, "private": None,
        "sizes": {"body_bytes": len(body.encode("utf-8")), "source_bytes": len(source.encode("utf-8")), "wasm_bytes": None},
    }
    # 원본/계약을 변환 전에 남겨 예기치 않은 중단도 시도 기록으로 보존한다.
    save_json(directory / "report.json", {**report, "status": "running"})
    try:
        validate_body(route, body)
        body_error = None
    except ValueError as exc:
        body_error = str(exc)
    if body_error is not None:
        conversion = {"status": "body_contract_error", "error": body_error, "wall_seconds": None}
    elif route == "c":
        conversion = compile_c(find_clang(clang), directory, config.wall_timeout_seconds)
    else:
        conversion = run_worker("assemble", {"source": str(source_path), "wasm": str(directory / "module.wasm")},
                                directory, "conversion", config.wall_timeout_seconds)
    report["conversion"] = conversion
    if conversion["status"] == "passed":
        report["sizes"]["wasm_bytes"] = (directory / "module.wasm").stat().st_size
        for suite in suites:
            request = {"wasm": str(directory / "module.wasm"), "cases": dataset[suite],
                       "fuel_per_case": config.fuel_per_case}
            report[suite] = run_worker("evaluate", request, directory, suite, config.wall_timeout_seconds)
    report["status"] = conversion["status"]
    if conversion["status"] == "passed":
        report["status"] = "passed" if all(report[s]["status"] == "passed" for s in suites) else "failed"
    report["total_wall_seconds"] = perf_counter() - started
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    save_json(directory / "report.json", report)
    # 생성/수정 경로에 전달 가능한 유일한 파일. private 결과와 전체 판정은 제외한다.
    feedback = {"task_id": task.id, "route": route, "conversion": conversion, "public": report["public"]}
    save_json(directory / "public-feedback.json", feedback)
    return report, directory
