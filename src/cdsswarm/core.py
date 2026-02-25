"""Core download engine: Task, Result, and SwarmDownloader."""

from __future__ import annotations

import itertools
import logging
import os
import subprocess
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field

import cdsapi
from ._cds_metadata import LiveScraper, MetadataPoller
from ._cds_utils import (
    cancel_cds_request,
    find_reusable_jobs,
    install_progress_router,
    parse_request_id,
    silence_loggers,
    uninstall_progress_router,
)
from .adapters import OutputAdapter

log = logging.getLogger(__name__)


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
    start_time: float = 0.0
    end_time: float = 0.0
    file_size: int = 0
    warnings: list[str] = field(default_factory=list)


@dataclass
class _WorkerState:
    """Mutable state shared between the download executor and callbacks."""

    worker_id_map: dict = field(default_factory=dict)
    task_worker_map: dict = field(default_factory=dict)
    active_requests: dict = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)
    next_id: itertools.count = field(default_factory=itertools.count)


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
        reuse_jobs: bool = True,
        max_retries: int = 3,
        post_hook: str = "",
        on_task_done: Callable[[Result, str], None] | None = None,
        on_request_id: Callable[[str, str], None] | None = None,
        initial_reuse_map: dict[str, str] | None = None,
        pre_messages: list[str] | None = None,
    ):
        self._all_tasks = list(tasks)
        self._adapter = adapter
        self._num_workers = num_workers
        self._skip_existing = skip_existing
        self._reuse_jobs = reuse_jobs
        self._max_retries = max(max_retries, 1)
        self._post_hook = post_hook
        self._on_task_done = on_task_done
        self._on_request_id = on_request_id
        self._initial_reuse_map = initial_reuse_map or {}
        self._pre_messages = pre_messages or []
        self._cancel_event = threading.Event()
        self._server_ok = threading.Event()
        self._server_ok.set()  # assume server is OK until told otherwise
        self._server_degraded = threading.Event()  # set = degraded
        self._state: _WorkerState | None = None
        self._pool: ThreadPoolExecutor | None = None
        self._task_timing: dict[str, tuple[float, float, int]] = {}
        self._task_warnings: dict[str, list[str]] = {}
        self._task_request_ids: dict[str, str] = {}

    def cancel(self):
        """Cancel all in-flight CDS requests and shut down the pool.

        Safe to call from any thread (e.g. the main/UI thread while
        ``run()`` executes in a background thread).
        """
        self._cancel_event.set()
        # Cancel active CDS requests immediately via the API
        if self._state is not None:
            self._cancel_active(self._state)
        if self._pool is not None:
            self._pool.shutdown(wait=False, cancel_futures=True)

    def run(self) -> list[Result] | None:
        """Execute all downloads. Returns list of Results, or None if interrupted."""
        for msg in self._pre_messages:
            self._adapter.on_global_message(msg)

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

        skipped_targets = (
            {t.target for t in self._all_tasks if os.path.exists(t.target)}
            if self._skip_existing
            else set()
        )
        self._adapter.on_tasks_initialized(self._all_tasks, skipped_targets)

        reuse_map: dict[str, str] = dict(self._initial_reuse_map)
        if self._reuse_jobs and pending:
            # Only scan API for tasks not already in the initial reuse map
            tasks_to_scan = [t for t in pending if t.target not in reuse_map]
            if tasks_to_scan:
                self._adapter.on_global_message("Checking for reusable CDS jobs...")
                try:
                    lookup_client = cdsapi.Client(quiet=True, progress=False)
                    api_reuse = find_reusable_jobs(lookup_client, tasks_to_scan)
                    reuse_map.update(api_reuse)
                except Exception as exc:
                    self._adapter.on_global_message(
                        f"Job reuse lookup failed ({exc}), submitting new requests"
                    )
                else:
                    if api_reuse:
                        self._adapter.on_global_message(
                            f"Found {len(api_reuse)} reusable job(s)"
                        )
                    else:
                        self._adapter.on_global_message("")
            if self._initial_reuse_map:
                self._adapter.on_global_message(
                    f"Reusing {len(self._initial_reuse_map)} saved job ID(s) from session"
                )

        self._state = state = _WorkerState()
        results: list[Result] = []
        # Pre-populate results for skipped tasks
        if self._skip_existing:
            for t in self._all_tasks:
                if os.path.exists(t.target):
                    results.append(Result(task=t, success=True))

        completed = 0
        router_state = install_progress_router(
            self._adapter,
            state.worker_id_map,
            state.lock,
        )
        poller = MetadataPoller(
            self._adapter, state, self._cancel_event, poll_interval=10.0
        )
        poller.start()
        live_scraper = LiveScraper(
            self._adapter,
            self._cancel_event,
            poll_interval=60.0,
            server_ok_event=self._server_ok,
            server_degraded_event=self._server_degraded,
        )
        live_scraper.start()
        self._pool = pool = ThreadPoolExecutor(max_workers=self._num_workers)
        futures = {
            pool.submit(
                self._download_one,
                task,
                state,
                reuse_map.get(task.target),
                poller,
            ): task
            for task in pending
        }

        try:
            for future in as_completed(futures):
                if self._cancel_event.is_set():
                    pool.shutdown(wait=False, cancel_futures=True)
                    self._cancel_active(state)
                    return None
                task = futures[future]
                completed += 1
                with state.lock:
                    wid = state.task_worker_map.get(task.target, 0)
                    timing = self._task_timing.get(task.target, (0.0, 0.0, 0))
                    warns = self._task_warnings.get(task.target, [])
                self._adapter.on_progress_update(completed, len(pending), skipped)
                try:
                    future.result()
                    self._adapter.on_task_completed(wid, task, True)
                    result = Result(
                        task=task,
                        success=True,
                        start_time=timing[0],
                        end_time=timing[1],
                        file_size=timing[2],
                        warnings=warns,
                    )
                    results.append(result)
                    if self._on_task_done is not None:
                        rid = self._task_request_ids.get(task.target, "")
                        self._on_task_done(result, rid)
                except Exception as e:
                    self._adapter.on_task_completed(wid, task, False, str(e))
                    result = Result(
                        task=task,
                        success=False,
                        error=str(e),
                        start_time=timing[0],
                        end_time=timing[1],
                        file_size=timing[2],
                        warnings=warns,
                    )
                    results.append(result)
                    if self._on_task_done is not None:
                        rid = self._task_request_ids.get(task.target, "")
                        self._on_task_done(result, rid)
        except KeyboardInterrupt:
            self._cancel_event.set()
            self._adapter.on_global_message("Interrupted — cancelling CDS requests...")
            pool.shutdown(wait=False, cancel_futures=True)
            try:
                self._cancel_active(state)
            except KeyboardInterrupt:
                self._adapter.on_global_message("Force quit — skipping cancellation")
            return None
        else:
            pool.shutdown(wait=True)
        finally:
            poller.stop()
            live_scraper.stop()
            self._pool = None
            self._state = None
            uninstall_progress_router(router_state)

        all_ok = all(r.success for r in results)
        if all_ok:
            self._adapter.on_global_message("All downloads completed successfully.")
        else:
            failed = sum(1 for r in results if not r.success)
            self._adapter.on_global_message(f"{failed} download(s) failed.")
        return results

    def _download_one(
        self,
        task: Task,
        state: _WorkerState,
        reuse_id: str | None = None,
        poller: MetadataPoller | None = None,
    ):
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
                    self._task_request_ids[task.target] = rid
                if self._on_request_id is not None:
                    self._on_request_id(task.target, rid)

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
                formatted = str(msg)
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
        if poller is not None:
            poller.register_client(client)
        start = time.time()
        self._adapter.on_task_started(wid, task)
        os.makedirs(os.path.dirname(os.path.abspath(task.target)), exist_ok=True)

        try:
            attempt = 0
            while True:
                # Wait if server is degraded/down
                if not self._server_ok.is_set():
                    self._adapter.on_worker_state(wid, "paused")
                    while not self._server_ok.wait(timeout=1.0):
                        if self._cancel_event.is_set():
                            return
                    self._adapter.on_worker_state(wid, "active")
                attempt += 1
                try:
                    if reuse_id is not None:
                        self._adapter.on_task_message(
                            wid, f"Reusing existing job {reuse_id}"
                        )
                        inner = getattr(client, "client", None)
                        if inner is not None:
                            remote = inner.get_remote(reuse_id)
                            remote.download(task.target)
                        else:
                            client.retrieve(task.dataset, task.request, task.target)
                    else:
                        client.retrieve(task.dataset, task.request, task.target)
                except Exception as exc:
                    with state.lock:
                        state.active_requests.pop(task.target, None)
                    degraded = self._server_degraded.is_set()
                    if not degraded and (
                        attempt >= self._max_retries or self._cancel_event.is_set()
                    ):
                        raise
                    if self._cancel_event.is_set():
                        raise
                    delay = min(2 ** (attempt - 1), 60)
                    if degraded:
                        self._adapter.on_task_message(
                            wid,
                            f"Attempt {attempt} failed (server degraded): "
                            f"{exc}, retrying in {delay}s...",
                        )
                    else:
                        self._adapter.on_task_message(
                            wid,
                            f"Attempt {attempt}/{self._max_retries} failed: "
                            f"{exc}, retrying in {delay}s...",
                        )
                    reuse_id = None  # don't reuse a broken job
                    self._adapter.on_worker_state(wid, "retrying")
                    for _ in range(int(delay * 2)):
                        if self._cancel_event.is_set():
                            raise
                        self._cancel_event.wait(0.5)
                    self._adapter.on_worker_state(wid, "active")
                    continue

                with state.lock:
                    state.active_requests.pop(task.target, None)

                if self._post_hook:
                    self._run_post_hook(wid, task, state)

                break  # Success, exit retry loop
        finally:
            end = time.time()
            size = os.path.getsize(task.target) if os.path.exists(task.target) else 0
            with state.lock:
                self._task_timing[task.target] = (start, end, size)

    def _run_post_hook(self, wid: int, task: Task, state: _WorkerState):
        """Run the post-download hook command. Failure = warning only."""
        cmd = self._post_hook.replace("{file}", task.target).replace(
            "{dataset}", task.dataset
        )
        self._adapter.on_task_hook_started(wid, cmd)
        try:
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
            if result.returncode != 0:
                stderr = result.stderr.strip()
                warning = f"exit code {result.returncode}"
                if stderr:
                    warning += f": {stderr}"
                self._adapter.on_task_hook_finished(wid, False, warning)
                with state.lock:
                    self._task_warnings.setdefault(task.target, []).append(
                        f"Post-hook failed ({warning})"
                    )
            else:
                self._adapter.on_task_hook_finished(wid, True)
        except Exception as exc:
            warning = str(exc)
            self._adapter.on_task_hook_finished(wid, False, warning)
            with state.lock:
                self._task_warnings.setdefault(task.target, []).append(
                    f"Post-hook error ({warning})"
                )

    def _get_worker_id(self, state: _WorkerState) -> int:
        tid = threading.current_thread().ident
        with state.lock:
            if tid not in state.worker_id_map:
                state.worker_id_map[tid] = next(state.next_id)
            return int(state.worker_id_map[tid])

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
