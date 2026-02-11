"""Output adapters for routing download status to different display backends."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

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
    def on_task_completed(self, worker_id: int, task: Task, success: bool, error: str = ""): ...

    @abstractmethod
    def on_progress_update(self, completed: int, total: int, skipped: int): ...

    @abstractmethod
    def on_global_message(self, message: str): ...

    def on_task_request_id(self, worker_id: int, request_id: str):
        pass

    def on_task_cancelled(self, worker_id: int):
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
        pass

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


class CursesAdapter(OutputAdapter):
    """Routes events to the curses TUI."""

    def __init__(self, tui: CursesTUI):
        self._tui = tui

    def on_task_started(self, worker_id, task):
        self._tui.set_worker_cds_status(worker_id, "accepted")
        self._tui.set_worker_request_id(worker_id, "")
        self._tui.clear_worker_log(worker_id)
        self._tui.append_worker_log(worker_id, f"Started: {task.label}")

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

    def on_progress_update(self, completed, total, skipped):
        self._tui.update_progress(completed, total, skipped)

    def on_task_request_id(self, worker_id, request_id):
        self._tui.set_worker_request_id(worker_id, request_id)

    def on_task_cancelled(self, worker_id):
        self._tui.set_worker_cds_status(worker_id, "cancelled")
        self._tui.append_worker_log(worker_id, "Request cancelled")

    def on_global_message(self, message):
        self._tui.set_status_line(message)
