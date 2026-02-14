"""Textual-based TUI for displaying concurrent download worker status."""

from __future__ import annotations

import os
import threading
import time
from collections import deque
from dataclasses import dataclass, field

from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.message import Message
from textual.screen import ModalScreen, Screen
from textual.widgets import (
    DataTable,
    Header,
    RichLog,
    Static,
    TabbedContent,
    TabPane,
)


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------


def format_eta(seconds: float) -> str:
    """Format seconds into a human-readable ETA string."""
    if seconds < 0:
        return "??:??"
    seconds = int(seconds)
    if seconds >= 3600:
        h, remainder = divmod(seconds, 3600)
        m, s = divmod(remainder, 60)
        return f"{h}h{m:02d}m{s:02d}s"
    m, s = divmod(seconds, 60)
    return f"{m}m{s:02d}s"


def format_size(nbytes: int) -> str:
    """Format byte count into human-readable size."""
    if nbytes <= 0:
        return "\u2014"
    if nbytes >= 1024**3:
        return f"{nbytes / (1024**3):.1f} GB"
    if nbytes >= 1024**2:
        return f"{nbytes / (1024**2):.1f} MB"
    if nbytes >= 1024:
        return f"{nbytes / 1024:.1f} KB"
    return f"{nbytes} B"


# ---------------------------------------------------------------------------
# Per-worker data
# ---------------------------------------------------------------------------


@dataclass
class WorkerData:
    """All mutable state for one download worker."""

    cds_status: str = "idle"
    filename: str = ""
    request_id: str = ""
    start_time: float | None = None
    finish_time: float | None = None
    dl_bytes: int = 0
    dl_total: int = 0
    dataset: str = ""
    target: str = ""
    request_params: dict = field(default_factory=dict)
    logs: deque = field(default_factory=lambda: deque(maxlen=100))
    server_progress: int | None = None
    file_size: int | None = None
    checksum: bool | None = None
    server_created: str | None = None
    server_started: str | None = None
    server_finished: str | None = None
    dataset_title: str = ""
    request_labels: dict | None = None

    def reset(self):
        """Reset state for reuse when worker picks up a new task."""
        self.cds_status = "idle"
        self.filename = ""
        self.request_id = ""
        self.start_time = None
        self.finish_time = None
        self.dl_bytes = 0
        self.dl_total = 0
        self.dataset = ""
        self.target = ""
        self.request_params = {}
        self.logs.clear()
        self.server_progress = None
        self.file_size = None
        self.checksum = None
        self.server_created = None
        self.server_started = None
        self.server_finished = None
        self.dataset_title = ""
        self.request_labels = None


# ---------------------------------------------------------------------------
# File-level tracking
# ---------------------------------------------------------------------------


@dataclass
class FileData:
    """Tracking state for one download file."""

    target: str
    dataset: str
    label: str
    request: dict
    status: str = "pending"  # pending, cached, active, successful, failed
    worker_id: int | None = None
    error: str = ""
    dl_bytes: int = 0
    dl_total: int = 0


# ---------------------------------------------------------------------------
# Textual Messages (posted from adapter via call_from_thread)
# ---------------------------------------------------------------------------


class WorkerStarted(Message):
    def __init__(
        self, worker_id: int, filename: str, dataset: str, request: dict, target: str
    ) -> None:
        super().__init__()
        self.worker_id = worker_id
        self.filename = filename
        self.dataset = dataset
        self.request = request
        self.target = target


class WorkerMessage(Message):
    def __init__(self, worker_id: int, message: str) -> None:
        super().__init__()
        self.worker_id = worker_id
        self.message = message


class WorkerCdsStatus(Message):
    def __init__(self, worker_id: int, cds_status: str) -> None:
        super().__init__()
        self.worker_id = worker_id
        self.cds_status = cds_status


class WorkerRequestId(Message):
    def __init__(self, worker_id: int, request_id: str) -> None:
        super().__init__()
        self.worker_id = worker_id
        self.request_id = request_id


class WorkerProgress(Message):
    def __init__(self, worker_id: int, downloaded: int, total: int) -> None:
        super().__init__()
        self.worker_id = worker_id
        self.downloaded = downloaded
        self.total = total


class WorkerFinished(Message):
    def __init__(self, worker_id: int) -> None:
        super().__init__()
        self.worker_id = worker_id


class WorkerServerProgress(Message):
    def __init__(self, worker_id: int, progress: int) -> None:
        super().__init__()
        self.worker_id = worker_id
        self.progress = progress


class WorkerFileSize(Message):
    def __init__(self, worker_id: int, file_size: int) -> None:
        super().__init__()
        self.worker_id = worker_id
        self.file_size = file_size


class WorkerChecksum(Message):
    def __init__(self, worker_id: int, passed: bool) -> None:
        super().__init__()
        self.worker_id = worker_id
        self.passed = passed


class WorkerServerTimestamps(Message):
    def __init__(
        self, worker_id: int, created: str, started: str, finished: str
    ) -> None:
        super().__init__()
        self.worker_id = worker_id
        self.created = created
        self.started = started
        self.finished = finished


class WorkerDatasetTitle(Message):
    def __init__(self, worker_id: int, title: str) -> None:
        super().__init__()
        self.worker_id = worker_id
        self.title = title


class WorkerRequestLabels(Message):
    def __init__(self, worker_id: int, labels: dict) -> None:
        super().__init__()
        self.worker_id = worker_id
        self.labels = labels


class ProgressUpdate(Message):
    def __init__(self, completed: int, total: int, skipped: int) -> None:
        super().__init__()
        self.completed = completed
        self.total = total
        self.skipped = skipped


class GlobalMessage(Message):
    def __init__(self, message: str) -> None:
        super().__init__()
        self.message = message


class TasksInitialized(Message):
    def __init__(self, tasks: list, skipped_targets: set[str]) -> None:
        super().__init__()
        self.tasks = tasks
        self.skipped_targets = skipped_targets


class QosUpdate(Message):
    def __init__(self, queued: int, running: int, limit: int) -> None:
        super().__init__()
        self.queued = queued
        self.running = running
        self.limit = limit


class FileActive(Message):
    def __init__(self, target: str, worker_id: int) -> None:
        super().__init__()
        self.target = target
        self.worker_id = worker_id


class FileCompleted(Message):
    def __init__(self, target: str, success: bool, error: str = "") -> None:
        super().__init__()
        self.target = target
        self.success = success
        self.error = error


class WorkerCancelled(Message):
    def __init__(self, worker_id: int) -> None:
        super().__init__()
        self.worker_id = worker_id


class ShowChecksumDialog(Message):
    def __init__(
        self,
        worker_id: int,
        expected: str,
        result_event: threading.Event,
        result_holder: list,
    ) -> None:
        super().__init__()
        self.worker_id = worker_id
        self.expected = expected
        self.result_event = result_event
        self.result_holder = result_holder


# ---------------------------------------------------------------------------
# Status styling
# ---------------------------------------------------------------------------

STATUS_STYLES = {
    "idle": "dim",
    "accepted": "bold orange1",
    "running": "bold yellow",
    "successful": "bold green",
    "failed": "bold red",
    "cancelled": "bold magenta",
}


def styled_status(status: str) -> Text:
    """Return a Rich Text with colored status badge."""
    style = STATUS_STYLES.get(status, "")
    return Text(status, style=style)


# ---------------------------------------------------------------------------
# Widgets
# ---------------------------------------------------------------------------


class WorkerInfoPanel(Static):
    """Info panel showing details for the selected worker."""

    def render_worker(self, w: WorkerData | None, worker_id: int) -> str:
        if w is None:
            return "No worker selected"

        filetype = ""
        if w.filename:
            dot = w.filename.rfind(".")
            filetype = w.filename[dot + 1 :].upper() if dot >= 0 else "\u2014"
        else:
            filetype = "\u2014"

        dest = os.path.dirname(w.target) if w.target else "\u2014"
        ds = w.dataset_title if w.dataset_title else (w.dataset or "\u2014")
        req_id = w.request_id or "\u2014"

        # Checksum or queue wait
        left_info = ""
        if w.checksum is not None:
            left_info = (
                "Checksum: OK \u2713" if w.checksum else "Checksum: MISMATCH \u2717"
            )
        else:
            queue_wait = self._format_queue_wait(w)
            if queue_wait:
                left_info = f"Queued: {queue_wait}"

        if left_info:
            row8 = f"{left_info} \u2502 Request ID: {req_id}"
        else:
            row8 = f"Request ID: {req_id}"

        # Params
        if w.request_labels:
            param_str = ", ".join(f"{k}: {v}" for k, v in w.request_labels.items())
        elif w.request_params:
            param_str = ", ".join(f"{k}={v}" for k, v in w.request_params.items())
        else:
            param_str = "\u2014"

        fname = w.filename or "\u2014"
        lines = [
            f"[bold cyan] Worker {worker_id} [/] \u2502 Type: {filetype} \u2502 Filename: {fname}",
            f"Destination: {dest}",
            f"Dataset: {ds}",
            row8,
            f"[dim]{param_str}[/]",
        ]
        return "\n".join(lines)

    def _format_queue_wait(self, w: WorkerData) -> str:
        if not w.server_created:
            return ""
        try:
            from datetime import datetime, timezone

            created_dt = datetime.fromisoformat(w.server_created.replace("Z", "+00:00"))
            if w.server_started:
                end_dt = datetime.fromisoformat(w.server_started.replace("Z", "+00:00"))
            else:
                end_dt = datetime.now(timezone.utc)
            delta = (end_dt - created_dt).total_seconds()
            return format_eta(delta) if delta >= 0 else ""
        except Exception:
            return ""


class FilesInfoPanel(Static):
    """Info panel showing details for the selected file."""

    def render_file(self, files: list[FileData], selected: int) -> str:
        if not files or selected < 0 or selected >= len(files):
            return "No file selected"

        f = files[selected]
        num_files = len(files)

        # Summary counts
        counts: dict[str, int] = {}
        for fd in files:
            counts[fd.status] = counts.get(fd.status, 0) + 1
        parts = []
        for key in ["cached", "pending", "active", "successful", "failed"]:
            if counts.get(key, 0) > 0:
                parts.append(f"{counts[key]} {key}")
        summary = " | ".join(parts) if parts else "\u2014"

        worker_info = f"Worker: {f.worker_id}" if f.worker_id is not None else ""
        dest = os.path.dirname(f.target) if f.target else "\u2014"

        if f.request:
            param_str = ", ".join(f"{k}={v}" for k, v in f.request.items())
        else:
            param_str = "\u2014"

        lines = [
            f"[bold cyan] Files ({num_files}) [/] \u2502 Status: {f.status} \u2502 {worker_info}",
            summary,
            f"Filename: {f.label}",
            f"{dest} \u2502 {f.dataset}",
            f"[dim]{param_str}[/]",
        ]
        return "\n".join(lines)


class ProgressFooter(Static):
    """Bottom bar showing overall progress and status."""

    def render_progress(
        self,
        completed: int,
        total: int,
        skipped: int,
        eta_start: float | None,
        status: str,
        qos_queued: int,
        qos_running: int,
        qos_limit: int,
    ) -> str:
        grand_total = total + skipped
        grand_done = completed + skipped

        if grand_total > 0:
            pct = grand_done * 100 / grand_total
            bar_width = 30
            filled = int(bar_width * grand_done / grand_total)
            bar = "\u2588" * filled + "\u2591" * (bar_width - filled)
            text = f"[yellow]\\[{bar}] {grand_done}/{grand_total}  {pct:.0f}%[/]"
            if skipped:
                text += f"  ({skipped} cached)"
            if eta_start:
                import time as _time

                elapsed = _time.monotonic() - eta_start
                text += f"  Elapsed: {format_eta(elapsed)}"
                if completed > 0:
                    remaining = total - completed
                    eta_seconds = (elapsed / completed) * remaining
                    text += f"  ETA: {format_eta(eta_seconds)}"
            elif completed == 0 and total > 0:
                text += "  ETA: estimating..."
        else:
            text = "Preparing..."

        # Status line
        status_line = ""
        if qos_queued > 0 or qos_running > 0:
            qos_text = (
                f"CDS Server: {qos_queued} queued | {qos_running}/{qos_limit} running"
            )
            status_line = f"{qos_text} | {status}" if status else qos_text
        else:
            status_line = status

        if status_line:
            text += f"\n{status_line}"
        else:
            text += "\n"

        return text


# ---------------------------------------------------------------------------
# Screens
# ---------------------------------------------------------------------------


class LogScreen(Screen):
    """Full-screen log view for a worker."""

    BINDINGS = [
        Binding("escape", "dismiss", "Back"),
    ]

    def __init__(self, worker_id: int, filename: str, logs: list[str]) -> None:
        super().__init__()
        self.worker_id = worker_id
        self.filename = filename
        self.initial_logs = logs

    def compose(self) -> ComposeResult:
        yield Static(
            f" Worker {self.worker_id} \u2014 {self.filename}  [dim][Esc] back[/]",
            id="log-header",
        )
        yield RichLog(id="log-content", wrap=True, highlight=True)

    def on_mount(self) -> None:
        log = self.query_one("#log-content", RichLog)
        for line in self.initial_logs:
            log.write(line)


class ParamsScreen(Screen):
    """Full-screen parameter view for a worker."""

    BINDINGS = [
        Binding("escape", "dismiss", "Back"),
    ]

    def __init__(
        self, worker_id: int, params: dict, labels: dict | None = None
    ) -> None:
        super().__init__()
        self.worker_id = worker_id
        self.params = params
        self.labels = labels

    def compose(self) -> ComposeResult:
        yield Static(
            f" Worker {self.worker_id} \u2014 Parameters  [dim][Esc] back[/]",
            id="params-header",
        )
        if self.labels:
            param_str = "\n".join(f"[bold]{k}:[/] {v}" for k, v in self.labels.items())
        elif self.params:
            param_str = "\n".join(f"[bold]{k}:[/] {v}" for k, v in self.params.items())
        else:
            param_str = "\u2014"
        yield Static(param_str, id="params-content")


class ChecksumScreen(ModalScreen[str]):
    """Modal dialog for checksum mismatch."""

    BINDINGS = [
        Binding("c", "continue_download", "Continue"),
        Binding("r", "retry_download", "Retry"),
    ]

    def __init__(
        self, worker_id: int, filename: str, expected: str, target: str
    ) -> None:
        super().__init__()
        self.worker_id = worker_id
        self.filename = filename
        self.expected = expected
        self.target_path = target

    def compose(self) -> ComposeResult:
        actual = self._compute_actual()
        with Vertical(id="checksum-dialog"):
            yield Static("CHECKSUM MISMATCH", classes="checksum-title")
            yield Static(
                f"Worker {self.worker_id}: {self.filename}\n"
                f"Expected: {self.expected}\n"
                f"Got:      {actual}",
                classes="checksum-info",
            )
            yield Static(
                "[bold][r] Retry download (Recommended)[/]  |  [c] Continue and ignore",
                classes="checksum-buttons",
            )

    def _compute_actual(self) -> str:
        try:
            from ._cds_metadata import compute_file_hash, parse_multihash

            algo, _ = parse_multihash(self.expected)
            return (
                compute_file_hash(self.target_path, algo).hex()
                if self.target_path
                else "?"
            )
        except Exception:
            return "?"

    def action_continue_download(self) -> None:
        self.dismiss("continue")

    def action_retry_download(self) -> None:
        self.dismiss("retry")


# ---------------------------------------------------------------------------
# Worker table column keys
# ---------------------------------------------------------------------------

WORKER_COLUMNS = [
    ("W", 4),
    ("Status", 12),
    ("Prog", 6),
    ("Filename", 20),
    ("Started", 10),
    ("Elapsed", 10),
    ("Size", 10),
    ("DL %", 7),
    ("Request ID", 36),
]

FILES_COLUMNS = [
    ("#", 5),
    ("Status", 12),
    ("Filename", 20),
    ("Dataset", 30),
    ("Size", 10),
    ("Worker", 7),
    ("DL %", 7),
]


# ---------------------------------------------------------------------------
# Main App
# ---------------------------------------------------------------------------


class CdsswarmApp(App):
    """Textual TUI for cdsswarm concurrent downloads."""

    TITLE = "cdsswarm"
    CSS = """
    Screen {
        background: $surface;
    }
    #worker-info, #files-info {
        height: auto;
        max-height: 8;
        border: round $primary;
        padding: 0 1;
        margin: 0 0 0 0;
    }
    #worker-table, #files-table {
        height: 1fr;
    }
    ProgressFooter {
        dock: bottom;
        height: 2;
        padding: 0 1;
        background: $boost;
    }
    #tabs {
        height: 1fr;
    }
    LogScreen RichLog {
        height: 1fr;
    }
    LogScreen Static#log-header {
        height: 1;
        background: $primary;
        color: $text;
        text-style: bold;
        padding: 0 1;
    }
    ParamsScreen Static#params-header {
        height: 1;
        background: $primary;
        color: $text;
        text-style: bold;
        padding: 0 1;
    }
    ParamsScreen Static#params-content {
        height: 1fr;
        padding: 1 2;
    }
    ChecksumScreen {
        align: center middle;
    }
    ChecksumScreen #checksum-dialog {
        width: 64;
        height: auto;
        border: thick $error;
        background: $surface;
        padding: 1 2;
    }
    ChecksumScreen .checksum-title {
        text-style: bold;
        color: $error;
        margin-bottom: 1;
    }
    ChecksumScreen .checksum-info {
        margin-bottom: 1;
    }
    ChecksumScreen .checksum-buttons {
        margin-top: 1;
        height: auto;
    }
    """

    BINDINGS = [
        Binding("q", "quit_app", "Quit", priority=True),
        Binding("t", "switch_tab", "Switch Tab"),
        Binding("tab", "switch_tab", "Switch Tab", show=False),
        Binding("enter", "open_detail", "Logs"),
        Binding("a", "open_params", "Params"),
    ]

    def __init__(
        self,
        num_workers: int = 4,
        title: str = "cdsswarm",
        downloader=None,
        log_file=None,
    ) -> None:
        super().__init__()
        self.num_workers = num_workers
        self.app_title = title
        self.downloader = downloader
        self.log_file = log_file

        # Worker state
        self.worker_data: list[WorkerData] = [WorkerData() for _ in range(num_workers)]
        # File state
        self.files: list[FileData] = []
        self.file_index: dict[str, int] = {}  # target -> index in files
        self.worker_to_target: dict[int, str] = {}

        # Progress state
        self.progress_completed = 0
        self.progress_total = 0
        self.progress_skipped = 0
        self.eta_start_time: float | None = None
        self.status_message = ""
        self.qos_queued = 0
        self.qos_running = 0
        self.qos_limit = 0

        # Download results
        self.download_results = None
        self._download_done = threading.Event()

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with TabbedContent("Workers", "Files", id="tabs"):
            with TabPane("Workers", id="workers-pane"):
                yield WorkerInfoPanel(id="worker-info")
                yield DataTable(id="worker-table", cursor_type="row")
            with TabPane("Files", id="files-pane"):
                yield FilesInfoPanel(id="files-info")
                yield DataTable(id="files-table", cursor_type="row")
        yield ProgressFooter(id="progress-footer")

    def on_mount(self) -> None:
        self.title = self.app_title

        # Setup worker table
        wt = self.query_one("#worker-table", DataTable)
        for label, width in WORKER_COLUMNS:
            wt.add_column(label, key=label, width=width)
        for i in range(self.num_workers):
            wt.add_row(
                str(i),
                styled_status("idle"),
                "---",
                "\u2014",
                "\u2014",
                "\u2014",
                "\u2014",
                "\u2014",
                "\u2014",
                key=str(i),
            )

        # Setup files table
        ft = self.query_one("#files-table", DataTable)
        for label, width in FILES_COLUMNS:
            ft.add_column(label, key=label, width=width)

        # Initial info panel
        self._update_worker_info()

        # Start elapsed time ticker
        self.set_interval(1.0, self._tick_elapsed)

        # Start download if a downloader was provided
        if self.downloader is not None:
            self._run_downloader()

    @work(thread=True)
    def _run_downloader(self) -> None:
        """Run the downloader in a background thread."""
        self.download_results = self.downloader.run()
        self._download_done.set()

    # -- Elapsed time ticker --

    def _tick_elapsed(self) -> None:
        """Update elapsed time for running workers."""
        wt = self.query_one("#worker-table", DataTable)
        for i, w in enumerate(self.worker_data):
            if w.start_time is not None and w.finish_time is None:
                elapsed = format_eta(time.time() - w.start_time)
                wt.update_cell(str(i), "Elapsed", elapsed)
        # Also update progress footer
        self._update_progress_footer()

    # -- Message handlers --

    def on_worker_started(self, msg: WorkerStarted) -> None:
        w = self.worker_data[msg.worker_id]
        w.cds_status = "accepted"
        w.filename = msg.filename
        w.dataset = msg.dataset
        w.request_params = msg.request
        w.target = msg.target
        w.request_id = ""
        w.start_time = time.time()
        w.finish_time = None
        w.dl_bytes = 0
        w.dl_total = 0
        w.logs.clear()
        w.server_progress = None
        w.file_size = None
        w.checksum = None
        w.server_created = None
        w.server_started = None
        w.server_finished = None
        w.dataset_title = ""
        w.request_labels = None
        w.logs.append(f"Started: {msg.filename}")

        wt = self.query_one("#worker-table", DataTable)
        wt.update_cell(str(msg.worker_id), "Status", styled_status("accepted"))
        wt.update_cell(str(msg.worker_id), "Prog", "---")
        wt.update_cell(str(msg.worker_id), "Filename", msg.filename)
        wt.update_cell(
            str(msg.worker_id),
            "Started",
            time.strftime("%H:%M:%S", time.localtime(w.start_time)),
        )
        wt.update_cell(str(msg.worker_id), "Elapsed", "0m00s")
        wt.update_cell(str(msg.worker_id), "Size", "\u2014")
        wt.update_cell(str(msg.worker_id), "DL %", "\u2014")
        wt.update_cell(str(msg.worker_id), "Request ID", "\u2014")
        self._update_worker_info()

    def on_worker_message(self, msg: WorkerMessage) -> None:
        w = self.worker_data[msg.worker_id]
        w.logs.append(msg.message)

    def on_worker_cds_status(self, msg: WorkerCdsStatus) -> None:
        w = self.worker_data[msg.worker_id]
        if w.cds_status == "cancelled":
            return
        w.cds_status = msg.cds_status
        wt = self.query_one("#worker-table", DataTable)
        wt.update_cell(
            str(msg.worker_id),
            "Status",
            styled_status(msg.cds_status),
        )
        self._update_worker_info()

    def on_worker_request_id(self, msg: WorkerRequestId) -> None:
        w = self.worker_data[msg.worker_id]
        w.request_id = msg.request_id
        wt = self.query_one("#worker-table", DataTable)
        wt.update_cell(str(msg.worker_id), "Request ID", msg.request_id or "\u2014")
        self._update_worker_info()

    def on_worker_progress(self, msg: WorkerProgress) -> None:
        w = self.worker_data[msg.worker_id]
        w.dl_bytes = msg.downloaded
        w.dl_total = msg.total
        wt = self.query_one("#worker-table", DataTable)
        if msg.total > 0:
            pct = int(msg.downloaded * 100 / msg.total)
            wt.update_cell(str(msg.worker_id), "DL %", f"{pct}%")
            wt.update_cell(str(msg.worker_id), "Size", format_size(msg.total))
        # Update file-level tracking
        target = self.worker_to_target.get(msg.worker_id)
        if target and target in self.file_index:
            idx = self.file_index[target]
            self.files[idx].dl_bytes = msg.downloaded
            self.files[idx].dl_total = msg.total
            self._update_file_row(idx)

    def on_worker_finished(self, msg: WorkerFinished) -> None:
        w = self.worker_data[msg.worker_id]
        w.finish_time = time.time()
        wt = self.query_one("#worker-table", DataTable)
        elapsed = format_eta(w.finish_time - w.start_time) if w.start_time else "\u2014"
        wt.update_cell(
            str(msg.worker_id),
            "Elapsed",
            Text(f"{elapsed} \u2713", style="green"),
        )
        if w.dl_total > 0:
            pct = int(w.dl_bytes * 100 / w.dl_total)
            wt.update_cell(
                str(msg.worker_id),
                "DL %",
                Text(f"{pct}% \u2713", style="green"),
            )
        self._update_worker_info()

    def on_worker_server_progress(self, msg: WorkerServerProgress) -> None:
        w = self.worker_data[msg.worker_id]
        w.server_progress = msg.progress
        wt = self.query_one("#worker-table", DataTable)
        wt.update_cell(str(msg.worker_id), "Prog", f"{msg.progress}%")

    def on_worker_file_size(self, msg: WorkerFileSize) -> None:
        w = self.worker_data[msg.worker_id]
        w.file_size = msg.file_size
        if w.dl_total <= 0:
            w.dl_total = msg.file_size
            wt = self.query_one("#worker-table", DataTable)
            wt.update_cell(str(msg.worker_id), "Size", format_size(msg.file_size))

    def on_worker_checksum(self, msg: WorkerChecksum) -> None:
        self.worker_data[msg.worker_id].checksum = msg.passed
        self._update_worker_info()

    def on_worker_server_timestamps(self, msg: WorkerServerTimestamps) -> None:
        w = self.worker_data[msg.worker_id]
        w.server_created = msg.created
        w.server_started = msg.started
        w.server_finished = msg.finished
        self._update_worker_info()

    def on_worker_dataset_title(self, msg: WorkerDatasetTitle) -> None:
        self.worker_data[msg.worker_id].dataset_title = msg.title
        self._update_worker_info()

    def on_worker_request_labels(self, msg: WorkerRequestLabels) -> None:
        self.worker_data[msg.worker_id].request_labels = msg.labels
        self._update_worker_info()

    def on_progress_update(self, msg: ProgressUpdate) -> None:
        if self.eta_start_time is None and msg.total > 0:
            self.eta_start_time = time.monotonic()
        self.progress_completed = msg.completed
        self.progress_total = msg.total
        self.progress_skipped = msg.skipped
        self._update_progress_footer()

    def on_global_message(self, msg: GlobalMessage) -> None:
        self.status_message = msg.message
        self._update_progress_footer()

    def on_tasks_initialized(self, msg: TasksInitialized) -> None:
        self.files = []
        self.file_index = {}
        ft = self.query_one("#files-table", DataTable)
        ft.clear()
        for i, task in enumerate(msg.tasks):
            status = "cached" if task.target in msg.skipped_targets else "pending"
            fd = FileData(
                target=task.target,
                dataset=task.dataset,
                label=task.label,
                request=task.request,
                status=status,
            )
            self.files.append(fd)
            self.file_index[task.target] = i
            ft.add_row(
                str(i),
                styled_status(status)
                if status == "cached"
                else Text(status, style="dim"),
                task.label,
                task.dataset,
                "\u2014",
                "\u2014",
                "\u2014",
                key=str(i),
            )
        self._update_files_info()

    def on_qos_update(self, msg: QosUpdate) -> None:
        self.qos_queued = msg.queued
        self.qos_running = msg.running
        self.qos_limit = msg.limit
        self._update_progress_footer()

    def on_file_active(self, msg: FileActive) -> None:
        if msg.target in self.file_index:
            idx = self.file_index[msg.target]
            self.files[idx].status = "active"
            self.files[idx].worker_id = msg.worker_id
            self.worker_to_target[msg.worker_id] = msg.target
            self._update_file_row(idx)
            self._update_files_info()

    def on_file_completed(self, msg: FileCompleted) -> None:
        if msg.target in self.file_index:
            idx = self.file_index[msg.target]
            f = self.files[idx]
            f.status = "successful" if msg.success else "failed"
            f.error = msg.error
            # Snapshot dl data from worker
            if f.worker_id is not None and 0 <= f.worker_id < self.num_workers:
                w = self.worker_data[f.worker_id]
                f.dl_bytes = w.dl_bytes
                f.dl_total = w.dl_total
                self.worker_to_target.pop(f.worker_id, None)
            self._update_file_row(idx)
            self._update_files_info()

    def on_worker_cancelled(self, msg: WorkerCancelled) -> None:
        w = self.worker_data[msg.worker_id]
        w.cds_status = "cancelled"
        w.logs.append("Request cancelled")
        wt = self.query_one("#worker-table", DataTable)
        wt.update_cell(
            str(msg.worker_id),
            "Status",
            styled_status("cancelled"),
        )

    def on_show_checksum_dialog(self, msg: ShowChecksumDialog) -> None:
        w = self.worker_data[msg.worker_id]

        def _on_dismiss(result: str) -> None:
            msg.result_holder.append(result or "continue")
            msg.result_event.set()

        self.push_screen(
            ChecksumScreen(
                msg.worker_id,
                w.filename,
                msg.expected,
                w.target,
            ),
            callback=_on_dismiss,
        )

    # -- DataTable cursor change -> update info panel --

    @on(DataTable.RowHighlighted, "#worker-table")
    def _on_worker_cursor(self, event: DataTable.RowHighlighted) -> None:
        self._update_worker_info()

    @on(DataTable.RowHighlighted, "#files-table")
    def _on_files_cursor(self, event: DataTable.RowHighlighted) -> None:
        self._update_files_info()

    # -- Actions --

    def action_quit_app(self) -> None:
        if self.downloader is not None:
            self.downloader.cancel()
        self.exit()

    def action_switch_tab(self) -> None:
        tabs = self.query_one("#tabs", TabbedContent)
        if tabs.active == "workers-pane":
            tabs.active = "files-pane"
        else:
            tabs.active = "workers-pane"

    def action_open_detail(self) -> None:
        tabs = self.query_one("#tabs", TabbedContent)
        if tabs.active != "workers-pane":
            return
        wt = self.query_one("#worker-table", DataTable)
        row_idx = wt.cursor_row
        if 0 <= row_idx < self.num_workers:
            w = self.worker_data[row_idx]
            self.push_screen(LogScreen(row_idx, w.filename or "\u2014", list(w.logs)))

    def action_open_params(self) -> None:
        tabs = self.query_one("#tabs", TabbedContent)
        if tabs.active != "workers-pane":
            return
        wt = self.query_one("#worker-table", DataTable)
        row_idx = wt.cursor_row
        if 0 <= row_idx < self.num_workers:
            w = self.worker_data[row_idx]
            self.push_screen(ParamsScreen(row_idx, w.request_params, w.request_labels))

    # -- Internal helpers --

    def _update_worker_info(self) -> None:
        wt = self.query_one("#worker-table", DataTable)
        row_idx = wt.cursor_row
        panel = self.query_one("#worker-info", WorkerInfoPanel)
        if 0 <= row_idx < self.num_workers:
            panel.update(panel.render_worker(self.worker_data[row_idx], row_idx))
        else:
            panel.update("No worker selected")

    def _update_files_info(self) -> None:
        ft = self.query_one("#files-table", DataTable)
        row_idx = ft.cursor_row
        panel = self.query_one("#files-info", FilesInfoPanel)
        panel.update(panel.render_file(self.files, row_idx))

    def _update_file_row(self, idx: int) -> None:
        f = self.files[idx]
        ft = self.query_one("#files-table", DataTable)
        ft.update_cell(str(idx), "Status", styled_status(f.status))
        worker_str = str(f.worker_id) if f.worker_id is not None else "\u2014"
        ft.update_cell(str(idx), "Worker", worker_str)
        if f.dl_total > 0:
            ft.update_cell(str(idx), "Size", format_size(f.dl_total))
            pct = int(f.dl_bytes * 100 / f.dl_total)
            if f.status == "successful":
                ft.update_cell(str(idx), "DL %", Text(f"{pct}% \u2713", style="green"))
            else:
                ft.update_cell(str(idx), "DL %", f"{pct}%")

    def _update_progress_footer(self) -> None:
        footer = self.query_one("#progress-footer", ProgressFooter)
        footer.update(
            footer.render_progress(
                self.progress_completed,
                self.progress_total,
                self.progress_skipped,
                self.eta_start_time,
                self.status_message,
                self.qos_queued,
                self.qos_running,
                self.qos_limit,
            )
        )
