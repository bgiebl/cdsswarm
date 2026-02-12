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


class CursesTUI:
    """Terminal UI showing worker panels with status badges and a progress bar."""

    MIN_HEIGHT = 10
    MIN_WIDTH = 40
    PROGRESS_ROWS = 2
    HEADER_ROWS = 1

    _STATUS_COLORS = {
        "accepted": _CP_STATUS_ACCEPTED,
        "running": _CP_STATUS_RUNNING,
        "successful": _CP_STATUS_SUCCESS,
        "failed": _CP_STATUS_FAILED,
        "cancelled": _CP_STATUS_CANCELLED,
    }

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
        self._focused_worker: int | None = None
        self._scroll_offset = [0] * num_workers
        self._last_size: tuple[int, int] = (0, 0)

    def start(self, stdscr):
        self._stdscr = stdscr
        curses.curs_set(0)
        curses.use_default_colors()
        stdscr.nodelay(True)
        stdscr.clear()
        self._init_colors()
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
            curses.init_pair(_CP_STATUS_ACCEPTED, curses.COLOR_BLACK, curses.COLOR_YELLOW)
        curses.init_pair(_CP_STATUS_SUCCESS, curses.COLOR_BLACK, curses.COLOR_GREEN)
        curses.init_pair(_CP_WORKER_LABEL, curses.COLOR_WHITE, curses.COLOR_CYAN)
        curses.init_pair(_CP_STATUS_CANCELLED, curses.COLOR_WHITE, curses.COLOR_MAGENTA)
        curses.init_pair(_CP_STATUS_RUNNING, curses.COLOR_BLACK, curses.COLOR_YELLOW)
        curses.init_pair(_CP_HEADER, curses.COLOR_WHITE, curses.COLOR_BLUE)

    def _panel_geometry(self):
        height, width = self._stdscr.getmaxyx()
        usable = height - self.PROGRESS_ROWS - self.HEADER_ROWS

        if self._focused_worker is not None:
            # Give all space to the focused worker
            panels = []
            for i in range(self._num_workers):
                if i == self._focused_worker:
                    panels.append((self.HEADER_ROWS, usable))
                else:
                    panels.append((0, 0))
            return panels, width

        panel_h = max(3, usable // self._num_workers)
        panels = []
        for i in range(self._num_workers):
            start = self.HEADER_ROWS + i * panel_h
            h = panel_h if i < self._num_workers - 1 else (usable - i * panel_h)
            panels.append((start, h))
        return panels, width

    def _draw_header(self, width):
        scr = self._stdscr
        scr.move(0, 0)
        scr.clrtoeol()
        hints = "[q] quit  [Tab] focus  [\u2191/\u2193] scroll"
        title = f" {self._title}"
        # Fill entire header row with the header color
        header_text = title.ljust(width - 1)
        scr.addnstr(0, 0, header_text, width - 1,
                     curses.color_pair(_CP_HEADER) | curses.A_BOLD)
        # Right-align the hints
        hint_col = width - len(hints) - 2
        if hint_col > len(title):
            scr.addnstr(0, hint_col, hints, width - hint_col - 1,
                         curses.color_pair(_CP_HEADER))

    def _draw_worker_panel(self, worker_id, start_row, height, width):
        if height <= 0:
            return
        scr = self._stdscr

        header_row = start_row
        scr.move(header_row, 0)
        scr.clrtoeol()

        label = f" Worker {worker_id} "
        if self._focused_worker == worker_id:
            label += "[focused] "
        cds_status = self._worker_cds_status[worker_id]
        request_id = self._worker_request_id[worker_id]

        col = 0
        scr.addnstr(header_row, col, label, width - col - 1,
                     curses.color_pair(_CP_WORKER_LABEL) | curses.A_BOLD)
        col += len(label)

        if cds_status and col + 14 < width:
            if col < width - 1:
                scr.addstr(header_row, col, " ")
                col += 1

            status_label = " Status: "
            if col + len(status_label) < width:
                scr.addnstr(header_row, col, status_label, width - col - 1,
                            curses.color_pair(_CP_STATUS_LABEL))
                col += len(status_label)

            status_text = f" {cds_status} "
            color_pair = self._STATUS_COLORS.get(cds_status, _CP_STATUS_LABEL)
            if col + len(status_text) < width:
                scr.addnstr(header_row, col, status_text, width - col - 1,
                            curses.color_pair(color_pair))
                col += len(status_text)

            if request_id and col + 2 < width:
                scr.addstr(header_row, col, " ")
                col += 1
                avail = width - col - 1
                if avail > 0:
                    display_id = request_id[:avail]
                    scr.addnstr(header_row, col, display_id, avail)
                    col += len(display_id)

        interior_h = height - 2
        log = list(self._worker_logs[worker_id])
        offset = self._scroll_offset[worker_id]
        if offset > 0 and offset < len(log):
            visible_log = log[:len(log) - offset]
        else:
            visible_log = log
        visible = visible_log[-interior_h:]

        for i in range(interior_h):
            row = start_row + 1 + i
            scr.move(row, 0)
            scr.clrtoeol()
            if i < len(visible):
                scr.addnstr(row, 1, visible[i][:width - 2], width - 2)

        border_row = start_row + height - 1
        scr.move(border_row, 0)
        scr.clrtoeol()
        line = "\u2500" * (width - 1)
        scr.addnstr(border_row, 0, line, width - 1, curses.color_pair(_CP_BORDER))

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
            bar = "\u2588" * filled + "\u2591" * (bar_width - filled)
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
            scr.addnstr(status_row, 0, " " + self._status_line[:width - 2], width - 1)

    # -- Input handling methods (called from main thread) --

    def handle_resize(self):
        with self._lock:
            if self._stdscr:
                # Get actual terminal size from OS (not curses' stale cache)
                size = shutil.get_terminal_size()
                curses.resizeterm(size.lines, size.columns)
                self._do_refresh()

    def cycle_focus(self):
        with self._lock:
            if self._focused_worker is None:
                self._focused_worker = 0
            elif self._focused_worker >= self._num_workers - 1:
                self._focused_worker = None
            else:
                self._focused_worker += 1
            # Panel layout changed — force full erase on next draw
            self._last_size = (0, 0)
            self._do_refresh()

    def scroll_log_up(self):
        with self._lock:
            if self._focused_worker is not None:
                wid = self._focused_worker
                max_offset = max(0, len(self._worker_logs[wid]) - 1)
                self._scroll_offset[wid] = min(
                    self._scroll_offset[wid] + 3, max_offset,
                )
                self._do_refresh()

    def scroll_log_down(self):
        with self._lock:
            if self._focused_worker is not None:
                wid = self._focused_worker
                self._scroll_offset[wid] = max(0, self._scroll_offset[wid] - 3)
                self._do_refresh()

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

    def append_worker_log(self, worker_id, message):
        with self._lock:
            if 0 <= worker_id < self._num_workers:
                self._worker_logs[worker_id].append(message)
                self._scroll_offset[worker_id] = 0
                self._do_refresh()

    def clear_worker_log(self, worker_id):
        with self._lock:
            if 0 <= worker_id < self._num_workers:
                self._worker_logs[worker_id].clear()
                self._scroll_offset[worker_id] = 0

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

            # Only erase the whole screen when the geometry changed
            # (resize, focus toggle).  Normal content updates use
            # per-row clrtoeol() in the draw methods, which avoids
            # any visible flicker.
            if cur_size != self._last_size:
                self._stdscr.erase()
                self._last_size = cur_size

            self._draw_header(width)
            panels, w = self._panel_geometry()
            for wid in range(self._num_workers):
                start, h = panels[wid]
                if h > 0:
                    self._draw_worker_panel(wid, start, h, w)
            self._draw_progress_bar(height, w)
            self._stdscr.noutrefresh()
            curses.doupdate()
        except curses.error:
            pass
