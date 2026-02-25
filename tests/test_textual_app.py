"""Tests for the Textual TUI."""

import time

import pytest
from textual.widgets import DataTable

from cdsswarm.core import Task
from cdsswarm.status import FileStatus, WorkerStatus
from cdsswarm.textual_app import (
    CdsswarmApp,
    FileActive,
    FileCompleted,
    FileData,
    FilesInfoPanel,
    GlobalMessage,
    LogScreen,
    MeterBar,
    ParamsScreen,
    ProgressUpdate,
    ServerStatsUpdate,
    TasksInitialized,
    WorkerCdsStatus,
    WorkerData,
    WorkerDatasetTitle,
    WorkerFileSize,
    WorkerFinished,
    WorkerInfoPanel,
    WorkerMessage,
    WorkerProgress,
    WorkerStateUpdate,
    WorkerRequestId,
    WorkerRequestLabels,
    WorkerServerTimestamps,
    WorkerStarted,
    WorkerCancelled,
    format_eta,
    format_size,
    styled_status,
)


# ---------------------------------------------------------------------------
# Formatting helper tests
# ---------------------------------------------------------------------------


class TestFormatEta:
    def test_negative(self):
        assert format_eta(-1) == "??:??"

    def test_zero(self):
        assert format_eta(0) == "0m00s"

    def test_seconds(self):
        assert format_eta(45) == "0m45s"

    def test_minutes(self):
        assert format_eta(125) == "2m05s"

    def test_hours(self):
        assert format_eta(3661) == "1h01m01s"

    def test_large(self):
        assert format_eta(7200) == "2h00m00s"


class TestFormatSize:
    def test_zero(self):
        assert format_size(0) == "\u2014"

    def test_bytes(self):
        assert format_size(512) == "512 B"

    def test_kilobytes(self):
        assert format_size(2048) == "2.0 KB"

    def test_megabytes(self):
        assert format_size(10 * 1024 * 1024) == "10.0 MB"

    def test_gigabytes(self):
        assert format_size(2 * 1024**3) == "2.0 GB"


class TestStyledStatus:
    def test_idle(self):
        t = styled_status(WorkerStatus.IDLE)
        assert t.plain == "idle"

    def test_running(self):
        t = styled_status(WorkerStatus.RUNNING)
        assert t.plain == "running"

    def test_successful(self):
        t = styled_status(WorkerStatus.SUCCESSFUL)
        assert t.plain == "successful"


# ---------------------------------------------------------------------------
# WorkerData tests
# ---------------------------------------------------------------------------


class TestWorkerData:
    def test_default(self):
        w = WorkerData()
        assert w.cds_status is WorkerStatus.IDLE
        assert w.filename == ""
        assert w.start_time is None

    def test_reset(self):
        w = WorkerData()
        w.cds_status = WorkerStatus.RUNNING
        w.filename = "test.grib"
        w.start_time = 1000.0
        w.logs.append("hello")
        w.reset()
        assert w.cds_status is WorkerStatus.IDLE
        assert w.filename == ""
        assert w.start_time is None
        assert len(w.logs) == 0


# ---------------------------------------------------------------------------
# Helper to create test tasks
# ---------------------------------------------------------------------------


def _make_tasks(n=5):
    return [
        Task(
            dataset=f"dataset-{i}",
            request={"variable": f"var_{i}", "year": "2024"},
            target=f"/data/file_{i}.grib",
        )
        for i in range(n)
    ]


# ---------------------------------------------------------------------------
# Pilot-based async tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_app_starts_with_worker_table():
    """App creates a worker table with correct number of rows."""
    app = CdsswarmApp(num_workers=3)
    async with app.run_test():
        wt = app.query_one("#worker-table", DataTable)
        assert wt.row_count == 3


@pytest.mark.asyncio
async def test_worker_started_updates_table():
    """WorkerStarted message updates the table."""
    app = CdsswarmApp(num_workers=2)
    async with app.run_test() as pilot:
        app.post_message(
            WorkerStarted(
                0,
                "era5_2024.grib",
                "reanalysis-era5",
                {"var": "t2m"},
                "/out/era5.grib",
            )
        )
        await pilot.pause()
        wt = app.query_one("#worker-table", DataTable)
        row = wt.get_row("0")
        # Filename should be in the row
        assert any("era5_2024.grib" in str(cell) for cell in row)


@pytest.mark.asyncio
async def test_worker_cds_status_updates_table():
    """WorkerCdsStatus message updates status cell."""
    app = CdsswarmApp(num_workers=2)
    async with app.run_test() as pilot:
        app.post_message(WorkerCdsStatus(0, WorkerStatus.RUNNING))
        await pilot.pause()
        wt = app.query_one("#worker-table", DataTable)
        row = wt.get_row("0")
        assert any("running" in str(cell) for cell in row)


@pytest.mark.asyncio
async def test_worker_request_id_updates_table():
    """WorkerRequestId message updates request ID cell."""
    app = CdsswarmApp(num_workers=2)
    async with app.run_test() as pilot:
        app.post_message(WorkerRequestId(0, "abc123-def"))
        await pilot.pause()
        wt = app.query_one("#worker-table", DataTable)
        row = wt.get_row("0")
        assert any("abc123-def" in str(cell) for cell in row)


@pytest.mark.asyncio
async def test_worker_progress_updates_table():
    """WorkerProgress message updates DL % and Size cells."""
    app = CdsswarmApp(num_workers=2)
    async with app.run_test() as pilot:
        app.post_message(WorkerProgress(0, 500, 1000))
        await pilot.pause()
        wt = app.query_one("#worker-table", DataTable)
        row = wt.get_row("0")
        assert any("50%" in str(cell) for cell in row)


@pytest.mark.asyncio
async def test_worker_finished_shows_checkmark():
    """WorkerFinished message marks elapsed with checkmark."""
    app = CdsswarmApp(num_workers=2)
    async with app.run_test() as pilot:
        # First start the worker so it has start_time
        app.post_message(
            WorkerStarted(
                0,
                "test.grib",
                "ds",
                {},
                "/out/test.grib",
            )
        )
        await pilot.pause()
        app.post_message(WorkerFinished(0))
        await pilot.pause()
        wt = app.query_one("#worker-table", DataTable)
        row = wt.get_row("0")
        # Elapsed should contain checkmark
        assert any("\u2713" in str(cell) for cell in row)


@pytest.mark.asyncio
async def test_worker_finished_failure_shows_cross():
    """WorkerFinished with success=False marks elapsed with red cross."""
    app = CdsswarmApp(num_workers=2)
    async with app.run_test() as pilot:
        app.post_message(WorkerStarted(0, "test.grib", "ds", {}, "/out/test.grib"))
        await pilot.pause()
        app.post_message(WorkerFinished(0, success=False, error="timeout"))
        await pilot.pause()
        wt = app.query_one("#worker-table", DataTable)
        row = wt.get_row("0")
        assert any("\u2717" in str(cell) for cell in row)
        assert "FAILED: timeout" in app.worker_data[0].logs


@pytest.mark.asyncio
async def test_worker_cancelled_status():
    """WorkerCancelled sets status to cancelled."""
    app = CdsswarmApp(num_workers=2)
    async with app.run_test() as pilot:
        app.post_message(WorkerCancelled(0))
        await pilot.pause()
        assert app.worker_data[0].cds_status is WorkerStatus.CANCELLED
        wt = app.query_one("#worker-table", DataTable)
        row = wt.get_row("0")
        assert any("cancelled" in str(cell) for cell in row)


@pytest.mark.asyncio
async def test_cancelled_worker_ignores_status_update():
    """Once cancelled, further status updates are ignored."""
    app = CdsswarmApp(num_workers=2)
    async with app.run_test() as pilot:
        app.post_message(WorkerCancelled(0))
        await pilot.pause()
        app.post_message(WorkerCdsStatus(0, WorkerStatus.RUNNING))
        await pilot.pause()
        assert app.worker_data[0].cds_status is WorkerStatus.CANCELLED


@pytest.mark.asyncio
async def test_progress_update():
    """ProgressUpdate updates the footer."""
    app = CdsswarmApp(num_workers=2)
    async with app.run_test() as pilot:
        app.post_message(ProgressUpdate(3, 10, 2))
        await pilot.pause()
        assert app.progress_completed == 3
        assert app.progress_total == 10
        assert app.progress_skipped == 2


@pytest.mark.asyncio
async def test_global_message():
    """GlobalMessage updates status message."""
    app = CdsswarmApp(num_workers=2)
    async with app.run_test() as pilot:
        app.post_message(GlobalMessage("Downloading 20 files"))
        await pilot.pause()
        assert app.status_message == "Downloading 20 files"


@pytest.mark.asyncio
async def test_tasks_initialized_populates_files():
    """TasksInitialized sets up the files table."""
    app = CdsswarmApp(num_workers=2)
    async with app.run_test() as pilot:
        tasks = _make_tasks(5)
        skipped = {tasks[0].target, tasks[2].target}
        app.post_message(TasksInitialized(tasks, skipped))
        await pilot.pause()
        ft = app.query_one("#files-table", DataTable)
        assert ft.row_count == 5
        assert app.files[0].status is FileStatus.CACHED
        assert app.files[1].status is FileStatus.PENDING
        assert app.files[2].status is FileStatus.CACHED


@pytest.mark.asyncio
async def test_file_active():
    """FileActive marks a file as active."""
    app = CdsswarmApp(num_workers=2)
    async with app.run_test() as pilot:
        tasks = _make_tasks(3)
        app.post_message(TasksInitialized(tasks, set()))
        await pilot.pause()
        app.post_message(FileActive(tasks[1].target, 0))
        await pilot.pause()
        assert app.files[1].status is FileStatus.ACTIVE
        assert app.files[1].worker_id == 0


@pytest.mark.asyncio
async def test_file_completed_success():
    """FileCompleted marks a file as successful."""
    app = CdsswarmApp(num_workers=2)
    async with app.run_test() as pilot:
        tasks = _make_tasks(3)
        app.post_message(TasksInitialized(tasks, set()))
        await pilot.pause()
        app.post_message(FileActive(tasks[0].target, 0))
        await pilot.pause()
        app.post_message(FileCompleted(tasks[0].target, True))
        await pilot.pause()
        assert app.files[0].status is FileStatus.SUCCESSFUL


@pytest.mark.asyncio
async def test_file_completed_failure():
    """FileCompleted marks a file as failed."""
    app = CdsswarmApp(num_workers=2)
    async with app.run_test() as pilot:
        tasks = _make_tasks(3)
        app.post_message(TasksInitialized(tasks, set()))
        await pilot.pause()
        app.post_message(FileActive(tasks[0].target, 1))
        await pilot.pause()
        app.post_message(FileCompleted(tasks[0].target, False, "timeout"))
        await pilot.pause()
        assert app.files[0].status is FileStatus.FAILED
        assert app.files[0].error == "timeout"


@pytest.mark.asyncio
async def test_tab_switching():
    """'t' key switches between Workers and Files tabs."""
    app = CdsswarmApp(num_workers=2)
    async with app.run_test() as pilot:
        assert app._active_tab == "workers"
        await pilot.press("t")
        assert app._active_tab == "files"
        # Worker table should be hidden, files table visible
        assert app.query_one("#worker-table").has_class("hidden")
        assert not app.query_one("#files-table").has_class("hidden")
        await pilot.press("t")
        assert app._active_tab == "workers"
        assert not app.query_one("#worker-table").has_class("hidden")
        assert app.query_one("#files-table").has_class("hidden")


@pytest.mark.asyncio
async def test_open_log_screen():
    """Enter key pushes LogScreen for selected worker."""
    app = CdsswarmApp(num_workers=2)
    async with app.run_test() as pilot:
        app.worker_data[0].filename = "test.grib"
        app.worker_data[0].logs.append("line 1")
        app.worker_data[0].logs.append("line 2")
        await pilot.press("enter")
        await pilot.pause()
        # LogScreen should be pushed
        assert isinstance(app.screen, LogScreen)
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, LogScreen)


@pytest.mark.asyncio
async def test_open_params_screen():
    """'a' key pushes ParamsScreen for selected worker."""
    app = CdsswarmApp(num_workers=2)
    async with app.run_test() as pilot:
        app.worker_data[0].request_params = {"variable": "t2m", "year": "2024"}
        await pilot.press("a")
        await pilot.pause()
        assert isinstance(app.screen, ParamsScreen)
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, ParamsScreen)


@pytest.mark.asyncio
async def test_worker_message_appends_to_logs():
    """WorkerMessage appends to worker's log buffer."""
    app = CdsswarmApp(num_workers=2)
    async with app.run_test() as pilot:
        app.post_message(WorkerMessage(0, "hello"))
        app.post_message(WorkerMessage(0, "world"))
        await pilot.pause()
        assert list(app.worker_data[0].logs) == ["hello", "world"]


@pytest.mark.asyncio
async def test_worker_file_size_sets_size():
    """WorkerFileSize updates the file size and dl_total fallback."""
    app = CdsswarmApp(num_workers=2)
    async with app.run_test() as pilot:
        app.post_message(WorkerFileSize(0, 95418))
        await pilot.pause()
        assert app.worker_data[0].file_size == 95418
        assert app.worker_data[0].dl_total == 95418


@pytest.mark.asyncio
async def test_worker_file_size_no_override_tqdm():
    """WorkerFileSize does not override dl_total already set by tqdm."""
    app = CdsswarmApp(num_workers=2)
    async with app.run_test() as pilot:
        app.worker_data[0].dl_total = 100000  # Already set
        app.post_message(WorkerFileSize(0, 95418))
        await pilot.pause()
        assert app.worker_data[0].file_size == 95418
        assert app.worker_data[0].dl_total == 100000  # Not overridden


@pytest.mark.asyncio
async def test_worker_dataset_title():
    """WorkerDatasetTitle updates dataset title."""
    app = CdsswarmApp(num_workers=2)
    async with app.run_test() as pilot:
        app.post_message(WorkerDatasetTitle(0, "ERA5 hourly data"))
        await pilot.pause()
        assert app.worker_data[0].dataset_title == "ERA5 hourly data"


@pytest.mark.asyncio
async def test_worker_request_labels():
    """WorkerRequestLabels updates request labels."""
    app = CdsswarmApp(num_workers=2)
    async with app.run_test() as pilot:
        labels = {"Variable": "2m temperature", "Year": "2024"}
        app.post_message(WorkerRequestLabels(0, labels))
        await pilot.pause()
        assert app.worker_data[0].request_labels == labels


@pytest.mark.asyncio
async def test_worker_server_timestamps():
    """WorkerServerTimestamps updates server timestamps."""
    app = CdsswarmApp(num_workers=2)
    async with app.run_test() as pilot:
        app.post_message(
            WorkerServerTimestamps(
                0,
                "2024-01-01T00:00:00Z",
                "2024-01-01T00:05:00Z",
                "",
            )
        )
        await pilot.pause()
        assert app.worker_data[0].server_created == "2024-01-01T00:00:00Z"
        assert app.worker_data[0].server_started == "2024-01-01T00:05:00Z"


@pytest.mark.asyncio
async def test_worker_progress_updates_file():
    """WorkerProgress also updates file-level tracking."""
    app = CdsswarmApp(num_workers=2)
    async with app.run_test() as pilot:
        tasks = _make_tasks(2)
        app.post_message(TasksInitialized(tasks, set()))
        await pilot.pause()
        app.post_message(FileActive(tasks[0].target, 0))
        await pilot.pause()
        app.post_message(WorkerProgress(0, 300, 600))
        await pilot.pause()
        assert app.files[0].dl_bytes == 300
        assert app.files[0].dl_total == 600


# ---------------------------------------------------------------------------
# Group A: Widget rendering edge cases (pure function tests)
# ---------------------------------------------------------------------------


class TestWorkerInfoPanelRendering:
    def test_no_worker(self):
        panel = WorkerInfoPanel()
        assert panel.render_worker(w=None, worker_id=0) == "No worker selected"

    def test_format_queue_wait_no_server_started(self):
        """Queue wait computes elapsed against now when server_started is None."""
        panel = WorkerInfoPanel()
        w = WorkerData()
        w.server_created = "2020-01-01T00:00:00Z"
        w.server_started = None
        result = panel._format_queue_wait(w)
        assert result != ""  # Should show time since 2020

    def test_format_queue_wait_invalid_date(self):
        """Invalid date returns empty string."""
        panel = WorkerInfoPanel()
        w = WorkerData()
        w.server_created = "bad"
        assert panel._format_queue_wait(w) == ""

    def test_format_queue_wait_no_server_created(self):
        """No server_created returns empty string."""
        panel = WorkerInfoPanel()
        w = WorkerData()
        w.server_created = None
        assert panel._format_queue_wait(w) == ""


class TestFilesInfoPanelRendering:
    def test_no_file(self):
        panel = FilesInfoPanel()
        assert panel.render_file([], 0) == "No file selected"

    def test_empty_request(self):
        panel = FilesInfoPanel()
        fd = FileData(target="/tmp/f.grib", dataset="ds", label="f.grib", request={})
        result = panel.render_file([fd], 0)
        assert "\u2014" in result


class TestMeterBarRendering:
    def test_eta_dots_with_eta_start_no_completed(self):
        """Shows 'ETA ...' when eta_start is set but completed=0."""
        meter = MeterBar()
        result = meter.render_progress(
            completed=0,
            total=10,
            skipped=0,
            eta_start=time.monotonic(),
            status="",
        )
        assert "ETA ..." in result

    def test_eta_dots_no_eta_start(self):
        """Shows 'ETA ...' when eta_start is None and completed=0 with total>0."""
        meter = MeterBar()
        result = meter.render_progress(
            completed=0,
            total=10,
            skipped=0,
            eta_start=None,
            status="",
        )
        assert "ETA ..." in result

    def test_preparing_when_no_total(self):
        meter = MeterBar()
        result = meter.render_progress(
            completed=0,
            total=0,
            skipped=0,
            eta_start=None,
            status="",
        )
        assert "Preparing..." in result


# ---------------------------------------------------------------------------
# Group B: Click handlers (async pilot tests)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tab_strip_click_workers():
    """Clicking left side of TabStrip switches to workers tab."""
    app = CdsswarmApp(num_workers=2)
    async with app.run_test() as pilot:
        # Switch to files first
        await pilot.press("t")
        assert app._active_tab == "files"
        # Click left side of tab strip
        await pilot.click("#tab-strip", offset=(5, 0))
        assert app._active_tab == "workers"


@pytest.mark.asyncio
async def test_tab_strip_click_files():
    """Clicking right side of TabStrip switches to files tab."""
    app = CdsswarmApp(num_workers=2)
    async with app.run_test() as pilot:
        assert app._active_tab == "workers"
        await pilot.click("#tab-strip", offset=(15, 0))
        assert app._active_tab == "files"


@pytest.mark.asyncio
async def test_key_bar_click_quit():
    """Clicking quit zone of KeyBar exits."""
    from unittest.mock import patch

    app = CdsswarmApp(num_workers=2)
    async with app.run_test() as pilot:
        with patch.object(app, "action_quit_app") as mock_quit:
            await pilot.click("#key-bar", offset=(5, 0))
            mock_quit.assert_called_once()


@pytest.mark.asyncio
async def test_key_bar_click_tab():
    """Clicking tab zone of KeyBar toggles tab."""
    app = CdsswarmApp(num_workers=2)
    async with app.run_test() as pilot:
        assert app._active_tab == "workers"
        await pilot.click("#key-bar", offset=(14, 0))
        assert app._active_tab == "files"


@pytest.mark.asyncio
async def test_key_bar_click_logs():
    """Clicking logs zone of KeyBar opens LogScreen."""
    app = CdsswarmApp(num_workers=2)
    async with app.run_test() as pilot:
        await pilot.click("#key-bar", offset=(25, 0))
        await pilot.pause()
        assert isinstance(app.screen, LogScreen)


@pytest.mark.asyncio
async def test_key_bar_click_params():
    """Clicking params zone of KeyBar opens ParamsScreen."""
    app = CdsswarmApp(num_workers=2)
    async with app.run_test() as pilot:
        await pilot.click("#key-bar", offset=(35, 0))
        await pilot.pause()
        assert isinstance(app.screen, ParamsScreen)


@pytest.mark.asyncio
async def test_back_bar_click_dismisses():
    """Clicking the BackBar on LogScreen dismisses it."""
    app = CdsswarmApp(num_workers=2)
    async with app.run_test() as pilot:
        app.worker_data[0].filename = "test.grib"
        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, LogScreen)
        await pilot.click("#log-header")
        await pilot.pause()
        assert not isinstance(app.screen, LogScreen)


# ---------------------------------------------------------------------------
# Group C: Screens
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_params_screen_with_labels():
    """ParamsScreen shows labels when provided."""
    app = CdsswarmApp(num_workers=2)
    async with app.run_test() as pilot:
        from textual.widgets import Static

        app.push_screen(ParamsScreen(0, {"k": "v"}, labels={"Label": "Value"}))
        await pilot.pause()
        assert isinstance(app.screen, ParamsScreen)
        content = app.screen.query_one("#params-content", Static)
        assert "Label:" in str(content.content)


@pytest.mark.asyncio
async def test_params_screen_empty():
    """ParamsScreen shows dash when params are empty."""
    app = CdsswarmApp(num_workers=2)
    async with app.run_test() as pilot:
        from textual.widgets import Static

        app.push_screen(ParamsScreen(0, {}, None))
        await pilot.pause()
        content = app.screen.query_one("#params-content", Static)
        assert "\u2014" in str(content.content)


# ---------------------------------------------------------------------------
# Group D: App-level behavior
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_app_with_downloader():
    """App calls downloader.run() when downloader is provided."""
    from unittest.mock import MagicMock

    mock_downloader = MagicMock()
    mock_downloader.run.return_value = []
    app = CdsswarmApp(num_workers=2, downloader=mock_downloader)
    async with app.run_test() as pilot:
        # Give background thread time to call run()
        await pilot.pause()
        await pilot.pause()
        await pilot.pause()
    mock_downloader.run.assert_called_once()


@pytest.mark.asyncio
async def test_tick_elapsed_updates_table():
    """Elapsed cell updates when worker is running (via _tick_elapsed)."""
    app = CdsswarmApp(num_workers=2)
    async with app.run_test() as pilot:
        app.post_message(WorkerStarted(0, "test.grib", "ds", {}, "/out/test.grib"))
        await pilot.pause()
        # Force a tick to update elapsed
        app._tick_elapsed()
        await pilot.pause()
        wt = app.query_one("#worker-table", DataTable)
        row = wt.get_row("0")
        elapsed_cell = str(row[5])  # Elapsed is index 5 (after W-State)
        assert "m" in elapsed_cell or "s" in elapsed_cell


@pytest.mark.asyncio
async def test_worker_finished_dl_pct():
    """WorkerFinished shows checkmark in DL% when dl_total > 0."""
    app = CdsswarmApp(num_workers=2)
    async with app.run_test() as pilot:
        app.post_message(WorkerStarted(0, "test.grib", "ds", {}, "/out/test.grib"))
        await pilot.pause()
        app.post_message(WorkerProgress(0, 1000, 1000))
        await pilot.pause()
        app.post_message(WorkerFinished(0))
        await pilot.pause()
        wt = app.query_one("#worker-table", DataTable)
        row = wt.get_row("0")
        dl_pct_cell = str(row[7])  # DL % is index 7 (after W-State)
        assert "\u2713" in dl_pct_cell


@pytest.mark.asyncio
async def test_quit_with_downloader():
    """Pressing 'q' with downloader cancels and exits."""
    from unittest.mock import MagicMock

    mock_downloader = MagicMock()
    # Make run() block until cancelled
    import threading

    block = threading.Event()
    mock_downloader.run.side_effect = lambda: block.wait(2)
    app = CdsswarmApp(num_workers=2, downloader=mock_downloader)
    async with app.run_test() as pilot:
        await pilot.pause()
        app.action_quit_app()
        await pilot.pause()
        assert app._cancel_event.is_set()


@pytest.mark.asyncio
async def test_ctrl_c_first_press():
    """First Ctrl+C increments count and triggers quit."""
    from unittest.mock import patch

    app = CdsswarmApp(num_workers=2)
    async with app.run_test():
        with patch.object(app, "action_quit_app") as mock_quit:
            app.action_ctrl_c()
            assert app._ctrl_c_count == 1
            mock_quit.assert_called_once()


@pytest.mark.asyncio
async def test_ctrl_c_rage_quit():
    """Second Ctrl+C exits immediately."""
    from unittest.mock import patch

    app = CdsswarmApp(num_workers=2)
    async with app.run_test():
        app._ctrl_c_count = 1
        with patch.object(app, "exit") as mock_exit:
            app.action_ctrl_c()
            assert app._ctrl_c_count == 2
            mock_exit.assert_called_once()


@pytest.mark.asyncio
async def test_open_params_on_files_tab():
    """Pressing 'a' on files tab does not open ParamsScreen."""
    app = CdsswarmApp(num_workers=2)
    async with app.run_test() as pilot:
        await pilot.press("t")
        assert app._active_tab == "files"
        await pilot.press("a")
        await pilot.pause()
        assert not isinstance(app.screen, ParamsScreen)


@pytest.mark.asyncio
async def test_open_logs_on_files_tab():
    """Pressing enter on files tab does not open LogScreen."""
    app = CdsswarmApp(num_workers=2)
    async with app.run_test() as pilot:
        await pilot.press("t")
        assert app._active_tab == "files"
        app._open_worker_logs()
        await pilot.pause()
        assert not isinstance(app.screen, LogScreen)


@pytest.mark.asyncio
async def test_file_row_successful_checkmark():
    """Successful file row shows checkmark in DL%."""
    app = CdsswarmApp(num_workers=2)
    async with app.run_test() as pilot:
        tasks = _make_tasks(2)
        app.post_message(TasksInitialized(tasks, set()))
        await pilot.pause()
        app.post_message(FileActive(tasks[0].target, 0))
        await pilot.pause()
        app.post_message(WorkerProgress(0, 500, 1000))
        await pilot.pause()
        app.post_message(FileCompleted(tasks[0].target, True))
        await pilot.pause()
        ft = app.query_one("#files-table", DataTable)
        row = ft.get_row("0")
        dl_pct_cell = str(row[6])  # DL % is index 6 in files table
        assert "\u2713" in dl_pct_cell


# ---------------------------------------------------------------------------
# Group E: Remaining coverage (L637, L1225, L1286)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_quit_without_downloader():
    """action_quit_app() calls exit() when downloader is None."""
    from unittest.mock import patch

    app = CdsswarmApp(num_workers=2)
    async with app.run_test():
        assert app.downloader is None
        with patch.object(app, "exit") as mock_exit:
            app.action_quit_app()
            mock_exit.assert_called_once()


@pytest.mark.asyncio
async def test_update_worker_info_no_selection():
    """_update_worker_info() shows 'No worker selected' when cursor is out of bounds."""
    from unittest.mock import PropertyMock, patch

    app = CdsswarmApp(num_workers=1)
    async with app.run_test() as pilot:
        # Patch cursor_row to return -1 (out of bounds)
        with patch.object(
            type(app.query_one("#worker-table", DataTable)),
            "cursor_row",
            new_callable=PropertyMock,
            return_value=-1,
        ):
            app._update_worker_info()
            await pilot.pause()
        panel = app.query_one("#worker-info", WorkerInfoPanel)
        assert "No worker selected" in str(panel.content)


# ---------------------------------------------------------------------------
# Group F: ServerStatsUpdate (covers textual_app lines 241-245, 448-457, 1034-1038)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_server_stats_update():
    """ServerStatsUpdate updates app state and meter bar."""
    app = CdsswarmApp(num_workers=2)
    async with app.run_test() as pilot:
        app.post_message(ServerStatsUpdate(5, 10, 3, "OK"))
        await pilot.pause()
        assert app.server_running == 5
        assert app.server_queued == 10
        assert app.server_running_users == 3
        assert app.server_system_status == "OK"


@pytest.mark.asyncio
async def test_server_stats_meter_bar_status_dots():
    """MeterBar renders colored dots for different system statuses."""
    meter = MeterBar()
    for status, expected_color in [
        ("OK", "[green]"),
        ("Degraded", "[blink yellow]"),
        ("Down", "[blink red]"),
        ("unknown", "Server:"),
    ]:
        result = meter.render_progress(
            completed=1,
            total=10,
            skipped=0,
            eta_start=time.monotonic() - 10,
            status="",
            server_queued=5,
            server_running=2,
            running_users=1,
            system_status=status,
        )
        assert expected_color in result
        assert "5 queued" in result
        assert "2 running" in result

    # Degraded with status message
    result = meter.render_progress(
        completed=1,
        total=10,
        skipped=0,
        eta_start=time.monotonic() - 10,
        status="",
        server_queued=5,
        server_running=2,
        running_users=1,
        system_status="Degraded",
        system_status_message="DSS upgrade in progress",
    )
    assert "Reason:" in result and "DSS upgrade in progress" in result
    assert "workers paused" in result


@pytest.mark.asyncio
async def test_worker_state_column():
    """WorkerStateUpdate sets correct badges in the W-State column."""
    app = CdsswarmApp(num_workers=2)
    async with app.run_test() as pilot:
        wt = app.query_one("#worker-table", DataTable)

        # Worker enters pause
        app.post_message(WorkerStateUpdate(0, "paused"))
        await pilot.pause()
        row0 = wt.get_row("0")
        assert any("paused" in str(cell) for cell in row0)

        # Worker resumes
        app.post_message(WorkerStateUpdate(0, "active"))
        await pilot.pause()
        row0 = wt.get_row("0")
        assert any("active" in str(cell) for cell in row0)
        assert not any("paused" in str(cell) for cell in row0)

        # Worker retrying
        app.post_message(WorkerStateUpdate(0, "retrying"))
        await pilot.pause()
        row0 = wt.get_row("0")
        assert any("retrying" in str(cell) for cell in row0)
