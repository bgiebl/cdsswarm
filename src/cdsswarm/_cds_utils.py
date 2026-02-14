"""CDS API utilities: status parsing, request cancellation, progress routing."""

import logging
import os
import re
import sys
import threading

import requests as http_requests

# CDS API status messages:
#   Old cdsapi:           "Request is <state>"
#   New ecmwf-datastores: "status has been updated to <state>"
CDS_STATUS_PATTERNS = [
    re.compile(r"Request is (\w+)"),
    re.compile(r"status has been updated to (\w+)"),
]

CDS_STATE_MAP = {
    # Old CDS API states
    "queued": "accepted",
    "running": "running",
    "completed": "successful",
    "failed": "failed",
    # New CDS API (CADS) states
    "accepted": "accepted",
    "successful": "successful",
}

# Old cdsapi: "Request ID is <id>, sleep <n>" (debug_callback)
# New ecmwf.datastores: "Request ID is <id>" (info_callback)
CDS_REQUEST_ID_RE = re.compile(r"Request ID is ([\w-]+)")


def normalize_request(request: dict) -> dict:
    """Normalize CDS request params for comparison.

    Converts all values to sorted-key dicts of string lists so that
    ``"year": "2024"`` matches ``"year": ["2024"]``.
    List order is preserved (important for positional params like ``area``).
    """
    normalized = {}
    for key in sorted(request):
        value = request[key]
        if isinstance(value, (list, tuple)):
            normalized[key] = [str(v) for v in value]
        else:
            normalized[key] = [str(value)]
    return normalized


def find_reusable_jobs(client, tasks, limit=200) -> dict[str, str]:
    """Find existing CDS jobs that match the given tasks.

    Queries the CADS ``GET /jobs`` endpoint for jobs whose dataset and
    request parameters match.  Only works with the new ``ecmwf.datastores``
    ``LegacyClient``; returns ``{}`` for old-style clients.

    Returns:
        Mapping of ``{task.target: job_request_id}`` for tasks with a
        reusable server-side job.
    """
    # Only the new CADS LegacyClient exposes an inner ecmwf.datastores.Client
    inner = getattr(client, "client", None)
    if inner is None or not hasattr(inner, "get_remote"):
        return {}

    # Build lookup index: (dataset, normalized_request_key) -> list of targets
    needed: dict[tuple, list[str]] = {}
    task_by_target: dict[str, tuple[str, dict]] = {}
    for task in tasks:
        norm = normalize_request(task.request)
        key = (task.dataset, _dict_key(norm))
        needed.setdefault(key, []).append(task.target)
        task_by_target[task.target] = (task.dataset, norm)

    # Collect datasets we need so we can skip non-matching jobs cheaply
    needed_datasets = {k[0] for k in needed}

    reuse_map: dict[str, str] = {}
    remaining = sum(len(v) for v in needed.values())

    # Query jobs: successful first (ready to download), then running, then accepted
    for status in ("successful", "running", "accepted"):
        if remaining <= 0:
            break
        try:
            _scan_jobs(inner, status, limit, needed_datasets, needed, reuse_map)
        except Exception:
            continue
        remaining = sum(len(v) for v in needed.values())

    return reuse_map


def _dict_key(d: dict) -> tuple:
    """Convert a normalized request dict to a hashable tuple for use as dict key."""
    return tuple((k, tuple(v)) for k, v in sorted(d.items()))


def _scan_jobs(inner, status, limit, needed_datasets, needed, reuse_map):
    """Scan one page of jobs for the given status and populate reuse_map."""
    try:
        jobs_response = inner.get_jobs(
            status=status,
            sortby="-created",
            limit=limit,
        )
    except Exception:
        return

    # Jobs are plain dicts in the paginated JSON response
    jobs = jobs_response._json_dict.get("jobs", [])
    for job in jobs:
        process_id = job.get("processID")
        if process_id not in needed_datasets:
            continue
        job_id = job.get("jobID")
        if not job_id:
            continue
        try:
            remote = inner.get_remote(job_id)
            norm_job = normalize_request(dict(remote.request))
        except Exception:
            continue
        key = (process_id, _dict_key(norm_job))
        if key in needed:
            targets = needed[key]
            # Assign this job to the first unmatched target
            target = targets.pop(0)
            reuse_map[target] = job_id
            if not targets:
                del needed[key]


def parse_cds_status(message: str) -> str | None:
    """Extract normalized CDS status from a log message."""
    for pattern in CDS_STATUS_PATTERNS:
        m = pattern.search(message)
        if m:
            return CDS_STATE_MAP.get(m.group(1))
    return None


def parse_request_id(message: str) -> str | None:
    """Extract CDS request ID from a log message."""
    m = CDS_REQUEST_ID_RE.search(message)
    return m.group(1) if m else None


def silence_loggers():
    """Silence library loggers to prevent output that corrupts the TUI."""
    for name in ("cdsapi", "urllib3", "requests", "ecmwf"):
        lgr = logging.getLogger(name)
        lgr.handlers.clear()
        lgr.addHandler(logging.NullHandler())
        lgr.propagate = False


def cancel_cds_request(client, request_id: str):
    """Cancel a CDS API request, supporting both old and new clients.

    Old cdsapi (key with colon): DELETE {url}/tasks/{id}
    New ecmwf.datastores (LegacyClient): POST {url}/retrieve/v1/jobs/delete
    """
    try:
        from ecmwf.datastores import config as ds_config
    except ImportError:
        ds_config = None

    inner = getattr(client, "client", None)
    if inner is not None and hasattr(inner, "_get_headers") and ds_config is not None:
        api_version = getattr(ds_config, "SUPPORTED_API_VERSION", "v1")
        url = f"{inner.url}/retrieve/{api_version}/jobs/delete"
        sess = http_requests.Session()
        resp = sess.post(
            url,
            json={"job_ids": [request_id]},
            headers=inner._get_headers(),
            verify=inner.verify,
            timeout=10,
        )
        resp.raise_for_status()
    else:
        task_url = f"{client.url}/tasks/{request_id}"
        resp = client.session.delete(
            task_url,
            verify=client.verify,
            timeout=10,
        )
        resp.raise_for_status()


def install_progress_router(adapter, worker_id_map, id_lock):
    """Monkey-patch tqdm so cdsapi download progress goes through our adapter.

    Returns a dict ``{"patches": [...], "devnull": file_handle}`` for cleanup.
    """
    try:
        import tqdm as tqdm_mod
    except ImportError:
        return {}

    orig_tqdm = tqdm_mod.tqdm
    _devnull = open(os.devnull, "w")

    class _ProgressTqdm(orig_tqdm):
        def __init__(self, *args, **kwargs):
            kwargs["file"] = _devnull
            kwargs["disable"] = False
            super().__init__(*args, **kwargs)
            self._last_pct = -1

        def update(self, n=1):
            super().update(n)
            if not self.total:
                return
            pct = int(self.n * 100 / self.total)
            if pct == self._last_pct:
                return
            self._last_pct = pct
            tid = threading.current_thread().ident
            with id_lock:
                wid = worker_id_map.get(tid, 0)
            done_mb = self.n / (1024 * 1024)
            total_mb = self.total / (1024 * 1024)
            adapter.on_task_message(
                wid, f"Downloading: {done_mb:.0f}/{total_mb:.0f} MB ({pct}%)"
            )
            adapter.on_task_progress(wid, self.n, self.total)

    patched = []
    for mod_name, mod in list(sys.modules.items()):
        if mod is None:
            continue
        if not mod_name.startswith(("tqdm", "cdsapi", "ecmwf")):
            continue
        for attr in list(vars(mod)):
            try:
                if getattr(mod, attr) is orig_tqdm:
                    setattr(mod, attr, _ProgressTqdm)
                    patched.append((mod, attr, orig_tqdm))
            except Exception:
                pass

    return {"patches": patched, "devnull": _devnull}


def uninstall_progress_router(router_state):
    """Restore original tqdm references and close the devnull file handle."""
    if not router_state:
        return
    for mod, attr, orig in router_state.get("patches", []):
        try:
            setattr(mod, attr, orig)
        except Exception:
            pass
    devnull = router_state.get("devnull")
    if devnull is not None:
        try:
            devnull.close()
        except Exception:
            pass
