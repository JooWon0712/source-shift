"""외부 도구의 출력과 제한 시간을 관리하고 초과 시 자식 트리를 종료한다."""

from __future__ import annotations

import math
import ctypes
import os
from pathlib import Path
import signal
import subprocess
from typing import Mapping, Sequence


_CLEANUP_TIMEOUT = 1.0


class _WindowsJob:
    """Windows의 도구 프로세스를 익명 job에 묶어 후손까지 종료한다."""

    def __init__(self) -> None:
        from ctypes import wintypes

        class BasicLimits(ctypes.Structure):
            _fields_ = [
                ("process_time", ctypes.c_int64), ("job_time", ctypes.c_int64),
                ("flags", wintypes.DWORD), ("min_working_set", ctypes.c_size_t),
                ("max_working_set", ctypes.c_size_t), ("active_process_limit", wintypes.DWORD),
                ("affinity", ctypes.c_size_t), ("priority", wintypes.DWORD),
                ("scheduling", wintypes.DWORD),
            ]

        class ExtendedLimits(ctypes.Structure):
            _fields_ = [("basic", BasicLimits), ("io_counters", ctypes.c_uint64 * 6),
                        ("process_memory", ctypes.c_size_t), ("job_memory", ctypes.c_size_t),
                        ("peak_process_memory", ctypes.c_size_t), ("peak_job_memory", ctypes.c_size_t)]

        self.api = ctypes.WinDLL("kernel32", use_last_error=True)
        self.api.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        self.api.CreateJobObjectW.restype = wintypes.HANDLE
        self.api.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        self.api.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        self.api.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        self.api.CloseHandle.argtypes = [wintypes.HANDLE]
        self.handle = self.api.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = ExtendedLimits()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.api.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            error = ctypes.WinError(ctypes.get_last_error())
            self.close()
            raise error

    def assign(self, process: subprocess.Popen[bytes]) -> None:
        if not self.api.AssignProcessToJobObject(self.handle, int(process._handle)):
            raise ctypes.WinError(ctypes.get_last_error())

    def terminate(self) -> None:
        self.api.TerminateJobObject(self.handle, 1)

    def close(self) -> None:
        if self.handle:
            self.api.CloseHandle(self.handle)
            self.handle = None


def _terminate_tree(process: subprocess.Popen[bytes], job: _WindowsJob | None = None) -> None:
    if job is not None:
        job.terminate()
    elif os.name == "nt":
        # 부모를 먼저 kill하면 taskkill이 자식 관계를 찾지 못할 수 있다.
        taskkill = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "taskkill.exe"
        try:
            subprocess.run(
                [str(taskkill), "/PID", str(process.pid), "/T", "/F"],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=_CLEANUP_TIMEOUT, creationflags=subprocess.CREATE_NO_WINDOW,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    if process.poll() is None:
        try:
            process.kill()
        except OSError:
            pass


def _finish_after_timeout(
    process: subprocess.Popen[bytes], expired: subprocess.TimeoutExpired,
) -> tuple[bytes, bytes]:
    try:
        return process.communicate(timeout=_CLEANUP_TIMEOUT)
    except subprocess.TimeoutExpired as cleanup:
        # 트리 종료가 실패하거나 Windows 부모가 먼저 끝났더라도 파이프를
        # 잡은 후손 때문에 communicate()를 무기한 기다리지 않는다.
        stdout = cleanup.output if cleanup.output is not None else expired.output
        stderr = cleanup.stderr if cleanup.stderr is not None else expired.stderr
        try:
            process.kill()
        except OSError:
            pass
        try:
            process.wait(timeout=_CLEANUP_TIMEOUT)
        except subprocess.TimeoutExpired:
            pass
        # reader thread가 붙잡은 Windows 파이프를 여기서 close하면 close도
        # 멈출 수 있다. daemon reader가 EOF를 받으면 스스로 정리하게 둔다.
        return stdout or b"", stderr or b""


def run_process(
    command: Sequence[str | os.PathLike[str]], *,
    cwd: str | os.PathLike[str] | None = None,
    env: Mapping[str, str] | None = None,
    timeout: float,
) -> subprocess.CompletedProcess[bytes]:
    """shell 없이 실행하고 bytes 출력을 반환한다. 초과 시 TimeoutExpired를 낸다.

    timeout 뒤 트리 정리와 파이프 수거에는 각각 짧은 별도 상한을 적용한다.
    Windows에서는 익명 job에 생성 직후 할당한다. 이는 제한된 외부 도구를
    관리하는 장치이며, 고의로 격리를 벗어나려는 코드를 위한 sandbox는 아니다.
    """
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("timeout은 유한한 양수여야 합니다.")
    job = _WindowsJob() if os.name == "nt" else None
    try:
        process = subprocess.Popen(
            command, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0,
            start_new_session=os.name != "nt",
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        try:
            if job is not None:
                job.assign(process)
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired as expired:
            _terminate_tree(process, job)
            stdout, stderr = _finish_after_timeout(process, expired)
            raise subprocess.TimeoutExpired(command, timeout, output=stdout, stderr=stderr) from None
        except BaseException:
            _terminate_tree(process, job)
            _finish_after_timeout(process, subprocess.TimeoutExpired(command, timeout))
            raise
        return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
    finally:
        if job is not None:
            job.close()
