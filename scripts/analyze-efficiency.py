"""고정된 묶음 파일럿의 실제 판정·usage·로컬 시간을 요약한다."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
from statistics import median

from source_shift.benchmark import benchmark_pair
from source_shift.runner import save_json
from source_shift.tasks import list_tasks
from source_shift.usage import collect_usage, TOKEN_FIELDS

ROOT = Path(__file__).resolve().parents[1]


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def sanitize(value):
    if isinstance(value, dict):
        return {key: sanitize(item) for key, item in value.items()}
    if isinstance(value, list):
        return [sanitize(item) for item in value]
    if isinstance(value, str):
        value = value.replace(str(ROOT), "<workspace>")
        profile = os.environ.get("USERPROFILE")
        if profile:
            value = value.replace(profile, "<user>")
        return value
    return value


def supervisor_id(logs, parent_id, agent_path):
    matches = []
    for path in sorted(logs.rglob("*.jsonl")):
        with path.open(encoding="utf-8") as stream:
            try:
                item = json.loads(next(stream))
            except (ValueError, StopIteration):
                continue
        meta = item.get("payload", {})
        source = meta.get("source")
        if not isinstance(source, dict):
            continue
        spawn = source.get("subagent", {}).get("thread_spawn", {})
        if spawn.get("parent_thread_id") == parent_id and spawn.get("agent_path") == agent_path:
            matches.append(meta["id"])
    if len(matches) != 1:
        raise ValueError("조율 에이전트의 로컬 로그를 유일하게 식별하지 못했습니다.")
    return matches[0]


def recover(args):
    directory, export = args.directory.resolve(), args.export.resolve()
    manifest = read(directory / "manifest.json")
    expected = read(directory / "dispatch-ledger.json")
    if set(expected) != {job["id"] for job in manifest["jobs"]}:
        raise ValueError("생성기 ledger가 6개 job과 일치하지 않습니다.")
    parent = supervisor_id(args.logs, args.root_thread_id, args.supervisor)
    usage = collect_usage(args.logs, parent, expected)
    supervision = collect_usage(args.logs, args.root_thread_id, {"supervision": args.supervisor})
    for job in manifest["jobs"]:
        record = usage["jobs"][job["id"]]
        observed = record.get("user_text_part_sha256", [])
        record["planned_prompt_hashes"] = [attempt["prompt_sha256"] for attempt in job["attempts"]]
        matched = all(value in observed for value in record["planned_prompt_hashes"])
        # 자식 생성기의 JSONL에는 작업 전달문이 평문 message로 남지 않을 수 있다.
        # 미관측을 실제 불일치(False) 또는 일치(True)로 꾸미지 않는다.
        record["exact_prompt_parts_observed"] = True if matched else None
        record["prompt_audit_scope"] = "plaintext part matched" if matched else "not independently observable in child transcript; planned prompt and dispatcher ledger retained"
    save_json(export / "usage.json", {"generation": usage, "supervision": supervision,
                                      "scope": "generation agents and common dispatcher separated; primary implementation/analysis excluded",
                                      "collected_at": datetime.now(timezone.utc).isoformat()})
    print(json.dumps({"generation": usage["status"], "supervision": supervision["status"],
                      "prompt_hashes_match": True if all(job["exact_prompt_parts_observed"] is True for job in usage["jobs"].values()) else None,
                      "usage": usage["usage"]}))


def benchmark(args):
    directory, export = args.directory.resolve(), args.export.resolve()
    evaluation = read(export / "evaluation.json")
    if not read(directory / "manifest.json")["finalized_at"]:
        raise ValueError("전체 생성·채점 종료 후 로컬 성능을 측정합니다.")
    first = {row["route"]: row for row in evaluation["rows"] if row["replicate"] == 1}
    bodies = {}
    for route, row in first.items():
        try:
            bodies[route] = read(export / row["attempts"][-1]["artifact_file"])
        except (ValueError, UnicodeError):
            bodies[route] = None
    results = {}
    for task in list_tasks():
        report_file = directory / "benchmarks" / task.id.lower() / "benchmark.json"
        if report_file.exists():
            result = read(report_file)
            if result["status"] == "running":
                raise ValueError("중단된 벤치마크가 있습니다. 조건을 바꾸어 재실행하지 말고 원인을 기록하세요.")
        elif all(first[route]["attempts"][-1]["reports"].get(task.id, {}).get("status") == "passed"
                 and isinstance(bodies[route], dict) and task.id in bodies[route] for route in ("c", "wat")):
            result = benchmark_pair(task.id, bodies["c"][task.id], bodies["wat"][task.id], report_file.parent,
                                    args.clang, build_rounds=5, runtime_rounds=5, cycles=100)
        else:
            result = {"status": "not_run", "reason": "preselected first-replicate pair failed correctness", "summary": None}
        results[task.id] = sanitize(result)
        save_json(export / "benchmark.json", results)
        print(json.dumps({"task": task.id, "status": result["status"]}), flush=True)


def cost(usage, rates, cached):
    if usage is None or usage["cache_write_input_tokens"] != 0:
        return None
    input_price = rates["ordinary_input_per_million_usd"]
    cache_price = rates["cached_input_per_million_usd"]
    output_price = rates["output_per_million_usd"]
    ordinary = usage["input_tokens"] - usage["cached_input_tokens"] if cached else usage["input_tokens"]
    cache = usage["cached_input_tokens"] if cached else 0
    return (ordinary * input_price + cache * cache_price + usage["output_tokens"] * output_price) / 1e6


def analyze(args):
    export = args.export.resolve()
    evaluation, recovered = read(export / "evaluation.json"), read(export / "usage.json")
    local = read(export / "benchmark.json")
    protocol = evaluation["protocol"]
    valid = recovered["generation"]["status"] == "ok"
    native = recovered["generation"]["jobs"]
    configurations = {(record.get("model"), record.get("effort")) for record in native.values()}
    pricing_valid = valid and len(configurations) == 1 and next(iter(configurations))[0] == protocol["api_price_scenario"]["model_alias"]
    rows = []
    for source in evaluation["rows"]:
        record = native[source["job"]]
        first, final = source["attempts"][0]["reports"], source["attempts"][-1]["reports"]
        scored = [report for attempt in source["attempts"] for report in attempt["reports"].values()]
        rows.append({"job": source["job"], "replicate": source["replicate"], "route": source["route"],
                     "first_correct": sum(report["status"] == "passed" for report in first.values()),
                     "final_correct": sum(report["status"] == "passed" for report in final.values()),
                     "private_passed": sum(report["private"]["passed"] for report in final.values()) if len(final) == 10 and all(report["private"] is not None and report["private"].get("passed") is not None for report in final.values()) else None,
                     "private_expected": sum(len(task.private_cases) for task in list_tasks()),
                     "repairs": source["repairs"], "usage": record["usage"],
                     "agent_turn_elapsed_seconds": record.get("agent_turn_elapsed_seconds"),
                     "response_count": record.get("response_count"), "tool_call_counts": record.get("tool_call_counts"),
                     "prompt_hash_match": record.get("exact_prompt_parts_observed"),
                     "cache_neutral_api_equivalent_usd": cost(record["usage"], protocol["api_price_scenario"], False) if pricing_valid else None,
                     "observed_cache_api_equivalent_usd": cost(record["usage"], protocol["api_price_scenario"], True) if pricing_valid else None,
                     "final_body_bytes": sum(report["sizes"]["body_bytes"] for report in final.values()) if len(final) == 10 and all(report["sizes"]["body_bytes"] is not None for report in final.values()) else None,
                     "final_wasm_bytes": sum(report["sizes"]["wasm_bytes"] for report in final.values()) if len(final) == 10 and all(report["sizes"]["wasm_bytes"] is not None for report in final.values()) else None,
                     "all_attempt_posthoc_wall_seconds": sum(report["posthoc_wall_seconds"] for report in scored) if scored else None})
    cost_valid = pricing_valid and all(row["cache_neutral_api_equivalent_usd"] is not None
                                       and row["cache_neutral_api_equivalent_usd"] > 0 for row in rows)
    paired = []
    for repeat in (1, 2, 3):
        pair = {row["route"]: row for row in rows if row["replicate"] == repeat}
        c, wat = pair["c"], pair["wat"]
        cost_ratio = wat["cache_neutral_api_equivalent_usd"] / c["cache_neutral_api_equivalent_usd"] if cost_valid else None
        paired.append({"replicate": repeat, "equal_correctness": c["final_correct"] == wat["final_correct"] == 10,
                       "wat_over_c_output_tokens": wat["usage"]["output_tokens"] / c["usage"]["output_tokens"] if valid and c["usage"]["output_tokens"] else None,
                       "wat_over_c_cache_neutral_cost": cost_ratio,
                       "c_cost_reduction_fraction": 1 - 1 / cost_ratio if cost_ratio else None})
    supported = (valid and cost_valid and all(row["prompt_hash_match"] is not False for row in rows)
                 and all(pair["equal_correctness"] for pair in paired))
    reductions = [pair["c_cost_reduction_fraction"] for pair in paired] if cost_valid else []
    c_advantage = supported and all(value > 0 for value in reductions) and median(reductions) >= .05
    wat_reductions = [1 - pair["wat_over_c_cache_neutral_cost"] for pair in paired] if cost_valid else []
    wat_advantage = supported and all(value > 0 for value in wat_reductions) and median(wat_reductions) >= .05
    summary = {"protocol_id": protocol["experiment_id"], "protocol_sha256": sha256((args.directory / "protocol.json").read_bytes()).hexdigest(),
               "configuration": [list(item) for item in sorted(configurations, key=repr)], "rows": rows, "paired": paired,
               "preregistered_primary_winner": "c" if c_advantage else "wat" if wat_advantage else "inconclusive",
               "actual_billed_cost": None, "generation_usage": recovered["generation"]["usage"],
               "shared_supervision_usage": recovered["supervision"]["usage"],
               "primary_development_analysis_usage": None, "benchmark_status": {key: value["status"] for key, value in local.items()},
               "analysis_script_sha256": sha256(Path(__file__).read_bytes()).hexdigest(),
               "limitations": ["10 known pilot tasks generated as a batch; 3 paired batch repetitions only", "API prices are hypothetical, not Codex billing", "no provider-level output-token cap or backend snapshot", "agent turn time includes queue and tool work", "local background load uncontrolled and warm time includes common harness overhead", "all inference usage includes tool instructions and file write acknowledgement; not just source-body tokens", "planned prompts and dispatcher ledger retained; opaque logged inter-agent message transport prevents independent plaintext hash matching"]}
    save_json(export / "analysis.json", summary)
    lines = ["# C/WAT 효율 실험 결과", "", f"실험: `{protocol['experiment_id']}`. 2026-10-27, Asia/Seoul.", "",
             "동일한 공개 10과제를 한 묶음으로 생성하고 경로마다 독립 문맥 3개를 사용했다. 최초 응답과 공개 수정이 끝난 뒤 모든 시도를 비공개 평가했다. 판단 기준은 실행 전에 고정했다.", "",
             "| 반복·경로 | 첫/최종 정답 | 수정 | 입력(캐시 포함) | 출력(reasoning 포함) | 에이전트 턴(초) | 캐시 통일 환산($) |", "| --- | --- | --- | --- | --- | --- | --- |"]
    for row in rows:
        usage = row["usage"] or {}
        elapsed = row["agent_turn_elapsed_seconds"]
        estimate = row["cache_neutral_api_equivalent_usd"]
        lines.append(f"| {row['replicate']} / {row['route'].upper()} | {row['first_correct']}/10 → {row['final_correct']}/10 | {row['repairs']} | {usage.get('input_tokens', '미관측')} | {usage.get('output_tokens', '미관측')} | {elapsed if elapsed is not None else '미관측'} | {f'{estimate:.6f}' if estimate is not None else '미관측'} |")
    lines += ["", f"사전 기준의 생성 비용 판정: **{summary['preregistered_primary_winner']}**. 입력을 모두 일반 입력 단가로 통일한 가상 API 환산이다. 실제 청구액이 아니다.", "",
              "## 판단", ""]
    if c_advantage:
        lines += [f"이번 범위에서는 **C 경유를 기본 생성 경로로 권한다.** 최종 정확도가 같고 생성 비용 환산은 3개 짝 모두 C가 낮았다. 짝별 C 절감률은 {', '.join(f'{value*100:.1f}%' for value in reductions)}이며 중앙값은 {median(reductions)*100:.1f}%다.", ""]
    elif wat_advantage:
        lines += ["이번 범위에서는 WAT의 생성 비용 이점이 사전 기준을 충족했다. 일반화 결론은 별도 평가가 필요하다.", ""]
    else:
        lines += ["생성 비용의 실용적인 우열은 사전 기준으로 확정하지 못했다. 정확도·토큰·시간을 분리해 판단한다.", ""]
    totals = {route: {"output_tokens": sum(row["usage"]["output_tokens"] for row in rows if row["route"] == route),
                      "agent_turn_median_seconds": median(row["agent_turn_elapsed_seconds"] for row in rows if row["route"] == route),
                      "wasm_median_bytes": median(row["final_wasm_bytes"] for row in rows if row["route"] == route)} for route in ("c", "wat")} if valid and all(row["agent_turn_elapsed_seconds"] is not None and row["final_wasm_bytes"] is not None for row in rows) else None
    if totals:
        lines += ["| 관측 합계/중앙값 | C | WAT |", "| --- | --- | --- |",
                  f"| 3회 생성 출력 토큰, reasoning 포함 | {totals['c']['output_tokens']:,} | {totals['wat']['output_tokens']:,} |",
                  f"| 생성 에이전트 턴 중앙값(초) | {totals['c']['agent_turn_median_seconds']:.3f} | {totals['wat']['agent_turn_median_seconds']:.3f} |",
                  f"| 10개 Wasm 파일 합계의 반복 중앙값(bytes) | {totals['c']['wasm_median_bytes']:.0f} | {totals['wat']['wasm_median_bytes']:.0f} |", "",
                  "WAT는 조립 시간이 훨씬 짧았고 최종 파일도 작았다. 파일 크기에는 컴파일러 메타데이터·모듈 구성·최적화 차이가 반영된다. warm 실행 시간은 과제별로 방향이 갈렸으므로 전체 실행 속도의 승자는 정하지 않는다. 생성 에이전트 턴은 순수 모델 요청 지연이 아니라 도구·대기·완료 보고까지 포함한다.", ""]
    lines += [
              "## 로컬 변환·실행", "", "첫 반복의 10쌍을 사전에 선택했다. 아래 값은 최초 warmup을 제외한 5회 중앙값이다. WAT는 라이브러리 조립, C는 Clang CLI를 사용하므로 프로세스 시작·링킹·최적화 차이도 포함한다.", "",
              "| 과제 | C 변환(ms) | WAT 조립(ms) | C warm batch(ms) | WAT warm batch(ms) |", "| --- | --- | --- | --- | --- |"]
    for task in list_tasks():
        result = local[task.id]
        if result["summary"]:
            c, wat = result["summary"]["c"], result["summary"]["wat"]
            lines.append(f"| {task.id} | {c['build_median_seconds']*1000:.3f} | {wat['build_median_seconds']*1000:.3f} | {c['batch_seconds_median']*1000:.3f} | {wat['batch_seconds_median']*1000:.3f} |")
        else:
            lines.append(f"| {task.id} | {result['status']} | 미집계 | 미집계 | 미집계 |")
    lines += ["", "warm batch는 과제별 공개·비공개 입력 전체를 100회 반복하며 입력 수가 과제마다 다르다. 공통 guest harness·import 호출·결과 검증 시간을 포함한다. 과제 간 절대 시간이나 순수 함수 시간으로 해석하지 않는다. JIT/인스턴스 생성 및 모든 원시 반복은 benchmark.json에 별도 기록했다.", "",
              "## 범위와 재현", "", "- 별도 과금 API는 호출하지 않았다. 생성기 6개와 공통 조율기의 세션 사용량은 usage.json에 분리했다. 주 에이전트의 개발·분석 사용량은 이 비교 집계에 포함하지 않는다.",
              "- 캐시 입력은 input의 부분집합이며 reasoning은 output에 포함된다. 이중 합산하지 않았다. 관측 캐시 환산은 analysis.json에 보조 지표로 남겼다.",
              "- 5%는 사전에 정한 실용 판단 기준이다. 반복 3개로 모집단에 대한 통계적 우월성을 주장하지 않는다. 각 묶음 안의 함수 10개도 독립 생성 표본으로 취급하지 않는다.",
              "- 전달 조건은 저장한 프롬프트와 조율기 ledger로 관리했다. 세션 로그의 에이전트 간 전달문은 평문으로 관측되지 않아 실제 전달문 전체의 해시를 독립적으로 대조하지 못했다. 이를 불일치나 검증 성공으로 채우지 않고 미관측으로 남겼다.",
              "- 이미 알려진 쉬운 파일럿의 해당 모델·추론·전달 방식에 한정한 결과다. 별도 평가나 다른 모델로 일반화하지 않는다.",
              "- 생성 원본·공개 프롬프트·모든 시도는 sources/에, 채점은 evaluation.json에, 토큰·짝별 비교는 analysis.json과 usage.json에 보존했다. 원시 개인 경로와 계정 로그는 공개 사본에서 제외했다.", ""]
    resource_file = export / "resource-audit.json"
    if resource_file.exists():
        resource = read(resource_file)
        lines += ["## 세션 사용량 범위", "", "| 범위 | 입력+출력 토큰, 캐시 포함 |", "| --- | --- |"]
        for name, values in resource["scopes"].items():
            lines.append(f"| {name} | {values['total_tokens']:,} |")
        lines += ["", f"집계 시점: {resource['snapshot_at']}. 위 시점까지 관측한 합계는 {resource['combined_observed_usage']['total_tokens']:,}토큰이다. 주 에이전트의 이후 보고·최종 응답은 포함하지 않는 스냅샷이다. 큰 대화·도구 문맥이 호출마다 다시 집계되며 입력 대부분은 캐시다. 공통 측정기 구현·조율·분석 사용량이 생성기보다 컸고, 이를 C나 WAT 한쪽 비용으로 배분하지 않았다. 별도 유료 API는 호출하지 않았으며 실제 Codex 청구액·한도 차감은 미관측이다. 추가 생성 반복은 실행하지 않았다.", ""]
    write_text = "\n".join(lines)
    (export / "report.md").write_text(write_text, encoding="utf-8", newline="\n")
    print(json.dumps({"winner": summary["preregistered_primary_winner"], "paired": paired, "usage": summary["generation_usage"]}))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("usage", "benchmark", "analyze"))
    parser.add_argument("--directory", type=Path, default=ROOT / ".runs/efficiency-study-v1")
    parser.add_argument("--export", type=Path, default=ROOT / "results/efficiency-study-v1")
    parser.add_argument("--logs", type=Path, default=Path(os.environ.get("USERPROFILE", "")) / ".codex/sessions/2026/10/27")
    parser.add_argument("--root-thread-id")
    parser.add_argument("--supervisor", default="/root/batch_dispatch")
    parser.add_argument("--clang", default=str(ROOT / ".tools/llvm-23.1.4/bin/clang.exe"))
    args = parser.parse_args()
    if args.action == "usage" and not args.root_thread_id:
        parser.error("usage에는 --root-thread-id가 필요합니다.")
    {"usage": recover, "benchmark": benchmark, "analyze": analyze}[args.action](args)


if __name__ == "__main__":
    main()
