"""Output adapters for routing download status to different display backends."""

from __future__ import annotations

import datetime
import sys
import threading
from abc import ABC, abstractmethod
from typing import IO, TYPE_CHECKING

from ._cds_utils import parse_cds_status
from .status import WorkerStatus

if TYPE_CHECKING:
    from .core import Task
    from .textual_app import CdsswarmApp

# 16 distinct ANSI styles: 6 colors × normal/bold + 4 bright colors.
# Covers up to 16 workers with unique visual identity; beyond that, cycles.
_WORKER_STYLES = [
    "34",  # blue
    "32",  # green
    "33",  # yellow
    "36",  # cyan
    "35",  # magenta
    "31",  # red
    "1;34",  # bold blue
    "1;32",  # bold green
    "1;33",  # bold yellow
    "1;36",  # bold cyan
    "1;35",  # bold magenta
    "1;31",  # bold red
    "94",  # bright blue
    "92",  # bright green
    "93",  # bright yellow
    "96",  # bright cyan
]

_CDS_STATUS_DESCRIPTIONS = {
    WorkerStatus.ACCEPTED: "request queued on CDS server",
    WorkerStatus.RUNNING: "server is processing request",
    WorkerStatus.SUCCESSFUL: "server finished, starting download",
    WorkerStatus.FAILED: "request failed on server",
    WorkerStatus.CANCELLED: "request cancelled",
}

# ANSI codes for CDS status colors (orange uses 256-color mode)
_CDS_STATUS_COLORS = {
    WorkerStatus.ACCEPTED: "33",  # yellow
    WorkerStatus.RUNNING: "38;5;208",  # orange
    WorkerStatus.SUCCESSFUL: "32",  # green
    WorkerStatus.FAILED: "31",  # red
    WorkerStatus.CANCELLED: "35",  # purple
}


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

    def on_task_file_size(self, worker_id: int, file_size: int):
        pass

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

    def on_task_hook_started(self, worker_id: int, command: str):
        pass

    def on_task_hook_finished(self, worker_id: int, success: bool, warning: str = ""):
        pass

    def on_tasks_initialized(self, tasks: list[Task], skipped_targets: set[str]):
        pass


class PlainTextAdapter(OutputAdapter):
    """Simple text output for script/non-interactive mode."""

    _PROGRESS_MILESTONES = frozenset({25, 50, 75, 100})

    def __init__(
        self,
        write_fn=None,
        use_color: bool | None = None,
        interactive: bool | None = None,
    ):
        self._write = write_fn or print
        self._lock = threading.Lock()
        self._done = 0
        self._total = 0
        self._last_status: dict[int, str] = {}
        self._last_qos: tuple[int, int, int] | None = None
        self._last_dl_milestone: dict[int, int] = {}
        self._worker_labels: dict[int, str] = {}
        if use_color is None:
            self._color = hasattr(sys.stdout, "isatty") and sys.stdout.isatty()
        else:
            self._color = use_color
        if interactive is None:
            self._interactive = hasattr(sys.stdin, "isatty") and sys.stdin.isatty()
        else:
            self._interactive = interactive

    def _worker_tag(self, worker_id: int) -> str:
        tag = f"[Worker {worker_id}]"
        if self._color:
            style = _WORKER_STYLES[worker_id % len(_WORKER_STYLES)]
            tag = f"\033[{style}m{tag}\033[0m"
        return tag

    def on_task_started(self, worker_id, task):
        with self._lock:
            self._last_status.pop(worker_id, None)
            self._last_dl_milestone.pop(worker_id, None)
            self._worker_labels[worker_id] = task.label
        self._write(f"  {self._worker_tag(worker_id)} starting {task.label}")

    def _status_text(self, cds_status: str) -> str:
        """Format a CDS status name with color and description."""
        desc = _CDS_STATUS_DESCRIPTIONS.get(cds_status, "")
        if self._color:
            color = _CDS_STATUS_COLORS.get(cds_status, "0")
            name = f"\033[{color}m{cds_status}\033[0m"
        else:
            name = cds_status
        if desc:
            return f"{name} — {desc}"
        return name

    def on_task_message(self, worker_id, message):
        cds_status = parse_cds_status(message)
        if cds_status:
            with self._lock:
                if self._last_status.get(worker_id) == cds_status:
                    return
                self._last_status[worker_id] = cds_status
                label = self._worker_labels.get(worker_id, "")
            status = self._status_text(cds_status)
            if label:
                self._write(f"  {self._worker_tag(worker_id)} {label}: {status}")
            else:
                self._write(f"  {self._worker_tag(worker_id)} {status}")

    def on_task_progress(self, worker_id, downloaded_bytes, total_bytes):
        if not total_bytes:
            return
        pct = int(downloaded_bytes * 100 / total_bytes)
        milestone = max(
            (m for m in self._PROGRESS_MILESTONES if pct >= m), default=None
        )
        if milestone is None:
            return
        with self._lock:
            if self._last_dl_milestone.get(worker_id) == milestone:
                return
            self._last_dl_milestone[worker_id] = milestone
            label = self._worker_labels.get(worker_id, "")
        done_mb = downloaded_bytes / (1024 * 1024)
        total_mb = total_bytes / (1024 * 1024)
        prefix = f"{label}: " if label else ""
        self._write(
            f"  {self._worker_tag(worker_id)} {prefix}downloading "
            f"{done_mb:.0f}/{total_mb:.0f} MB ({milestone}%)"
        )

    def _green(self, text: str) -> str:
        if self._color:
            return f"\033[32m{text}\033[0m"
        return text

    def on_task_completed(self, worker_id, task, success, error=""):
        with self._lock:
            done_count = self._done
            total_count = self._total
        if success:
            counter = self._green(f"[{done_count}/{total_count}]")
            done = self._green("done")
            self._write(f"  {counter} {task.label} {done}")
        else:
            self._write(f"  [{done_count}/{total_count}] {task.label} FAILED: {error}")

    def on_progress_update(self, completed, total, skipped):
        with self._lock:
            self._done = completed
            self._total = total

    def on_global_message(self, message):
        if self._color and "All downloads completed" in message:
            self._write(self._green(message))
        else:
            self._write(message)

    def on_task_hook_started(self, worker_id, command):
        with self._lock:
            label = self._worker_labels.get(worker_id, "")
        prefix = f"{label}: " if label else ""
        self._write(f"  {self._worker_tag(worker_id)} {prefix}running post-hook")

    def on_task_hook_finished(self, worker_id, success, warning=""):
        if not success:
            with self._lock:
                label = self._worker_labels.get(worker_id, "")
            prefix = f"{label}: " if label else ""
            msg = f"WARNING: post-hook failed: {warning}"
            if self._color:
                msg = f"\033[1;38;5;208m{msg}\033[0m"
            self._write(f"  {self._worker_tag(worker_id)} {prefix}{msg}")

    def on_qos_update(self, queued, running, limit):
        new_qos = (queued, running, limit)
        with self._lock:
            if new_qos == self._last_qos:
                return
            self._last_qos = new_qos
        if queued > 0 or running > 0:
            self._write(f"  [CDS server] {queued} queued | {running}/{limit} running")


class TextualAdapter(OutputAdapter):
    """Routes events to the Textual TUI via call_from_thread."""

    def __init__(self, app: CdsswarmApp):
        self._app = app

    def _post(self, message):
        """Post a Textual Message from a worker thread."""
        try:
            self._app.call_from_thread(self._app.post_message, message)
        except RuntimeError:
            pass  # App already shut down

    def on_tasks_initialized(self, tasks, skipped_targets):
        from .textual_app import TasksInitialized

        self._post(TasksInitialized(tasks, skipped_targets))

    def on_task_started(self, worker_id, task):
        from .textual_app import FileActive, WorkerStarted

        self._post(
            WorkerStarted(
                worker_id,
                task.label,
                task.dataset,
                task.request,
                task.target,
            )
        )
        self._post(FileActive(task.target, worker_id))

    def on_task_message(self, worker_id, message):
        from .textual_app import WorkerCdsStatus, WorkerMessage

        cds_status = parse_cds_status(message)
        if cds_status:
            self._post(WorkerCdsStatus(worker_id, cds_status))
        self._post(WorkerMessage(worker_id, message))

    def on_task_completed(self, worker_id, task, success, error=""):
        from .textual_app import (
            FileCompleted,
            WorkerCdsStatus,
            WorkerFinished,
            WorkerMessage,
        )

        if success:
            self._post(WorkerCdsStatus(worker_id, WorkerStatus.SUCCESSFUL))
            self._post(WorkerMessage(worker_id, f"Completed: {task.label}"))
        else:
            self._post(WorkerCdsStatus(worker_id, WorkerStatus.FAILED))
            self._post(WorkerMessage(worker_id, f"Error: {error}"))
        self._post(WorkerFinished(worker_id, success, error))
        self._post(FileCompleted(task.target, success, error))

    def on_progress_update(self, completed, total, skipped):
        from .textual_app import ProgressUpdate

        self._post(ProgressUpdate(completed, total, skipped))

    def on_task_request_id(self, worker_id, request_id):
        from .textual_app import WorkerRequestId

        self._post(WorkerRequestId(worker_id, request_id))

    def on_task_progress(self, worker_id, downloaded_bytes, total_bytes):
        from .textual_app import WorkerProgress

        self._post(WorkerProgress(worker_id, downloaded_bytes, total_bytes))

    def on_task_cancelled(self, worker_id):
        from .textual_app import WorkerCancelled

        self._post(WorkerCancelled(worker_id))

    def on_task_file_size(self, worker_id, file_size):
        from .textual_app import WorkerFileSize

        self._post(WorkerFileSize(worker_id, file_size))

    def on_task_server_timestamps(self, worker_id, created, started, finished):
        from .textual_app import WorkerServerTimestamps

        self._post(WorkerServerTimestamps(worker_id, created, started, finished))

    def on_task_dataset_title(self, worker_id, title):
        from .textual_app import WorkerDatasetTitle

        self._post(WorkerDatasetTitle(worker_id, title))

    def on_task_request_labels(self, worker_id, labels):
        from .textual_app import WorkerRequestLabels

        self._post(WorkerRequestLabels(worker_id, labels))

    def on_task_hook_started(self, worker_id, command):
        from .textual_app import WorkerMessage

        self._post(WorkerMessage(worker_id, f"Running post-hook: {command}"))

    def on_task_hook_finished(self, worker_id, success, warning=""):
        from .textual_app import WorkerMessage

        if not success:
            self._post(WorkerMessage(worker_id, f"Post-hook failed: {warning}"))

    def on_qos_update(self, queued, running, limit):
        from .textual_app import QosUpdate

        self._post(QosUpdate(queued, running, limit))

    def on_global_message(self, message):
        from .textual_app import GlobalMessage

        self._post(GlobalMessage(message))


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

    def on_task_file_size(self, worker_id, file_size):
        self._write(f"[worker {worker_id}] file size: {file_size}")
        self._inner.on_task_file_size(worker_id, file_size)

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

    def on_task_hook_started(self, worker_id, command):
        self._write(f"[worker {worker_id}] post-hook started: {command}")
        self._inner.on_task_hook_started(worker_id, command)

    def on_task_hook_finished(self, worker_id, success, warning=""):
        status = "ok" if success else f"FAILED: {warning}"
        self._write(f"[worker {worker_id}] post-hook finished: {status}")
        self._inner.on_task_hook_finished(worker_id, success, warning)

    def on_qos_update(self, queued, running, limit):
        self._write(f"qos: queued={queued} running={running} limit={limit}")
        self._inner.on_qos_update(queued, running, limit)
