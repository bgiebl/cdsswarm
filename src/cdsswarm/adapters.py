"""Output adapters for routing download status to different display backends."""

from __future__ import annotations

import datetime
import threading
from abc import ABC, abstractmethod
from typing import IO, TYPE_CHECKING

from ._cds_utils import parse_cds_status

if TYPE_CHECKING:
    from .core import Task
    from .textual_app import CdsswarmApp


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


class TextualAdapter(OutputAdapter):
    """Routes events to the Textual TUI via call_from_thread."""

    def __init__(self, app: CdsswarmApp):
        self._app = app

    def _post(self, message):
        """Post a Textual Message from a worker thread."""
        self._app.call_from_thread(self._app.post_message, message)

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
            self._post(WorkerCdsStatus(worker_id, "successful"))
            self._post(WorkerMessage(worker_id, f"Completed: {task.label}"))
        else:
            self._post(WorkerCdsStatus(worker_id, "failed"))
            self._post(WorkerMessage(worker_id, f"Error: {error}"))
        self._post(WorkerFinished(worker_id))
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

    def on_task_server_progress(self, worker_id, progress):
        from .textual_app import WorkerServerProgress

        self._post(WorkerServerProgress(worker_id, progress))

    def on_task_file_size(self, worker_id, file_size):
        from .textual_app import WorkerFileSize

        self._post(WorkerFileSize(worker_id, file_size))

    def on_task_checksum_result(self, worker_id, passed, expected):
        from .textual_app import ShowChecksumDialog, WorkerChecksum

        self._post(WorkerChecksum(worker_id, passed))
        if not passed:
            result_event = threading.Event()
            result_holder: list[str] = []
            self._post(
                ShowChecksumDialog(
                    worker_id,
                    expected,
                    result_event,
                    result_holder,
                )
            )
            result_event.wait()
            return result_holder[0] if result_holder else "continue"
        return "continue"

    def on_task_server_timestamps(self, worker_id, created, started, finished):
        from .textual_app import WorkerServerTimestamps

        self._post(WorkerServerTimestamps(worker_id, created, started, finished))

    def on_task_dataset_title(self, worker_id, title):
        from .textual_app import WorkerDatasetTitle

        self._post(WorkerDatasetTitle(worker_id, title))

    def on_task_request_labels(self, worker_id, labels):
        from .textual_app import WorkerRequestLabels

        self._post(WorkerRequestLabels(worker_id, labels))

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
