"""실제 자식 프로세스와 inherited pipe를 사용해 실행 제한을 검증한다."""

import ctypes
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from time import monotonic, sleep
import unittest

from source_shift.processes import run_process


def is_running(pid: int) -> bool:
    if os.name == "nt":
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
        kernel32.OpenProcess.restype = ctypes.c_void_p
        kernel32.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = kernel32.OpenProcess(0x1000, 0, pid)
        if not handle:
            return False
        try:
            code = ctypes.c_uint32()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                raise ctypes.WinError(ctypes.get_last_error())
            return code.value == 259
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    # killpg가 성공했어도 init이 수거하기 전 잠시 zombie일 수 있다.
    status = Path(f"/proc/{pid}/stat")
    if status.exists() and status.read_text().split(") ", 1)[1].startswith("Z"):
        return False
    return True


class ProcessTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1] / ".runs" / "test-temp"
        root.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=root)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_returns_output_error_exit_code_and_working_directory(self):
        env = os.environ.copy()
        env["SOURCE_SHIFT_PROCESS_TEST"] = "configured"
        result = run_process(
            [sys.executable, "-c", "import os,sys; print(os.getcwd()); print(os.environ['SOURCE_SHIFT_PROCESS_TEST']); print('error', file=sys.stderr); sys.exit(3)"],
            cwd=self.root, env=env, timeout=5,
        )
        self.assertEqual(result.returncode, 3)
        self.assertIn(str(self.root).encode(), result.stdout)
        self.assertIn(b"configured", result.stdout)
        self.assertIn(b"error", result.stderr)

    def test_timeout_preserves_output_and_kills_inheriting_child(self):
        self.check_inheriting_child_timeout(parent_stays_alive=True)

    def test_exited_parent_with_open_child_pipe_is_still_bounded(self):
        self.check_inheriting_child_timeout(parent_stays_alive=False)

    def check_inheriting_child_timeout(self, parent_stays_alive):
        pid_file = self.root / "child-pid.txt"
        script = (
            "import pathlib, subprocess, sys, time\n"
            "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
            "pathlib.Path(sys.argv[1]).write_text(str(child.pid))\n"
            "print('parent-output', flush=True)\n"
            "print('parent-error', file=sys.stderr, flush=True)\n"
            "if sys.argv[2] == 'sleep': time.sleep(60)\n"
        )
        started = monotonic()
        try:
            with self.assertRaises(subprocess.TimeoutExpired) as raised:
                run_process([sys.executable, "-c", script, str(pid_file),
                             "sleep" if parent_stays_alive else "exit"], timeout=0.75)
            self.assertLess(monotonic() - started, 5)
            self.assertIn(b"parent-output", raised.exception.stdout)
            self.assertIn(b"parent-error", raised.exception.stderr)
            self.assertEqual(raised.exception.timeout, 0.75)
            self.assertTrue(pid_file.exists(), "초과 전에 자식 프로세스가 시작돼야 한다.")
            pid = int(pid_file.read_text())
            deadline = monotonic() + 1
            while is_running(pid) and monotonic() < deadline:
                sleep(0.02)
            self.assertFalse(is_running(pid), "시간 초과 뒤 자식이 남아 있다.")
        finally:
            if pid_file.exists():
                pid = int(pid_file.read_text())
                if is_running(pid):
                    if os.name == "nt":
                        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=2)
                    else:
                        os.kill(pid, 9)

    def test_rejects_invalid_timeout_before_launch(self):
        for timeout in (0, -1, float("nan"), float("inf")):
            with self.subTest(timeout=timeout), self.assertRaises(ValueError):
                run_process([sys.executable, "-c", "pass"], timeout=timeout)


if __name__ == "__main__":
    unittest.main()
