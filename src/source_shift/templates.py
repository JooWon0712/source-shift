"""고정된 ABI 템플릿. 입력 본문을 추출하거나 자동 수정하지 않는다."""

TEMPLATE_VERSION = "ss-001-templates-v1"
C_FLAGS = (
    "--target=wasm32-unknown-unknown",
    "-std=c17",
    "-O2",
    "-ffreestanding",
    "-fno-builtin",
    "-nostdlib",
    "-Wl,--no-entry",
    "-Wl,--export=solve",
    "-Wl,--fatal-warnings",
    "-Wl,--stack-first",
    "-Wl,-z,stack-size=65536",
    "-Wl,--initial-memory=131072",
    "-Wl,--max-memory=2097152",
)


def wrap_body(route: str, body: str) -> str:
    if route == "c":
        return (
            "#include <stdint.h>\n"
            "int32_t solve(int32_t a, int32_t b, int32_t c) {\n"
            + body + "\n}\n"
        )
    if route == "wat":
        return (
            '(module\n  (func (export "solve") '
            "(param $a i32) (param $b i32) (param $c i32) (result i32)\n"
            + body + "\n  )\n)\n"
        )
    raise ValueError(f"지원하지 않는 경로: {route}")
