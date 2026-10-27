# 선행연구와 참고 자료

대화에서 확인한 자료를 2026-10-27 기준으로 정리했다. 아래 수치는 각 저자나 프로젝트의 보고이며 source-shift가 재현한 결과가 아니다. 출판·저장소 상태와 링크는 향후 변경될 수 있다.

## 1. AI용 표현과 토큰 효율

### SimPy — ISSTA 2024

**AI Coders Are Among Us: Rethinking Programming Language Grammar Towards Efficient Code Generation**

- [논문](https://arxiv.org/html/2404.16333v2)
- [공개 코드](https://github.com/v587su/SimPy)

Python과 같은 AST를 유지하는 AI용 문법을 만들고, 사람이 Python을 보고 AI가 SimPy를 쓰는 가역 변환 방식도 제안한다. 표현의 토큰 수와 별도로 학습한 소형 모델의 HumanEval 생성 성능을 평가했다.

논문의 토큰 절감 수치는 토크나이저·추가 토큰 설정에 의존한다. GPT-4 토크나이저로 센 표현 크기를 GPT-4가 실제 생성해서 절약한 비용으로 해석하지 않는다. 바이트코드 직접 생성, 현재 클라우드 모델에 대한 프롬프트만의 효과, 수정 루프 전체 비용을 입증한 연구가 아니다.

### Token Sugar — 2025

**Token Sugar: Making Source Code Sweeter for LLMs through Token-Efficient Shorthand**

- [논문](https://arxiv.org/html/2512.08266v1)
- [공개 코드](https://github.com/v587su/TokenSugar)

반복되는 Python 패턴 799개를 가역 약어로 치환한다. 모델 3개를 추가 학습하고 HumanEval 164문제로 평가했다. 약어 생성물의 표현 토큰을 풀기 전/후 비교해 최대 11.2% 절감을 보고한다. Qwen 계열 실험의 Pass@1은 26.2%에서 25.6%였다.

전체 에이전트 소비 토큰이나 시간의 11.2% 절감이라는 뜻은 아니다. 추가 학습하지 않은 GPT-4.1의 코드 완성 실험에서는 Python 94.5%에서 약어 표현 51.2%로 정확도가 하락했다. 새 표현에 대한 모델 적응 비용을 고려해야 한다.

### The Best Programming Language for Tokenmaxxing — 2026 프리프린트

**An Investigation of Coding Agent Behavior Across Programming Languages**

- [논문 v2, 2026-10-21](https://arxiv.org/html/2607.22807v2)

5개 모델, Python/Java/Rust/OCaml, 100문제를 조합한 2,000개 에이전트 실행을 분석한다. 실패와 추론·수정을 포함한 출력 토큰을 비교한다. 문제 난도를 통제한 OCaml의 출력 토큰은 Python의 1.28~1.69배로 보고한다.

최종 소스 길이보다 개발 과정의 비용을 측정해야 한다는 참고다. IR·WAT·바이트코드 생성의 비교가 아니다. 논문에 공개 예정이라고 쓰인 코드·데이터를 현재 공개된 것으로 단정하지 않는다.

## 2. 저수준 코드 직접 생성과 컴파일

### BinMetric — IJCAI 2025

**A Comprehensive Binary Code Analysis Benchmark for Large Language Models**

- [논문과 원문](https://ijcai.org/proceedings/2025/858)

AIG 평가 항목은 자연어 기능 설명에서 Intel 문법 어셈블리를 생성한다. 100문항에서 컴파일과 실행 정확도를 측정하며 해당 조건에서 평균 실행 정답률 1% 미만을 보고한다.

오래된 GPT-4/CodeLlama 등 모델과 제한된 출력 조건의 결과다. 2026년 현재 모델의 성능으로 일반화하지 않는다. 고수준 소스 경유 방식 대비 총비용 비교도 아니다.

### GenerativeCompilation — 공개 실험 저장소

- [코드와 리포트](https://github.com/mgrainger87/GenerativeCompilation)

C에서 LLM으로 어셈블리를 생성하거나 기존 어셈블리를 최적화하고, Clang 결과와 정확성·실행 성능을 비교한다. 테스트·오류 수정·성능 측정 구조가 참고할 만하다.

입력에 C가 남으므로 자연어에서 고수준 소스를 생략하는 실험과 다르다. 전체 모델 토큰·생성 시간의 통제 비교로 해석하지 않는다.

### Meta LLM Compiler — 2024

- [논문](https://arxiv.org/abs/2407.02524)
- [공식 모델 카드](https://huggingface.co/facebook/llm-compiler-7b-ftd/blob/main/README.md)

LLVM IR 최적화와 assembly→IR 등의 작업을 다루는 모델이다. 저수준 표현에 특화된 모델 연구지만 자연어에서 앱 바이너리를 만드는 도구는 아니다. 공개 가중치와 자체 라이선스 모델을 무조건적인 오픈소스로 표현하지 않는다.

## 3. 인접 프로젝트와 개발 리포트

| 자료 | 참고할 내용 | 검증 범위의 한계 |
| --- | --- | --- |
| [Tacit](https://github.com/weetster/tacit) | AST를 원본으로 두고 AI용·검토용 표현을 제공, LLVM으로 컴파일 | 설계와 공개 구현의 존재가 총비용 우월성의 증거는 아님 |
| [LOVA](https://github.com/npcnpc09/lova-lang) | 정수 시퀀스 표현, VM, 공개 실험 목록과 부정적 결과 기록 | 저자의 소규모 자체 실험. 60/60 결과는 명시된 수식의 전사 성격이며 일반 합성 능력으로 읽지 않음. 압축 Stage 2를 모델이 직접 작성하는 능력은 미검증 |
| [NERD](https://github.com/Nerd-Lang/nerd-lang-core) | 기계 작성 중심 언어, LLVM 경유 도구 | 초기 실험. README의 큰 토큰 절감 주장을 통제된 생성 비용 검증으로 취급하지 않음 |
| [LMVM CLI Agent 발표](https://www.linkedin.com/pulse/lmvm-cli-agent-natural-language-native-binary-via-llvm-banerjee-epxze) | 자연어→LLVM IR→native binary라는 직접적인 시제품 발표 | 발표 원문을 확인했으며 연결된 저장소 구현·재현 가능성은 확인하지 못함 |
| [StrongDM Software Factory](https://factory.strongdm.ai/) / [Attractor](https://github.com/strongdm/attractor) | 명세·시나리오 중심의 개발과 사람의 코드 리뷰를 줄이는 실험 | 바이트코드 직접 생성이 아님. Attractor 저장소의 핵심 공개물은 자연어 명세 |

## 4. 구현에 사용할 공식 문서

- [WebAssembly 소개와 명세](https://webassembly.github.io/spec/core/intro/introduction.html): 가상 명령 체계, 텍스트·바이너리 형식, 검증·실행 의미
- [WABT](https://github.com/WebAssembly/wabt): WAT/Wasm 변환·검사 도구 후보
- [Wasmtime Python](https://github.com/bytecodealliance/wasmtime-py): Python 실행기 후보
- [Wasmtime 실행 중단](https://docs.wasmtime.dev/examples-interrupting-wasm.html): 무한 실행을 제한하는 fuel/epoch 기능과 overhead
- [Clang 사용자 문서](https://clang.llvm.org/docs/UsersManual.html): 컴파일 조건과 도구 사용
- [LLVM IR 언어 문서](https://llvm.org/docs/LangRef.html): IR·bitcode·텍스트 표현과 구조 검증
- [MLIR 설계 근거](https://mlir.llvm.org/docs/Rationale/Rationale/): 고수준 의미를 유지하며 단계적으로 낮추는 설계
- [MLIR Toy 튜토리얼](https://mlir.llvm.org/docs/Tutorials/Toy/): 후속 언어/IR 구현 학습 자료

## 5. source-shift의 연구 위치

기존 자료는 AI용 표현의 압축, 별도 학습 모델의 생성 성능, 저수준 코드 생성의 정확도, 에이전트의 과정 비용을 각각 다룬다.

본 대화에서 조사한 범위에서는 동일한 자연어 과제의 C→Wasm과 WAT→Wasm을 같은 모델로 비교하며 실패·수정·기능 변경의 총비용까지 통제한 자료를 찾지 못했다. 이는 문헌 전체에 해당 연구가 없다는 증명도, 최초 연구라는 근거도 아니다.

source-shift는 그 구체적인 비교를 작은 범위에서 재현 가능하게 수행하려 한다. 문헌조사를 확장하거나 새 관련 연구를 발견하면 이 문서를 갱신한다.

## 6. 수정 표현의 추가 후보

2026-10-27 대화에서 다음 1차 자료를 확인했다. 이번에는 조사만 했으며 source-shift의 수정 비교 실험은 실행하지 않았다.

- [Aider edit formats](https://aider.chat/docs/more/edit-formats.html): 전체 파일 재생성, SEARCH/REPLACE와 수정된 unified diff 형식을 설명한다. 모델별 형식 적합성이 다르며, 부분 출력이 전체 입력·추론·재시도 비용 절감을 보장하는 것은 아니다.
- [Geometric AST Edits 실험](https://www.geometric.so/research/ast-edits): 제작자가 Python 수정 과제 29개와 모델 4개로 편집 형식의 적용·테스트 성공과 출력 토큰을 비교했다. AST 대상 수정도 후보지만 AST가 diff보다 항상 짧다는 결과는 아니다. 작은 제작자 실험이며 전체 에이전트 비용 절감의 근거로 확대하지 않는다.

생성용 축약 문법과 수정용 편집 명령은 별도로 검토한다. AST를 그대로 JSON으로 출력하면 필드·구분자 때문에 길어질 수도 있다. 작은 함수의 부분 수정도 주소·문맥·변경 명령의 부가 비용을 포함해야 한다. 작은 DSL을 설계할 경우 C 기준선에도 동등한 연산 기능을 제공하여, 기능 추상화 차이를 표현 자체의 효율로 오인하지 않는다. 후속 후보는 [세션 정리](session-handoff.md)에 미착수 상태로 기록했다.
