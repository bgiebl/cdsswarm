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
    """Silence cdsapi/urllib3/requests loggers to prevent duplicate output."""
    for name in ("cdsapi", "urllib3", "requests"):
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
            task_url, verify=client.verify, timeout=10,
        )
        resp.raise_for_status()


def install_progress_router(adapter, worker_id_map, id_lock):
    """Monkey-patch tqdm so cdsapi download progress goes through our adapter.

    Returns a list of (module, attr_name, original_value) for cleanup.
    """
    try:
        import tqdm as tqdm_mod
    except ImportError:
        return []

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

    return patched


def uninstall_progress_router(patched):
    """Restore original tqdm references."""
    for mod, attr, orig in patched:
        try:
            setattr(mod, attr, orig)
        except Exception:
            pass
