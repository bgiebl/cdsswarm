"""Core download engine: Task, Result, and SwarmDownloader."""

from __future__ import annotations

import itertools
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field

import cdsapi
import requests as http_requests

from ._cds_metadata import MetadataPoller, fetch_job_results, verify_checksum
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
        reuse_jobs: bool = False,
        max_retries: int = 3,
    ):
        self._all_tasks = list(tasks)
        self._adapter = adapter
        self._num_workers = num_workers
        self._skip_existing = skip_existing
        self._reuse_jobs = reuse_jobs
        self._max_retries = max(max_retries, 1)
        self._cancel_event = threading.Event()
        self._state: _WorkerState | None = None
        self._pool: ThreadPoolExecutor | None = None
        self._task_timing: dict[str, tuple[float, float, int]] = {}
        self._task_warnings: dict[str, list[str]] = {}

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

        reuse_map: dict[str, str] = {}
        if self._reuse_jobs and pending:
            self._adapter.on_global_message("Checking for reusable CDS jobs...")
            try:
                lookup_client = cdsapi.Client(quiet=True, progress=False)
                reuse_map = find_reusable_jobs(lookup_client, pending)
            except Exception as exc:
                self._adapter.on_global_message(
                    f"Job reuse lookup failed ({exc}), submitting new requests"
                )
            else:
                if reuse_map:
                    self._adapter.on_global_message(
                        f"Found {len(reuse_map)} reusable job(s)"
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
                    results.append(
                        Result(
                            task=task,
                            success=True,
                            start_time=timing[0],
                            end_time=timing[1],
                            file_size=timing[2],
                            warnings=warns,
                        )
                    )
                except Exception as e:
                    self._adapter.on_task_completed(wid, task, False, str(e))
                    results.append(
                        Result(
                            task=task,
                            success=False,
                            error=str(e),
                            start_time=timing[0],
                            end_time=timing[1],
                            file_size=timing[2],
                            warnings=warns,
                        )
                    )
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
                except Exception:
                    with state.lock:
                        state.active_requests.pop(task.target, None)
                    if attempt >= self._max_retries or self._cancel_event.is_set():
                        raise
                    delay = min(2 ** (attempt - 1), 60)
                    self._adapter.on_task_message(
                        wid,
                        f"Attempt {attempt}/{self._max_retries} failed, "
                        f"retrying in {delay}s...",
                    )
                    reuse_id = None  # don't reuse a broken job
                    for _ in range(int(delay * 2)):
                        if self._cancel_event.is_set():
                            raise
                        self._cancel_event.wait(0.5)
                    continue

                # Checksum verification
                rid = None
                with state.lock:
                    req_info = state.active_requests.get(task.target)
                    if req_info:
                        rid = req_info[0]

                should_retry = False
                if rid:
                    try:
                        inner = getattr(client, "client", None)
                        if inner is not None and hasattr(inner, "_get_headers"):
                            _file_size, checksum_md5 = fetch_job_results(inner, rid)
                            if checksum_md5:
                                passed = verify_checksum(task.target, checksum_md5)
                                decision = self._adapter.on_task_checksum_result(
                                    wid, passed, checksum_md5
                                )
                                if not passed:
                                    self._adapter.on_task_message(
                                        wid,
                                        f"Checksum mismatch (expected {checksum_md5})",
                                    )
                                    if decision == "retry":
                                        should_retry = True
                                    else:
                                        with state.lock:
                                            self._task_warnings.setdefault(
                                                task.target, []
                                            ).append(
                                                f"Checksum mismatch (expected {checksum_md5})"
                                            )
                                else:
                                    self._adapter.on_task_message(wid, "Checksum OK")
                    except http_requests.RequestException as exc:
                        log.debug("Checksum fetch failed for %s: %s", rid, exc)
                    except (OSError, ValueError) as exc:
                        log.warning("Checksum verification error: %s", exc)

                with state.lock:
                    state.active_requests.pop(task.target, None)

                if should_retry:
                    self._adapter.on_task_message(wid, "Retrying download...")
                    continue
                break  # Success, exit retry loop
        finally:
            end = time.time()
            size = os.path.getsize(task.target) if os.path.exists(task.target) else 0
            with state.lock:
                self._task_timing[task.target] = (start, end, size)

    def _get_worker_id(self, state: _WorkerState) -> int:
        tid = threading.current_thread().ident
        with state.lock:
            if tid not in state.worker_id_map:
                state.worker_id_map[tid] = next(state.next_id)
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
