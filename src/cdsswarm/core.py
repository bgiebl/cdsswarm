"""Core download engine: Task, Result, and SwarmDownloader."""

from __future__ import annotations

import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field

import cdsapi

from ._cds_utils import (
    cancel_cds_request,
    install_progress_router,
    parse_request_id,
    silence_loggers,
    uninstall_progress_router,
)
from .adapters import OutputAdapter


@dataclass
class Task:
    """A single CDS API download request.

    Args:
        dataset: CDS dataset name (e.g. "reanalysis-era5-single-levels").
        request: Request parameters dict, same format as cdsapi.Client.retrieve().
        target: Local file path to save the downloaded data to.
    """
    dataset: str
    request: dict
    target: str

    @property
    def label(self) -> str:
        return os.path.basename(self.target)


@dataclass
class Result:
    """Outcome of a single download task."""
    task: Task
    success: bool
    error: str = ""


@dataclass
class _WorkerState:
    """Mutable state shared between the download executor and callbacks."""
    worker_id_map: dict = field(default_factory=dict)
    task_worker_map: dict = field(default_factory=dict)
    active_requests: dict = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)
    next_id: list = field(default_factory=lambda: [0])


class SwarmDownloader:
    """Concurrent CDS API downloader.

    Args:
        tasks: List of Task objects to download.
        adapter: OutputAdapter for progress display.
        num_workers: Number of parallel download workers.
        skip_existing: Skip tasks whose target file already exists.
    """

    def __init__(
        self,
        tasks: list[Task],
        adapter: OutputAdapter,
        num_workers: int = 4,
        skip_existing: bool = True,
    ):
        self._all_tasks = list(tasks)
        self._adapter = adapter
        self._num_workers = num_workers
        self._skip_existing = skip_existing

    def run(self) -> list[Result] | None:
        """Execute all downloads. Returns list of Results, or None if interrupted."""
        if self._skip_existing:
            pending = [t for t in self._all_tasks if not os.path.exists(t.target)]
            skipped = len(self._all_tasks) - len(pending)
        else:
            pending = list(self._all_tasks)
            skipped = 0

        if skipped:
            self._adapter.on_global_message(
                f"{skipped}/{len(self._all_tasks)} files already exist, skipping"
            )
        if not pending:
            self._adapter.on_global_message("All files already downloaded.")
            return [Result(task=t, success=True) for t in self._all_tasks]

        self._adapter.on_global_message(
            f"Downloading {len(pending)} files ({self._num_workers} workers)"
        )
        self._adapter.on_progress_update(0, len(pending), skipped)

        state = _WorkerState()
        results: list[Result] = []
        # Pre-populate results for skipped tasks
        if self._skip_existing:
            for t in self._all_tasks:
                if os.path.exists(t.target):
                    results.append(Result(task=t, success=True))

        completed = 0
        patched = install_progress_router(self._adapter, state.worker_id_map, state.lock)
        pool = ThreadPoolExecutor(max_workers=self._num_workers)
        futures = {
            pool.submit(self._download_one, task, state): task
            for task in pending
        }

        try:
            for future in as_completed(futures):
                task = futures[future]
                completed += 1
                wid = state.task_worker_map.get(task.target, 0)
                try:
                    future.result()
                    self._adapter.on_task_completed(wid, task, True)
                    results.append(Result(task=task, success=True))
                except Exception as e:
                    self._adapter.on_task_completed(wid, task, False, str(e))
                    results.append(Result(task=task, success=False, error=str(e)))
                self._adapter.on_progress_update(completed, len(pending), skipped)
        except KeyboardInterrupt:
            self._adapter.on_global_message("Interrupted \u2014 cancelling...")
            pool.shutdown(wait=False, cancel_futures=True)
            self._cancel_active(state)
            return None
        else:
            pool.shutdown(wait=True)
        finally:
            uninstall_progress_router(patched)

        all_ok = all(r.success for r in results)
        if all_ok:
            self._adapter.on_global_message("All downloads completed successfully.")
        else:
            failed = sum(1 for r in results if not r.success)
            self._adapter.on_global_message(f"{failed} download(s) failed.")
        return results

    def _download_one(self, task: Task, state: _WorkerState):
        """Download a single task. Runs in a worker thread."""
        wid = self._get_worker_id(state)
        with state.lock:
            state.task_worker_map[task.target] = wid

        def _check_request_id(formatted):
            rid = parse_request_id(formatted)
            if rid:
                self._adapter.on_task_request_id(wid, rid)
                with state.lock:
                    state.active_requests[task.target] = (rid, client)

        def _info_cb(msg, *args):
            try:
                formatted = msg % args if args else str(msg)
            except Exception:
                formatted = str(msg)
            _check_request_id(formatted)
            self._adapter.on_task_message(wid, formatted)

        def _debug_cb(msg, *args):
            try:
                formatted = msg % args if args else str(msg)
            except Exception:
                return
            _check_request_id(formatted)

        client = cdsapi.Client(
            quiet=True,
            progress=True,
            info_callback=_info_cb,
            warning_callback=_info_cb,
            error_callback=_info_cb,
            debug_callback=_debug_cb,
        )
        silence_loggers()
        self._adapter.on_task_started(wid, task)
        os.makedirs(os.path.dirname(os.path.abspath(task.target)), exist_ok=True)
        try:
            client.retrieve(task.dataset, task.request, task.target)
        finally:
            with state.lock:
                state.active_requests.pop(task.target, None)

    def _get_worker_id(self, state: _WorkerState) -> int:
        tid = threading.current_thread().ident
        with state.lock:
            if tid not in state.worker_id_map:
                state.worker_id_map[tid] = state.next_id[0]
                state.next_id[0] += 1
            return state.worker_id_map[tid]

    def _cancel_active(self, state: _WorkerState):
        with state.lock:
            to_cancel = list(state.active_requests.items())
        if not to_cancel:
            return
        self._adapter.on_global_message(
            f"Cancelling {len(to_cancel)} in-flight CDS request(s)..."
        )
        for target, (rid, client) in to_cancel:
            try:
                cancel_cds_request(client, rid)
                self._adapter.on_global_message(f"  Cancelled {rid}")
                wid = state.task_worker_map.get(target)
                if wid is not None:
                    self._adapter.on_task_cancelled(wid)
            except Exception as exc:
                self._adapter.on_global_message(f"  Failed to cancel {rid}: {exc}")
