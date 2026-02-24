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
from textual.message import Message
from textual.screen import Screen
from textual.theme import Theme
from textual.widgets import (
    DataTable,
    RichLog,
    Static,
)

from .status import FileStatus, WorkerStatus


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

    cds_status: WorkerStatus = WorkerStatus.IDLE
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
    file_size: int | None = None
    server_created: str | None = None
    server_started: str | None = None
    server_finished: str | None = None
    dataset_title: str = ""
    request_labels: dict | None = None

    def reset(self):
        """Reset state for reuse when worker picks up a new task."""
        self.cds_status = WorkerStatus.IDLE
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
        self.file_size = None
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
    status: FileStatus = FileStatus.PENDING
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
    def __init__(self, worker_id: int, cds_status: WorkerStatus) -> None:
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
    def __init__(self, worker_id: int, success: bool = True, error: str = "") -> None:
        super().__init__()
        self.worker_id = worker_id
        self.success = success
        self.error = error


class WorkerFileSize(Message):
    def __init__(self, worker_id: int, file_size: int) -> None:
        super().__init__()
        self.worker_id = worker_id
        self.file_size = file_size


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


class ServerStatsUpdate(Message):
    def __init__(
        self,
        server_running: int,
        server_queued: int,
        running_users: int,
        system_status: str,
    ) -> None:
        super().__init__()
        self.server_running = server_running
        self.server_queued = server_queued
        self.running_users = running_users
        self.system_status = system_status


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


# ---------------------------------------------------------------------------
# Status styling
# ---------------------------------------------------------------------------

STATUS_STYLES = {
    WorkerStatus.IDLE: "dim",
    WorkerStatus.ACCEPTED: "bold orange1",
    WorkerStatus.RUNNING: "bold yellow",
    WorkerStatus.SUCCESSFUL: "bold green",
    WorkerStatus.FAILED: "bold red",
    WorkerStatus.CANCELLED: "bold magenta",
}


def styled_status(status: WorkerStatus | FileStatus) -> Text:
    """Return a Rich Text with colored status badge."""
    style = STATUS_STYLES.get(status, "")
    return Text(status.value, style=style)


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

        # Queue wait
        left_info = ""
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
        counts: dict[FileStatus, int] = {}
        for fd in files:
            counts[fd.status] = counts.get(fd.status, 0) + 1
        parts = []
        for key in FileStatus:
            if counts.get(key, 0) > 0:
                parts.append(f"{counts[key]} {key.value}")
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
        if f.error:
            lines.append(f"[bold red]Error: {f.error}[/]")
        return "\n".join(lines)


class HeaderBar(Static):
    """Compact header bar: app title + worker count + clock."""

    pass


class MeterBar(Static):
    """Compact meter area showing progress bar and server status."""

    def render_progress(
        self,
        completed: int,
        total: int,
        skipped: int,
        eta_start: float | None,
        status: str,
        server_running: int = 0,
        server_queued: int = 0,
        running_users: int = 0,
        system_status: str = "",
    ) -> str:
        grand_total = total + skipped
        grand_done = completed + skipped

        if grand_total > 0:
            pct = grand_done * 100 / grand_total
            bar_width = 30
            filled = int(bar_width * grand_done / grand_total)
            bar = "\u2588" * filled + "\u2591" * (bar_width - filled)
            text = f"[bold]Progress[/] [yellow]\\[{bar}][/] {grand_done}/{grand_total}  {pct:.0f}%"
            if skipped:
                text += f"  ({skipped} cached)"
            if eta_start:
                elapsed = time.monotonic() - eta_start
                if completed > 0:
                    remaining = total - completed
                    eta_seconds = (elapsed / completed) * remaining
                    text += f"  ETA {format_eta(eta_seconds)}"
                else:
                    text += "  ETA ..."
            elif completed == 0 and total > 0:
                text += "  ETA ..."
        else:
            text = "Preparing..."

        # Server status line
        parts = []

        # Server-wide stats
        if server_queued > 0 or server_running > 0:
            dot = ""
            if system_status:
                status_lower = system_status.lower()
                if status_lower == "ok":
                    dot = "[green]\u25cf[/] "
                elif status_lower == "degraded":
                    dot = "[yellow]\u25cf[/] "
                elif status_lower == "down":
                    dot = "[red]\u25cf[/] "
            parts.append(
                f"{dot}Server: {server_queued} queued \u2502 "
                f"{server_running} running \u2502 {running_users} users"
            )

        if status:
            parts.append(status)
        status_line = " \u2502 ".join(parts) if parts else ""

        if status_line:
            text += f"\n[dim]{status_line}[/]"

        return text


class TabStrip(Static):
    """Lightweight tab selector strip."""

    def on_click(self, event) -> None:
        # "Workers" occupies roughly x < 10, "Files" x >= 10
        app = self.app
        if event.x < 10:
            if app._active_tab != "workers":
                app.action_switch_tab()
        else:
            if app._active_tab != "files":
                app.action_switch_tab()


class KeyBar(Static):
    """htop-style key hint bar at the bottom."""

    def on_click(self, event) -> None:
        # Approximate hit zones for: q Quit | t Tab | Enter Logs | a Params
        app = self.app
        x = event.x
        if x < 10:
            app.action_quit_app()
        elif x < 18:
            app.action_switch_tab()
        elif x < 31:
            app._open_worker_logs()
        else:
            app.action_open_params()


# ---------------------------------------------------------------------------
# Screens
# ---------------------------------------------------------------------------


class _BackBar(Static):
    """Static that dismisses the parent screen on click."""

    def on_click(self) -> None:
        self.screen.dismiss()


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
        yield _BackBar(
            f" Worker {self.worker_id} \u2014 {self.filename}",
            id="log-header",
        )
        yield RichLog(id="log-content", wrap=True, highlight=True)
        yield _BackBar(
            " [bold white on #444444] Esc [/] Back",
            id="log-keybar",
        )

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
        yield _BackBar(
            f" Worker {self.worker_id} \u2014 Parameters",
            id="params-header",
        )
        if self.labels:
            param_str = "\n".join(f"[bold]{k}:[/] {v}" for k, v in self.labels.items())
        elif self.params:
            param_str = "\n".join(f"[bold]{k}:[/] {v}" for k, v in self.params.items())
        else:
            param_str = "\u2014"
        yield Static(param_str, id="params-content")
        yield _BackBar(
            " [bold white on #444444] Esc [/] Back",
            id="params-keybar",
        )


# ---------------------------------------------------------------------------
# Worker table column keys
# ---------------------------------------------------------------------------

WORKER_COLUMNS = [
    ("W", 4),
    ("Status", 12),
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


HTOP_THEME = Theme(
    name="htop",
    primary="#00d4aa",
    secondary="#16213e",
    background="#0a0a0a",
    surface="#0a0a0a",
    panel="#0f0f0f",
    boost="#1a1a1a",
    accent="#00d4aa",
    dark=True,
)


class CdsswarmApp(App):
    """Textual TUI for cdsswarm concurrent downloads."""

    TITLE = "cdsswarm"
    CSS = """
    .hidden {
        display: none;
    }
    #header-bar {
        dock: top;
        height: 1;
        background: #16213e;
        color: #00d4aa;
        text-style: bold;
        padding: 0 1;
    }
    #meter-bar {
        height: auto;
        max-height: 4;
        border: round #444444;
        padding: 0 1;
        margin: 0;
        background: #0f0f0f;
    }
    #tab-strip {
        height: 1;
        padding: 0 1;
        background: #1a1a1a;
    }
    #worker-info, #files-info {
        height: auto;
        max-height: 6;
        padding: 0 1;
        margin: 0;
        background: #0f0f0f;
    }
    #worker-table, #files-table {
        height: 1fr;
        background: #0a0a0a;
    }
    DataTable > .datatable--header {
        text-style: bold;
        color: #00d4aa;
        background: #1a1a1a;
    }
    DataTable > .datatable--cursor {
        background: #003333;
        color: #e0e0e0;
    }
    DataTable > .datatable--even-row {
        background: #0a0a0a;
    }
    DataTable > .datatable--odd-row {
        background: #0e0e0e;
    }
    #key-bar {
        dock: bottom;
        height: 1;
        background: #000000;
        padding: 0 1;
    }
    LogScreen {
        background: #0a0a0a;
    }
    LogScreen RichLog {
        height: 1fr;
        background: #0a0a0a;
    }
    LogScreen #log-header {
        height: 1;
        background: #16213e;
        color: #00d4aa;
        text-style: bold;
        padding: 0 1;
    }
    LogScreen #log-keybar {
        dock: bottom;
        height: 1;
        background: #000000;
        padding: 0 1;
    }
    ParamsScreen {
        background: #0a0a0a;
    }
    ParamsScreen #params-header {
        height: 1;
        background: #16213e;
        color: #00d4aa;
        text-style: bold;
        padding: 0 1;
    }
    ParamsScreen Static#params-content {
        height: 1fr;
        padding: 1 2;
        background: #0a0a0a;
    }
    ParamsScreen #params-keybar {
        dock: bottom;
        height: 1;
        background: #000000;
        padding: 0 1;
    }
    """

    BINDINGS = [
        Binding("q", "quit_app", "Quit", priority=True),
        Binding("ctrl+c", "ctrl_c", "Force Quit", show=False, priority=True),
        Binding("t", "switch_tab", "Switch Tab"),
        Binding("tab", "switch_tab", "Switch Tab", show=False),
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
        self.register_theme(HTOP_THEME)
        self.theme = "htop"
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
        self.server_running = 0
        self.server_queued = 0
        self.server_running_users = 0
        self.server_system_status = ""

        # Tab state
        self._active_tab = "workers"

        # Cancel / quit state
        self._cancel_event = threading.Event()
        self._ctrl_c_count = 0

        # Download results
        self.download_results = None
        self._download_done = threading.Event()

    def compose(self) -> ComposeResult:
        yield HeaderBar(id="header-bar")
        yield MeterBar(id="meter-bar")
        yield TabStrip(id="tab-strip")
        # Workers view (visible by default)
        yield WorkerInfoPanel(id="worker-info")
        yield DataTable(id="worker-table", cursor_type="row")
        # Files view (hidden initially)
        yield FilesInfoPanel(id="files-info", classes="hidden")
        yield DataTable(id="files-table", cursor_type="row", classes="hidden")
        yield KeyBar(
            " [bold white on #444444] q [/] Quit  "
            "[bold white on #444444] t [/] Tab  "
            "[bold white on #444444] Enter [/] Logs  "
            "[bold white on #444444] a [/] Params",
            id="key-bar",
        )

    def on_mount(self) -> None:
        self.theme = "htop"
        self.title = self.app_title

        # Initial header bar
        self._update_header_bar()

        # Initial tab strip
        self._update_tab_strip()

        # Setup worker table
        wt = self.query_one("#worker-table", DataTable)
        for label, width in WORKER_COLUMNS:
            wt.add_column(label, key=label, width=width)
        for i in range(self.num_workers):
            wt.add_row(
                str(i),
                styled_status(WorkerStatus.IDLE),
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
        self._update_header_bar()
        wt = self.query_one("#worker-table", DataTable)
        for i, w in enumerate(self.worker_data):
            if w.start_time is not None and w.finish_time is None:
                elapsed = format_eta(time.time() - w.start_time)
                wt.update_cell(str(i), "Elapsed", elapsed)
        self._update_meter_bar()

    # -- Message handlers --

    def on_worker_started(self, msg: WorkerStarted) -> None:
        w = self.worker_data[msg.worker_id]
        w.cds_status = WorkerStatus.ACCEPTED
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
        w.file_size = None
        w.server_created = None
        w.server_started = None
        w.server_finished = None
        w.dataset_title = ""
        w.request_labels = None
        w.logs.append(f"Started: {msg.filename}")

        wt = self.query_one("#worker-table", DataTable)
        wt.update_cell(
            str(msg.worker_id), "Status", styled_status(WorkerStatus.ACCEPTED)
        )
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
        if w.cds_status is WorkerStatus.CANCELLED:
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
        if msg.success:
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
        else:
            wt.update_cell(
                str(msg.worker_id),
                "Elapsed",
                Text(f"{elapsed} \u2717", style="red"),
            )
            if msg.error:
                w.logs.append(f"FAILED: {msg.error}")
        self._update_worker_info()

    def on_worker_file_size(self, msg: WorkerFileSize) -> None:
        w = self.worker_data[msg.worker_id]
        w.file_size = msg.file_size
        if w.dl_total <= 0:
            w.dl_total = msg.file_size
            wt = self.query_one("#worker-table", DataTable)
            wt.update_cell(str(msg.worker_id), "Size", format_size(msg.file_size))

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
        self._update_meter_bar()

    def on_global_message(self, msg: GlobalMessage) -> None:
        self.status_message = msg.message
        self._update_meter_bar()

    def on_tasks_initialized(self, msg: TasksInitialized) -> None:
        self.files = []
        self.file_index = {}
        ft = self.query_one("#files-table", DataTable)
        ft.clear()
        for i, task in enumerate(msg.tasks):
            status = (
                FileStatus.CACHED
                if task.target in msg.skipped_targets
                else FileStatus.PENDING
            )
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
                if status is FileStatus.CACHED
                else Text(status.value, style="dim"),
                task.label,
                task.dataset,
                "\u2014",
                "\u2014",
                "\u2014",
                key=str(i),
            )
        self._update_files_info()

    def on_server_stats_update(self, msg: ServerStatsUpdate) -> None:
        self.server_running = msg.server_running
        self.server_queued = msg.server_queued
        self.server_running_users = msg.running_users
        self.server_system_status = msg.system_status
        self._update_meter_bar()

    def on_file_active(self, msg: FileActive) -> None:
        if msg.target in self.file_index:
            idx = self.file_index[msg.target]
            self.files[idx].status = FileStatus.ACTIVE
            self.files[idx].worker_id = msg.worker_id
            self.worker_to_target[msg.worker_id] = msg.target
            self._update_file_row(idx)
            self._update_files_info()

    def on_file_completed(self, msg: FileCompleted) -> None:
        if msg.target in self.file_index:
            idx = self.file_index[msg.target]
            f = self.files[idx]
            f.status = FileStatus.SUCCESSFUL if msg.success else FileStatus.FAILED
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
        w.cds_status = WorkerStatus.CANCELLED
        w.logs.append("Request cancelled")
        wt = self.query_one("#worker-table", DataTable)
        wt.update_cell(
            str(msg.worker_id),
            "Status",
            styled_status(WorkerStatus.CANCELLED),
        )

    # -- DataTable cursor change -> update info panel --

    @on(DataTable.RowHighlighted, "#worker-table")
    def _on_worker_cursor(self, event: DataTable.RowHighlighted) -> None:
        self._update_worker_info()

    @on(DataTable.RowHighlighted, "#files-table")
    def _on_files_cursor(self, event: DataTable.RowHighlighted) -> None:
        self._update_files_info()

    @on(DataTable.RowSelected, "#worker-table")
    def _on_worker_row_selected(self, event: DataTable.RowSelected) -> None:
        self._open_worker_logs()

    # -- Actions --

    def action_quit_app(self) -> None:
        if self.downloader is not None and not self._cancel_event.is_set():
            self._cancel_event.set()
            self.status_message = "Cancelling CDS requests..."
            self._update_meter_bar()
            self._cancel_and_exit()
        else:
            self.exit()

    def action_ctrl_c(self) -> None:
        self._ctrl_c_count += 1
        if self._ctrl_c_count >= 2:
            # Rage quit — exit immediately
            self.exit()
        else:
            # First Ctrl+C — graceful cancel
            self.action_quit_app()

    @work(thread=True)
    def _cancel_and_exit(self) -> None:
        """Cancel active CDS requests in a background thread, then exit."""
        if self.downloader is not None:
            self.downloader.cancel()
        self.call_from_thread(self.exit)

    def action_switch_tab(self) -> None:
        if self._active_tab == "workers":
            self._active_tab = "files"
            self.query_one("#worker-info").add_class("hidden")
            self.query_one("#worker-table").add_class("hidden")
            self.query_one("#files-info").remove_class("hidden")
            self.query_one("#files-table").remove_class("hidden")
        else:
            self._active_tab = "workers"
            self.query_one("#worker-info").remove_class("hidden")
            self.query_one("#worker-table").remove_class("hidden")
            self.query_one("#files-info").add_class("hidden")
            self.query_one("#files-table").add_class("hidden")
        self._update_tab_strip()

    def action_open_params(self) -> None:
        if self._active_tab != "workers":
            return
        wt = self.query_one("#worker-table", DataTable)
        row_idx = wt.cursor_row
        if 0 <= row_idx < self.num_workers:
            w = self.worker_data[row_idx]
            self.push_screen(ParamsScreen(row_idx, w.request_params, w.request_labels))

    # -- Internal helpers --

    def _open_worker_logs(self) -> None:
        """Open log screen for the currently selected worker."""
        if self._active_tab != "workers":
            return
        wt = self.query_one("#worker-table", DataTable)
        row_idx = wt.cursor_row
        if 0 <= row_idx < self.num_workers:
            w = self.worker_data[row_idx]
            self.push_screen(LogScreen(row_idx, w.filename or "\u2014", list(w.logs)))

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
            if f.status is FileStatus.SUCCESSFUL:
                ft.update_cell(str(idx), "DL %", Text(f"{pct}% \u2713", style="green"))
            else:
                ft.update_cell(str(idx), "DL %", f"{pct}%")

    def _update_header_bar(self) -> None:
        header = self.query_one("#header-bar", HeaderBar)
        clock = time.strftime("%H:%M:%S")
        header.update(
            f" {self.app_title} \u2500 {self.num_workers} workers{'':>40}{clock}"
        )

    def _update_tab_strip(self) -> None:
        strip = self.query_one("#tab-strip", TabStrip)
        if self._active_tab == "workers":
            strip.update(" [reverse] Workers [/]  Files")
        else:
            strip.update(" Workers  [reverse] Files [/]")

    def _update_meter_bar(self) -> None:
        meter = self.query_one("#meter-bar", MeterBar)
        meter.update(
            meter.render_progress(
                self.progress_completed,
                self.progress_total,
                self.progress_skipped,
                self.eta_start_time,
                self.status_message,
                self.server_running,
                self.server_queued,
                self.server_running_users,
                self.server_system_status,
            )
        )
