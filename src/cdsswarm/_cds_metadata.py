"""CDS API metadata: job polling and human-readable labels."""

from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass, field

import requests as http_requests

log = logging.getLogger(__name__)


@dataclass
class JobMetadata:
    """Parsed metadata for a single CDS job."""

    job_id: str = ""
    created: str = ""
    started: str = ""
    finished: str = ""
    dataset_title: str = ""
    request_labels: dict = field(default_factory=dict)
    file_size: int = 0


@dataclass
class ServerStats:
    """Server-wide CDS queue statistics and system status."""

    server_running: int = 0
    server_queued: int = 0
    running_users: int = 0
    system_status: str = ""


def fetch_job_metadata(
    inner, job_id: str, *, include_request: bool = False
) -> JobMetadata:
    """Fetch metadata for a CDS job via the REST API.

    Args:
        inner: The inner ecmwf.datastores client (has .url, ._get_headers, .verify).
        job_id: The CDS job/request ID.
        include_request: Whether to request human-readable labels (?request=true).

    Returns:
        JobMetadata for the job.
    """
    params = []
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

    return _parse_job_metadata(data, job_id)


def _parse_job_metadata(data: dict, job_id: str) -> JobMetadata:
    """Extract JobMetadata fields from a job status response."""
    meta = JobMetadata(job_id=job_id)
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


class MetadataPoller:
    """Daemon thread that polls the CDS REST API for job metadata.

    Polls all active jobs periodically, extracts per-job metadata (progress,
    timestamps, labels, dataset title, file size).
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

        for target, (rid, _client) in active.items():
            wid = task_worker.get(target)
            if wid is None:
                continue

            try:
                meta = fetch_job_metadata(
                    inner,
                    rid,
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

    def _dispatch_updates(self, wid: int, rid: str, meta: JobMetadata):
        """Fire adapter callbacks only when values change."""
        known = self._known_metadata.get(rid, {})

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


# ---------------------------------------------------------------------------
# Server-wide stats (unauthenticated scraping)
# ---------------------------------------------------------------------------

_NEXT_DATA_RE = re.compile(r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)

_LIVE_URL = "https://cds.climate.copernicus.eu/live"
_STATUS_URL = "https://apps.ecmwf.int/status/status"


def fetch_live_stats(url: str = _LIVE_URL) -> ServerStats:
    """Scrape /live page for statistics from __NEXT_DATA__."""
    import json

    resp = http_requests.get(url, timeout=15)
    resp.raise_for_status()
    match = _NEXT_DATA_RE.search(resp.text)
    if not match:
        raise ValueError("__NEXT_DATA__ not found in /live page")
    data = json.loads(match.group(1))
    props = data.get("props", {})
    page_props = props.get("pageProps", {})
    # Data lives in statisticData (current) or queue_status (legacy)
    sd = page_props.get("statisticData") or page_props.get("queue_status") or {}
    return ServerStats(
        server_running=int(
            sd.get("running_requests", 0)
            or sd.get("running_requests_real_time", 0)
            or 0
        ),
        server_queued=int(
            sd.get("queued_requests", 0) or sd.get("queued_requests_real_time", 0) or 0
        ),
        running_users=int(sd.get("running_users", 0) or 0),
    )


def fetch_system_status(url: str = _STATUS_URL) -> str:
    """GET the ECMWF status API and return the Data Stores status string."""
    resp = http_requests.get(url, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    # Current format: {"nodes": [{"node": {"Title": ..., "Status": ...}}, ...]}
    # Legacy format: [{"Title": ..., "Status": ...}, ...]
    nodes = data.get("nodes", data) if isinstance(data, dict) else data
    for entry in nodes:
        node = entry.get("node", entry) if isinstance(entry, dict) else {}
        if node.get("Title") == "Data Stores":
            return str(node.get("Status", ""))
    return ""


class LiveScraper:
    """Daemon thread that polls server-wide CDS stats and system status.

    Fetches once immediately on start, then every ``poll_interval`` seconds.
    Deduplicates: only calls ``adapter.on_server_stats_update()`` when values change.

    Args:
        adapter: OutputAdapter to receive callbacks.
        cancel_event: Threading event to signal shutdown.
        poll_interval: Seconds between poll cycles (default 60).
    """

    def __init__(self, adapter, cancel_event, poll_interval=60.0):
        self._adapter = adapter
        self._cancel_event = cancel_event
        self._poll_interval = poll_interval
        self._thread: threading.Thread | None = None
        self._last: tuple[int, int, int, str] | None = None

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        # Don't set cancel_event here — it's shared with the downloader.
        # The cancel_event is set by the downloader on shutdown.
        if self._thread is not None:
            self._thread.join(timeout=5)

    def _run(self):
        # Fetch once immediately
        self._poll_once()
        while not self._cancel_event.wait(self._poll_interval):
            self._poll_once()

    def _poll_once(self):
        stats = ServerStats()
        got_data = False
        try:
            live = fetch_live_stats()
            stats.server_running = live.server_running
            stats.server_queued = live.server_queued
            stats.running_users = live.running_users
            got_data = True
        except Exception:
            log.debug("Failed to fetch /live stats", exc_info=True)

        try:
            stats.system_status = fetch_system_status()
            got_data = True
        except Exception:
            log.debug("Failed to fetch system status", exc_info=True)

        if not got_data:
            return

        current = (
            stats.server_running,
            stats.server_queued,
            stats.running_users,
            stats.system_status,
        )
        if current != self._last:
            self._last = current
            self._adapter.on_server_stats_update(
                stats.server_running,
                stats.server_queued,
                stats.running_users,
                stats.system_status,
            )
