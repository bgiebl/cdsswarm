"""Curses-based TUI for displaying concurrent download worker status."""

import curses
import shutil
import threading
import time
from collections import deque

# Color pair indices
_CP_GREEN_TEXT = 1
_CP_RED_TEXT = 2
_CP_BORDER = 3
_CP_PROGRESS = 4
_CP_STATUS_LABEL = 5
_CP_STATUS_FAILED = 6
_CP_STATUS_ACCEPTED = 7
_CP_STATUS_RUNNING = 8
_CP_STATUS_SUCCESS = 9
_CP_WORKER_LABEL = 10
_CP_STATUS_CANCELLED = 11
_CP_HEADER = 12


def _format_eta(seconds: float) -> str:
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


def _format_size(nbytes: int) -> str:
    """Format byte count into human-readable size."""
    if nbytes <= 0:
        return "—"
    if nbytes >= 1024**3:
        return f"{nbytes / (1024**3):.1f} GB"
    if nbytes >= 1024**2:
        return f"{nbytes / (1024**2):.1f} MB"
    if nbytes >= 1024:
        return f"{nbytes / 1024:.1f} KB"
    return f"{nbytes} B"


class CursesTUI:
    """Terminal UI showing an htop-style worker table with a progress bar."""

    MIN_HEIGHT = 10
    MIN_WIDTH = 40
    PROGRESS_ROWS = 2
    HEADER_ROWS = 2  # title bar + column headers

    _STATUS_COLORS = {
        "accepted": _CP_STATUS_ACCEPTED,
        "running": _CP_STATUS_RUNNING,
        "successful": _CP_STATUS_SUCCESS,
        "failed": _CP_STATUS_FAILED,
        "cancelled": _CP_STATUS_CANCELLED,
    }

    # Fixed column widths
    _COL_WORKER = 8
    _COL_STATUS = 12
    _COL_FILENAME = 20
    _COL_TYPE = 6
    _COL_STARTED = 10
    _COL_ELAPSED = 10
    _COL_FINISHED = 10
    _COL_SIZE = 10
    _COL_DL_PCT = 7

    def __init__(self, num_workers: int, title: str = "cdsswarm"):
        self._num_workers = num_workers
        self._title = title
        self._lock = threading.Lock()
        self._stdscr = None
        self._worker_status = ["idle"] * num_workers
        self._worker_cds_status: list[str | None] = [None] * num_workers
        self._worker_request_id = [""] * num_workers
        self._worker_logs: list[deque] = [deque(maxlen=100) for _ in range(num_workers)]
        self._progress_completed = 0
        self._progress_total = 0
        self._progress_skipped = 0
        self._status_line = ""
        self._eta_start_time = None
        self._last_size: tuple[int, int] = (0, 0)

        # htop-style table state
        self._selected_worker: int = 0
        self._expanded_workers: set[int] = set()
        self._worker_start_time: list[float | None] = [None] * num_workers
        self._worker_finish_time: list[float | None] = [None] * num_workers
        self._worker_dl_bytes: list[int] = [0] * num_workers
        self._worker_dl_total: list[int] = [0] * num_workers
        self._worker_filename: list[str] = [""] * num_workers
        self._row_worker_map: dict[int, int] = {}
        self._table_scroll: int = 0

    def start(self, stdscr):
        self._stdscr = stdscr
        curses.curs_set(0)
        curses.use_default_colors()
        stdscr.nodelay(True)
        stdscr.clear()
        self._init_colors()
        curses.mousemask(curses.ALL_MOUSE_EVENTS)
        curses.mouseinterval(0)
        self.refresh()

    def _init_colors(self):
        curses.init_pair(_CP_GREEN_TEXT, curses.COLOR_GREEN, -1)
        curses.init_pair(_CP_RED_TEXT, curses.COLOR_RED, -1)
        curses.init_pair(_CP_BORDER, curses.COLOR_CYAN, -1)
        curses.init_pair(_CP_PROGRESS, curses.COLOR_YELLOW, -1)
        curses.init_pair(_CP_STATUS_LABEL, curses.COLOR_BLACK, curses.COLOR_WHITE)
        curses.init_pair(_CP_STATUS_FAILED, curses.COLOR_WHITE, curses.COLOR_RED)
        if curses.COLORS >= 256:
            curses.init_pair(_CP_STATUS_ACCEPTED, curses.COLOR_BLACK, 208)
        else:
            curses.init_pair(
                _CP_STATUS_ACCEPTED, curses.COLOR_BLACK, curses.COLOR_YELLOW
            )
        curses.init_pair(_CP_STATUS_SUCCESS, curses.COLOR_BLACK, curses.COLOR_GREEN)
        curses.init_pair(_CP_WORKER_LABEL, curses.COLOR_WHITE, curses.COLOR_CYAN)
        curses.init_pair(_CP_STATUS_CANCELLED, curses.COLOR_WHITE, curses.COLOR_MAGENTA)
        curses.init_pair(_CP_STATUS_RUNNING, curses.COLOR_BLACK, curses.COLOR_YELLOW)
        curses.init_pair(_CP_HEADER, curses.COLOR_WHITE, curses.COLOR_BLUE)

    # -- Drawing methods --

    def _draw_header(self, width):
        scr = self._stdscr
        scr.move(0, 0)
        scr.clrtoeol()
        hints = "[q] quit  [↑/↓] select  [Enter] expand"
        title = f" {self._title}"
        header_text = title.ljust(width - 1)
        scr.addnstr(
            0, 0, header_text, width - 1, curses.color_pair(_CP_HEADER) | curses.A_BOLD
        )
        hint_col = width - len(hints) - 2
        if hint_col > len(title):
            scr.addnstr(
                0, hint_col, hints, width - hint_col - 1, curses.color_pair(_CP_HEADER)
            )

    def _draw_column_headers(self, width):
        scr = self._stdscr
        row = 1
        scr.move(row, 0)
        scr.clrtoeol()

        cols = self._column_specs(width)
        parts = []
        for label, w in cols:
            parts.append(label[:w].ljust(w))
        header_line = "│".join(parts)
        scr.addnstr(
            row,
            0,
            header_line,
            width - 1,
            curses.A_BOLD | curses.A_UNDERLINE,
        )

    def _column_specs(self, width):
        """Return list of (label, col_width) for the table columns."""
        fixed = (
            self._COL_WORKER
            + self._COL_STATUS
            + self._COL_FILENAME
            + self._COL_TYPE
            + self._COL_STARTED
            + self._COL_ELAPSED
            + self._COL_FINISHED
            + self._COL_SIZE
            + self._COL_DL_PCT
            + 9  # 9 separators
        )
        req_id_width = max(8, width - fixed)
        return [
            ("Worker", self._COL_WORKER),
            ("Status", self._COL_STATUS),
            ("Filename", self._COL_FILENAME),
            ("Type", self._COL_TYPE),
            ("Started", self._COL_STARTED),
            ("Elapsed", self._COL_ELAPSED),
            ("Finished", self._COL_FINISHED),
            ("Size", self._COL_SIZE),
            ("DL %", self._COL_DL_PCT),
            ("Request ID", req_id_width),
        ]

    def _draw_table(self, width, available_height):
        scr = self._stdscr
        self._row_worker_map.clear()

        # Build the list of screen rows: each worker takes 1 row, plus
        # expanded workers add up to 3 log lines below
        row_entries = []  # list of (worker_id, is_log_line, log_text)
        for wid in range(self._num_workers):
            row_entries.append((wid, False, ""))
            if wid in self._expanded_workers:
                logs = list(self._worker_logs[wid])
                recent = logs[-3:] if logs else []
                for line in recent:
                    row_entries.append((wid, True, line))

        # Apply scroll
        max_scroll = max(0, len(row_entries) - available_height)
        self._table_scroll = max(0, min(self._table_scroll, max_scroll))
        visible = row_entries[
            self._table_scroll : self._table_scroll + available_height
        ]

        cols = self._column_specs(width)

        for i, (wid, is_log, log_text) in enumerate(visible):
            screen_row = self.HEADER_ROWS + i
            scr.move(screen_row, 0)
            scr.clrtoeol()

            if is_log:
                # Draw log line: dimmed, indented
                display = f"  Log: {log_text}"
                scr.addnstr(
                    screen_row, 0, display[: width - 1], width - 1, curses.A_DIM
                )
            else:
                # Draw worker row
                self._row_worker_map[screen_row] = wid
                is_selected = wid == self._selected_worker
                self._draw_worker_row(screen_row, wid, is_selected, cols, width)

        # Clear remaining rows
        for i in range(len(visible), available_height):
            screen_row = self.HEADER_ROWS + i
            scr.move(screen_row, 0)
            scr.clrtoeol()

    def _draw_worker_row(self, screen_row, wid, is_selected, cols, width):
        scr = self._stdscr

        # Build cell values
        indicator = "▸" if is_selected else " "
        worker_str = f"{indicator} {wid}"
        cds_status = self._worker_cds_status[wid] or "idle"
        filename = self._worker_filename[wid] or "—"
        filetype = self._format_filetype(wid)
        started = self._format_start_time(wid)
        elapsed = self._format_elapsed_time(wid)
        finished = self._format_finish_time(wid)
        size = self._format_dl_size(wid)
        dl_pct = self._format_dl_pct(wid)
        request_id = self._worker_request_id[wid] or "—"

        values = [
            worker_str,
            cds_status,
            filename,
            filetype,
            started,
            elapsed,
            finished,
            size,
            dl_pct,
            request_id,
        ]
        is_finished = self._worker_finish_time[wid] is not None

        row_attr = curses.A_REVERSE if is_selected else 0

        col_pos = 0
        for idx, ((label, w), val) in enumerate(zip(cols, values)):
            # Draw separator
            if idx > 0 and col_pos < width - 1:
                scr.addnstr(screen_row, col_pos, "│", 1, row_attr)
                col_pos += 1

            cell_text = val[:w].ljust(w)
            remaining = width - col_pos - 1
            if remaining <= 0:
                break

            # Status column gets colored badge
            if label == "Status":
                color_pair = self._STATUS_COLORS.get(cds_status, 0)
                if color_pair:
                    badge = f" {cds_status} "
                    pad_right = " " * max(0, w - len(badge))
                    badge_len = min(len(badge), remaining)
                    scr.addnstr(
                        screen_row,
                        col_pos,
                        badge,
                        badge_len,
                        curses.color_pair(color_pair),
                    )
                    pad_start = col_pos + len(badge)
                    pad_remaining = min(len(pad_right), width - pad_start - 1)
                    if pad_remaining > 0:
                        scr.addnstr(
                            screen_row, pad_start, pad_right, pad_remaining, row_attr
                        )
                else:
                    scr.addnstr(
                        screen_row, col_pos, cell_text, min(w, remaining), row_attr
                    )
            elif label == "Finished" and is_finished:
                # Green background for completed tasks
                scr.addnstr(
                    screen_row,
                    col_pos,
                    cell_text,
                    min(w, remaining),
                    curses.color_pair(_CP_STATUS_SUCCESS),
                )
            else:
                scr.addnstr(screen_row, col_pos, cell_text, min(w, remaining), row_attr)

            col_pos += w

    def _draw_progress_bar(self, height, width):
        scr = self._stdscr
        bar_row = height - 2
        status_row = height - 1
        pending = self._progress_total
        done = self._progress_completed
        skipped = self._progress_skipped

        grand_total = pending + skipped
        grand_done = done + skipped

        if grand_total > 0:
            pct = grand_done * 100 / grand_total
            bar_width = max(10, width - 50)
            filled = int(bar_width * grand_done / grand_total)
            bar = "█" * filled + "░" * (bar_width - filled)
            text = f" [{bar}] {grand_done}/{grand_total}  {pct:.0f}%"
            if skipped:
                text += f"  ({skipped} cached)"
            if self._eta_start_time:
                elapsed = time.monotonic() - self._eta_start_time
                text += f"  Elapsed: {_format_eta(elapsed)}"
                if done > 0:
                    remaining_tasks = pending - done
                    eta_seconds = (elapsed / done) * remaining_tasks
                    text += f"  ETA: {_format_eta(eta_seconds)}"
            elif done == 0 and pending > 0:
                text += "  ETA: estimating..."
        else:
            text = " Preparing..."

        scr.move(bar_row, 0)
        scr.clrtoeol()
        scr.addnstr(bar_row, 0, text, width - 1, curses.color_pair(_CP_PROGRESS))
        scr.move(status_row, 0)
        scr.clrtoeol()
        if self._status_line:
            scr.addnstr(status_row, 0, " " + self._status_line[: width - 2], width - 1)

    # -- Formatting helpers --

    def _format_start_time(self, wid):
        t = self._worker_start_time[wid]
        if t is None:
            return "—"
        return time.strftime("%H:%M:%S", time.localtime(t))

    def _format_elapsed_time(self, wid):
        start = self._worker_start_time[wid]
        if start is None:
            return "—"
        finish = self._worker_finish_time[wid]
        end = finish if finish is not None else time.time()
        elapsed = max(0, end - start)
        return _format_eta(elapsed)

    def _format_filetype(self, wid):
        fname = self._worker_filename[wid]
        if not fname:
            return "—"
        dot = fname.rfind(".")
        if dot < 0:
            return "—"
        return fname[dot + 1 :]

    def _format_finish_time(self, wid):
        t = self._worker_finish_time[wid]
        if t is None:
            return "—"
        return time.strftime("%H:%M:%S", time.localtime(t))

    def _format_dl_size(self, wid):
        total = self._worker_dl_total[wid]
        if total <= 0:
            return "—"
        return _format_size(total)

    def _format_dl_pct(self, wid):
        total = self._worker_dl_total[wid]
        if total <= 0:
            return "—"
        pct = int(self._worker_dl_bytes[wid] * 100 / total)
        return f"{pct}%"

    # -- Input handling methods (called from main thread) --

    def handle_resize(self):
        with self._lock:
            if self._stdscr:
                size = shutil.get_terminal_size()
                curses.resizeterm(size.lines, size.columns)
                self._do_refresh()

    def select_up(self):
        with self._lock:
            self._selected_worker = max(0, self._selected_worker - 1)
            self._ensure_selected_visible()
            self._do_refresh()

    def select_down(self):
        with self._lock:
            self._selected_worker = min(
                self._num_workers - 1, self._selected_worker + 1
            )
            self._ensure_selected_visible()
            self._do_refresh()

    def toggle_expand(self):
        with self._lock:
            wid = self._selected_worker
            if wid in self._expanded_workers:
                self._expanded_workers.discard(wid)
            else:
                self._expanded_workers.add(wid)
            self._do_refresh()

    def select_worker(self, worker_id):
        with self._lock:
            if 0 <= worker_id < self._num_workers:
                self._selected_worker = worker_id
                self._ensure_selected_visible()
                self._do_refresh()

    def handle_mouse(self, mouse_event):
        """Handle mouse click/scroll events.

        mouse_event: tuple from curses.getmouse() — (id, x, y, z, bstate)
        """
        with self._lock:
            _, mx, my, _, bstate = mouse_event
            if bstate & curses.BUTTON1_CLICKED:
                wid = self._row_worker_map.get(my)
                if wid is not None:
                    self._selected_worker = wid
            elif bstate & curses.BUTTON1_DOUBLE_CLICKED:
                wid = self._row_worker_map.get(my)
                if wid is not None:
                    self._selected_worker = wid
                    if wid in self._expanded_workers:
                        self._expanded_workers.discard(wid)
                    else:
                        self._expanded_workers.add(wid)
            elif bstate & curses.BUTTON4_PRESSED:
                # Scroll up
                self._selected_worker = max(0, self._selected_worker - 1)
                self._ensure_selected_visible()
            elif bstate & curses.BUTTON5_PRESSED:
                # Scroll down
                self._selected_worker = min(
                    self._num_workers - 1, self._selected_worker + 1
                )
                self._ensure_selected_visible()
            self._do_refresh()

    def _ensure_selected_visible(self):
        """Adjust table_scroll so the selected worker row is visible."""
        # Build row index for selected worker
        row_idx = 0
        for wid in range(self._num_workers):
            if wid == self._selected_worker:
                break
            row_idx += 1
            if wid in self._expanded_workers:
                logs = list(self._worker_logs[wid])
                row_idx += min(3, len(logs))

        if self._stdscr:
            height, _ = self._stdscr.getmaxyx()
            available = height - self.HEADER_ROWS - self.PROGRESS_ROWS
            if available > 0:
                if row_idx < self._table_scroll:
                    self._table_scroll = row_idx
                elif row_idx >= self._table_scroll + available:
                    self._table_scroll = row_idx - available + 1

    # -- Thread-safe public methods --

    def set_worker_status(self, worker_id, status):
        with self._lock:
            if 0 <= worker_id < self._num_workers:
                self._worker_status[worker_id] = status
                self._do_refresh()

    def set_worker_cds_status(self, worker_id, cds_status):
        with self._lock:
            if 0 <= worker_id < self._num_workers:
                if self._worker_cds_status[worker_id] == "cancelled":
                    return
                self._worker_cds_status[worker_id] = cds_status
                self._do_refresh()

    def set_worker_request_id(self, worker_id, request_id):
        with self._lock:
            if 0 <= worker_id < self._num_workers:
                self._worker_request_id[worker_id] = request_id
                self._do_refresh()

    def set_worker_filename(self, worker_id, filename):
        with self._lock:
            if 0 <= worker_id < self._num_workers:
                self._worker_filename[worker_id] = filename
                if self._worker_start_time[worker_id] is None:
                    self._worker_start_time[worker_id] = time.time()
                self._do_refresh()

    def set_worker_finished(self, worker_id):
        with self._lock:
            if 0 <= worker_id < self._num_workers:
                self._worker_finish_time[worker_id] = time.time()
                self._do_refresh()

    def update_worker_progress(self, worker_id, downloaded_bytes, total_bytes):
        with self._lock:
            if 0 <= worker_id < self._num_workers:
                self._worker_dl_bytes[worker_id] = downloaded_bytes
                self._worker_dl_total[worker_id] = total_bytes
                self._do_refresh()

    def append_worker_log(self, worker_id, message):
        with self._lock:
            if 0 <= worker_id < self._num_workers:
                self._worker_logs[worker_id].append(message)
                self._do_refresh()

    def clear_worker_log(self, worker_id):
        with self._lock:
            if 0 <= worker_id < self._num_workers:
                self._worker_logs[worker_id].clear()
                self._worker_start_time[worker_id] = None
                self._worker_finish_time[worker_id] = None
                self._worker_dl_bytes[worker_id] = 0
                self._worker_dl_total[worker_id] = 0
                self._worker_filename[worker_id] = ""

    def update_progress(self, completed, total, skipped):
        with self._lock:
            if self._eta_start_time is None and total > 0:
                self._eta_start_time = time.monotonic()
            self._progress_completed = completed
            self._progress_total = total
            self._progress_skipped = skipped
            self._do_refresh()

    def set_status_line(self, message):
        with self._lock:
            self._status_line = message
            self._do_refresh()

    def refresh(self):
        with self._lock:
            self._do_refresh()

    def _do_refresh(self):
        if not self._stdscr:
            return
        try:
            height, width = self._stdscr.getmaxyx()
            cur_size = (height, width)

            if height < self.MIN_HEIGHT or width < self.MIN_WIDTH:
                self._stdscr.erase()
                self._stdscr.addstr(0, 0, f"Terminal too small ({width}x{height})")
                self._stdscr.noutrefresh()
                curses.doupdate()
                self._last_size = cur_size
                return

            if cur_size != self._last_size:
                self._stdscr.erase()
                self._last_size = cur_size

            self._draw_header(width)
            self._draw_column_headers(width)
            available_height = height - self.HEADER_ROWS - self.PROGRESS_ROWS
            self._draw_table(width, available_height)
            self._draw_progress_bar(height, width)
            self._stdscr.noutrefresh()
            curses.doupdate()
        except curses.error:
            pass
