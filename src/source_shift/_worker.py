"""부모 프로세스의 wall-clock 제한 안에서 Wasmtime 작업을 수행한다."""

from __future__ import annotations

import json
from pathlib import Path
import sys
from time import perf_counter

from .runtime_config import ENGINE_CONFIG, STORE_LIMITS


def write_json(path: Path, data: dict) -> None:
    # timeout/강제 종료 시에도 마지막으로 완성된 기록을 읽을 수 있게 한다.
    pending = path.with_suffix(".tmp")
    pending.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    pending.replace(path)


def assemble(request: dict) -> dict:
    import wasmtime

    try:
        binary = wasmtime.wat2wasm(Path(request["source"]).read_text(encoding="utf-8"))
        Path(request["wasm"]).write_bytes(binary)
        return {"status": "passed", "error": None}
    except wasmtime.WasmtimeError as exc:
        return {"status": "conversion_error", "error": str(exc)}


def evaluate(request: dict, output: Path) -> dict:
    import wasmtime

    result = {
        "status": "running", "passed": 0, "total": len(request["cases"]),
        "cases": [], "validation_seconds": None, "test_seconds": None,
        "error": None,
    }
    write_json(output, result)
    started = perf_counter()
    config = wasmtime.Config()
    for name, value in ENGINE_CONFIG.items():
        setattr(config, name, value)
    engine = wasmtime.Engine(config)
    try:
        module = wasmtime.Module(engine, Path(request["wasm"]).read_bytes())
    except wasmtime.WasmtimeError as exc:
        result.update(status="validation_error", error=str(exc))
        result["validation_seconds"] = perf_counter() - started
        return result
    result["validation_seconds"] = perf_counter() - started
    solve_type = next((item.type for item in module.exports if item.name == "solve"), None)
    if module.imports or not isinstance(solve_type, wasmtime.FuncType):
        result.update(status="contract_error", error="imports 없이 solve 함수를 export해야 합니다.")
        return result
    if ([str(value) for value in solve_type.params] != ["i32"] * 3
            or [str(value) for value in solve_type.results] != ["i32"]):
        result.update(status="contract_error", error="solve의 ABI는 (i32,i32,i32)->i32여야 합니다.")
        return result
    started = perf_counter()
    for case in request["cases"]:
        record = {**case, "actual": None, "status": "passed", "error": None}
        # 각 입력마다 새 instance를 써서 이전 입력의 메모리/전역 상태가 남지 않는다.
        with wasmtime.Store(engine) as store:
            store.set_limits(**STORE_LIMITS)
            store.set_fuel(request["fuel_per_case"])
            try:
                instance = wasmtime.Instance(store, module, [])
                solve = instance.exports(store)["solve"]
                record["actual"] = solve(store, *case["inputs"])
                if record["actual"] != case["expected"]:
                    record["status"] = "wrong_answer"
            except wasmtime.Trap as exc:
                record["status"] = (
                    "fuel_exhausted" if exc.trap_code == wasmtime.TrapCode.OUT_OF_FUEL else "trap"
                )
                record["error"] = str(exc)
            except wasmtime.WasmtimeError as exc:
                record.update(status="runtime_error", error=str(exc))
        result["cases"].append(record)
        result["passed"] += record["status"] == "passed"
        result["test_seconds"] = perf_counter() - started
        write_json(output, result)
    result["status"] = "passed" if result["passed"] == result["total"] else "failed"
    return result


def main() -> int:
    mode, request_file, result_file = sys.argv[1:]
    output = Path(result_file)
    try:
        request = json.loads(Path(request_file).read_text(encoding="utf-8"))
        result = assemble(request) if mode == "assemble" else evaluate(request, output)
    except ImportError as exc:
        result = {"status": "tool_unavailable", "error": str(exc)}
    except Exception as exc:
        result = {"status": "internal_error", "error": f"{type(exc).__name__}: {exc}"}
    write_json(output, result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
