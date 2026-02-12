"""Tests for the curses TUI."""

from unittest.mock import MagicMock


from cdsswarm.tui import CursesTUI, _format_eta


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

    def test_panel_geometry_splits_correctly(self):
        tui = CursesTUI(num_workers=2)
        tui._stdscr = _mock_stdscr(height=40, width=80)
        panels, w = tui._panel_geometry()
        assert len(panels) == 2
        assert w == 80
        # Each panel starts at HEADER_ROWS offset
        assert panels[0][0] == tui.HEADER_ROWS
        # Both panels have positive height
        assert all(h > 0 for _, h in panels)

    def test_small_terminal_warning(self):
        tui = CursesTUI(num_workers=2)
        stdscr = _mock_stdscr(height=5, width=20)
        tui._stdscr = stdscr
        tui._do_refresh()
        stdscr.erase.assert_called()
        stdscr.addstr.assert_called()

    def test_cycle_focus(self):
        tui = CursesTUI(num_workers=3)
        tui._stdscr = _mock_stdscr()
        assert tui._focused_worker is None
        tui.cycle_focus()
        assert tui._focused_worker == 0
        tui.cycle_focus()
        assert tui._focused_worker == 1
        tui.cycle_focus()
        assert tui._focused_worker == 2
        tui.cycle_focus()
        assert tui._focused_worker is None

    def test_scroll_offset(self):
        tui = CursesTUI(num_workers=2)
        tui._stdscr = _mock_stdscr()
        # Add enough log entries
        for i in range(20):
            tui.append_worker_log(0, f"line {i}")
        # Focus worker 0
        tui.cycle_focus()
        assert tui._focused_worker == 0
        # Scroll up
        tui.scroll_log_up()
        assert tui._scroll_offset[0] == 3
        tui.scroll_log_up()
        assert tui._scroll_offset[0] == 6
        # Scroll down
        tui.scroll_log_down()
        assert tui._scroll_offset[0] == 3
        tui.scroll_log_down()
        assert tui._scroll_offset[0] == 0
        # Scroll down past 0 stays at 0
        tui.scroll_log_down()
        assert tui._scroll_offset[0] == 0

    def test_append_resets_scroll_offset(self):
        tui = CursesTUI(num_workers=2)
        tui._stdscr = _mock_stdscr()
        for i in range(20):
            tui.append_worker_log(0, f"line {i}")
        tui.cycle_focus()
        tui.scroll_log_up()
        assert tui._scroll_offset[0] > 0
        # New log entry resets scroll
        tui.append_worker_log(0, "new line")
        assert tui._scroll_offset[0] == 0

    def test_focused_panel_geometry(self):
        tui = CursesTUI(num_workers=3)
        tui._stdscr = _mock_stdscr(height=40, width=80)
        tui.cycle_focus()  # Focus worker 0
        panels, w = tui._panel_geometry()
        # Focused worker gets all space
        assert panels[0][1] > 0
        # Others get 0 height
        assert panels[1][1] == 0
        assert panels[2][1] == 0

    def test_title_parameter(self):
        tui = CursesTUI(num_workers=1, title="my-app")
        assert tui._title == "my-app"

    def test_scroll_no_focus_is_noop(self):
        tui = CursesTUI(num_workers=2)
        tui._stdscr = _mock_stdscr()
        tui.scroll_log_up()
        tui.scroll_log_down()
        assert tui._scroll_offset == [0, 0]
