"""CDS API metadata: job polling, QoS, and human-readable labels."""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field

import requests as http_requests

log = logging.getLogger(__name__)


@dataclass
class JobMetadata:
    """Parsed metadata for a single CDS job."""

    job_id: str = ""
    progress: int = 0
    created: str = ""
    started: str = ""
    finished: str = ""
    dataset_title: str = ""
    request_labels: dict = field(default_factory=dict)
    file_size: int = 0


@dataclass
class QoSData:
    """Global CDS queue statistics."""

    queued: int = 0
    running: int = 0
    limit: int = 0


def fetch_job_metadata(
    inner, job_id: str, *, include_qos: bool = False, include_request: bool = False
) -> tuple[JobMetadata, QoSData | None]:
    """Fetch metadata for a CDS job via the REST API.

    Args:
        inner: The inner ecmwf.datastores client (has .url, ._get_headers, .verify).
        job_id: The CDS job/request ID.
        include_qos: Whether to request QoS data (?qos=true).
        include_request: Whether to request human-readable labels (?request=true).

    Returns:
        Tuple of (JobMetadata, QoSData or None).
    """
    params = []
    if include_qos:
        params.append("qos=true")
    if include_request:
        params.append("request=true")

    try:
        from ecmwf.datastores import config as ds_config

        api_version = getattr(ds_config, "SUPPORTED_API_VERSION", "v1")
    except ImportError:
        api_version = "v1"

    url = f"{inner.url}/retrieve/{api_version}/jobs/{job_id}"
    if params:
        url += "?" + "&".join(params)

    resp = http_requests.get(
        url,
        headers=inner._get_headers(),
        verify=inner.verify,
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()

    meta = _parse_job_metadata(data, job_id)
    qos = _parse_qos(data) if include_qos else None
    return meta, qos


def _parse_job_metadata(data: dict, job_id: str) -> JobMetadata:
    """Extract JobMetadata fields from a job status response."""
    meta = JobMetadata(job_id=job_id)
    meta.progress = data.get("progress", 0) or 0
    meta.created = data.get("created", "") or ""
    meta.started = data.get("started", "") or ""
    meta.finished = data.get("finished", "") or ""

    metadata_block = data.get("metadata", {})

    # Dataset title
    ds_meta = metadata_block.get("datasetMetadata", {})
    meta.dataset_title = ds_meta.get("title", "") or ""

    # Human-readable labels
    req_meta = metadata_block.get("request", {})
    meta.request_labels = req_meta.get("labels", {}) or {}

    # File size from results embedded in status (if present)
    results = metadata_block.get("results", {})
    asset = results.get("asset", {}).get("value", {})
    meta.file_size = int(asset.get("file:size", 0) or 0)

    return meta


def _parse_qos(data: dict) -> QoSData:
    """Extract QoS data from a job status response."""
    qos = QoSData()
    metadata_block = data.get("metadata", {})
    qos_block = metadata_block.get("qos", {})
    status = qos_block.get("status", {})
    # API used "limit" historically, now uses "user"
    limits = status.get("user") or status.get("limit") or []
    if limits and isinstance(limits, list):
        entry = limits[0]
        qos.queued = int(entry.get("queued", 0) or 0)
        qos.running = int(entry.get("running", 0) or 0)
        conclusion = entry.get("conclusion", "0") or "0"
        try:
            qos.limit = int(conclusion)
        except (ValueError, TypeError):
            qos.limit = 0
    return qos


class MetadataPoller:
    """Daemon thread that polls the CDS REST API for job metadata and QoS.

    Polls all active jobs periodically, extracts per-job metadata (progress,
    timestamps, labels, dataset title, file size) and global QoS data.
    Routes updates through adapter callbacks.

    Args:
        adapter: OutputAdapter to receive callbacks.
        state: _WorkerState with active_requests and task_worker_map.
        cancel_event: Threading event to signal shutdown.
        poll_interval: Seconds between poll cycles (default 10).
    """

    def __init__(self, adapter, state, cancel_event, poll_interval=10.0):
        self._adapter = adapter
        self._state = state
        self._cancel_event = cancel_event
        self._poll_interval = poll_interval
        self._thread: threading.Thread | None = None
        self._inner_client = None
        self._inner_lock = threading.Lock()
        self._known_metadata: dict[str, dict] = {}

    def register_client(self, client):
        """Register an inner REST client from a worker's cdsapi.Client.

        Only captures the first client (all workers use the same credentials).
        """
        inner = getattr(client, "client", None)
        if inner is None or not hasattr(inner, "_get_headers"):
            return
        with self._inner_lock:
            if self._inner_client is None:
                self._inner_client = inner

    def start(self):
        """Start the polling daemon thread."""
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        """Signal the thread to stop and wait for it."""
        self._cancel_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def _run(self):
        """Main poll loop."""
        while not self._cancel_event.wait(self._poll_interval):
            with self._inner_lock:
                inner = self._inner_client
            if inner is None:
                continue
            self._poll_once(inner)

    def _poll_once(self, inner):
        """Poll all active jobs once."""
        with self._state.lock:
            active = dict(self._state.active_requests)
            task_worker = dict(self._state.task_worker_map)

        if not active:
            return

        qos_fetched = False
        for target, (rid, _client) in active.items():
            wid = task_worker.get(target)
            if wid is None:
                continue

            try:
                meta, qos = fetch_job_metadata(
                    inner,
                    rid,
                    include_qos=not qos_fetched,
                    include_request=True,
                )
            except http_requests.RequestException:
                log.debug("Failed to poll metadata for %s", rid, exc_info=True)
                continue
            except Exception:
                log.warning(
                    "Unexpected error polling metadata for %s", rid, exc_info=True
                )
                continue

            self._dispatch_updates(wid, rid, meta)

            if qos is not None and not qos_fetched:
                qos_fetched = True
                if qos.queued > 0 or qos.running > 0 or qos.limit > 0:
                    self._adapter.on_qos_update(qos.queued, qos.running, qos.limit)

    def _dispatch_updates(self, wid: int, rid: str, meta: JobMetadata):
        """Fire adapter callbacks only when values change."""
        known = self._known_metadata.get(rid, {})

        if meta.progress != known.get("progress"):
            self._adapter.on_task_server_progress(wid, meta.progress)
            known["progress"] = meta.progress

        if meta.file_size and meta.file_size != known.get("file_size"):
            self._adapter.on_task_file_size(wid, meta.file_size)
            known["file_size"] = meta.file_size

        timestamps_key = (meta.created, meta.started, meta.finished)
        if timestamps_key != known.get("timestamps"):
            self._adapter.on_task_server_timestamps(
                wid, meta.created, meta.started, meta.finished
            )
            known["timestamps"] = timestamps_key

        if meta.dataset_title and meta.dataset_title != known.get("dataset_title"):
            self._adapter.on_task_dataset_title(wid, meta.dataset_title)
            known["dataset_title"] = meta.dataset_title

        if meta.request_labels and meta.request_labels != known.get("request_labels"):
            self._adapter.on_task_request_labels(wid, meta.request_labels)
            known["request_labels"] = meta.request_labels

        self._known_metadata[rid] = known
