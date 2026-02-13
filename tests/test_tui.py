"""Tests for the curses TUI."""

import curses
import time
from contextlib import contextmanager
from unittest.mock import MagicMock, patch


from cdsswarm.tui import (
    CursesTUI,
    _CP_COL_HEADER,
    _CP_GREEN_TEXT,
    _CP_SELECTED_ROW,
    _CP_STATUS_RUNNING,
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

    def test_set_worker_task_info(self):
        tui = CursesTUI(num_workers=2)
        tui._stdscr = _mock_stdscr()
        params = {"variable": "2m_temperature", "year": "2024"}
        tui.set_worker_task_info(
            0, "reanalysis-era5-single-levels", params, "/data/out.grib"
        )
        assert tui._worker_dataset[0] == "reanalysis-era5-single-levels"
        assert tui._worker_request_params[0] == params
        assert tui._worker_target[0] == "/data/out.grib"

    def test_set_worker_task_info_out_of_range(self):
        tui = CursesTUI(num_workers=2)
        tui._stdscr = _mock_stdscr()
        tui.set_worker_task_info(5, "dataset", {})
        # Should not raise, original state unchanged
        assert tui._worker_dataset == ["", ""]


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
    def test_returns_nine_columns(self):
        tui = CursesTUI(num_workers=1)
        cols = tui._column_specs(120)
        assert len(cols) == 9

    def test_column_labels(self):
        tui = CursesTUI(num_workers=1)
        cols = tui._column_specs(120)
        labels = [label for label, _ in cols]
        assert labels == [
            "W",
            "Status",
            "Prog",
            "Filename",
            "Started",
            "Elapsed",
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
        assert "logs" in hints_text

    def test_draw_header_log_view_mode(self):
        tui, stdscr = self._make_tui()
        tui._view_mode = "logs"
        tui._worker_filename[0] = "test.grib"
        with _mock_curses():
            tui._draw_header(120)
        calls = stdscr.addnstr.call_args_list
        title_text = calls[0].args[2]
        assert "Worker 0" in title_text
        assert "test.grib" in title_text

    def test_draw_column_headers_at_correct_row(self):
        tui, stdscr = self._make_tui()
        with _mock_curses():
            tui._draw_column_headers(120)
        calls = stdscr.addnstr.call_args_list
        assert len(calls) == 1
        row, col, text, _, attr = calls[0].args
        assert row == tui.HEADER_ROWS - 1  # column headers on last header row
        assert col == 0
        assert "W" in text
        assert "Status" in text
        assert "Filename" in text
        assert "Request ID" in text
        assert "Type" not in text
        assert "Finished" not in text
        assert attr & curses.A_BOLD
        # Green background via _CP_COL_HEADER color pair
        assert attr & (_CP_COL_HEADER << 8)

    def test_draw_column_headers_uses_space_separator(self):
        tui, stdscr = self._make_tui()
        with _mock_curses():
            tui._draw_column_headers(120)
        text = stdscr.addnstr.call_args_list[0].args[2]
        assert "│" not in text
        # Columns are separated by spaces, not pipe characters
        assert "Status" in text and "Filename" in text

    def test_draw_table_populates_row_worker_map(self):
        tui, stdscr = self._make_tui(num_workers=3)
        with _mock_curses():
            tui._draw_table(120, available_height=20)
        # 3 workers → 3 entries in row_worker_map
        assert len(tui._row_worker_map) == 3
        # Workers 0,1,2 should be at rows HEADER_ROWS+0, +1, +2
        assert tui._row_worker_map[tui.HEADER_ROWS] == 0
        assert tui._row_worker_map[tui.HEADER_ROWS + 1] == 1
        assert tui._row_worker_map[tui.HEADER_ROWS + 2] == 2

    def test_draw_table_selected_row_gets_highlight(self):
        tui, stdscr = self._make_tui(num_workers=2)
        tui._selected_worker = 1
        with _mock_curses():
            tui._draw_table(120, available_height=20)
        # Find addnstr calls for screen_row = HEADER_ROWS + 1 (worker 1)
        selected_row = tui.HEADER_ROWS + 1
        calls_for_selected = [
            c for c in stdscr.addnstr.call_args_list if c.args[0] == selected_row
        ]
        # At least one call should have _CP_SELECTED_ROW color pair
        attrs = [c.args[4] if len(c.args) > 4 else 0 for c in calls_for_selected]
        assert any(a & (_CP_SELECTED_ROW << 8) for a in attrs)

    def test_draw_table_unselected_row_no_highlight(self):
        tui, stdscr = self._make_tui(num_workers=2)
        tui._selected_worker = 0
        with _mock_curses():
            tui._draw_table(120, available_height=20)
        # Worker 1 is at HEADER_ROWS + 1 and is NOT selected
        unselected_row = tui.HEADER_ROWS + 1
        calls_for_unselected = [
            c for c in stdscr.addnstr.call_args_list if c.args[0] == unselected_row
        ]
        attrs = [c.args[4] if len(c.args) > 4 else 0 for c in calls_for_unselected]
        assert all(not (a & (_CP_SELECTED_ROW << 8)) for a in attrs)

    def test_draw_table_selection_indicator(self):
        tui, stdscr = self._make_tui(num_workers=2)
        tui._selected_worker = 0
        with _mock_curses():
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
        with _mock_curses():
            tui._draw_table(120, available_height=20)
        unselected_row = tui.HEADER_ROWS + 1
        calls_for_unselected = [
            c for c in stdscr.addnstr.call_args_list if c.args[0] == unselected_row
        ]
        texts = [c.args[2] for c in calls_for_unselected]
        assert not any("▸" in t for t in texts)

    def test_draw_table_status_badge_color(self):
        tui, stdscr = self._make_tui(num_workers=2)
        tui._selected_worker = 0  # Select worker 0
        tui._worker_cds_status[1] = "running"  # Test color on non-selected worker 1
        with _mock_curses():
            tui._draw_table(120, available_height=20)
        row = tui.HEADER_ROWS + 1  # Worker 1 row
        calls = [c for c in stdscr.addnstr.call_args_list if c.args[0] == row]
        # Find the call that renders the status text with foreground color
        status_calls = [c for c in calls if "running" in c.args[2] and len(c.args) > 4]
        assert len(status_calls) >= 1
        # Status should use the _CP_STATUS_RUNNING color pair (mocked as n << 8)
        status_attr = status_calls[0].args[4]
        assert status_attr == _CP_STATUS_RUNNING << 8

    def test_draw_table_clears_remaining_rows(self):
        tui, stdscr = self._make_tui(num_workers=2)
        available = 10
        with _mock_curses():
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
        with _mock_curses():
            tui._draw_table(120, available_height=10)
        row = tui.HEADER_ROWS
        texts = [c.args[2] for c in stdscr.addnstr.call_args_list if c.args[0] == row]
        assert any("era5_data.grib" in t for t in texts)

    def test_draw_table_shows_request_id(self):
        tui, stdscr = self._make_tui(num_workers=1)
        tui._worker_request_id[0] = "af1e2306-28c3-4abc"
        with _mock_curses():
            tui._draw_table(120, available_height=10)
        row = tui.HEADER_ROWS
        texts = [c.args[2] for c in stdscr.addnstr.call_args_list if c.args[0] == row]
        assert any("af1e2306" in t for t in texts)

    def test_draw_table_finished_elapsed_green(self):
        """When a worker is finished, Elapsed and DL% get green foreground + checkmark."""
        tui, stdscr = self._make_tui(num_workers=2)
        tui._selected_worker = 0  # Select worker 0
        tui._worker_finish_time[1] = 1000.0  # mark worker 1 as finished
        tui._worker_start_time[1] = 900.0
        tui._worker_dl_bytes[1] = 500
        tui._worker_dl_total[1] = 1000
        with _mock_curses():
            tui._draw_table(120, available_height=10)
        row = tui.HEADER_ROWS + 1  # Worker 1 row (non-selected)
        calls = [
            c
            for c in stdscr.addnstr.call_args_list
            if c.args[0] == row and len(c.args) > 4
        ]
        # Find calls with the green text color pair (mocked as n << 8)
        green_calls = [c for c in calls if c.args[4] == _CP_GREEN_TEXT << 8]
        # Should have at least 2: one for Elapsed, one for DL%
        assert len(green_calls) >= 2
        green_texts = [c.args[2] for c in green_calls]
        # One should contain elapsed time with checkmark, other pct with checkmark
        assert any("✓" in t for t in green_texts)
        assert any("%" in t for t in green_texts)

    def test_draw_table_unfinished_no_green_elapsed(self):
        tui, stdscr = self._make_tui(num_workers=2)
        tui._selected_worker = 0  # Select worker 0
        # Worker 1 not finished — finish_time is None
        tui._worker_start_time[1] = 900.0
        with _mock_curses():
            tui._draw_table(120, available_height=10)
        row = tui.HEADER_ROWS + 1  # Worker 1 row (non-selected)
        calls = [
            c
            for c in stdscr.addnstr.call_args_list
            if c.args[0] == row and len(c.args) > 4
        ]
        green_calls = [c for c in calls if c.args[4] == _CP_GREEN_TEXT << 8]
        assert len(green_calls) == 0

    def test_do_refresh_calls_all_draw_methods(self):
        tui, stdscr = self._make_tui()
        tui._last_size = (0, 0)  # force full redraw
        with (
            _mock_curses(),
            patch.object(tui, "_draw_header") as dh,
            patch.object(tui, "_draw_info_panel") as dip,
            patch.object(tui, "_draw_column_headers") as dch,
            patch.object(tui, "_draw_table") as dt,
            patch.object(tui, "_draw_progress_bar") as dpb,
        ):
            tui._do_refresh()
            dh.assert_called_once()
            dip.assert_called_once()
            dch.assert_called_once()
            dt.assert_called_once()
            dpb.assert_called_once()

    def test_do_refresh_log_view_skips_table(self):
        tui, stdscr = self._make_tui()
        tui._view_mode = "logs"
        tui._last_size = (0, 0)
        with (
            _mock_curses(),
            patch.object(tui, "_draw_header") as dh,
            patch.object(tui, "_draw_info_panel") as dip,
            patch.object(tui, "_draw_column_headers") as dch,
            patch.object(tui, "_draw_table") as dt,
            patch.object(tui, "_draw_log_view") as dlv,
            patch.object(tui, "_draw_progress_bar") as dpb,
        ):
            tui._do_refresh()
            dh.assert_called_once()
            dip.assert_not_called()
            dch.assert_not_called()
            dt.assert_not_called()
            dlv.assert_called_once()
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


class TestInfoPanel:
    """Tests for the info panel drawing."""

    def _make_tui(self, num_workers=3, height=30, width=120):
        tui = CursesTUI(num_workers=num_workers)
        stdscr = _mock_stdscr(height, width)
        tui._stdscr = stdscr
        tui._last_size = (height, width)
        return tui, stdscr

    def test_info_panel_shows_worker_id(self):
        tui, stdscr = self._make_tui()
        tui._selected_worker = 1
        with _mock_curses():
            tui._draw_info_panel(120)
        # Worker badge is on row 2 (row 1 is top border)
        calls = [c for c in stdscr.addnstr.call_args_list if c.args[0] == 2]
        texts = [c.args[2] for c in calls]
        assert any("Worker 1" in t for t in texts)

    def test_info_panel_shows_dataset(self):
        tui, stdscr = self._make_tui()
        tui._worker_dataset[0] = "reanalysis-era5-single-levels"
        with _mock_curses():
            tui._draw_info_panel(120)
        # Dataset is on row 6
        calls = [c for c in stdscr.addnstr.call_args_list if c.args[0] == 6]
        texts = [c.args[2] for c in calls]
        assert any("reanalysis-era5-single-levels" in t for t in texts)

    def test_info_panel_shows_params(self):
        tui, stdscr = self._make_tui()
        tui._worker_request_params[0] = {"variable": "2m_temperature", "year": "2024"}
        with _mock_curses():
            tui._draw_info_panel(120)
        # Params are on rows 10-13
        calls = [
            c for c in stdscr.addnstr.call_args_list if c.args[0] in (10, 11, 12, 13)
        ]
        texts = [c.args[2] for c in calls]
        assert any("variable=2m_temperature" in t for t in texts)
        assert any("year=2024" in t for t in texts)

    def test_info_panel_shows_destination(self):
        tui, stdscr = self._make_tui()
        tui._worker_target[0] = "/data/downloads/era5_t2m.grib"
        with _mock_curses():
            tui._draw_info_panel(120)
        # Destination is on row 4
        calls = [c for c in stdscr.addnstr.call_args_list if c.args[0] == 4]
        texts = [c.args[2] for c in calls]
        assert any("/data/downloads" in t for t in texts)

    def test_info_panel_shows_filetype(self):
        tui, stdscr = self._make_tui()
        tui._worker_filename[0] = "era5_data.grib"
        with _mock_curses():
            tui._draw_info_panel(120)
        # Filetype (uppercase) is on row 2 (content row with Worker/Type/Filename)
        calls = [c for c in stdscr.addnstr.call_args_list if c.args[0] == 2]
        texts = [c.args[2] for c in calls]
        assert any("GRIB" in t for t in texts)

    def test_info_panel_updates_with_selection(self):
        tui, stdscr = self._make_tui()
        tui._worker_filename[0] = "file_a.grib"
        tui._worker_filename[1] = "file_b.nc"
        tui._selected_worker = 0
        with _mock_curses():
            tui._draw_info_panel(120)
        # Filename is on row 2
        calls_0 = [c for c in stdscr.addnstr.call_args_list if c.args[0] == 2]
        texts_0 = [c.args[2] for c in calls_0]
        assert any("file_a.grib" in t for t in texts_0)

        stdscr.addnstr.reset_mock()
        tui._selected_worker = 1
        with _mock_curses():
            tui._draw_info_panel(120)
        calls_1 = [c for c in stdscr.addnstr.call_args_list if c.args[0] == 2]
        texts_1 = [c.args[2] for c in calls_1]
        assert any("file_b.nc" in t for t in texts_1)


class TestLogView:
    """Tests for the full-screen log view."""

    def _make_tui(self, num_workers=3, height=30, width=120):
        tui = CursesTUI(num_workers=num_workers)
        stdscr = _mock_stdscr(height, width)
        tui._stdscr = stdscr
        tui._last_size = (height, width)
        return tui, stdscr

    def test_open_log_view_sets_mode(self):
        tui, _ = self._make_tui()
        assert tui._view_mode == "table"
        with _mock_curses():
            tui.open_log_view()
        assert tui._view_mode == "logs"

    def test_close_log_view_returns_to_table(self):
        tui, _ = self._make_tui()
        with _mock_curses():
            tui.open_log_view()
            tui.close_log_view()
        assert tui._view_mode == "table"

    def test_log_view_shows_log_lines(self):
        tui, stdscr = self._make_tui()
        tui.append_worker_log(0, "line A")
        tui.append_worker_log(0, "line B")
        tui.append_worker_log(0, "line C")
        tui._view_mode = "logs"
        tui._log_scroll = 0
        stdscr.addnstr.reset_mock()
        tui._draw_log_view(120, available_height=20)
        calls = stdscr.addnstr.call_args_list
        texts = [c.args[2] for c in calls]
        assert any("line A" in t for t in texts)
        assert any("line B" in t for t in texts)
        assert any("line C" in t for t in texts)

    def test_log_view_scroll(self):
        tui, stdscr = self._make_tui()
        for i in range(50):
            tui.append_worker_log(0, f"line {i}")
        tui._view_mode = "logs"
        tui._log_scroll = 40
        stdscr.addnstr.reset_mock()
        tui._draw_log_view(120, available_height=5)
        texts = [c.args[2] for c in stdscr.addnstr.call_args_list]
        assert any("line 40" in t for t in texts)

    def test_select_up_scrolls_in_log_view(self):
        tui, _ = self._make_tui()
        for i in range(50):
            tui.append_worker_log(0, f"line {i}")
        tui._view_mode = "logs"
        tui._log_scroll = 10
        with _mock_curses():
            tui.select_up()
        assert tui._log_scroll == 9

    def test_select_down_scrolls_in_log_view(self):
        tui, _ = self._make_tui()
        for i in range(50):
            tui.append_worker_log(0, f"line {i}")
        tui._view_mode = "logs"
        tui._log_scroll = 10
        with _mock_curses():
            tui.select_down()
        assert tui._log_scroll == 11

    def test_page_up(self):
        tui, _ = self._make_tui()
        for i in range(100):
            tui.append_worker_log(0, f"line {i}")
        tui._view_mode = "logs"
        tui._log_scroll = 50
        with _mock_curses():
            tui.page_up()
        assert tui._log_scroll < 50

    def test_page_down(self):
        tui, _ = self._make_tui()
        for i in range(100):
            tui.append_worker_log(0, f"line {i}")
        tui._view_mode = "logs"
        tui._log_scroll = 10
        with _mock_curses():
            tui.page_down()
        assert tui._log_scroll > 10

    def test_log_home(self):
        tui, _ = self._make_tui()
        for i in range(50):
            tui.append_worker_log(0, f"line {i}")
        tui._view_mode = "logs"
        tui._log_scroll = 30
        with _mock_curses():
            tui.log_home()
        assert tui._log_scroll == 0

    def test_log_end(self):
        tui, _ = self._make_tui()
        for i in range(50):
            tui.append_worker_log(0, f"line {i}")
        tui._view_mode = "logs"
        tui._log_scroll = 0
        with _mock_curses():
            tui.log_end()
        # Scroll should jump to the end (clamped by _draw_log_view during refresh)
        # available_height = 30 - 2 - 1 = 27, so max_scroll = 50 - 27 = 23
        assert tui._log_scroll > 0

    def test_double_click_opens_log_view(self):
        tui, stdscr = self._make_tui(num_workers=4)
        tui._last_size = (30, 120)
        with _mock_curses():
            tui._do_refresh()
        target_row = tui.HEADER_ROWS + 2
        with _mock_curses():
            tui.handle_mouse((0, 10, target_row, 0, curses.BUTTON1_DOUBLE_CLICKED))
        assert tui._selected_worker == 2
        assert tui._view_mode == "logs"

    def test_mouse_ignored_in_log_view(self):
        tui, _ = self._make_tui()
        tui._view_mode = "logs"
        tui._selected_worker = 0
        with _mock_curses():
            tui.handle_mouse((0, 10, 5, 0, curses.BUTTON1_CLICKED))
        # Should still be in log view, selection unchanged
        assert tui._view_mode == "logs"
        assert tui._selected_worker == 0


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
        # height=20, HEADER_ROWS=14, PROGRESS_ROWS=2 → available=4
        tui = CursesTUI(num_workers=10)
        tui._stdscr = _mock_stdscr(height=20, width=120)
        tui._table_scroll = 0
        tui._selected_worker = 8  # well below the visible 6 rows
        tui._ensure_selected_visible()
        # scroll should have moved so worker 8 is visible
        assert tui._table_scroll > 0
        assert tui._table_scroll <= 8

    def test_scroll_up_when_selected_above_view(self):
        tui = CursesTUI(num_workers=10)
        tui._stdscr = _mock_stdscr(height=20, width=120)
        tui._table_scroll = 5
        tui._selected_worker = 2  # above scroll position
        tui._ensure_selected_visible()
        assert tui._table_scroll <= 2

    def test_no_scroll_when_selected_visible(self):
        tui = CursesTUI(num_workers=4)
        tui._stdscr = _mock_stdscr(height=30, width=120)
        tui._table_scroll = 0
        tui._selected_worker = 2
        tui._ensure_selected_visible()
        assert tui._table_scroll == 0


class TestTableScroll:
    def test_scroll_clamped_to_max(self):
        tui = CursesTUI(num_workers=3)
        tui._stdscr = _mock_stdscr(height=30, width=120)
        tui._table_scroll = 100  # way too high
        tui._last_size = (30, 120)
        with _mock_curses():
            tui._draw_table(120, available_height=20)
        # After draw, scroll should be clamped (3 rows, 20 available → max_scroll=0)
        assert tui._table_scroll == 0

    def test_scroll_never_negative(self):
        tui = CursesTUI(num_workers=3)
        tui._stdscr = _mock_stdscr(height=30, width=120)
        tui._table_scroll = -5
        tui._last_size = (30, 120)
        with _mock_curses():
            tui._draw_table(120, available_height=20)
        assert tui._table_scroll >= 0


class TestNewMetadataSetters:
    """Tests for the new metadata-related setter methods."""

    def test_set_worker_server_progress(self):
        tui = CursesTUI(num_workers=2)
        tui._stdscr = _mock_stdscr()
        tui.set_worker_server_progress(0, 72)
        assert tui._worker_server_progress[0] == 72
        assert tui._worker_server_progress[1] is None

    def test_set_worker_file_size(self):
        tui = CursesTUI(num_workers=2)
        tui._stdscr = _mock_stdscr()
        tui.set_worker_file_size(0, 95418)
        assert tui._worker_file_size[0] == 95418
        # Also populates dl_total when not already set
        assert tui._worker_dl_total[0] == 95418

    def test_set_worker_file_size_no_override_tqdm(self):
        tui = CursesTUI(num_workers=2)
        tui._stdscr = _mock_stdscr()
        tui._worker_dl_total[0] = 100000  # Already set by tqdm
        tui.set_worker_file_size(0, 95418)
        assert tui._worker_file_size[0] == 95418
        assert tui._worker_dl_total[0] == 100000  # Not overridden

    def test_set_worker_checksum_result(self):
        tui = CursesTUI(num_workers=2)
        tui._stdscr = _mock_stdscr()
        tui.set_worker_checksum_result(0, True)
        assert tui._worker_checksum[0] is True
        tui.set_worker_checksum_result(1, False)
        assert tui._worker_checksum[1] is False

    def test_set_worker_server_timestamps(self):
        tui = CursesTUI(num_workers=2)
        tui._stdscr = _mock_stdscr()
        tui.set_worker_server_timestamps(
            0, "2024-01-01T00:00:00Z", "2024-01-01T00:05:00Z", ""
        )
        assert tui._worker_server_created[0] == "2024-01-01T00:00:00Z"
        assert tui._worker_server_started[0] == "2024-01-01T00:05:00Z"
        assert tui._worker_server_finished[0] == ""

    def test_set_worker_dataset_title(self):
        tui = CursesTUI(num_workers=2)
        tui._stdscr = _mock_stdscr()
        tui.set_worker_dataset_title(0, "ERA5 hourly data on single levels")
        assert tui._worker_dataset_title[0] == "ERA5 hourly data on single levels"
        assert tui._worker_dataset_title[1] == ""

    def test_set_worker_request_labels(self):
        tui = CursesTUI(num_workers=2)
        tui._stdscr = _mock_stdscr()
        labels = {"Variable": "2m temperature", "Year": "2024"}
        tui.set_worker_request_labels(0, labels)
        assert tui._worker_request_labels[0] == labels
        assert tui._worker_request_labels[1] is None

    def test_set_qos_data(self):
        tui = CursesTUI(num_workers=2)
        tui._stdscr = _mock_stdscr()
        tui.set_qos_data(5220, 400, 400)
        assert tui._qos_queued == 5220
        assert tui._qos_running == 400
        assert tui._qos_limit == 400

    def test_clear_worker_log_resets_metadata(self):
        tui = CursesTUI(num_workers=2)
        tui._stdscr = _mock_stdscr()
        tui.set_worker_server_progress(0, 50)
        tui.set_worker_file_size(0, 1000)
        tui.set_worker_checksum_result(0, True)
        tui.set_worker_server_timestamps(0, "a", "b", "c")
        tui.set_worker_dataset_title(0, "title")
        tui.set_worker_request_labels(0, {"k": "v"})
        tui.clear_worker_log(0)
        assert tui._worker_server_progress[0] is None
        assert tui._worker_file_size[0] is None
        assert tui._worker_checksum[0] is None
        assert tui._worker_server_created[0] is None
        assert tui._worker_server_started[0] is None
        assert tui._worker_server_finished[0] is None
        assert tui._worker_dataset_title[0] == ""
        assert tui._worker_request_labels[0] is None

    def test_out_of_range_setters(self):
        tui = CursesTUI(num_workers=2)
        tui._stdscr = _mock_stdscr()
        # Should not raise for out-of-range
        tui.set_worker_server_progress(5, 50)
        tui.set_worker_file_size(-1, 1000)
        tui.set_worker_checksum_result(99, True)
        tui.set_worker_server_timestamps(10, "", "", "")
        tui.set_worker_dataset_title(5, "title")
        tui.set_worker_request_labels(5, {})


class TestFormatServerProgress:
    def test_none(self):
        tui = CursesTUI(num_workers=1)
        assert tui._format_server_progress(0) == "---"

    def test_zero(self):
        tui = CursesTUI(num_workers=1)
        tui._worker_server_progress[0] = 0
        assert tui._format_server_progress(0) == "0%"

    def test_middle(self):
        tui = CursesTUI(num_workers=1)
        tui._worker_server_progress[0] = 72
        assert tui._format_server_progress(0) == "72%"

    def test_complete(self):
        tui = CursesTUI(num_workers=1)
        tui._worker_server_progress[0] = 100
        assert tui._format_server_progress(0) == "100%"


class TestInfoPanelNew:
    """Tests for new info panel features."""

    def _make_tui(self, num_workers=3, height=35, width=120):
        tui = CursesTUI(num_workers=num_workers)
        stdscr = _mock_stdscr(height, width)
        tui._stdscr = stdscr
        tui._last_size = (height, width)
        return tui, stdscr

    def test_header_rows_is_sixteen(self):
        assert CursesTUI.HEADER_ROWS == 16

    def test_info_panel_shows_dataset_title(self):
        tui, stdscr = self._make_tui()
        tui._worker_dataset_title[0] = "ERA5 hourly data on single levels"
        with _mock_curses():
            tui._draw_info_panel(120)
        calls = [c for c in stdscr.addnstr.call_args_list if c.args[0] == 6]
        texts = [c.args[2] for c in calls]
        assert any("ERA5 hourly data on single levels" in t for t in texts)

    def test_info_panel_falls_back_to_process_id(self):
        tui, stdscr = self._make_tui()
        tui._worker_dataset[0] = "reanalysis-era5-single-levels"
        tui._worker_dataset_title[0] = ""  # No title
        with _mock_curses():
            tui._draw_info_panel(120)
        calls = [c for c in stdscr.addnstr.call_args_list if c.args[0] == 6]
        texts = [c.args[2] for c in calls]
        assert any("reanalysis-era5-single-levels" in t for t in texts)

    def test_info_panel_shows_request_id_on_row_8(self):
        tui, stdscr = self._make_tui()
        tui._worker_request_id[0] = "af1e2306-28c3-test"
        with _mock_curses():
            tui._draw_info_panel(120)
        calls = [c for c in stdscr.addnstr.call_args_list if c.args[0] == 8]
        texts = [c.args[2] for c in calls]
        assert any("af1e2306-28c3-test" in t for t in texts)

    def test_info_panel_shows_checksum_ok(self):
        tui, stdscr = self._make_tui()
        tui._worker_checksum[0] = True
        with _mock_curses():
            tui._draw_info_panel(120)
        calls = [c for c in stdscr.addnstr.call_args_list if c.args[0] == 8]
        texts = [c.args[2] for c in calls]
        assert any("Checksum: OK" in t for t in texts)

    def test_info_panel_shows_checksum_mismatch(self):
        tui, stdscr = self._make_tui()
        tui._worker_checksum[0] = False
        with _mock_curses():
            tui._draw_info_panel(120)
        calls = [c for c in stdscr.addnstr.call_args_list if c.args[0] == 8]
        texts = [c.args[2] for c in calls]
        assert any("MISMATCH" in t for t in texts)

    def test_info_panel_uses_labels_when_available(self):
        tui, stdscr = self._make_tui()
        tui._worker_request_labels[0] = {"Variable": "2m temperature", "Year": "2024"}
        with _mock_curses():
            tui._draw_info_panel(120)
        calls = [
            c for c in stdscr.addnstr.call_args_list if c.args[0] in (10, 11, 12, 13)
        ]
        texts = [c.args[2] for c in calls]
        # Labels use "Key: Value" format
        assert any("Variable: 2m temperature" in t for t in texts)

    def test_info_panel_falls_back_to_raw_params(self):
        tui, stdscr = self._make_tui()
        tui._worker_request_labels[0] = None
        tui._worker_request_params[0] = {"variable": "2m_temperature"}
        with _mock_curses():
            tui._draw_info_panel(120)
        calls = [
            c for c in stdscr.addnstr.call_args_list if c.args[0] in (10, 11, 12, 13)
        ]
        texts = [c.args[2] for c in calls]
        assert any("variable=2m_temperature" in t for t in texts)


class TestQoSStatusLine:
    """Tests for QoS data in the status line."""

    def _make_tui(self, num_workers=2, height=30, width=120):
        tui = CursesTUI(num_workers=num_workers)
        stdscr = _mock_stdscr(height, width)
        tui._stdscr = stdscr
        tui._last_size = (height, width)
        return tui, stdscr

    def test_qos_prepended_to_status_line(self):
        tui, stdscr = self._make_tui()
        tui._qos_queued = 5220
        tui._qos_running = 400
        tui._qos_limit = 400
        tui._status_line = "Downloading 20 files"
        with _mock_curses():
            tui._draw_progress_bar(30, 120)
        status_row = 29  # height - 1
        calls = [c for c in stdscr.addnstr.call_args_list if c.args[0] == status_row]
        texts = [c.args[2] for c in calls]
        assert any("5220 queued" in t for t in texts)
        assert any("400/400 running" in t for t in texts)
        assert any("Downloading 20 files" in t for t in texts)

    def test_no_qos_when_zeros(self):
        tui, stdscr = self._make_tui()
        tui._qos_queued = 0
        tui._qos_running = 0
        tui._qos_limit = 0
        tui._status_line = "Downloading"
        with _mock_curses():
            tui._draw_progress_bar(30, 120)
        status_row = 29
        calls = [c for c in stdscr.addnstr.call_args_list if c.args[0] == status_row]
        texts = [c.args[2] for c in calls]
        assert not any("CDS Server" in t for t in texts)


class TestChecksumDialog:
    """Tests for the checksum dialog view mode."""

    def _make_tui(self, num_workers=3, height=30, width=120):
        tui = CursesTUI(num_workers=num_workers)
        stdscr = _mock_stdscr(height, width)
        tui._stdscr = stdscr
        tui._last_size = (height, width)
        return tui, stdscr

    def test_handle_checksum_key_not_in_dialog(self):
        tui, _ = self._make_tui()
        assert tui._view_mode == "table"
        result = tui.handle_checksum_key(ord("c"))
        assert result is False

    def test_handle_checksum_key_continue(self):
        tui, _ = self._make_tui()
        tui._view_mode = "checksum"
        tui._checksum_dialog_worker = 0
        with _mock_curses():
            result = tui.handle_checksum_key(ord("c"))
        assert result is True
        assert tui._checksum_dialog_result == "continue"
        assert tui._view_mode == "table"

    def test_handle_checksum_key_retry(self):
        tui, _ = self._make_tui()
        tui._view_mode = "checksum"
        tui._checksum_dialog_worker = 0
        with _mock_curses():
            result = tui.handle_checksum_key(ord("r"))
        assert result is True
        assert tui._checksum_dialog_result == "retry"
        assert tui._view_mode == "table"

    def test_handle_checksum_key_other(self):
        tui, _ = self._make_tui()
        tui._view_mode = "checksum"
        tui._checksum_dialog_worker = 0
        with _mock_curses():
            result = tui.handle_checksum_key(ord("x"))
        assert result is False
        assert tui._view_mode == "checksum"

    def test_do_refresh_checksum_view(self):
        tui, stdscr = self._make_tui()
        tui._view_mode = "checksum"
        tui._checksum_dialog_worker = 0
        tui._checksum_dialog_expected = "abc123"
        tui._worker_filename[0] = "test.grib"
        tui._last_size = (0, 0)
        with (
            _mock_curses(),
            patch.object(tui, "_draw_checksum_dialog") as dcd,
        ):
            tui._do_refresh()
            dcd.assert_called_once()
