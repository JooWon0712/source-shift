"""검토한 파일럿의 변환·cold·guest-loop warm CPU 시간을 로컬에 보존한다."""

from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path
from statistics import median
import struct
import subprocess
import sys
from time import perf_counter

from ._worker import write_json
from .body_contract import validate_body
from .processes import run_process
from .runner import compile_c, find_clang, save_json, tool_versions
from .tasks import DATASET_VERSION, get_task
from .templates import C_FLAGS, TEMPLATE_VERSION, wrap_body

ENGINE_CONFIG = {"consume_fuel": False, "parallel_compilation": False,
                 "cranelift_opt_level": "speed"}
TIMEOUT_SECONDS = 60.0


def _order(index: int) -> tuple[str, str]:
    return ("c", "wat") if index % 2 == 0 else ("wat", "c")


def _harness(cases: list[dict], cycles: int) -> str:
    # 별도 모듈의 동적 load와 imported call로 입력의 상수 전파를 피한다.
    data = b"".join(struct.pack("<iiii", *case["inputs"], case["expected"]) for case in cases)
    escaped = "".join(f"\\{byte:02x}" for byte in data)
    return f'''(module $benchmark_harness
      (import "target" "solve" (func $solve (param i32 i32 i32) (result i32)))
      (memory {max(1, (len(data) + 65535) // 65536)})
      (data (i32.const 0) "{escaped}")
      (func $bench_run (export "run") (result i32)
        (local $cycle i32) (local $p i32) (local $xor i32)
        (local $actual i32)
        (loop $cycles
          i32.const 0 local.set $p
          (loop $cases
            local.get $p i32.load
            local.get $p i32.load offset=4
            local.get $p i32.load offset=8
            call $solve local.set $actual
            local.get $xor local.get $actual i32.xor local.set $xor
            local.get $actual local.get $p i32.load offset=12 i32.ne
            if unreachable end
            local.get $p i32.const 16 i32.add local.tee $p
            i32.const {len(data)} i32.lt_u br_if $cases)
          local.get $cycle i32.const 1 i32.add local.tee $cycle
          i32.const {cycles} i32.lt_u br_if $cycles)
        local.get $xor))'''


def _runtime(request: dict, output: Path) -> dict:
    import wasmtime

    records, contexts = [], {}
    result = {"status": "running", "records": records}
    harness_bytes = Path(request["harness"]).read_bytes()
    for index in range(request["rounds"] + 1):
        for route in _order(index):
            record = {"phase": "cold", "route": route, "round": index,
                      "warmup": index == 0, "status": "running"}
            records.append(record)
            write_json(output, result)
            try:
                config = wasmtime.Config()
                for name, value in ENGINE_CONFIG.items():
                    setattr(config, name, value)
                engine = wasmtime.Engine(config)
                binary = Path(request["modules"][route]).read_bytes()
                started = perf_counter()
                module = wasmtime.Module(engine, binary)
                record["target_jit_seconds"] = perf_counter() - started
                started = perf_counter()
                harness = wasmtime.Module(engine, harness_bytes)
                record["harness_jit_seconds"] = perf_counter() - started
                store = wasmtime.Store(engine)
                started = perf_counter()
                target = wasmtime.Instance(store, module, [])
                record["target_instance_seconds"] = perf_counter() - started
                started = perf_counter()
                instance = wasmtime.Instance(store, harness, [target.exports(store)["solve"]])
                record["harness_instance_seconds"] = perf_counter() - started
                contexts[route] = (engine, store, target, instance)
                record["status"] = "passed"
            except Exception as exc:
                contexts.pop(route, None)
                record.update(status="runtime_error", error=f"{type(exc).__name__}: {exc}")
            write_json(output, result)
    # 마지막 cold instance를 그대로 사용한다. 첫 guest batch도 별도 보존한다.
    for index in range(request["rounds"] + 1):
        for route in _order(index):
            record = {"phase": "warm", "route": route, "round": index,
                      "warmup": index == 0, "status": "skipped"}
            records.append(record)
            if route in contexts:
                _, store, _, instance = contexts[route]
                run = instance.exports(store)["run"]
                started = perf_counter()
                try:
                    checksum = run(store)
                    elapsed = perf_counter() - started
                    record.update(status="passed", checksum=checksum & 0xffffffff,
                                  batch_seconds=elapsed)
                    if record["checksum"] != request["expected_xor"]:
                        record["status"] = "checksum_mismatch"
                except Exception as exc:
                    elapsed = perf_counter() - started
                    mismatch = (isinstance(exc, wasmtime.Trap) and exc.frames
                                and exc.frames[0].func_name == "bench_run")
                    status = "checksum_mismatch" if mismatch else ("trap" if isinstance(exc, wasmtime.Trap) else "runtime_error")
                    record.update(status=status,
                                  error=f"{type(exc).__name__}: {exc}", batch_seconds=elapsed)
            write_json(output, result)
    result["status"] = "passed" if all(r["status"] == "passed" for r in records) else "failed"
    write_json(output, result)
    return result


def _run_runtime(request: dict, directory: Path) -> dict:
    request_path, result_path = directory / "runtime-request.json", directory / "runtime-result.json"
    save_json(request_path, request)
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1]) + os.pathsep + env.get("PYTHONPATH", "")
    try:
        process = run_process([sys.executable, "-m", "source_shift.benchmark", str(request_path), str(result_path)],
                              cwd=directory, env=env, timeout=TIMEOUT_SECONDS)
        (directory / "runtime-stdout.txt").write_bytes(process.stdout)
        (directory / "runtime-stderr.txt").write_bytes(process.stderr)
        result = json.loads(result_path.read_text(encoding="utf-8")) if result_path.exists() else {"records": []}
        if process.returncode or "status" not in result:
            result.update(status="worker_error", error=f"worker exit code: {process.returncode}")
    except (OSError, subprocess.TimeoutExpired) as exc:
        result = json.loads(result_path.read_text(encoding="utf-8")) if result_path.exists() else {"records": []}
        result.update(status="wall_timeout" if isinstance(exc, subprocess.TimeoutExpired) else "worker_error", error=str(exc))
        (directory / "runtime-stdout.txt").write_bytes(getattr(exc, "stdout", None) or b"")
        (directory / "runtime-stderr.txt").write_bytes(getattr(exc, "stderr", None) or b"")
    save_json(result_path, result)
    return result


def benchmark_pair(task_id: str, c_body: str, wat_body: str, output_dir: Path, clang: str, *,
                   build_rounds: int = 5, runtime_rounds: int = 5, cycles: int = 100) -> dict:
    """API/모델 호출 없이 검토한 작은 파일럿만 측정한다. 출력은 비공개 로컬 원시 기록이다."""
    if any(type(value) is not int or not 0 < value < 2**31 for value in (build_rounds, runtime_rounds, cycles)):
        raise ValueError("rounds와 cycles는 1 이상 2**31 미만 정수여야 합니다.")
    task = get_task(task_id)
    cases = [case.to_dict() for case in (*task.public_cases, *task.private_cases)]
    dataset = json.dumps(cases, sort_keys=True, separators=(",", ":")).encode()
    expected_xor = 0
    for case in cases:
        expected_xor ^= case["expected"] & 0xffffffff
    expected_xor = expected_xor if cycles % 2 else 0
    directory = output_dir.resolve()
    directory.mkdir(parents=True, exist_ok=False)
    save_json(directory / "workload.json", {"cases": cases})
    report = {"schema_version": 1, "kind": "local-cpu-benchmark", "task_id": task_id,
              "status": "running", "dataset_version": DATASET_VERSION, "template_version": TEMPLATE_VERSION,
              "tools": tool_versions(clang), "config": {"engine": ENGINE_CONFIG, "c_flags": list(C_FLAGS),
              "build_rounds": build_rounds, "runtime_rounds": runtime_rounds, "cycles": cycles,
              "runtime_worker_timeout_seconds": TIMEOUT_SECONDS, "build_timeout_seconds": TIMEOUT_SECONDS},
              "environment": {"background_load": "uncontrolled"},
              "workload": {"sha256": sha256(dataset).hexdigest(), "case_count": len(cases),
              "function_calls_per_batch": len(cases) * cycles, "expected_xor": expected_xor,
              "scope": "public-and-private", "host_function_calls_per_batch": 1,
              "validation": "guest compares every result with expected and traps on any mismatch; returns xor",
              "harness_overhead": "shared guest loops, loads, xor, expected comparisons and imported calls included; no baseline subtraction"},
              "measurement_scope": "reviewed small pilot only; no hostile-code isolation claim",
              "build_timing": {"c": "compile_c subprocess wall time including diagnostics capture",
              "wat": "in-process wasmtime.wat2wasm plus binary artifact write; source preparation excluded"},
              "build": [], "runtime": None, "summary": None}
    save_json(directory / "benchmark.json", report)
    executable, modules = find_clang(clang), {}
    import wasmtime

    for index in range(build_rounds + 1):
        for route in _order(index):
            run_dir = directory / f"build-{index:02d}-{route}"
            run_dir.mkdir()
            body = c_body if route == "c" else wat_body
            (run_dir / "body.txt").write_bytes(body.encode("utf-8"))
            source = wrap_body(route, body)
            (run_dir / f"source.{route}").write_text(source, encoding="utf-8", newline="\n")
            try:
                validate_body(route, body)
                if route == "c":
                    build = compile_c(executable, run_dir, TIMEOUT_SECONDS)
                else:
                    started = perf_counter()
                    try:
                        binary = wasmtime.wat2wasm(source)
                        (run_dir / "module.wasm").write_bytes(binary)
                        build = {"status": "passed", "wall_seconds": perf_counter() - started}
                    except Exception as exc:
                        build = {"status": "conversion_error", "error": str(exc), "wall_seconds": perf_counter() - started}
            except ValueError as exc:
                build = {"status": "body_contract_error", "error": str(exc), "wall_seconds": None}
            record = {"route": route, "round": index, "warmup": index == 0, **build}
            report["build"].append(record)
            save_json(run_dir / "conversion.json", record)
            if build["status"] == "passed":
                modules[route] = str(run_dir / "module.wasm")
            save_json(directory / "benchmark.json", report)
    if all(record["status"] == "passed" for record in report["build"]):
        harness_text = _harness(cases, cycles)
        (directory / "harness.wat").write_text(harness_text, encoding="utf-8", newline="\n")
        (directory / "harness.wasm").write_bytes(wasmtime.wat2wasm(harness_text))
        report["runtime"] = _run_runtime({"modules": modules, "harness": str(directory / "harness.wasm"),
                                          "rounds": runtime_rounds, "expected_xor": expected_xor}, directory)
    passed = report["runtime"] is not None and report["runtime"]["status"] == "passed"
    report["status"] = "passed" if passed else "failed"
    if passed:
        report["summary"] = {}
        for route in ("c", "wat"):
            records = [r for r in report["runtime"]["records"] if r["route"] == route and not r["warmup"]]
            report["summary"][route] = {"build_median_seconds": median(r["wall_seconds"] for r in report["build"]
                if r["route"] == route and not r["warmup"]), **{key + "_median": median(r[key] for r in records if key in r)
                for key in ("target_jit_seconds", "target_instance_seconds", "harness_jit_seconds", "harness_instance_seconds", "batch_seconds")}}
    report["artifacts"] = [{"path": p.relative_to(directory).as_posix(), "bytes": p.stat().st_size,
                             "sha256": sha256(p.read_bytes()).hexdigest()}
                            for p in sorted(directory.rglob("*")) if p.is_file() and p.name != "benchmark.json"]
    save_json(directory / "benchmark.json", report)
    return report


if __name__ == "__main__":
    request_file, output_file = map(Path, sys.argv[1:])
    try:
        _runtime(json.loads(request_file.read_text(encoding="utf-8")), output_file)
    except Exception as error:
        partial = json.loads(output_file.read_text(encoding="utf-8")) if output_file.exists() else {"records": []}
        partial.update(status="worker_error", error=f"{type(error).__name__}: {error}")
        write_json(output_file, partial)
