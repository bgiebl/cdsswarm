"""Tests for the curses TUI."""

import curses
import time
from contextlib import contextmanager
from unittest.mock import MagicMock, patch


from cdsswarm.tui import (
    CursesTUI,
    _CP_STATUS_RUNNING,
    _CP_STATUS_SUCCESS,
    _format_eta,
    _format_size,
)


@contextmanager
def _mock_curses():
    """Patch curses functions that require initscr()."""
    with (
        patch("curses.color_pair", side_effect=lambda n: n << 8),
        patch("curses.doupdate"),
    ):
        yield


class TestFormatEta:
    def test_negative(self):
        assert _format_eta(-1) == "??:??"

    def test_zero(self):
        assert _format_eta(0) == "0m00s"

    def test_seconds(self):
        assert _format_eta(45) == "0m45s"

    def test_minutes(self):
        assert _format_eta(125) == "2m05s"

    def test_hours(self):
        assert _format_eta(3661) == "1h01m01s"

    def test_large(self):
        assert _format_eta(7200) == "2h00m00s"


class TestFormatSize:
    def test_zero(self):
        assert _format_size(0) == "—"

    def test_bytes(self):
        assert _format_size(512) == "512 B"

    def test_kilobytes(self):
        assert _format_size(2048) == "2.0 KB"

    def test_megabytes(self):
        assert _format_size(10 * 1024 * 1024) == "10.0 MB"

    def test_gigabytes(self):
        assert _format_size(2 * 1024**3) == "2.0 GB"


def _mock_stdscr(height=40, width=120):
    stdscr = MagicMock()
    stdscr.getmaxyx.return_value = (height, width)
    return stdscr


class TestCursesTUI:
    def test_set_worker_status(self):
        tui = CursesTUI(num_workers=2)
        tui._stdscr = _mock_stdscr()
        tui.set_worker_status(0, "downloading")
        assert tui._worker_status[0] == "downloading"

    def test_set_worker_cds_status(self):
        tui = CursesTUI(num_workers=2)
        tui._stdscr = _mock_stdscr()
        tui.set_worker_cds_status(0, "running")
        assert tui._worker_cds_status[0] == "running"

    def test_append_worker_log(self):
        tui = CursesTUI(num_workers=2)
        tui._stdscr = _mock_stdscr()
        tui.append_worker_log(0, "hello")
        tui.append_worker_log(0, "world")
        assert list(tui._worker_logs[0]) == ["hello", "world"]

    def test_update_progress(self):
        tui = CursesTUI(num_workers=2)
        tui._stdscr = _mock_stdscr()
        tui.update_progress(3, 10, 2)
        assert tui._progress_completed == 3
        assert tui._progress_total == 10
        assert tui._progress_skipped == 2

    def test_out_of_range_worker_id(self):
        tui = CursesTUI(num_workers=2)
        tui._stdscr = _mock_stdscr()
        # Should not raise
        tui.set_worker_status(5, "x")
        tui.set_worker_cds_status(-1, "running")
        tui.append_worker_log(99, "msg")
        # Original state unchanged
        assert tui._worker_status == ["idle", "idle"]

    def test_small_terminal_warning(self):
        tui = CursesTUI(num_workers=2)
        stdscr = _mock_stdscr(height=5, width=20)
        tui._stdscr = stdscr
        tui._do_refresh()
        stdscr.erase.assert_called()
        stdscr.addstr.assert_called()

    def test_title_parameter(self):
        tui = CursesTUI(num_workers=1, title="my-app")
        assert tui._title == "my-app"

    def test_default_selection(self):
        tui = CursesTUI(num_workers=4)
        assert tui._selected_worker == 0

    def test_select_down(self):
        tui = CursesTUI(num_workers=4)
        tui._stdscr = _mock_stdscr()
        tui.select_down()
        assert tui._selected_worker == 1
        tui.select_down()
        assert tui._selected_worker == 2
        tui.select_down()
        assert tui._selected_worker == 3
        # Clamped at max
        tui.select_down()
        assert tui._selected_worker == 3

    def test_select_up(self):
        tui = CursesTUI(num_workers=4)
        tui._stdscr = _mock_stdscr()
        tui.select_down()
        tui.select_down()
        assert tui._selected_worker == 2
        tui.select_up()
        assert tui._selected_worker == 1
        tui.select_up()
        assert tui._selected_worker == 0
        # Clamped at 0
        tui.select_up()
        assert tui._selected_worker == 0

    def test_toggle_expand(self):
        tui = CursesTUI(num_workers=3)
        tui._stdscr = _mock_stdscr()
        assert 0 not in tui._expanded_workers
        tui.toggle_expand()
        assert 0 in tui._expanded_workers
        tui.toggle_expand()
        assert 0 not in tui._expanded_workers

    def test_select_worker(self):
        tui = CursesTUI(num_workers=4)
        tui._stdscr = _mock_stdscr()
        tui.select_worker(2)
        assert tui._selected_worker == 2
        # Out of range does nothing
        tui.select_worker(10)
        assert tui._selected_worker == 2
        tui.select_worker(-1)
        assert tui._selected_worker == 2

    def test_update_worker_progress(self):
        tui = CursesTUI(num_workers=2)
        tui._stdscr = _mock_stdscr()
        tui.update_worker_progress(0, 500, 1000)
        assert tui._worker_dl_bytes[0] == 500
        assert tui._worker_dl_total[0] == 1000
        assert tui._worker_dl_bytes[1] == 0
        assert tui._worker_dl_total[1] == 0

    def test_clear_log_resets_progress(self):
        tui = CursesTUI(num_workers=2)
        tui._stdscr = _mock_stdscr()
        tui.update_worker_progress(0, 500, 1000)
        tui.set_worker_filename(0, "test.grib")
        tui.append_worker_log(0, "some log")
        # Manually set times for testing
        with tui._lock:
            tui._worker_start_time[0] = 1000.0
            tui._worker_finish_time[0] = 2000.0
        tui.clear_worker_log(0)
        assert list(tui._worker_logs[0]) == []
        assert tui._worker_dl_bytes[0] == 0
        assert tui._worker_dl_total[0] == 0
        assert tui._worker_filename[0] == ""
        assert tui._worker_start_time[0] is None
        assert tui._worker_finish_time[0] is None

    def test_set_worker_filename(self):
        tui = CursesTUI(num_workers=2)
        tui._stdscr = _mock_stdscr()
        tui.set_worker_filename(0, "era5_2024.grib")
        assert tui._worker_filename[0] == "era5_2024.grib"
        # Also sets start time
        assert tui._worker_start_time[0] is not None

    def test_set_worker_finished(self):
        tui = CursesTUI(num_workers=2)
        tui._stdscr = _mock_stdscr()
        assert tui._worker_finish_time[0] is None
        tui.set_worker_finished(0)
        assert tui._worker_finish_time[0] is not None


class TestFormattingHelpers:
    """Tests for per-worker formatting helpers."""

    def test_format_start_time_none(self):
        tui = CursesTUI(num_workers=1)
        assert tui._format_start_time(0) == "—"

    def test_format_start_time_set(self):
        tui = CursesTUI(num_workers=1)
        # Use a known timestamp: 2024-01-15 10:30:45 UTC
        tui._worker_start_time[0] = time.mktime(time.strptime("10:30:45", "%H:%M:%S"))
        assert tui._format_start_time(0) == "10:30:45"

    def test_format_elapsed_time_no_start(self):
        tui = CursesTUI(num_workers=1)
        assert tui._format_elapsed_time(0) == "—"

    def test_format_elapsed_time_with_finish(self):
        tui = CursesTUI(num_workers=1)
        tui._worker_start_time[0] = 1000.0
        tui._worker_finish_time[0] = 1125.0  # 125 seconds elapsed
        assert tui._format_elapsed_time(0) == "2m05s"

    def test_format_elapsed_time_still_running(self):
        tui = CursesTUI(num_workers=1)
        tui._worker_start_time[0] = time.time() - 60  # 60 seconds ago
        result = tui._format_elapsed_time(0)
        # Should be roughly "1m00s" (not "—")
        assert result != "—"
        assert "m" in result

    def test_format_finish_time_none(self):
        tui = CursesTUI(num_workers=1)
        assert tui._format_finish_time(0) == "—"

    def test_format_finish_time_set(self):
        tui = CursesTUI(num_workers=1)
        tui._worker_finish_time[0] = time.mktime(time.strptime("14:22:09", "%H:%M:%S"))
        assert tui._format_finish_time(0) == "14:22:09"

    def test_format_dl_size_no_total(self):
        tui = CursesTUI(num_workers=1)
        assert tui._format_dl_size(0) == "—"

    def test_format_dl_size_with_total(self):
        tui = CursesTUI(num_workers=1)
        tui._worker_dl_total[0] = 15 * 1024**3  # 15 GB
        assert tui._format_dl_size(0) == "15.0 GB"

    def test_format_dl_pct_no_total(self):
        tui = CursesTUI(num_workers=1)
        assert tui._format_dl_pct(0) == "—"

    def test_format_dl_pct_with_progress(self):
        tui = CursesTUI(num_workers=1)
        tui._worker_dl_bytes[0] = 480
        tui._worker_dl_total[0] = 1000
        assert tui._format_dl_pct(0) == "48%"

    def test_format_dl_pct_zero_downloaded(self):
        tui = CursesTUI(num_workers=1)
        tui._worker_dl_bytes[0] = 0
        tui._worker_dl_total[0] = 1000
        assert tui._format_dl_pct(0) == "0%"

    def test_format_dl_pct_complete(self):
        tui = CursesTUI(num_workers=1)
        tui._worker_dl_bytes[0] = 1000
        tui._worker_dl_total[0] = 1000
        assert tui._format_dl_pct(0) == "100%"

    def test_format_filetype_grib(self):
        tui = CursesTUI(num_workers=1)
        tui._worker_filename[0] = "era5_t2m_2024.grib"
        assert tui._format_filetype(0) == "grib"

    def test_format_filetype_nc(self):
        tui = CursesTUI(num_workers=1)
        tui._worker_filename[0] = "data.nc"
        assert tui._format_filetype(0) == "nc"

    def test_format_filetype_no_extension(self):
        tui = CursesTUI(num_workers=1)
        tui._worker_filename[0] = "datafile"
        assert tui._format_filetype(0) == "—"

    def test_format_filetype_empty(self):
        tui = CursesTUI(num_workers=1)
        assert tui._format_filetype(0) == "—"


class TestColumnSpecs:
    def test_returns_ten_columns(self):
        tui = CursesTUI(num_workers=1)
        cols = tui._column_specs(120)
        assert len(cols) == 10

    def test_column_labels(self):
        tui = CursesTUI(num_workers=1)
        cols = tui._column_specs(120)
        labels = [label for label, _ in cols]
        assert labels == [
            "Worker",
            "Status",
            "Filename",
            "Type",
            "Started",
            "Elapsed",
            "Finished",
            "Size",
            "DL %",
            "Request ID",
        ]

    def test_request_id_fills_remaining_width(self):
        tui = CursesTUI(num_workers=1)
        cols_narrow = tui._column_specs(100)
        cols_wide = tui._column_specs(200)
        # Request ID column (last) should grow with width
        req_narrow = cols_narrow[-1][1]
        req_wide = cols_wide[-1][1]
        assert req_wide > req_narrow

    def test_request_id_minimum_width(self):
        tui = CursesTUI(num_workers=1)
        # Even at very narrow width, Request ID has min 8
        cols = tui._column_specs(50)
        assert cols[-1][1] >= 8


class TestDrawing:
    """Tests that drawing methods produce correct curses calls."""

    def _make_tui(self, num_workers=3, height=30, width=120):
        tui = CursesTUI(num_workers=num_workers)
        stdscr = _mock_stdscr(height, width)
        tui._stdscr = stdscr
        tui._last_size = (height, width)  # avoid erase on first draw
        return tui, stdscr

    def test_draw_header_renders_title(self):
        tui, stdscr = self._make_tui()
        with _mock_curses():
            tui._draw_header(120)
        # First addnstr call should be the title bar at row 0
        calls = stdscr.addnstr.call_args_list
        assert len(calls) >= 1
        row, col, text, maxn, attr = calls[0].args
        assert row == 0
        assert col == 0
        assert "cdsswarm" in text
        assert attr & curses.A_BOLD

    def test_draw_header_renders_hints(self):
        tui, stdscr = self._make_tui()
        with _mock_curses():
            tui._draw_header(120)
        calls = stdscr.addnstr.call_args_list
        # Second call should be the hints
        assert len(calls) >= 2
        _, _, hints_text, _, _ = calls[1].args
        assert "[q] quit" in hints_text
        assert "select" in hints_text
        assert "expand" in hints_text

    def test_draw_column_headers_at_row_1(self):
        tui, stdscr = self._make_tui()
        tui._draw_column_headers(120)
        calls = stdscr.addnstr.call_args_list
        assert len(calls) == 1
        row, col, text, _, attr = calls[0].args
        assert row == 1
        assert col == 0
        assert "Worker" in text
        assert "Status" in text
        assert "Filename" in text
        assert "Request ID" in text
        assert attr & curses.A_BOLD
        assert attr & curses.A_UNDERLINE

    def test_draw_column_headers_uses_pipe_separator(self):
        tui, stdscr = self._make_tui()
        tui._draw_column_headers(120)
        text = stdscr.addnstr.call_args_list[0].args[2]
        assert "│" in text

    def test_draw_table_populates_row_worker_map(self):
        tui, stdscr = self._make_tui(num_workers=3)
        tui._draw_table(120, available_height=20)
        # 3 workers → 3 entries in row_worker_map
        assert len(tui._row_worker_map) == 3
        # Workers 0,1,2 should be at rows HEADER_ROWS+0, +1, +2
        assert tui._row_worker_map[tui.HEADER_ROWS] == 0
        assert tui._row_worker_map[tui.HEADER_ROWS + 1] == 1
        assert tui._row_worker_map[tui.HEADER_ROWS + 2] == 2

    def test_draw_table_selected_row_gets_reverse(self):
        tui, stdscr = self._make_tui(num_workers=2)
        tui._selected_worker = 1
        tui._draw_table(120, available_height=20)
        # Find addnstr calls for screen_row = HEADER_ROWS + 1 (worker 1)
        selected_row = tui.HEADER_ROWS + 1
        calls_for_selected = [
            c for c in stdscr.addnstr.call_args_list if c.args[0] == selected_row
        ]
        # At least one call should have A_REVERSE
        attrs = [c.args[4] if len(c.args) > 4 else 0 for c in calls_for_selected]
        assert any(a & curses.A_REVERSE for a in attrs)

    def test_draw_table_unselected_row_no_reverse(self):
        tui, stdscr = self._make_tui(num_workers=2)
        tui._selected_worker = 0
        tui._draw_table(120, available_height=20)
        # Worker 1 is at HEADER_ROWS + 1 and is NOT selected
        unselected_row = tui.HEADER_ROWS + 1
        calls_for_unselected = [
            c for c in stdscr.addnstr.call_args_list if c.args[0] == unselected_row
        ]
        attrs = [c.args[4] if len(c.args) > 4 else 0 for c in calls_for_unselected]
        assert all(not (a & curses.A_REVERSE) for a in attrs)

    def test_draw_table_selection_indicator(self):
        tui, stdscr = self._make_tui(num_workers=2)
        tui._selected_worker = 0
        tui._draw_table(120, available_height=20)
        selected_row = tui.HEADER_ROWS
        calls_for_selected = [
            c for c in stdscr.addnstr.call_args_list if c.args[0] == selected_row
        ]
        # First non-separator cell for the selected row should contain ▸
        texts = [c.args[2] for c in calls_for_selected]
        assert any("▸" in t for t in texts)

    def test_draw_table_unselected_no_indicator(self):
        tui, stdscr = self._make_tui(num_workers=2)
        tui._selected_worker = 0
        tui._draw_table(120, available_height=20)
        unselected_row = tui.HEADER_ROWS + 1
        calls_for_unselected = [
            c for c in stdscr.addnstr.call_args_list if c.args[0] == unselected_row
        ]
        texts = [c.args[2] for c in calls_for_unselected]
        assert not any("▸" in t for t in texts)

    def test_draw_table_status_badge_color(self):
        tui, stdscr = self._make_tui(num_workers=1)
        tui._worker_cds_status[0] = "running"
        with _mock_curses():
            tui._draw_table(120, available_height=20)
        row = tui.HEADER_ROWS
        calls = [c for c in stdscr.addnstr.call_args_list if c.args[0] == row]
        # Find the call that renders the status badge " running "
        badge_calls = [c for c in calls if "running" in c.args[2] and len(c.args) > 4]
        assert len(badge_calls) >= 1
        # The badge should use the _CP_STATUS_RUNNING color pair (mocked as n << 8)
        badge_attr = badge_calls[0].args[4]
        assert badge_attr == _CP_STATUS_RUNNING << 8

    def test_draw_table_expanded_shows_log_lines(self):
        tui, stdscr = self._make_tui(num_workers=2)
        tui.append_worker_log(0, "line A")
        tui.append_worker_log(0, "line B")
        tui._expanded_workers.add(0)
        stdscr.addnstr.reset_mock()
        tui._draw_table(120, available_height=20)
        # Log lines should appear after worker 0's row with A_DIM
        all_calls = stdscr.addnstr.call_args_list
        dim_calls = [
            c for c in all_calls if len(c.args) > 4 and c.args[4] == curses.A_DIM
        ]
        assert len(dim_calls) == 2  # 2 log lines
        dim_texts = [c.args[2] for c in dim_calls]
        assert any("line A" in t for t in dim_texts)
        assert any("line B" in t for t in dim_texts)

    def test_draw_table_expanded_max_3_log_lines(self):
        tui, stdscr = self._make_tui(num_workers=1)
        for i in range(10):
            tui.append_worker_log(0, f"line {i}")
        tui._expanded_workers.add(0)
        stdscr.addnstr.reset_mock()
        tui._draw_table(120, available_height=20)
        dim_calls = [
            c
            for c in stdscr.addnstr.call_args_list
            if len(c.args) > 4 and c.args[4] == curses.A_DIM
        ]
        # Only 3 most recent log lines shown
        assert len(dim_calls) == 3
        dim_texts = [c.args[2] for c in dim_calls]
        assert any("line 7" in t for t in dim_texts)
        assert any("line 8" in t for t in dim_texts)
        assert any("line 9" in t for t in dim_texts)

    def test_draw_table_expanded_log_lines_have_prefix(self):
        tui, stdscr = self._make_tui(num_workers=1)
        tui.append_worker_log(0, "test message")
        tui._expanded_workers.add(0)
        stdscr.addnstr.reset_mock()
        tui._draw_table(120, available_height=20)
        dim_calls = [
            c
            for c in stdscr.addnstr.call_args_list
            if len(c.args) > 4 and c.args[4] == curses.A_DIM
        ]
        assert len(dim_calls) == 1
        assert dim_calls[0].args[2].startswith("  Log: ")

    def test_draw_table_clears_remaining_rows(self):
        tui, stdscr = self._make_tui(num_workers=2)
        available = 10
        tui._draw_table(120, available_height=available)
        # move+clrtoeol should be called for all available rows
        move_calls = [c.args for c in stdscr.move.call_args_list]
        # Rows HEADER_ROWS through HEADER_ROWS + available - 1 should all be touched
        expected_rows = set(range(tui.HEADER_ROWS, tui.HEADER_ROWS + available))
        moved_rows = {row for row, col in move_calls}
        assert expected_rows.issubset(moved_rows)

    def test_draw_table_shows_filename(self):
        tui, stdscr = self._make_tui(num_workers=1)
        tui._worker_filename[0] = "era5_data.grib"
        tui._draw_table(120, available_height=10)
        row = tui.HEADER_ROWS
        texts = [c.args[2] for c in stdscr.addnstr.call_args_list if c.args[0] == row]
        assert any("era5_data.grib" in t for t in texts)

    def test_draw_table_shows_request_id(self):
        tui, stdscr = self._make_tui(num_workers=1)
        tui._worker_request_id[0] = "af1e2306-28c3-4abc"
        tui._draw_table(120, available_height=10)
        row = tui.HEADER_ROWS
        texts = [c.args[2] for c in stdscr.addnstr.call_args_list if c.args[0] == row]
        assert any("af1e2306" in t for t in texts)

    def test_draw_table_shows_filetype(self):
        tui, stdscr = self._make_tui(num_workers=1)
        tui._worker_filename[0] = "era5_data.grib"
        tui._draw_table(120, available_height=10)
        row = tui.HEADER_ROWS
        texts = [c.args[2] for c in stdscr.addnstr.call_args_list if c.args[0] == row]
        assert any("grib" in t for t in texts)

    def test_draw_table_finished_column_green_background(self):
        tui, stdscr = self._make_tui(num_workers=1)
        tui._worker_finish_time[0] = 1000.0  # mark as finished
        tui._worker_start_time[0] = 900.0
        with _mock_curses():
            tui._draw_table(120, available_height=10)
        row = tui.HEADER_ROWS
        calls = [
            c
            for c in stdscr.addnstr.call_args_list
            if c.args[0] == row and len(c.args) > 4
        ]
        # Find the call with the green success color pair (mocked as n << 8)
        green_calls = [c for c in calls if c.args[4] == _CP_STATUS_SUCCESS << 8]
        assert len(green_calls) >= 1
        # The green call should contain a time string (the finished time)
        green_text = green_calls[0].args[2]
        assert ":" in green_text  # HH:MM:SS format

    def test_draw_table_unfinished_no_green_finished(self):
        tui, stdscr = self._make_tui(num_workers=1)
        # Not finished — finish_time is None
        tui._worker_start_time[0] = 900.0
        with _mock_curses():
            tui._draw_table(120, available_height=10)
        row = tui.HEADER_ROWS
        calls = [
            c
            for c in stdscr.addnstr.call_args_list
            if c.args[0] == row and len(c.args) > 4
        ]
        green_calls = [c for c in calls if c.args[4] == _CP_STATUS_SUCCESS << 8]
        assert len(green_calls) == 0

    def test_do_refresh_calls_all_draw_methods(self):
        tui, stdscr = self._make_tui()
        tui._last_size = (0, 0)  # force full redraw
        with (
            _mock_curses(),
            patch.object(tui, "_draw_header") as dh,
            patch.object(tui, "_draw_column_headers") as dch,
            patch.object(tui, "_draw_table") as dt,
            patch.object(tui, "_draw_progress_bar") as dpb,
        ):
            tui._do_refresh()
            dh.assert_called_once()
            dch.assert_called_once()
            dt.assert_called_once()
            dpb.assert_called_once()

    def test_do_refresh_erase_on_size_change(self):
        tui, stdscr = self._make_tui()
        tui._last_size = (20, 80)  # different from current 30x120
        with _mock_curses():
            tui._do_refresh()
        stdscr.erase.assert_called()

    def test_do_refresh_no_erase_on_same_size(self):
        tui, stdscr = self._make_tui(height=30, width=120)
        tui._last_size = (30, 120)
        stdscr.erase.reset_mock()
        with _mock_curses():
            tui._do_refresh()
        stdscr.erase.assert_not_called()


class TestMouseHandling:
    def _make_tui_with_rows(self, num_workers=4):
        """Create a TUI and draw it so _row_worker_map is populated."""
        tui = CursesTUI(num_workers=num_workers)
        tui._stdscr = _mock_stdscr(height=30, width=120)
        tui._last_size = (30, 120)
        with _mock_curses():
            tui._do_refresh()  # populates _row_worker_map
        return tui

    def test_click_selects_worker(self):
        tui = self._make_tui_with_rows()
        target_row = tui.HEADER_ROWS + 2
        assert tui._row_worker_map.get(target_row) == 2
        with _mock_curses():
            tui.handle_mouse((0, 10, target_row, 0, curses.BUTTON1_CLICKED))
        assert tui._selected_worker == 2

    def test_click_on_empty_row_no_change(self):
        tui = self._make_tui_with_rows()
        tui._selected_worker = 1
        with _mock_curses():
            tui.handle_mouse((0, 10, 25, 0, curses.BUTTON1_CLICKED))
        assert tui._selected_worker == 1  # unchanged

    def test_double_click_expands(self):
        tui = self._make_tui_with_rows()
        target_row = tui.HEADER_ROWS + 1
        assert 1 not in tui._expanded_workers
        with _mock_curses():
            tui.handle_mouse((0, 10, target_row, 0, curses.BUTTON1_DOUBLE_CLICKED))
        assert tui._selected_worker == 1
        assert 1 in tui._expanded_workers

    def test_double_click_collapses(self):
        tui = self._make_tui_with_rows()
        tui._expanded_workers.add(0)
        target_row = tui.HEADER_ROWS
        with _mock_curses():
            tui.handle_mouse((0, 10, target_row, 0, curses.BUTTON1_DOUBLE_CLICKED))
        assert 0 not in tui._expanded_workers

    def test_scroll_wheel_up(self):
        tui = self._make_tui_with_rows()
        tui._selected_worker = 2
        with _mock_curses():
            tui.handle_mouse((0, 10, 5, 0, curses.BUTTON4_PRESSED))
        assert tui._selected_worker == 1

    def test_scroll_wheel_down(self):
        tui = self._make_tui_with_rows()
        tui._selected_worker = 1
        with _mock_curses():
            tui.handle_mouse((0, 10, 5, 0, curses.BUTTON5_PRESSED))
        assert tui._selected_worker == 2

    def test_scroll_wheel_up_clamped_at_zero(self):
        tui = self._make_tui_with_rows()
        tui._selected_worker = 0
        with _mock_curses():
            tui.handle_mouse((0, 10, 5, 0, curses.BUTTON4_PRESSED))
        assert tui._selected_worker == 0

    def test_scroll_wheel_down_clamped_at_max(self):
        tui = self._make_tui_with_rows(num_workers=3)
        tui._selected_worker = 2
        with _mock_curses():
            tui.handle_mouse((0, 10, 5, 0, curses.BUTTON5_PRESSED))
        assert tui._selected_worker == 2


class TestEnsureSelectedVisible:
    def test_scroll_down_when_selected_below_view(self):
        # Terminal with only 6 available rows (height=10, -2 header -2 progress = 6)
        tui = CursesTUI(num_workers=10)
        tui._stdscr = _mock_stdscr(height=10, width=120)
        tui._table_scroll = 0
        tui._selected_worker = 8  # well below the visible 6 rows
        tui._ensure_selected_visible()
        # scroll should have moved so worker 8 (row_idx=8) is visible
        assert tui._table_scroll > 0
        assert tui._table_scroll <= 8

    def test_scroll_up_when_selected_above_view(self):
        tui = CursesTUI(num_workers=10)
        tui._stdscr = _mock_stdscr(height=10, width=120)
        tui._table_scroll = 5
        tui._selected_worker = 2  # above scroll position
        tui._ensure_selected_visible()
        assert tui._table_scroll <= 2

    def test_no_scroll_when_selected_visible(self):
        tui = CursesTUI(num_workers=4)
        tui._stdscr = _mock_stdscr(height=20, width=120)
        tui._table_scroll = 0
        tui._selected_worker = 2
        tui._ensure_selected_visible()
        assert tui._table_scroll == 0

    def test_scroll_accounts_for_expanded_workers(self):
        tui = CursesTUI(num_workers=10)
        tui._stdscr = _mock_stdscr(height=10, width=120)
        # Expand workers 0-2, each with 3 log lines → 3*(1+3)=12 rows before worker 3
        for wid in range(3):
            tui._expanded_workers.add(wid)
            for i in range(5):
                tui._worker_logs[wid].append(f"log {i}")
        tui._table_scroll = 0
        tui._selected_worker = 5
        tui._ensure_selected_visible()
        # Worker 5 is at row_idx = 3*(1+3) + 2*1 = 14 (workers 3,4 = 2 rows)
        # wait, let me think: workers 0,1,2 expanded with 3 log lines each = 3*(1+3) = 12
        # worker 3 = 1 row (row_idx 12), worker 4 = 1 row (row_idx 13), worker 5 = row_idx 14
        # available = 10 - 2 - 2 = 6, so we need scroll >= 14 - 6 + 1 = 9
        assert tui._table_scroll > 0


class TestTableScroll:
    def test_scroll_clamped_to_max(self):
        tui = CursesTUI(num_workers=3)
        tui._stdscr = _mock_stdscr(height=30, width=120)
        tui._table_scroll = 100  # way too high
        tui._last_size = (30, 120)
        tui._draw_table(120, available_height=20)
        # After draw, scroll should be clamped (3 rows, 20 available → max_scroll=0)
        assert tui._table_scroll == 0

    def test_scroll_never_negative(self):
        tui = CursesTUI(num_workers=3)
        tui._stdscr = _mock_stdscr(height=30, width=120)
        tui._table_scroll = -5
        tui._last_size = (30, 120)
        tui._draw_table(120, available_height=20)
        assert tui._table_scroll >= 0
