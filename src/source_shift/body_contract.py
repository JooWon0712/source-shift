"""고정 함수 템플릿을 벗어나지 않는지 검사하는 작은 lexer.

허용 언어 전체의 문법·순수성·의미를 검증하지 않는다. 검사용으로만 줄바꿈과
C line splicing을 해석하며, 호출자가 받은 원본 본문을 변경하지 않는다.
"""

from __future__ import annotations


_HORIZONTAL_SPACE = " \t\v\f"
_TRIGRAPH_ENDINGS = frozenset("=/\'()!<>-")


def _splice_c(text: str) -> str:
    """C 번역 단계의 line splicing과 Clang의 공백 확장을 해석한다."""
    output: list[str] = []
    index = 0
    while index < len(text):
        if text[index] == "\\":
            following = index + 1
            while following < len(text) and text[following] in _HORIZONTAL_SPACE:
                following += 1
            if following < len(text) and text[following] == "\n":
                index = following + 1
                continue
        output.append(text[index])
        index += 1
    return "".join(output)


def _validate_c(body: str) -> None:
    # trigraph 지원은 컴파일러 옵션별로 달라진다. 먼저 거부하여 주석/문자열의
    # 경계가 검증기와 컴파일러에서 다르게 해석되지 않게 한다.
    for index in range(len(body) - 2):
        if body[index:index + 2] == "??" and body[index + 2] in _TRIGRAPH_ENDINGS:
            raise ValueError("C 본문에는 trigraph를 사용할 수 없습니다.")
    normalized = body.replace("\r\n", "\n").replace("\r", "\n")
    if normalized.rstrip(_HORIZONTAL_SPACE).endswith("\\"):
        raise ValueError("C 본문 끝의 backslash는 템플릿 줄바꿈을 삼킬 수 없습니다.")
    text = _splice_c(normalized)
    depth = 0
    index = 0
    state = "code"
    quote = ""
    while index < len(text):
        character = text[index]
        pair = text[index:index + 2]
        if state == "line-comment":
            if character == "\n":
                state = "code"
            index += 1
            continue
        if state == "block-comment":
            if pair == "*/":
                state = "code"
                index += 2
            else:
                index += 1
            continue
        if state == "quoted":
            if character == "\n":
                raise ValueError("C 문자열/문자 리터럴이 줄바꿈 전에 끝나지 않았습니다.")
            if character == "\\":
                if index + 1 >= len(text):
                    raise ValueError("C 문자열/문자 리터럴의 escape가 끝나지 않았습니다.")
                index += 2
            elif character == quote:
                state = "code"
                index += 1
            else:
                index += 1
            continue
        if pair == "//":
            state = "line-comment"
            index += 2
        elif pair == "/*":
            state = "block-comment"
            index += 2
        elif character in ('"', "'"):
            state = "quoted"
            quote = character
            index += 1
        elif character == "#" or pair == "%:":
            raise ValueError("C 본문에는 전처리 토큰 # 또는 %:를 사용할 수 없습니다.")
        elif character == "{" or pair == "<%":
            depth += 1
            index += 2 if pair == "<%" else 1
        elif character == "}" or pair == "%>":
            if depth == 0:
                raise ValueError("C 본문이 템플릿의 함수 중괄호를 닫을 수 없습니다.")
            depth -= 1
            index += 2 if pair == "%>" else 1
        else:
            index += 1
    if state == "block-comment":
        raise ValueError("C block comment가 끝나지 않았습니다.")
    if state == "quoted":
        raise ValueError("C 문자열/문자 리터럴이 끝나지 않았습니다.")
    if depth:
        raise ValueError("C 본문의 중괄호가 닫히지 않았습니다.")


def _validate_wat(body: str) -> None:
    depth = 0
    comment_depth = 0
    index = 0
    state = "code"
    while index < len(body):
        character = body[index]
        pair = body[index:index + 2]
        if state == "line-comment":
            if character in "\r\n":
                state = "code"
            index += 1
            continue
        if state == "block-comment":
            if pair == "(;":
                comment_depth += 1
                index += 2
            elif pair == ";)":
                comment_depth -= 1
                index += 2
                if comment_depth == 0:
                    state = "code"
            else:
                index += 1
            continue
        if state == "string":
            if character == "\\":
                if index + 1 >= len(body):
                    raise ValueError("WAT 문자열의 escape가 끝나지 않았습니다.")
                index += 2
            elif character == '"':
                state = "code"
                index += 1
            else:
                index += 1
            continue
        if pair == ";;":
            state = "line-comment"
            index += 2
        elif pair == "(;":
            state = "block-comment"
            comment_depth = 1
            index += 2
        elif character == '"':
            state = "string"
            index += 1
        elif character == "(":
            depth += 1
            index += 1
        elif character == ")":
            if depth == 0:
                raise ValueError("WAT 본문이 템플릿의 함수 괄호를 닫을 수 없습니다.")
            depth -= 1
            index += 1
        else:
            index += 1
    if state == "block-comment":
        raise ValueError("WAT block comment가 끝나지 않았습니다.")
    if state == "string":
        raise ValueError("WAT 문자열이 끝나지 않았습니다.")
    if depth:
        raise ValueError("WAT 본문의 괄호가 닫히지 않았습니다.")


def validate_body(route: str, body: str) -> None:
    """본문 경계를 넘거나 템플릿을 삼키는 입력은 ValueError로 거부한다."""
    if route == "c":
        _validate_c(body)
    elif route == "wat":
        _validate_wat(body)
    else:
        raise ValueError(f"지원하지 않는 경로: {route}")
