# 모델 없는 실행기 사용법

기준일: 2026-10-27. 이 실행기는 파일로 제공한 C/WAT 본문을 변환·실행·채점한다. 별도 세션 보조 스크립트로 현재 세션의 생성기 응답을 공개 검사·수정 프롬프트·사후 평가·요약에 연결했다. API 모델 호출과 실제 usage·비용 기록은 아직 구현하지 않았다.

## 설치

Python 3.11 이상과 Wasm 대상으로 컴파일할 수 있는 Clang/wasm-ld가 필요하다. Python 패키지는 프로젝트 가상환경에 설치한다.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
```

`python`이 Windows 실행 별칭으로만 잡혀 실행되지 않으면 실제 설치된 Python의 실행 파일 경로를 지정한다. 전역 PATH나 Git 설정을 바꿀 필요는 없다. 번들 Python처럼 `ensurepip` 사용이 어려운 환경에서는 pip가 있는 Python에서 `-m pip --python .venv\Scripts\python.exe install pip setuptools`로 가상환경을 준비할 수 있다.

현재 개발 환경에는 Wasmtime 50.0.1과 Clang/LLD 23.1.4를 프로젝트 내부에 설치했다. Clang은 `.tools/llvm-23.1.4/bin/clang.exe`에 있다. `.tools/`, `.venv/`, `.runs/`는 Git 추적 대상에서 제외한다.

다른 환경에서는 [LLVM 공식 배포](https://github.com/llvm/llvm-project/releases/tag/llvmorg-23.1.4)에서 운영체제에 맞는 Clang과 LLD를 준비한다. 이번 Windows 설치에 사용한 공식 파일은 `clang+llvm-23.1.4-x86_64-pc-windows-msvc.tar.zst`이며 SHA-256은 `1a3f2f998e230b5791cc4e2bfe11a1f7c07fe42c96ff44f227c9db0bedfaf091`이다. 컴파일러 실행 파일 외에 `wasm-ld`와 `lib/clang/23/include` 리소스 헤더도 필요하다.

```powershell
.\.venv\Scripts\python.exe -m source_shift doctor --clang .tools/llvm-23.1.4/bin/clang.exe
.\.venv\Scripts\python.exe -m source_shift tasks
```

`doctor`는 버전을 출력한다. C/WAT 전체 경로에 필요한 도구가 빠졌으면 종료 코드 1을 반환한다. 버전 존재 확인만으로 Wasm target 빌드까지 확인한 것은 아니므로 아래 예제도 실행한다.

## 직접 작성한 본문 채점

두 경로 모두 함수 **본문만** 파일로 제공한다. Markdown 코드 펜스나 설명은 넣지 않는다. 도구는 코드를 추출·보완하지 않는다.

```powershell
.\.venv\Scripts\python.exe -m source_shift evaluate --task P01 --route c --body examples/p01-sum.c --clang .tools/llvm-23.1.4/bin/clang.exe
.\.venv\Scripts\python.exe -m source_shift evaluate --task P01 --route wat --body examples/p01-sum.wat
```

C는 `int32_t solve(int32_t a, int32_t b, int32_t c)`로 감싼다. WAT에는 `$a`, `$b`, `$c` 인수와 `i32` 결과가 제공되며 본문 앞에 로컬 변수 선언을 둘 수 있다. WAT는 텍스트에서 Wasm으로 조립하며 raw binary를 생성하는 경로가 아니다.

정상 통과의 종료 코드는 0, 변환·채점 실패는 1, 입력 파일이나 옵션 오류는 2다. 의도적인 실패 예제도 준비했다.

```powershell
.\.venv\Scripts\python.exe -m source_shift evaluate --task P01 --route wat --body examples/p01-wrong.wat
.\.venv\Scripts\python.exe -m source_shift evaluate --task P01 --route wat --body examples/invalid.wat
.\.venv\Scripts\python.exe -m source_shift evaluate --task P01 --route wat --body examples/infinite-loop.wat --fuel 1000
```

## 계약과 제한

- 파일럿 데이터 버전은 `ss-001-pilot-v1`이며 공개 예제 30개와 비공개 입력 684개로 구성한다. 과제마다 경계값과 seed `20261027 + 과제 번호`의 난수 입력을 사용한다. P07은 작은 정의역을 공개/비공개로 나누어 전수 확인한다.
- C는 고정된 `-O2`, `wasm32-unknown-unknown`, C17, freestanding, 표준 라이브러리 없는 설정을 사용한다. 전체 플래그는 `templates.py`와 실행 기록에 남긴다.
- 공통 Wasmtime 설정은 fuel 사용, Cranelift `speed`, 병렬 컴파일 비활성화다. 매 입력마다 새 Store/Instance를 만들고 외부 import와 WASI를 연결하지 않는다.
- 입력당 fuel 기본값은 1,000,000이며 초기화와 함수 호출에 함께 적용한다. 메모리는 2 MiB, 테이블 요소는 1,000개, 인스턴스·테이블·메모리 수는 각각 1로 제한한다.
- `--wall-timeout` 기본값 10초는 **변환, 공개 채점, 비공개 채점 각 단계**에 별도로 적용한다. subprocess 시작과 Wasmtime JIT를 포함하며, 전체 시도 10초 제한을 뜻하지 않는다. 단계 종료·기록 처리에 소량의 추가 시간이 든다.
- 본문 경계, Wasm 유효성, 외부 import 부재와 `solve(i32,i32,i32)->i32` ABI를 검사한다. 부동소수·직접 메모리 접근·재귀 등 제외 기능의 완전한 정적 검사는 아직 없다. C 전처리 지시문은 거부하지만 OS 수준 컴파일러 샌드박스는 제공하지 않는다. 현재는 직접 작성하고 검토한 입력의 개발 검증에 사용한다.

이 설정은 실행기 개발용 기본값이다. 모델 비교 실험의 생성 예산·출력 상한·수정 상한·반복·실행 순서는 별도로 고정해야 한다. 테스트 통과는 의미 보존의 증명이 아니다.

## 기록과 공개/비공개 분리

시도마다 `.runs/<새 ID>/`를 만들며 기존 시도를 덮어쓰지 않는다. 다음 파일을 보관한다.

| 파일 | 내용 |
| --- | --- |
| `body.txt`, `source.c` 또는 `source.wat` | 제공한 UTF-8 본문과 템플릿을 합친 소스 |
| `module.wasm` | 변환 성공 시의 Wasm 바이너리 |
| `dataset.json` | 해당 과제의 공개·비공개 입력과 참조값 스냅샷 |
| `report.json` | 판정, 모든 채점 결과, 크기, 시간, 도구 버전, 소스/데이터 해시와 설정 |
| `public-feedback.json` | 변환 결과와 공개 채점만 담은 피드백 |
| `*-request.json`, `*-result.json`, `*-stderr.txt` 등 | worker 입력·출력, 오류와 부분 진행 기록 |

CLI는 입력 파일의 UTF-8 바이트를 읽어 본문 텍스트를 보존하며 CRLF를 LF로 자동 정규화하지 않는다. 변환 전에 본문과 초기 기록을 남긴다. worker가 시간 초과하면 마지막으로 완료한 입력까지의 기록을 보존한다. 외부에서 부모 프로세스 자체를 종료하면 `running` 기록이 남을 수 있다.

생성·수정 어댑터가 향후 사용할 수 있는 자료는 공개 명세인 `Task.public_view()`와 `public-feedback.json`뿐이다. 전체 보고서·비공개 request·dataset을 모델에 제공해서는 안 된다. 여기서 비공개란 저장소 접근 통제가 아니라 생성·수정 프롬프트와의 분리를 뜻한다. 현재 수동 명령은 수정 루프가 없으므로 해당 본문을 공개 채점한 뒤 바로 비공개 채점한다. 자동 루프에서는 모든 수정이 끝난 뒤 비공개 사후 평가를 해야 한다.

모델·usage·비용·모델 요청 시간과 실행 성능 벤치마크는 `null`이다. `manual-smoke` 기록을 모델 실험 결과로 집계하지 않는다. 현재 시간에는 Python 시작, JIT, 입력별 인스턴스 생성과 기록 비용이 들어가므로 함수 실행 성능으로 해석하지 않는다. 원시 기록에는 로컬 경로가 들어갈 수 있어 커밋하지 않는다.

## 개발 검증

2026-10-27 개발 환경에서 자동 테스트 46개가 모두 통과했고 건너뛴 항목은 없었다. 별도 수동 검증 8개에서도 C/WAT 합산 성공, 오답, C/WAT 변환 오류, fuel 소진, 본문 경계 위반과 wall-clock 초과가 예상한 판정으로 기록됐다. 성공·실패 원본은 로컬 `.runs/`에 보존한다.

```powershell
$env:SOURCE_SHIFT_CLANG = (Resolve-Path .tools/llvm-23.1.4/bin/clang.exe).Path
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

C 통합 테스트는 Clang과 Wasmtime이 있어야 실행되며 없으면 건너뛴다. 전체 경로 검증에서는 건너뛴 항목 수를 확인한다. OS 임시 폴더 쓰기를 제한하는 실행 환경에서는 프로젝트 안의 임시 폴더를 지정한다.

```powershell
New-Item -ItemType Directory -Force .runs/test-temp | Out-Null
$env:TEMP = (Resolve-Path .runs/test-temp).Path
$env:TMP = $env:TEMP
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

## 세션 생성 코드의 실제 검증

현재 세션 설정을 상속한 독립 생성기 20개에 공개 명세·예제만 제공해 파일럿을 실행했다. C 10개와 WAT 10개가 모두 첫 응답으로 공개·비공개 평가를 통과했다. 경로마다 공개 30건·비공개 684건, 사후 평가 합계 1,428건이며 수정 호출은 없었다. 최초 공개 검사 60건은 사후 평가와 별도로 수행했다. [보고서와 생성 원본](../results/session-validation-2026-10-27/report.md)을 저장했다.

보조 스크립트는 API를 호출하지 않는다. 클라우드 주 에이전트가 독립 생성기를 실행하고, 생성기가 지정한 응답 파일을 쓰면 스크립트가 검사한다. 반복 절차는 다음과 같다.

```powershell
.\.venv\Scripts\python.exe scripts/session-trial.py prepare --directory .runs/new-session-trial
# 각 과제·경로의 prompt.txt만 독립 생성기에 전달하고 지정한 response.txt를 받는다.
.\.venv\Scripts\python.exe scripts/session-trial.py check --directory .runs/new-session-trial
# 공개 실패가 있으면 생성된 다음 attempt의 prompt만 같은 생성기에 전달한다. 수정 상한은 2회다.
.\.venv\Scripts\python.exe scripts/session-trial.py finalize --directory .runs/new-session-trial --export results/new-session-trial
```

`check`는 `evaluate_body(..., include_private=False)`를 사용해 공개 입력만 저장·실행한다. 모든 과제·경로의 공개 과정이 끝나기 전에는 `finalize`를 거부한다. 사후 평가에서는 본문 해시가 공개 검사 후 바뀌지 않았는지 확인하고 모든 시도를 다시 변환·채점한다. 원시 평가 기록은 하위 실행기의 본문 검증 기록이며, 생성 출처와 절차는 상위 `session-integration` manifest에 기록한다.

초기 공개 검사와 사후 평가에서 각각 변환하므로 두 단계 시간은 별도로 기록한다. 초기 도구 응답에는 세션 생성기의 모델 설정·seed·usage·출력 토큰 상한·모델 요청 시간·금액이 없었다. 모델 요청 속도·비용이나 경로의 일반적 우월성에 대한 결론은 내리지 않는다. 다음 통제 비교 단계에는 API 호출 계약·예산과 실제 usage 기록이 필요하며, 앱 모델의 API 권한을 자동으로 가정하지 않는다.

후속 분석에서는 해당 생성기 20개의 로컬 로그에서 모델 별칭·추론 설정과 토큰 사용량을 복원했다. 원시 로그를 공개하지 않고 [별도 사용량 보고서](../results/session-validation-2026-10-27/usage-recovery.md)에 범위·집계·단가 환산 조건을 기록한다. 초기 보고서의 `null`은 최초 수집 시점 값으로 남기며, 실제 청구액·출력 상한·백엔드 모델 snapshot·순수 요청 지연은 계속 미관측이다.
