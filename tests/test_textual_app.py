"""Tests for the Textual TUI."""

import pytest
from textual.widgets import DataTable

from cdsswarm.core import Task
from cdsswarm.status import FileStatus, WorkerStatus
from cdsswarm.textual_app import (
    CdsswarmApp,
    ChecksumScreen,
    FileActive,
    FileCompleted,
    GlobalMessage,
    LogScreen,
    ParamsScreen,
    ProgressUpdate,
    QosUpdate,
    ShowChecksumDialog,
    TasksInitialized,
    WorkerCdsStatus,
    WorkerChecksum,
    WorkerData,
    WorkerDatasetTitle,
    WorkerFileSize,
    WorkerFinished,
    WorkerMessage,
    WorkerProgress,
    WorkerRequestId,
    WorkerRequestLabels,
    WorkerServerProgress,
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
async def test_worker_server_progress():
    """WorkerServerProgress updates Prog column."""
    app = CdsswarmApp(num_workers=2)
    async with app.run_test() as pilot:
        app.post_message(WorkerServerProgress(0, 72))
        await pilot.pause()
        wt = app.query_one("#worker-table", DataTable)
        row = wt.get_row("0")
        assert any("72%" in str(cell) for cell in row)


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
async def test_qos_update():
    """QosUpdate updates QoS state."""
    app = CdsswarmApp(num_workers=2)
    async with app.run_test() as pilot:
        app.post_message(QosUpdate(5220, 400, 400))
        await pilot.pause()
        assert app.qos_queued == 5220
        assert app.qos_running == 400
        assert app.qos_limit == 400


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
async def test_worker_checksum_updates_state():
    """WorkerChecksum updates checksum state."""
    app = CdsswarmApp(num_workers=2)
    async with app.run_test() as pilot:
        app.post_message(WorkerChecksum(0, True))
        await pilot.pause()
        assert app.worker_data[0].checksum is True
        app.post_message(WorkerChecksum(1, False))
        await pilot.pause()
        assert app.worker_data[1].checksum is False


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


@pytest.mark.asyncio
async def test_checksum_screen_continue():
    """ChecksumScreen can dismiss with 'continue'."""
    import threading

    app = CdsswarmApp(num_workers=2)
    async with app.run_test() as pilot:
        app.worker_data[0].filename = "test.grib"
        app.worker_data[0].target = "/tmp/test.grib"
        result_event = threading.Event()
        result_holder: list[str] = []
        app.post_message(
            ShowChecksumDialog(
                0,
                "abc123",
                result_event,
                result_holder,
            )
        )
        await pilot.pause()
        assert isinstance(app.screen, ChecksumScreen)
        await pilot.press("c")
        await pilot.pause()
        assert result_event.is_set()
        assert result_holder[0] == "continue"


@pytest.mark.asyncio
async def test_checksum_screen_retry():
    """ChecksumScreen can dismiss with 'retry'."""
    import threading

    app = CdsswarmApp(num_workers=2)
    async with app.run_test() as pilot:
        app.worker_data[0].filename = "test.grib"
        app.worker_data[0].target = "/tmp/test.grib"
        result_event = threading.Event()
        result_holder: list[str] = []
        app.post_message(
            ShowChecksumDialog(
                0,
                "abc123",
                result_event,
                result_holder,
            )
        )
        await pilot.pause()
        assert isinstance(app.screen, ChecksumScreen)
        await pilot.press("r")
        await pilot.pause()
        assert result_event.is_set()
        assert result_holder[0] == "retry"
