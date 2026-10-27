"""파일럿 데이터 계약과 참조 구현의 독립 검증."""

import itertools
import json
import runpy
import unittest

from source_shift import tasks


class TaskDatasetTests(unittest.TestCase):
    def test_all_pilot_tasks_exist_in_order(self):
        self.assertEqual([task.id for task in tasks.list_tasks()],
                         [f"P{index:02}" for index in range(1, 11)])
        for task in tasks.list_tasks():
            self.assertIs(tasks.get_task(task.id), task)
            self.assertEqual(len(task.public_cases), 3)
            self.assertTrue(task.private_cases)

    def test_private_inputs_are_disjoint_and_deterministic(self):
        before = [(task.id, task.private_cases) for task in tasks.list_tasks()]
        independent = runpy.run_path(tasks.__file__)
        after = [(task.id, task.private_cases) for task in independent["list_tasks"]()]
        # 독립 로딩한 dataclass는 다른 타입이므로 기본 값으로 비교한다.
        values = lambda rows: [(name, [(case.inputs, case.expected) for case in cases])
                               for name, cases in rows]
        self.assertEqual(values(before), values(after))
        for task in tasks.list_tasks():
            public = {case.inputs for case in task.public_cases}
            private = [case.inputs for case in task.private_cases]
            self.assertFalse(public.intersection(private), task.id)
            self.assertEqual(len(private), len(set(private)), task.id)
            if task.id != "P07":
                self.assertGreaterEqual(len(private), tasks.PRIVATE_RANDOM_CASES)

    def test_public_view_contains_only_public_fields(self):
        for task in tasks.list_tasks():
            view = task.public_view()
            self.assertEqual(set(view), {"id", "title", "specification", "public_cases"})
            self.assertEqual(view["public_cases"], [case.to_dict() for case in task.public_cases])
            serialized = json.dumps(view)
            self.assertNotIn("private", serialized)
            self.assertNotIn("private_cases", repr(task))
            # 반환 dict 변경이 원본 과제/예제에 영향을 주지 않는다.
            view["public_cases"].clear()
            self.assertEqual(len(task.public_cases), 3)

    def test_every_stored_case_obeys_domain_and_reference(self):
        for task in tasks.list_tasks():
            for case in task.public_cases + task.private_cases:
                with self.subTest(task=task.id, inputs=case.inputs):
                    self.assertEqual(case.expected, tasks.reference(task.id, *case.inputs))
                    self.assertTrue(-(2**31) <= case.expected < 2**31)

    def test_small_factorial_domain_is_exhaustive(self):
        task = tasks.get_task("P07")
        self.assertEqual({case.a for case in task.public_cases + task.private_cases}, set(range(13)))
        self.assertEqual(len(task.private_cases), 10)

    def test_required_boundaries_are_represented(self):
        expected = {
            "P01": [(-1000, -1000, -1000), (1000, 1000, 1000), (-1000, 0, 1000)],
            "P02": [(-1000, -1000, -1000), (1000, 1000, 1000), (0, 0, 0)],
            "P03": [(0, 0, 0), (-1000, -1000, 1000), (1000, -1000, 1000)],
            "P04": [(-1000, 0, 0), (0, 0, 0), (1000, 0, 0)],
            "P05": [(-1, 0, 0), (0, 0, 0), (1, 0, 0)],
            "P06": [(0, 0, 0), (1, 0, 0), (1000, 0, 0)],
            "P07": [(0, 0, 0), (1, 0, 0), (12, 0, 0)],
            "P08": [(0, 0, 0), (0, 10000, 0), (10000, 0, 0), (10000, 10000, 0)],
            "P09": [(0, 0, 0), (9, 0, 0), (10, 0, 0), (99, 0, 0), (100, 0, 0),
                    (999999999, 0, 0), (1000000000, 0, 0)],
            "P10": [(0, 0, 0), (1, 0, 0), (2, 0, 0), (49, 0, 0), (10000, 0, 0)],
        }
        for task_id, inputs in expected.items():
            task = tasks.get_task(task_id)
            actual = {case.inputs for case in task.public_cases + task.private_cases}
            self.assertTrue(set(inputs) <= actual, task_id)

    def test_reference_rejects_out_of_contract_inputs(self):
        invalid = {
            "P01": (1001, 0, 0), "P02": (0, -1001, 0), "P03": (0, 2, 1),
            "P04": (-1001, 0, 0), "P05": (0, 1, 0), "P06": (-1, 0, 0),
            "P07": (13, 0, 0), "P08": (0, 10001, 0),
            "P09": (1000000001, 0, 0), "P10": (10001, 0, 0),
        }
        for task_id, inputs in invalid.items():
            with self.subTest(task_id=task_id):
                with self.assertRaises(ValueError):
                    tasks.reference(task_id, *inputs)
        for value in (1.0, True, "1", None):
            with self.assertRaises(TypeError):
                tasks.reference("P01", value, 0, 0)
        with self.assertRaises(KeyError):
            tasks.get_task("P11")


class IndependentReferenceTests(unittest.TestCase):
    def test_sum_max_and_clamp_on_boundary_grid(self):
        values = (-1000, -999, -1, 0, 1, 999, 1000)
        for a, b, c in itertools.product(values, repeat=3):
            self.assertEqual(tasks.reference("P01", a, b, c), a + b + c)
            ordered = sorted((a, b, c))
            self.assertEqual(tasks.reference("P02", a, b, c), ordered[-1])
            if b <= c:
                expected = b if a < b else c if a > c else a
                self.assertEqual(tasks.reference("P03", a, b, c), expected)

    def test_abs_and_sign_over_full_domain(self):
        for a in range(-1000, 1001):
            self.assertEqual(tasks.reference("P04", a, 0, 0), -a if a < 0 else a)
            expected = -1 if a < 0 else 1 if a > 0 else 0
            self.assertEqual(tasks.reference("P05", a, 0, 0), expected)

    def test_integer_sum_and_factorial_over_full_domains(self):
        for n in range(1001):
            self.assertEqual(tasks.reference("P06", n, 0, 0), n * (n + 1) // 2)
        expected = 1
        for n in range(13):
            if n:
                expected *= n
            self.assertEqual(tasks.reference("P07", n, 0, 0), expected)

    def test_gcd_against_euclidean_algorithm(self):
        task = tasks.get_task("P08")
        for case in task.public_cases + task.private_cases:
            left, right = case.a, case.b
            while right:
                left, right = right, left % right
            self.assertEqual(case.expected, left)

    def test_digits_against_integer_division(self):
        task = tasks.get_task("P09")
        for case in task.public_cases + task.private_cases:
            value, expected = case.a, 1
            while value >= 10:
                value //= 10
                expected += 1
            self.assertEqual(case.expected, expected)

    def test_prime_over_full_domain_against_sieve(self):
        sieve = [True] * 10001
        sieve[0] = sieve[1] = False
        for candidate in range(2, 10001):
            if sieve[candidate]:
                for multiple in range(candidate * 2, 10001, candidate):
                    sieve[multiple] = False
        for a, expected in enumerate(sieve):
            self.assertEqual(tasks.reference("P10", a, 0, 0), int(expected), a)


if __name__ == "__main__":
    unittest.main()
