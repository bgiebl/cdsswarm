"""Session state persistence for resuming interrupted downloads."""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from dataclasses import asdict, dataclass, field


def _session_dir() -> str:
    """Return the sessions directory: $XDG_CACHE_HOME/cdsswarm/sessions/."""
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(
        os.path.expanduser("~"), ".cache"
    )
    return os.path.join(base, "cdsswarm", "sessions")


def session_path(request_file: str, output_dir: str = "") -> str:
    """Compute the state file path from SHA-256 of (resolved path + output_dir)."""
    resolved = os.path.realpath(request_file)
    key = resolved + "\0" + output_dir
    h = hashlib.sha256(key.encode()).hexdigest()
    return os.path.join(_session_dir(), f"{h}.json")


@dataclass
class TaskState:
    """Per-task persistent state."""

    dataset: str
    request: dict
    status: str = "pending"  # "pending" | "completed" | "failed"
    error: str = ""
    cds_request_id: str = ""
    start_time: float = 0.0
    end_time: float = 0.0
    file_size: int = 0


@dataclass
class SessionState:
    """Persistent session state for resume support."""

    version: int = 1
    request_file: str = ""
    started: str = ""
    settings: dict = field(default_factory=dict)
    tasks: dict[str, TaskState] = field(default_factory=dict)

    @classmethod
    def new(
        cls,
        request_file: str,
        tasks: list,
        settings: dict | None = None,
    ) -> SessionState:
        """Create a fresh session with all tasks pending."""

        task_states = {}
        for t in tasks:
            task_states[t.target] = TaskState(
                dataset=t.dataset, request=dict(t.request)
            )
        return cls(
            version=1,
            request_file=os.path.realpath(request_file),
            started=time.strftime("%Y-%m-%dT%H:%M:%S"),
            settings=dict(settings) if settings else {},
            tasks=task_states,
        )

    @classmethod
    def load(cls, path: str) -> SessionState:
        """Load session state from a JSON file."""
        with open(path) as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError(
                f"Invalid session file: expected object, got {type(data).__name__}"
            )
        version = data.get("version")
        if version != 1:
            raise ValueError(f"Unsupported session version: {version}")
        task_states = {}
        for target, ts in data.get("tasks", {}).items():
            task_states[target] = TaskState(
                dataset=ts.get("dataset", ""),
                request=ts.get("request", {}),
                status=ts.get("status", "pending"),
                error=ts.get("error", ""),
                cds_request_id=ts.get("cds_request_id", ""),
                start_time=ts.get("start_time", 0.0),
                end_time=ts.get("end_time", 0.0),
                file_size=ts.get("file_size", 0),
            )
        return cls(
            version=1,
            request_file=data.get("request_file", ""),
            started=data.get("started", ""),
            settings=data.get("settings", {}),
            tasks=task_states,
        )

    def save(self, path: str) -> None:
        """Atomic write: tmp + fsync + replace."""
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp_path = f"{path}.{os.getpid()}.{threading.get_ident()}.tmp"
        data = {
            "version": self.version,
            "request_file": self.request_file,
            "started": self.started,
            "settings": self.settings,
            "tasks": {t: asdict(ts) for t, ts in self.tasks.items()},
        }
        with open(tmp_path, "w") as f:
            json.dump(data, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)

    def mark_completed(
        self,
        target: str,
        start_time: float,
        end_time: float,
        file_size: int,
        request_id: str = "",
    ) -> None:
        ts = self.tasks.get(target)
        if ts is None:
            return
        ts.status = "completed"
        ts.error = ""
        ts.start_time = start_time
        ts.end_time = end_time
        ts.file_size = file_size
        if request_id:
            ts.cds_request_id = request_id

    def mark_failed(
        self,
        target: str,
        error: str,
        start_time: float,
        end_time: float,
        request_id: str = "",
    ) -> None:
        ts = self.tasks.get(target)
        if ts is None:
            return
        ts.status = "failed"
        ts.error = error
        ts.start_time = start_time
        ts.end_time = end_time
        if request_id:
            ts.cds_request_id = request_id

    def set_request_id(self, target: str, request_id: str) -> None:
        ts = self.tasks.get(target)
        if ts is not None:
            ts.cds_request_id = request_id

    def pending_targets(self) -> list[str]:
        """Targets not completed, or completed but file missing on disk."""
        result = []
        for target, ts in self.tasks.items():
            if ts.status != "completed":
                result.append(target)
            elif not os.path.exists(target):
                result.append(target)
        return result

    def clear_stale_request_ids(self) -> int:
        """Clear request IDs for incomplete tasks (likely dismissed on the server).

        After a cancelled session, in-flight CDS jobs are dismissed but their
        request IDs remain in the session state.  Reusing dismissed IDs wastes
        retry attempts, so clear them on resume.  Returns the number cleared.
        """
        cleared = 0
        for ts in self.tasks.values():
            if ts.status != "completed" and ts.cds_request_id:
                ts.cds_request_id = ""
                cleared += 1
        return cleared

    def reuse_map(self) -> dict[str, str]:
        """Return {target: cds_request_id} for pending tasks with known IDs."""
        result = {}
        for target in self.pending_targets():
            ts = self.tasks.get(target)
            if ts and ts.cds_request_id:
                result[target] = ts.cds_request_id
        return result
