"""Output adapters for routing download status to different display backends."""

from __future__ import annotations

import datetime
from abc import ABC, abstractmethod
from typing import IO, TYPE_CHECKING

from ._cds_utils import parse_cds_status

if TYPE_CHECKING:
    from .core import Task
    from .tui import CursesTUI


class OutputAdapter(ABC):
    """Interface for receiving download progress events."""

    @abstractmethod
    def on_task_started(self, worker_id: int, task: Task): ...

    @abstractmethod
    def on_task_message(self, worker_id: int, message: str): ...

    @abstractmethod
    def on_task_completed(
        self, worker_id: int, task: Task, success: bool, error: str = ""
    ): ...

    @abstractmethod
    def on_progress_update(self, completed: int, total: int, skipped: int): ...

    @abstractmethod
    def on_global_message(self, message: str): ...

    def on_task_request_id(self, worker_id: int, request_id: str):
        pass

    def on_task_progress(self, worker_id: int, downloaded_bytes: int, total_bytes: int):
        pass

    def on_task_cancelled(self, worker_id: int):
        pass

    def on_task_server_progress(self, worker_id: int, progress: int):
        pass

    def on_task_file_size(self, worker_id: int, file_size: int):
        pass

    def on_task_checksum_result(
        self, worker_id: int, passed: bool, expected: str
    ) -> str:
        """Handle checksum result. Returns 'continue' or 'retry'."""
        return "continue"

    def on_task_server_timestamps(
        self, worker_id: int, created: str, started: str, finished: str
    ):
        pass

    def on_task_dataset_title(self, worker_id: int, title: str):
        pass

    def on_task_request_labels(self, worker_id: int, labels: dict):
        pass

    def on_qos_update(self, queued: int, running: int, limit: int):
        pass

    def on_tasks_initialized(self, tasks: list[Task], skipped_targets: set[str]):
        pass


class PlainTextAdapter(OutputAdapter):
    """Simple text output for script/non-interactive mode."""

    def __init__(self, write_fn=None):
        self._write = write_fn or print
        self._done = 0
        self._total = 0

    def on_task_started(self, worker_id, task):
        pass

    def on_task_message(self, worker_id, message):
        cds_status = parse_cds_status(message)
        if cds_status:
            self._write(f"  [worker {worker_id}] status: {cds_status}")

    def on_task_completed(self, worker_id, task, success, error=""):
        if success:
            self._write(f"  [{self._done}/{self._total}] {task.label} done")
        else:
            self._write(f"  [{self._done}/{self._total}] {task.label} FAILED: {error}")

    def on_progress_update(self, completed, total, skipped):
        self._done = completed
        self._total = total

    def on_global_message(self, message):
        self._write(message)

    def on_task_checksum_result(self, worker_id, passed, expected):
        if not passed:
            self._write(
                f"  [worker {worker_id}] WARNING: checksum mismatch "
                f"(expected {expected})"
            )
        return "continue"  # Non-interactive: always continue

    def on_qos_update(self, queued, running, limit):
        if queued > 0 or running > 0:
            self._write(f"  CDS Server: {queued} queued | {running}/{limit} running")


class CursesAdapter(OutputAdapter):
    """Routes events to the curses TUI."""

    def __init__(self, tui: CursesTUI):
        self._tui = tui

    def on_tasks_initialized(self, tasks, skipped_targets):
        self._tui.init_file_list(tasks, skipped_targets)

    def on_task_started(self, worker_id, task):
        self._tui.set_worker_cds_status(worker_id, "accepted")
        self._tui.set_worker_request_id(worker_id, "")
        self._tui.clear_worker_log(worker_id)
        self._tui.set_worker_filename(worker_id, task.label)
        self._tui.set_worker_task_info(
            worker_id, task.dataset, task.request, task.target
        )
        self._tui.append_worker_log(worker_id, f"Started: {task.label}")
        self._tui.set_file_active(task.target, worker_id)

    def on_task_message(self, worker_id, message):
        cds_status = parse_cds_status(message)
        if cds_status:
            self._tui.set_worker_cds_status(worker_id, cds_status)
        self._tui.append_worker_log(worker_id, message)

    def on_task_completed(self, worker_id, task, success, error=""):
        if success:
            self._tui.set_worker_cds_status(worker_id, "successful")
            self._tui.append_worker_log(worker_id, f"Completed: {task.label}")
        else:
            self._tui.set_worker_cds_status(worker_id, "failed")
            self._tui.append_worker_log(worker_id, f"Error: {error}")
        self._tui.set_worker_finished(worker_id)
        self._tui.set_file_completed(task.target, success, error)

    def on_progress_update(self, completed, total, skipped):
        self._tui.update_progress(completed, total, skipped)

    def on_task_request_id(self, worker_id, request_id):
        self._tui.set_worker_request_id(worker_id, request_id)

    def on_task_progress(self, worker_id, downloaded_bytes, total_bytes):
        self._tui.update_worker_progress(worker_id, downloaded_bytes, total_bytes)

    def on_task_cancelled(self, worker_id):
        self._tui.set_worker_cds_status(worker_id, "cancelled")
        self._tui.append_worker_log(worker_id, "Request cancelled")

    def on_task_server_progress(self, worker_id, progress):
        self._tui.set_worker_server_progress(worker_id, progress)

    def on_task_file_size(self, worker_id, file_size):
        self._tui.set_worker_file_size(worker_id, file_size)

    def on_task_checksum_result(self, worker_id, passed, expected):
        self._tui.set_worker_checksum_result(worker_id, passed)
        if not passed:
            result = self._tui.show_checksum_dialog(worker_id, expected)
            return result or "continue"
        return "continue"

    def on_task_server_timestamps(self, worker_id, created, started, finished):
        self._tui.set_worker_server_timestamps(worker_id, created, started, finished)

    def on_task_dataset_title(self, worker_id, title):
        self._tui.set_worker_dataset_title(worker_id, title)

    def on_task_request_labels(self, worker_id, labels):
        self._tui.set_worker_request_labels(worker_id, labels)

    def on_qos_update(self, queued, running, limit):
        self._tui.set_qos_data(queued, running, limit)

    def on_global_message(self, message):
        self._tui.set_status_line(message)


class LoggingAdapter(OutputAdapter):
    """Decorator that logs all adapter events to a file, then delegates."""

    def __init__(self, inner: OutputAdapter, log_file: IO[str]):
        self._inner = inner
        self._log_file = log_file

    def _write(self, line: str):
        ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._log_file.write(f"[{ts}] {line}\n")
        self._log_file.flush()

    def on_tasks_initialized(self, tasks, skipped_targets):
        self._write(
            f"tasks initialized: {len(tasks)} total, {len(skipped_targets)} cached"
        )
        self._inner.on_tasks_initialized(tasks, skipped_targets)

    def on_task_started(self, worker_id, task):
        self._write(f"[worker {worker_id}] started: {task.label}")
        self._inner.on_task_started(worker_id, task)

    def on_task_message(self, worker_id, message):
        self._write(f"[worker {worker_id}] {message}")
        self._inner.on_task_message(worker_id, message)

    def on_task_completed(self, worker_id, task, success, error=""):
        status = "ok" if success else f"FAILED: {error}"
        self._write(f"[worker {worker_id}] completed: {task.label} ({status})")
        self._inner.on_task_completed(worker_id, task, success, error)

    def on_progress_update(self, completed, total, skipped):
        self._write(f"progress: {completed}/{total} (skipped {skipped})")
        self._inner.on_progress_update(completed, total, skipped)

    def on_global_message(self, message):
        self._write(message)
        self._inner.on_global_message(message)

    def on_task_request_id(self, worker_id, request_id):
        self._write(f"[worker {worker_id}] request_id: {request_id}")
        self._inner.on_task_request_id(worker_id, request_id)

    def on_task_progress(self, worker_id, downloaded_bytes, total_bytes):
        # Skip logging — too noisy
        self._inner.on_task_progress(worker_id, downloaded_bytes, total_bytes)

    def on_task_cancelled(self, worker_id):
        self._write(f"[worker {worker_id}] cancelled")
        self._inner.on_task_cancelled(worker_id)

    def on_task_server_progress(self, worker_id, progress):
        self._write(f"[worker {worker_id}] server progress: {progress}%")
        self._inner.on_task_server_progress(worker_id, progress)

    def on_task_file_size(self, worker_id, file_size):
        self._write(f"[worker {worker_id}] file size: {file_size}")
        self._inner.on_task_file_size(worker_id, file_size)

    def on_task_checksum_result(self, worker_id, passed, expected):
        status = "passed" if passed else "FAILED"
        self._write(f"[worker {worker_id}] checksum {status} (expected {expected})")
        return self._inner.on_task_checksum_result(worker_id, passed, expected)

    def on_task_server_timestamps(self, worker_id, created, started, finished):
        self._write(
            f"[worker {worker_id}] server timestamps: "
            f"created={created} started={started} finished={finished}"
        )
        self._inner.on_task_server_timestamps(worker_id, created, started, finished)

    def on_task_dataset_title(self, worker_id, title):
        self._write(f"[worker {worker_id}] dataset: {title}")
        self._inner.on_task_dataset_title(worker_id, title)

    def on_task_request_labels(self, worker_id, labels):
        self._write(f"[worker {worker_id}] labels: {labels}")
        self._inner.on_task_request_labels(worker_id, labels)

    def on_qos_update(self, queued, running, limit):
        self._write(f"qos: queued={queued} running={running} limit={limit}")
        self._inner.on_qos_update(queued, running, limit)
