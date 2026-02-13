"""Curses-based TUI for displaying concurrent download worker status."""

import curses
import os
import shutil
import textwrap
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
_CP_WORKER_BADGE = 13
_CP_COL_HEADER = 14
_CP_SELECTED_ROW = 15


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

    MIN_HEIGHT = 17
    MIN_WIDTH = 40
    PROGRESS_ROWS = 2
    HEADER_ROWS = 14  # title + info panel box (12 rows) + column headers
    _INFO_PANEL_PARAM_LINES = 4

    _STATUS_COLORS = {
        "accepted": _CP_STATUS_ACCEPTED,
        "running": _CP_STATUS_RUNNING,
        "successful": _CP_STATUS_SUCCESS,
        "failed": _CP_STATUS_FAILED,
        "cancelled": _CP_STATUS_CANCELLED,
    }

    # Fixed column widths
    _COL_W = 4
    _COL_STATUS = 12
    _COL_FILENAME = 20
    _COL_STARTED = 10
    _COL_ELAPSED = 10
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
        self._worker_start_time: list[float | None] = [None] * num_workers
        self._worker_finish_time: list[float | None] = [None] * num_workers
        self._worker_dl_bytes: list[int] = [0] * num_workers
        self._worker_dl_total: list[int] = [0] * num_workers
        self._worker_filename: list[str] = [""] * num_workers
        self._row_worker_map: dict[int, int] = {}
        self._table_scroll: int = 0

        # Info panel data
        self._worker_dataset: list[str] = [""] * num_workers
        self._worker_request_params: list[dict] = [{}] * num_workers
        self._worker_target: list[str] = [""] * num_workers

        # Log/params view state
        self._view_mode: str = "table"  # "table", "logs", or "params"
        self._log_scroll: int = 0
        self._params_scroll: int = 0

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
        curses.init_pair(_CP_STATUS_FAILED, curses.COLOR_RED, -1)
        curses.init_pair(_CP_STATUS_SUCCESS, curses.COLOR_GREEN, -1)
        curses.init_pair(_CP_STATUS_CANCELLED, curses.COLOR_MAGENTA, -1)
        curses.init_pair(_CP_STATUS_RUNNING, curses.COLOR_YELLOW, -1)
        curses.init_pair(_CP_COL_HEADER, curses.COLOR_BLACK, curses.COLOR_GREEN)
        if curses.COLORS >= 256:
            curses.init_pair(_CP_STATUS_ACCEPTED, 208, -1)
            curses.init_pair(_CP_WORKER_BADGE, curses.COLOR_WHITE, 39)
            curses.init_pair(_CP_SELECTED_ROW, curses.COLOR_WHITE, 24)
        else:
            curses.init_pair(_CP_STATUS_ACCEPTED, curses.COLOR_YELLOW, -1)
            curses.init_pair(_CP_WORKER_BADGE, curses.COLOR_WHITE, curses.COLOR_CYAN)
            curses.init_pair(_CP_SELECTED_ROW, curses.COLOR_WHITE, curses.COLOR_BLUE)
        curses.init_pair(_CP_WORKER_LABEL, curses.COLOR_WHITE, curses.COLOR_CYAN)
        curses.init_pair(_CP_HEADER, curses.COLOR_WHITE, curses.COLOR_BLUE)

    # -- Drawing methods --

    def _draw_header(self, width):
        scr = self._stdscr
        scr.move(0, 0)
        scr.clrtoeol()
        if self._view_mode == "logs":
            wid = self._selected_worker
            fname = self._worker_filename[wid] or "—"
            hints = "[Esc] back  [↑/↓] scroll  [PgUp/PgDn] page  [q] quit"
            title = f" Worker {wid} — {fname}"
        elif self._view_mode == "params":
            wid = self._selected_worker
            hints = "[Esc] back  [↑/↓] scroll  [PgUp/PgDn] page  [q] quit"
            title = f" Worker {wid} — Parameters"
        else:
            hints = "[q] quit  [↑/↓] select  [Enter] logs"
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

    def _draw_info_panel(self, width):
        """Draw the info panel for the selected worker (rows 1-12) with box drawing."""
        scr = self._stdscr
        wid = self._selected_worker
        info_attr = 0
        box_w = width - 1
        inner_w = max(1, box_w - 2)

        filename = self._worker_filename[wid] or "—"
        filetype = self._format_filetype(wid).upper()
        request_id = self._worker_request_id[wid] or "—"
        dataset = self._worker_dataset[wid] or "—"
        target = self._worker_target[wid] or "—"
        params = self._worker_request_params[wid]

        def _hline(row, left, right, junctions=None):
            chars = list("─" * inner_w)
            if junctions:
                for pos, ch in junctions:
                    if 0 <= pos < inner_w:
                        chars[pos] = ch
            line = left + "".join(chars) + right
            scr.move(row, 0)
            scr.clrtoeol()
            scr.addnstr(row, 0, line[:box_w], box_w, info_attr)

        def _content(row, text, attr=0):
            padded = text[:inner_w].ljust(inner_w)
            line = "│" + padded + "│"
            scr.move(row, 0)
            scr.clrtoeol()
            scr.addnstr(row, 0, line[:box_w], box_w, info_attr | attr)

        # Build content texts to find separator positions
        worker_badge = f" Worker {wid} "
        row2_text = f"{worker_badge} │ Type: {filetype} │ Filename: {filename}"
        row6_text = f" Request ID: {request_id} │ Dataset: {dataset}"
        row2_seps = [i for i, ch in enumerate(row2_text) if ch == "│"]
        row6_seps = [i for i, ch in enumerate(row6_text) if ch == "│"]

        # Row 1: top border with ┬ at row 2's separator positions
        _hline(1, "┌", "┐", [(p, "┬") for p in row2_seps])

        # Row 2: Worker badge, Type, Filename
        _content(2, row2_text)
        scr.addnstr(
            2,
            1,
            worker_badge,
            min(len(worker_badge), inner_w),
            curses.color_pair(_CP_WORKER_BADGE) | curses.A_BOLD,
        )

        # Row 3: divider with ┴ from row 2
        _hline(3, "├", "┤", [(p, "┴") for p in row2_seps])

        # Row 4: Destination
        dest_dir = os.path.dirname(target) if target != "—" else "—"
        _content(4, f" Destination: {dest_dir}")

        # Row 5: divider with ┬ for row 6
        _hline(5, "├", "┤", [(p, "┬") for p in row6_seps])

        # Row 6: Request ID, Dataset
        _content(6, row6_text)

        # Row 7: divider with ┴ from row 6
        _hline(7, "├", "┤", [(p, "┴") for p in row6_seps])

        # Rows 8-11: Params (up to 4 lines, ▼ if truncated)
        if params:
            param_str = "Params: " + ", ".join(f"{k}={v}" for k, v in params.items())
        else:
            param_str = "Params: —"

        line_width = max(10, inner_w - 1)
        wrapped = textwrap.wrap(param_str, width=line_width) or [param_str]
        truncated = len(wrapped) > self._INFO_PANEL_PARAM_LINES

        for i in range(self._INFO_PANEL_PARAM_LINES):
            row = 8 + i
            if i < len(wrapped):
                text = f" {wrapped[i]}"
                if truncated and i == self._INFO_PANEL_PARAM_LINES - 1:
                    text = text[: inner_w - 1]
            else:
                text = ""
            _content(row, text, curses.A_DIM)

        # Show "[a] show all" badge on last param row when truncated
        if truncated:
            badge = " [a] show all "
            badge_col = max(2, 1 + inner_w - len(badge))
            scr.addnstr(
                11,
                badge_col,
                badge,
                min(len(badge), box_w - badge_col),
                curses.color_pair(_CP_HEADER) | curses.A_BOLD,
            )

        # Row 12: bottom border (separator before table)
        _hline(12, "└", "┘")

    def _draw_column_headers(self, width):
        scr = self._stdscr
        row = self.HEADER_ROWS - 1  # last row before table
        scr.move(row, 0)
        scr.clrtoeol()

        cols = self._column_specs(width)
        parts = []
        for label, w in cols:
            parts.append(label[:w].ljust(w))
        header_line = " ".join(parts).ljust(width - 1)
        scr.addnstr(
            row,
            0,
            header_line,
            width - 1,
            curses.color_pair(_CP_COL_HEADER) | curses.A_BOLD,
        )

    def _column_specs(self, width):
        """Return list of (label, col_width) for the table columns."""
        fixed = (
            self._COL_W
            + self._COL_STATUS
            + self._COL_FILENAME
            + self._COL_STARTED
            + self._COL_ELAPSED
            + self._COL_SIZE
            + self._COL_DL_PCT
            + 7  # 7 separators
        )
        req_id_width = max(8, width - fixed)
        return [
            ("W", self._COL_W),
            ("Status", self._COL_STATUS),
            ("Filename", self._COL_FILENAME),
            ("Started", self._COL_STARTED),
            ("Elapsed", self._COL_ELAPSED),
            ("Size", self._COL_SIZE),
            ("DL %", self._COL_DL_PCT),
            ("Request ID", req_id_width),
        ]

    def _draw_table(self, width, available_height):
        scr = self._stdscr
        self._row_worker_map.clear()

        # Build the list of screen rows: one per worker
        row_entries = []
        for wid in range(self._num_workers):
            row_entries.append(wid)

        # Apply scroll
        max_scroll = max(0, len(row_entries) - available_height)
        self._table_scroll = max(0, min(self._table_scroll, max_scroll))
        visible = row_entries[
            self._table_scroll : self._table_scroll + available_height
        ]

        cols = self._column_specs(width)

        for i, wid in enumerate(visible):
            screen_row = self.HEADER_ROWS + i
            scr.move(screen_row, 0)
            scr.clrtoeol()

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
        worker_str = f"{indicator}{wid}"
        cds_status = self._worker_cds_status[wid] or "idle"
        filename = self._worker_filename[wid] or "—"
        started = self._format_start_time(wid)
        elapsed = self._format_elapsed_time(wid)
        size = self._format_dl_size(wid)
        dl_pct = self._format_dl_pct(wid)
        request_id = self._worker_request_id[wid] or "—"

        is_finished = self._worker_finish_time[wid] is not None
        if is_finished:
            elapsed = elapsed + " ✓"
            dl_pct = dl_pct + " ✓"

        values = [
            worker_str,
            cds_status,
            filename,
            started,
            elapsed,
            size,
            dl_pct,
            request_id,
        ]

        row_attr = curses.color_pair(_CP_SELECTED_ROW) if is_selected else 0

        col_pos = 0
        for idx, ((label, w), val) in enumerate(zip(cols, values)):
            # Draw space separator
            if idx > 0 and col_pos < width - 1:
                scr.addnstr(screen_row, col_pos, " ", 1, row_attr)
                col_pos += 1

            cell_text = val[:w].ljust(w)
            remaining = width - col_pos - 1
            if remaining <= 0:
                break

            # Status column gets foreground color (non-selected rows only)
            if label == "Status" and not is_selected:
                color_pair = self._STATUS_COLORS.get(cds_status, 0)
                if color_pair:
                    scr.addnstr(
                        screen_row,
                        col_pos,
                        cell_text,
                        min(w, remaining),
                        curses.color_pair(color_pair),
                    )
                else:
                    scr.addnstr(
                        screen_row, col_pos, cell_text, min(w, remaining), row_attr
                    )
            elif label in ("Elapsed", "DL %") and is_finished and not is_selected:
                # Green foreground for completed tasks
                scr.addnstr(
                    screen_row,
                    col_pos,
                    cell_text,
                    min(w, remaining),
                    curses.color_pair(_CP_GREEN_TEXT),
                )
            else:
                scr.addnstr(screen_row, col_pos, cell_text, min(w, remaining), row_attr)

            col_pos += w

    def _draw_log_view(self, width, available_height):
        """Draw the full-screen log view for the selected worker."""
        scr = self._stdscr
        wid = self._selected_worker
        logs = list(self._worker_logs[wid])

        # Apply scroll
        max_scroll = max(0, len(logs) - available_height)
        self._log_scroll = max(0, min(self._log_scroll, max_scroll))
        visible = logs[self._log_scroll : self._log_scroll + available_height]

        for i, line in enumerate(visible):
            screen_row = 1 + i  # start after title bar
            scr.move(screen_row, 0)
            scr.clrtoeol()
            scr.addnstr(screen_row, 0, line[: width - 1], width - 1)

        # Clear remaining rows
        for i in range(len(visible), available_height):
            screen_row = 1 + i
            scr.move(screen_row, 0)
            scr.clrtoeol()

    def _draw_params_view(self, width, available_height):
        """Draw the full-screen params view for the selected worker."""
        scr = self._stdscr
        wid = self._selected_worker
        params = self._worker_request_params[wid]

        if params:
            param_str = "Params: " + ", ".join(f"{k}={v}" for k, v in params.items())
        else:
            param_str = "Params: —"

        line_width = max(10, width - 2)
        wrapped = textwrap.wrap(param_str, width=line_width) or [param_str]

        # Apply scroll
        max_scroll = max(0, len(wrapped) - available_height)
        self._params_scroll = max(0, min(self._params_scroll, max_scroll))
        visible = wrapped[self._params_scroll : self._params_scroll + available_height]

        for i, line in enumerate(visible):
            screen_row = 1 + i  # start after title bar
            scr.move(screen_row, 0)
            scr.clrtoeol()
            scr.addnstr(screen_row, 0, " " + line[: width - 2], width - 1)

        # Clear remaining rows
        for i in range(len(visible), available_height):
            screen_row = 1 + i
            scr.move(screen_row, 0)
            scr.clrtoeol()

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
            if self._view_mode == "logs":
                self._log_scroll = max(0, self._log_scroll - 1)
            elif self._view_mode == "params":
                self._params_scroll = max(0, self._params_scroll - 1)
            else:
                self._selected_worker = max(0, self._selected_worker - 1)
                self._ensure_selected_visible()
            self._do_refresh()

    def select_down(self):
        with self._lock:
            if self._view_mode == "logs":
                self._log_scroll += 1  # clamped in _draw_log_view
            elif self._view_mode == "params":
                self._params_scroll += 1  # clamped in _draw_params_view
            else:
                self._selected_worker = min(
                    self._num_workers - 1, self._selected_worker + 1
                )
                self._ensure_selected_visible()
            self._do_refresh()

    def open_log_view(self):
        with self._lock:
            self._view_mode = "logs"
            self._log_scroll = max(0, len(self._worker_logs[self._selected_worker]) - 1)
            self._do_refresh()

    def open_params_view(self):
        with self._lock:
            self._view_mode = "params"
            self._params_scroll = 0
            self._do_refresh()

    def close_fullscreen_view(self):
        with self._lock:
            self._view_mode = "table"
            self._do_refresh()

    def close_log_view(self):
        self.close_fullscreen_view()

    def page_up(self):
        with self._lock:
            if self._view_mode in ("logs", "params"):
                if self._stdscr:
                    height, _ = self._stdscr.getmaxyx()
                    page = height - self.PROGRESS_ROWS - 1
                else:
                    page = 10
                if self._view_mode == "logs":
                    self._log_scroll = max(0, self._log_scroll - page)
                else:
                    self._params_scroll = max(0, self._params_scroll - page)
                self._do_refresh()

    def page_down(self):
        with self._lock:
            if self._view_mode in ("logs", "params"):
                if self._stdscr:
                    height, _ = self._stdscr.getmaxyx()
                    page = height - self.PROGRESS_ROWS - 1
                else:
                    page = 10
                if self._view_mode == "logs":
                    self._log_scroll += page  # clamped in _draw_log_view
                else:
                    self._params_scroll += page  # clamped in _draw_params_view
                self._do_refresh()

    def log_home(self):
        with self._lock:
            if self._view_mode == "logs":
                self._log_scroll = 0
                self._do_refresh()
            elif self._view_mode == "params":
                self._params_scroll = 0
                self._do_refresh()

    def log_end(self):
        with self._lock:
            if self._view_mode == "logs":
                self._log_scroll = max(
                    0, len(self._worker_logs[self._selected_worker]) - 1
                )
                self._do_refresh()
            elif self._view_mode == "params":
                self._params_scroll = 999999  # clamped in _draw_params_view
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
            if self._view_mode != "table":
                return
            _, mx, my, _, bstate = mouse_event
            if bstate & curses.BUTTON1_CLICKED:
                wid = self._row_worker_map.get(my)
                if wid is not None:
                    self._selected_worker = wid
            elif bstate & curses.BUTTON1_DOUBLE_CLICKED:
                wid = self._row_worker_map.get(my)
                if wid is not None:
                    self._selected_worker = wid
                    self._view_mode = "logs"
                    self._log_scroll = max(0, len(self._worker_logs[wid]) - 1)
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
        row_idx = self._selected_worker

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

    def set_worker_task_info(self, worker_id, dataset, request_params, target=""):
        with self._lock:
            if 0 <= worker_id < self._num_workers:
                self._worker_dataset[worker_id] = dataset
                self._worker_request_params[worker_id] = request_params
                self._worker_target[worker_id] = target
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

            if self._view_mode == "logs":
                self._draw_header(width)
                log_height = height - self.PROGRESS_ROWS - 1  # 1 for title bar
                self._draw_log_view(width, log_height)
                self._draw_progress_bar(height, width)
            elif self._view_mode == "params":
                self._draw_header(width)
                params_height = height - self.PROGRESS_ROWS - 1
                self._draw_params_view(width, params_height)
                self._draw_progress_bar(height, width)
            else:
                self._draw_header(width)
                self._draw_info_panel(width)
                self._draw_column_headers(width)
                available_height = height - self.HEADER_ROWS - self.PROGRESS_ROWS
                self._draw_table(width, available_height)
                self._draw_progress_bar(height, width)
            self._stdscr.noutrefresh()
            curses.doupdate()
        except curses.error:
            pass
