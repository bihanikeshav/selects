"""Cancellable subprocess execution and a one-heavy-job engine."""
from __future__ import annotations

import os
import signal
import subprocess
import threading
import uuid
from concurrent.futures import CancelledError, Future, ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Callable, Literal


class JobCancelled(RuntimeError):
    """Raised when a running media job is cancelled."""


class JobProcessError(RuntimeError):
    """Raised when a subprocess exits unsuccessfully."""


JobState = Literal["queued", "running", "completed", "failed", "cancelled"]


@dataclass(frozen=True)
class FFmpegProgress:
    out_time_sec: float | None
    percent: float | None
    speed: str | None
    status: str | None


@dataclass(frozen=True)
class ProcessResult:
    returncode: int
    stdout: str
    stderr: str


def parse_progress_line(
    line: str,
    *,
    duration_sec: float | None = None,
    state: dict[str, str] | None = None,
) -> FFmpegProgress | None:
    """Parse one line from FFmpeg's ``-progress pipe:1`` protocol."""
    if "=" not in line:
        return None
    key, value = line.strip().split("=", 1)
    if state is not None:
        state[key] = value
    if key not in {"out_time_ms", "progress", "speed"}:
        return None
    raw_time = (state or {}).get("out_time_ms")
    try:
        out_time = float(raw_time) / 1_000_000.0 if raw_time is not None else None
    except ValueError:
        out_time = None
    percent = None
    if duration_sec and out_time is not None:
        percent = max(0.0, min(100.0, out_time / duration_sec * 100.0))
    return FFmpegProgress(out_time, percent, (state or {}).get("speed"), (state or {}).get("progress"))


def _popen_kwargs() -> dict[str, Any]:
    if os.name == "nt":
        return {"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)}
    return {"start_new_session": True}


def terminate_process_group(process: Any, *, grace_sec: float = 2.0) -> None:
    """Terminate a child process and its media descendants."""
    if process.poll() is not None:
        return
    if os.name == "nt":
        try:
            process.send_signal(getattr(signal, "CTRL_BREAK_EVENT", signal.SIGTERM))
        except (AttributeError, OSError):
            process.terminate()
    else:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except (OSError, AttributeError):
            process.terminate()
    try:
        process.wait(timeout=grace_sec)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=grace_sec)


def run_process(
    command: list[str] | tuple[str, ...],
    *,
    duration_sec: float | None = None,
    on_progress: Callable[[FFmpegProgress], None] | None = None,
    cancel_event: threading.Event | None = None,
    cwd: str | os.PathLike[str] | None = None,
    env: dict[str, str] | None = None,
) -> ProcessResult:
    """Run an argument-array process with FFmpeg progress and cancellation."""
    process = subprocess.Popen(
        list(command),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        cwd=cwd,
        env=env,
        **_popen_kwargs(),
    )
    stderr_lines: list[str] = []

    def read_stderr() -> None:
        if process.stderr is not None:
            stderr_lines.extend(process.stderr.readlines())

    stderr_thread = threading.Thread(target=read_stderr, daemon=True)
    stderr_thread.start()
    cancel_watcher_stop = threading.Event()

    def watch_cancel() -> None:
        while not cancel_watcher_stop.wait(0.05):
            if cancel_event is not None and cancel_event.is_set():
                if process.poll() is None:
                    terminate_process_group(process)
                return

    cancel_watcher = threading.Thread(target=watch_cancel, daemon=True)
    cancel_watcher.start()
    progress_state: dict[str, str] = {}
    stdout_lines: list[str] = []
    try:
        if process.stdout is not None:
            for line in process.stdout:
                stdout_lines.append(line)
                update = parse_progress_line(line, duration_sec=duration_sec, state=progress_state)
                if update is not None and on_progress is not None:
                    on_progress(update)
                if cancel_event is not None and cancel_event.is_set():
                    terminate_process_group(process)
                    raise JobCancelled("media process cancelled")
        returncode = process.wait()
        stderr_thread.join(timeout=2.0)
    except BaseException:
        if process.poll() is None:
            terminate_process_group(process)
        cancel_watcher_stop.set()
        cancel_watcher.join(timeout=1.0)
        stderr_thread.join(timeout=2.0)
        raise
    cancel_watcher_stop.set()
    cancel_watcher.join(timeout=1.0)
    if cancel_event is not None and cancel_event.is_set():
        raise JobCancelled("media process cancelled")
    result = ProcessResult(returncode, "".join(stdout_lines), "".join(stderr_lines))
    if returncode != 0:
        raise JobProcessError(result.stderr.strip() or f"process exited with status {returncode}")
    return result


@dataclass(frozen=True)
class JobSnapshot:
    id: str
    kind: str
    state: JobState
    progress: float | None
    message: str | None
    result: Any = None
    error: str | None = None


class JobContext:
    def __init__(self, engine: "HeavyJobEngine", job_id: str, cancel_event: threading.Event) -> None:
        self._engine = engine
        self.job_id = job_id
        self.cancel_event = cancel_event

    def report(self, progress: float | None = None, message: str | None = None) -> None:
        self._engine._report(self.job_id, progress, message)

    def check_cancelled(self) -> None:
        if self.cancel_event.is_set():
            raise JobCancelled("media job cancelled")

    def run_process(self, command: list[str] | tuple[str, ...], **kwargs: Any) -> ProcessResult:
        kwargs.setdefault("cancel_event", self.cancel_event)
        kwargs.setdefault("on_progress", self._report_progress)
        return run_process(command, **kwargs)

    def _report_progress(self, progress: FFmpegProgress) -> None:
        self.report(progress.percent, progress.status)


@dataclass
class _Job:
    snapshot: JobSnapshot
    cancel_event: threading.Event
    future: Future[Any] | None = None


class HeavyJobEngine:
    """Serialize heavyweight media work while allowing queued cancellation."""

    def __init__(self) -> None:
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="selects-media")
        self._lock = threading.RLock()
        self._jobs: dict[str, _Job] = {}
        self._closed = False

    def submit(self, work: Callable[[JobContext], Any], *, kind: str = "media") -> str:
        if not callable(work):
            raise TypeError("work must be callable")
        job_id = uuid.uuid4().hex
        cancel_event = threading.Event()
        with self._lock:
            if self._closed:
                raise RuntimeError("job engine is shut down")
            self._jobs[job_id] = _Job(
                JobSnapshot(job_id, kind, "queued", None, None),
                cancel_event,
            )
            future = self._executor.submit(self._execute, job_id, work, cancel_event)
            self._jobs[job_id].future = future
        return job_id

    def submit_export(self, source: str, destination: str, recipe: Any, **kwargs: Any) -> str:
        from .export import run_export

        return self.submit(
            lambda context: run_export(
                source,
                destination,
                recipe,
                process_runner=context.run_process,
                on_progress=context._report_progress,
                cancel_event=context.cancel_event,
                **kwargs,
            ),
            kind="export",
        )

    def _execute(self, job_id: str, work: Callable[[JobContext], Any], cancel_event: threading.Event) -> Any:
        self._set_snapshot(job_id, state="running")
        context = JobContext(self, job_id, cancel_event)
        try:
            context.check_cancelled()
            result = work(context)
        except JobCancelled as exc:
            self._set_snapshot(job_id, state="cancelled", error=str(exc))
            return None
        except BaseException as exc:
            self._set_snapshot(job_id, state="failed", error=str(exc))
            return None
        self._set_snapshot(job_id, state="completed", progress=100.0, result=result)
        return result

    def _set_snapshot(self, job_id: str, **changes: Any) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job.snapshot = JobSnapshot(**{**job.snapshot.__dict__, **changes})

    def _report(self, job_id: str, progress: float | None, message: str | None) -> None:
        self._set_snapshot(job_id, progress=progress, message=message)

    def snapshot(self, job_id: str) -> JobSnapshot:
        with self._lock:
            return self._jobs[job_id].snapshot

    def cancel(self, job_id: str) -> bool:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.snapshot.state in {"completed", "failed", "cancelled"}:
                return False
            job.cancel_event.set()
            if job.snapshot.state == "queued" and job.future is not None:
                job.future.cancel()
                job.snapshot = JobSnapshot(**{**job.snapshot.__dict__, "state": "cancelled", "error": "cancelled before start"})
            return True

    def wait(self, job_id: str, timeout: float | None = None) -> JobSnapshot:
        future = self._jobs[job_id].future
        if future is not None:
            try:
                future.result(timeout=timeout)
            except CancelledError:
                pass
        return self.snapshot(job_id)

    def shutdown(self, wait: bool = True) -> None:
        with self._lock:
            self._closed = True
        self._executor.shutdown(wait=wait, cancel_futures=True)


JobEngine = HeavyJobEngine
