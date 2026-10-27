"""SS-001 파일럿 과제와 모델에 공개하지 않는 결정적 평가 입력.

이 모듈은 채점기에서 사용한다. 생성/수정 프롬프트에는 ``Task.public_view``의
반환값만 전달하며 ``Task`` 전체나 ``private_cases``를 직렬화하지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from math import factorial, gcd, isqrt
from random import Random
from typing import Callable, Iterable


DATASET_VERSION = "ss-001-pilot-v1"
PRIVATE_RANDOM_SEED = 20261027
PRIVATE_RANDOM_CASES = 64

Inputs = tuple[int, int, int]


@dataclass(frozen=True)
class Case:
    a: int
    b: int
    c: int
    expected: int

    @property
    def inputs(self) -> Inputs:
        return self.a, self.b, self.c

    def to_dict(self) -> dict[str, object]:
        return {"inputs": list(self.inputs), "expected": self.expected}


@dataclass(frozen=True)
class Task:
    id: str
    title: str
    specification: str
    public_cases: tuple[Case, ...]
    private_cases: tuple[Case, ...] = field(repr=False)

    def public_view(self) -> dict[str, object]:
        """공개 명세와 예제만 복사하여 반환한다. 비공개 입력은 포함하지 않는다."""
        return {
            "id": self.id,
            "title": self.title,
            "specification": self.specification,
            "public_cases": [case.to_dict() for case in self.public_cases],
        }


def _prime(a: int, _b: int, _c: int) -> int:
    if a < 2:
        return 0
    return int(all(a % divisor for divisor in range(2, isqrt(a) + 1)))


_REFERENCES: dict[str, Callable[[int, int, int], int]] = {
    "P01": lambda a, b, c: sum((a, b, c)),
    "P02": lambda a, b, c: max(a, b, c),
    "P03": lambda a, b, c: min(max(a, b), c),
    "P04": lambda a, _b, _c: abs(a),
    "P05": lambda a, _b, _c: (a > 0) - (a < 0),
    "P06": lambda a, _b, _c: sum(range(a + 1)),
    "P07": lambda a, _b, _c: factorial(a),
    "P08": lambda a, b, _c: gcd(a, b),
    "P09": lambda a, _b, _c: len(str(a)),
    "P10": _prime,
}


def _validate_inputs(task_id: str, a: int, b: int, c: int) -> None:
    if any(type(value) is not int for value in (a, b, c)):
        raise TypeError("파일럿 입력은 세 개의 정수여야 합니다.")
    if task_id in ("P01", "P02", "P03"):
        valid = all(-1000 <= value <= 1000 for value in (a, b, c))
        if task_id == "P03":
            valid = valid and b <= c
    elif task_id in ("P04", "P05"):
        valid = -1000 <= a <= 1000 and b == c == 0
    elif task_id == "P06":
        valid = 0 <= a <= 1000 and b == c == 0
    elif task_id == "P07":
        valid = 0 <= a <= 12 and b == c == 0
    elif task_id == "P08":
        valid = 0 <= a <= 10000 and 0 <= b <= 10000 and c == 0
    elif task_id == "P09":
        valid = 0 <= a <= 1000000000 and b == c == 0
    elif task_id == "P10":
        valid = 0 <= a <= 10000 and b == c == 0
    else:
        raise KeyError(task_id)
    if not valid:
        raise ValueError(f"{task_id} 입력 계약을 벗어났습니다: {(a, b, c)!r}")


def reference(task_id: str, a: int, b: int, c: int) -> int:
    """계약 내 입력의 독립 Python 참조값을 계산한다."""
    _validate_inputs(task_id, a, b, c)
    return _REFERENCES[task_id](a, b, c)


def _case(task_id: str, inputs: Inputs) -> Case:
    return Case(*inputs, reference(task_id, *inputs))


def _random_input(task_id: str, rng: Random) -> Inputs:
    if task_id in ("P01", "P02"):
        return rng.randint(-1000, 1000), rng.randint(-1000, 1000), rng.randint(-1000, 1000)
    if task_id == "P03":
        lower, upper = sorted((rng.randint(-1000, 1000), rng.randint(-1000, 1000)))
        return rng.randint(-1000, 1000), lower, upper
    if task_id in ("P04", "P05"):
        return rng.randint(-1000, 1000), 0, 0
    if task_id == "P06":
        return rng.randint(0, 1000), 0, 0
    if task_id == "P08":
        return rng.randint(0, 10000), rng.randint(0, 10000), 0
    if task_id == "P09":
        return rng.randint(0, 1000000000), 0, 0
    if task_id == "P10":
        return rng.randint(0, 10000), 0, 0
    raise KeyError(task_id)


def _make_task(
    task_id: str,
    title: str,
    specification: str,
    public: Iterable[Inputs],
    boundaries: Iterable[Inputs],
) -> Task:
    public_inputs = tuple(public)
    seen = set(public_inputs)
    private_inputs: list[Inputs] = []
    for inputs in boundaries:
        if inputs not in seen:
            private_inputs.append(inputs)
            seen.add(inputs)
    # P07의 작은 정의역은 공개 예제를 제외한 모든 입력으로 평가한다.
    if task_id != "P07":
        rng = Random(PRIVATE_RANDOM_SEED + int(task_id[1:]))
        count = 0
        while count < PRIVATE_RANDOM_CASES:
            inputs = _random_input(task_id, rng)
            if inputs not in seen:
                private_inputs.append(inputs)
                seen.add(inputs)
                count += 1
    return Task(
        task_id,
        title,
        specification,
        tuple(_case(task_id, inputs) for inputs in public_inputs),
        tuple(_case(task_id, inputs) for inputs in private_inputs),
    )


def _unary(values: Iterable[int]) -> tuple[Inputs, ...]:
    return tuple((value, 0, 0) for value in values)


_TASKS = (
    _make_task(
        "P01", "세 정수의 합",
        "a, b, c는 각각 -1000 이상 1000 이하 정수다. 세 정수의 합을 반환한다.",
        ((1, 2, 3), (-4, 1, 3), (0, 0, 0)),
        ((-1000, -1000, -1000), (1000, 1000, 1000), (-1000, 0, 1000),
         (0, -1000, 1000), (1000, -1000, 0), (-1, 0, 0), (0, 0, 1)),
    ),
    _make_task(
        "P02", "세 정수의 최댓값",
        "a, b, c는 각각 -1000 이상 1000 이하 정수다. 최댓값을 반환하며 동률을 허용한다.",
        ((2, 7, 4), (-5, -2, -9), (3, 3, 1)),
        ((-1000, -1000, -1000), (1000, 1000, 1000), (0, 0, 0),
         (5, 1, 5), (1, 5, 5), (1000, 0, -1000), (-1000, 1000, 0),
         (0, -1000, 1000), (-3, -2, -1), (-1, -3, -2)),
    ),
    _make_task(
        "P03", "범위 제한",
        "a, b, c는 각각 -1000 이상 1000 이하 정수이며 b <= c다. "
        "a가 b보다 작으면 b, c보다 크면 c, 그 외에는 a를 반환한다. b = c도 허용한다.",
        ((5, 0, 10), (-5, 0, 10), (20, 0, 10)),
        ((-1000, -1000, 1000), (1000, -1000, 1000), (0, 0, 0),
         (-1000, 0, 0), (1000, 0, 0), (-5, -10, -1), (-10, -10, -1),
         (-1, -10, -1), (-11, -10, -1), (0, -10, -1),
         (1000, 1000, 1000), (-1000, -1000, -1000)),
    ),
    _make_task(
        "P04", "절댓값",
        "a는 -1000 이상 1000 이하 정수이며 b = c = 0이다. a의 절댓값을 반환한다.",
        _unary((-7, 0, 8)), _unary((-1000, -999, -1, 1, 999, 1000)),
    ),
    _make_task(
        "P05", "부호 판정",
        "a는 -1000 이상 1000 이하 정수이며 b = c = 0이다. "
        "a가 음수면 -1, 0이면 0, 양수면 1을 반환한다.",
        _unary((-8, 0, 13)), _unary((-1000, -999, -1, 1, 999, 1000)),
    ),
    _make_task(
        "P06", "0부터 n까지 정수 합",
        "a = n은 0 이상 1000 이하 정수이며 b = c = 0이다. "
        "0부터 n까지 양 끝을 포함한 모든 정수의 합을 반환한다.",
        _unary((0, 1, 5)), _unary((2, 3, 10, 499, 500, 999, 1000)),
    ),
    _make_task(
        "P07", "팩토리얼",
        "a = n은 0 이상 12 이하 정수이며 b = c = 0이다. n!을 반환한다. 0! = 1이다.",
        _unary((0, 1, 5)), _unary(range(13)),
    ),
    _make_task(
        "P08", "최대공약수",
        "a, b는 각각 0 이상 10000 이하 정수이며 c = 0이다. "
        "a와 b의 최대공약수를 반환한다. gcd(0, 0) = 0이다.",
        ((12, 18, 0), (0, 7, 0), (0, 0, 0)),
        ((1, 0, 0), (0, 1, 0), (10000, 0, 0), (0, 10000, 0),
         (10000, 10000, 0), (1, 10000, 0), (10000, 1, 0),
         (9999, 10000, 0), (9999, 9999, 0), (18, 12, 0),
         (48, 18, 0), (17, 19, 0), (6765, 4181, 0)),
    ),
    _make_task(
        "P09", "십진수 자릿수",
        "a는 0 이상 1000000000 이하 정수이며 b = c = 0이다. "
        "십진수 표현의 자릿수를 반환한다. 0의 자릿수는 1이다.",
        _unary((0, 42, 100)),
        _unary((1, 8, 9, *(value for power in range(1, 10)
                           for value in (10**power - 1, 10**power)
                           if value <= 1000000000), 999999999)),
    ),
    _make_task(
        "P10", "소수 판정",
        "a는 0 이상 10000 이하 정수이며 b = c = 0이다. a가 소수면 1, 아니면 0을 반환한다. "
        "0과 1은 소수가 아니다.",
        _unary((1, 2, 9)),
        _unary((0, 3, 4, 5, 6, 7, 10, 11, 25, 49, 97, 121, 169,
                9973, 9999, 10000, 9409, 9801)),
    ),
)

_TASK_BY_ID = {task.id: task for task in _TASKS}


def list_tasks() -> tuple[Task, ...]:
    """P01부터 P10까지 고정된 순서로 반환한다."""
    return _TASKS


def get_task(task_id: str) -> Task:
    return _TASK_BY_ID[task_id]
