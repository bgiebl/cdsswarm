"""Tests for output adapters."""

import io
import threading
from unittest.mock import MagicMock

from cdsswarm.adapters import (
    LoggingAdapter,
    OutputAdapter,
    PlainTextAdapter,
    TextualAdapter,
)
from cdsswarm.core import Task
from cdsswarm.status import WorkerStatus


class _NoOpAdapter(OutputAdapter):
    """Minimal concrete adapter for testing default method behavior."""

    def on_task_started(self, worker_id, task):
        pass

    def on_task_message(self, worker_id, message):
        pass

    def on_task_completed(self, worker_id, task, success, error=""):
        pass

    def on_progress_update(self, completed, total, skipped):
        pass

    def on_global_message(self, message):
        pass


class TestOutputAdapterDefaults:
    def test_on_task_request_id(self):
        adapter = _NoOpAdapter()
        adapter.on_task_request_id(0, "abc-123")

    def test_on_task_progress(self):
        adapter = _NoOpAdapter()
        adapter.on_task_progress(0, 1024, 4096)

    def test_on_task_cancelled(self):
        adapter = _NoOpAdapter()
        adapter.on_task_cancelled(0)

    def test_on_task_file_size(self):
        adapter = _NoOpAdapter()
        adapter.on_task_file_size(0, 1024 * 1024)

    def test_on_task_server_timestamps(self):
        adapter = _NoOpAdapter()
        adapter.on_task_server_timestamps(0, "2024-01-01", "2024-01-02", "2024-01-03")

    def test_on_task_dataset_title(self):
        adapter = _NoOpAdapter()
        adapter.on_task_dataset_title(0, "ERA5 hourly data")

    def test_on_task_request_labels(self):
        adapter = _NoOpAdapter()
        adapter.on_task_request_labels(0, {"year": "2024"})

    def test_on_task_hook_started(self):
        adapter = _NoOpAdapter()
        adapter.on_task_hook_started(0, "gzip file.grib")

    def test_on_task_hook_finished(self):
        adapter = _NoOpAdapter()
        adapter.on_task_hook_finished(0, True)

    def test_on_task_hook_finished_failure(self):
        adapter = _NoOpAdapter()
        adapter.on_task_hook_finished(0, False, "exit code 1")

    def test_on_tasks_initialized(self):
        adapter = _NoOpAdapter()
        tasks = [Task("ds", {}, "file.grib")]
        adapter.on_tasks_initialized(tasks, set())

    def test_on_server_stats_update(self):
        adapter = _NoOpAdapter()
        adapter.on_server_stats_update(5, 10, 3, "OK")


class TestPlainTextAdapter:
    def test_completed_message(self):
        messages = []
        adapter = PlainTextAdapter(write_fn=messages.append)
        adapter.on_progress_update(1, 5, 0)
        task = Task("ds", {}, "/path/to/file.grib")
        adapter.on_task_completed(0, task, success=True)
        assert any("file.grib" in m and "done" in m for m in messages)

    def test_failed_message(self):
        messages = []
        adapter = PlainTextAdapter(write_fn=messages.append)
        adapter.on_progress_update(1, 5, 0)
        task = Task("ds", {}, "/path/to/file.grib")
        adapter.on_task_completed(0, task, success=False, error="timeout")
        assert any("FAILED" in m and "timeout" in m for m in messages)

    def test_global_message(self):
        messages = []
        adapter = PlainTextAdapter(write_fn=messages.append)
        adapter.on_global_message("hello world")
        assert "hello world" in messages

    def test_progress_tracking(self):
        adapter = PlainTextAdapter()
        adapter.on_progress_update(3, 10, 2)
        assert adapter._done == 3
        assert adapter._total == 10

    def test_started_prints_label(self):
        messages = []
        adapter = PlainTextAdapter(write_fn=messages.append)
        task = Task("ds", {}, "file.grib")
        adapter.on_task_started(0, task)
        assert len(messages) == 1
        assert "file.grib" in messages[0]
        assert "Worker 0" in messages[0]
        assert "starting" in messages[0]

    def test_message_non_cds_is_silent(self):
        messages = []
        adapter = PlainTextAdapter(write_fn=messages.append)
        adapter.on_task_message(0, "some CDS log line")
        assert len(messages) == 0

    def test_message_cds_status_printed(self):
        messages = []
        adapter = PlainTextAdapter(write_fn=messages.append)
        adapter.on_task_message(0, "Request is queued")
        assert len(messages) == 1
        assert "accepted" in messages[0]
        assert "queued on CDS server" in messages[0]

    def test_message_cds_status_includes_label(self):
        messages = []
        adapter = PlainTextAdapter(write_fn=messages.append)
        task = Task("ds", {}, "file.grib")
        adapter.on_task_started(0, task)
        messages.clear()
        adapter.on_task_message(0, "Request is queued")
        assert "file.grib" in messages[0]
        assert "accepted" in messages[0]
        assert "queued on CDS server" in messages[0]

    def test_message_cds_status_deduplicated(self):
        messages = []
        adapter = PlainTextAdapter(write_fn=messages.append)
        adapter.on_task_message(0, "Request is queued")
        adapter.on_task_message(0, "Request is queued")
        adapter.on_task_message(0, "Request is queued")
        assert len(messages) == 1

    def test_message_cds_status_transition_printed(self):
        messages = []
        adapter = PlainTextAdapter(write_fn=messages.append)
        adapter.on_task_message(0, "Request is queued")
        adapter.on_task_message(0, "Request is running")
        assert len(messages) == 2
        assert "accepted" in messages[0]
        assert "running" in messages[1]
        assert "processing request" in messages[1]

    def test_message_status_resets_on_new_task(self):
        messages = []
        adapter = PlainTextAdapter(write_fn=messages.append)
        adapter.on_task_message(0, "Request is queued")
        task = Task("ds", {}, "file2.grib")
        adapter.on_task_started(0, task)
        adapter.on_task_message(0, "Request is queued")
        # started line + 2 status lines
        assert sum(1 for m in messages if "accepted" in m) == 2

    def test_status_color_applied(self):
        messages = []
        adapter = PlainTextAdapter(write_fn=messages.append, use_color=True)
        adapter.on_task_message(0, "Request is queued")
        # accepted should be yellow (\033[33m)
        assert "\033[33m" in messages[0]
        assert "accepted" in messages[0]

    def test_status_color_not_applied_when_disabled(self):
        messages = []
        adapter = PlainTextAdapter(write_fn=messages.append, use_color=False)
        adapter.on_task_message(0, "Request is queued")
        assert "\033" not in messages[0]
        assert "accepted" in messages[0]

    def test_download_progress_milestones(self):
        messages = []
        adapter = PlainTextAdapter(write_fn=messages.append)
        total = 100 * 1024 * 1024  # 100 MB
        # Simulate progress at every percent
        for pct in range(101):
            adapter.on_task_progress(0, int(total * pct / 100), total)
        dl_msgs = [m for m in messages if "downloading" in m]
        assert len(dl_msgs) == 4  # 25%, 50%, 75%, 100%
        assert "25%" in dl_msgs[0]
        assert "50%" in dl_msgs[1]
        assert "75%" in dl_msgs[2]
        assert "100%" in dl_msgs[3]

    def test_download_progress_includes_label(self):
        messages = []
        adapter = PlainTextAdapter(write_fn=messages.append)
        task = Task("ds", {}, "file.grib")
        adapter.on_task_started(0, task)
        messages.clear()
        total = 100 * 1024 * 1024
        adapter.on_task_progress(0, total // 2, total)  # 50%
        assert "file.grib" in messages[0]
        assert "downloading" in messages[0]

    def test_download_progress_resets_on_new_task(self):
        messages = []
        adapter = PlainTextAdapter(write_fn=messages.append)
        total = 100 * 1024 * 1024
        adapter.on_task_progress(0, total, total)  # 100%
        task = Task("ds", {}, "file2.grib")
        adapter.on_task_started(0, task)
        adapter.on_task_progress(0, total // 2, total)  # 50%
        dl_msgs = [m for m in messages if "downloading" in m]
        assert len(dl_msgs) == 2

    def test_worker_tag_no_color(self):
        messages = []
        adapter = PlainTextAdapter(write_fn=messages.append, use_color=False)
        task = Task("ds", {}, "file.grib")
        adapter.on_task_started(0, task)
        assert "[Worker 0]" in messages[0]
        assert "\033" not in messages[0]

    def test_worker_tag_with_color(self):
        messages = []
        adapter = PlainTextAdapter(write_fn=messages.append, use_color=True)
        task = Task("ds", {}, "file.grib")
        adapter.on_task_started(0, task)
        assert "\033[" in messages[0]
        assert "Worker 0" in messages[0]

    def test_multiple_workers_distinct_tags(self):
        messages = []
        adapter = PlainTextAdapter(write_fn=messages.append, use_color=False)
        for i in range(4):
            adapter.on_task_message(i, "Request is queued")
        assert "[Worker 0]" in messages[0]
        assert "[Worker 1]" in messages[1]
        assert "[Worker 2]" in messages[2]
        assert "[Worker 3]" in messages[3]

    def test_has_lock(self):
        adapter = PlainTextAdapter()
        assert isinstance(adapter._lock, type(threading.Lock()))

    def test_concurrent_task_lifecycle(self):
        """Multiple threads calling adapter methods concurrently don't raise."""
        messages = []
        lock = threading.Lock()

        def safe_append(msg):
            with lock:
                messages.append(msg)

        adapter = PlainTextAdapter(write_fn=safe_append, use_color=False)
        errors = []

        def worker(wid):
            try:
                task = Task("ds", {}, f"file_{wid}.grib")
                adapter.on_task_started(wid, task)
                adapter.on_task_message(wid, "Request is queued")
                adapter.on_task_message(wid, "Request is running")
                total = 100 * 1024 * 1024
                for pct in (25, 50, 75, 100):
                    adapter.on_task_progress(wid, total * pct // 100, total)
                adapter.on_progress_update(wid + 1, 8, 0)
                adapter.on_task_completed(wid, task, success=True)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Threads raised exceptions: {errors}"
        # Each worker should produce at least: started + 2 status + 4 milestones + completed
        assert len(messages) >= 8 * 4  # conservative lower bound

    def test_concurrent_status_dedup(self):
        """Status deduplication works correctly under concurrent access."""
        messages = []
        lock = threading.Lock()

        def safe_append(msg):
            with lock:
                messages.append(msg)

        adapter = PlainTextAdapter(write_fn=safe_append, use_color=False)
        errors = []
        barrier = threading.Barrier(4)

        def worker(wid):
            try:
                # All threads hammer the same worker_id to stress check-then-act
                barrier.wait()
                for _ in range(50):
                    adapter.on_task_message(0, "Request is queued")
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        # "accepted" should appear exactly once due to deduplication
        accepted_msgs = [m for m in messages if "accepted" in m]
        assert len(accepted_msgs) == 1

    def test_progress_zero_total_silent(self):
        messages = []
        adapter = PlainTextAdapter(write_fn=messages.append)
        adapter.on_task_progress(0, 0, 0)
        assert len(messages) == 0

    def test_global_message_all_completed_with_color(self):
        messages = []
        adapter = PlainTextAdapter(write_fn=messages.append, use_color=True)
        adapter.on_global_message("All downloads completed successfully.")
        assert len(messages) == 1
        assert "\033[32m" in messages[0]

    def test_completed_with_color(self):
        messages = []
        adapter = PlainTextAdapter(write_fn=messages.append, use_color=True)
        adapter.on_progress_update(1, 5, 0)
        task = Task("ds", {}, "file.grib")
        adapter.on_task_completed(0, task, success=True)
        assert any("\033[32m" in m for m in messages)

    def test_status_text_unknown_status_no_description(self):
        adapter = PlainTextAdapter(use_color=False)
        result = adapter._status_text("unknown_status")
        assert result == "unknown_status"
        assert "—" not in result

    def test_hook_started_prints(self):
        messages = []
        adapter = PlainTextAdapter(write_fn=messages.append, use_color=False)
        task = Task("ds", {}, "file.grib")
        adapter.on_task_started(0, task)
        messages.clear()
        adapter.on_task_hook_started(0, "gzip file.grib")
        assert len(messages) == 1
        assert "running post-hook" in messages[0]
        assert "file.grib" in messages[0]

    def test_hook_finished_success_silent(self):
        messages = []
        adapter = PlainTextAdapter(write_fn=messages.append, use_color=False)
        adapter.on_task_hook_finished(0, True)
        assert len(messages) == 0

    def test_hook_finished_failure_prints_warning(self):
        messages = []
        adapter = PlainTextAdapter(write_fn=messages.append, use_color=False)
        adapter.on_task_hook_finished(0, False, "exit code 1")
        assert len(messages) == 1
        assert "WARNING" in messages[0]
        assert "post-hook failed" in messages[0]
        assert "exit code 1" in messages[0]

    def test_hook_finished_failure_with_color(self):
        messages = []
        adapter = PlainTextAdapter(write_fn=messages.append, use_color=True)
        adapter.on_task_hook_finished(0, False, "exit code 1")
        assert "\033[1;38;5;208m" in messages[0]

    def test_server_stats_update(self):
        messages = []
        adapter = PlainTextAdapter(write_fn=messages.append)
        adapter.on_server_stats_update(5, 10, 3, "OK")
        assert len(messages) == 1
        assert "10 queued" in messages[0]
        assert "5 running" in messages[0]
        assert "3 users" in messages[0]
        assert "Status: OK" in messages[0]

    def test_server_stats_update_deduplicated(self):
        messages = []
        adapter = PlainTextAdapter(write_fn=messages.append)
        adapter.on_server_stats_update(5, 10, 3, "")
        adapter.on_server_stats_update(5, 10, 3, "")
        assert len(messages) == 1

    def test_server_stats_update_change_emits(self):
        messages = []
        adapter = PlainTextAdapter(write_fn=messages.append)
        adapter.on_server_stats_update(5, 10, 3, "")
        adapter.on_server_stats_update(6, 10, 3, "")
        assert len(messages) == 2


class TestLoggingAdapter:
    def _make_adapter(self):
        log_file = io.StringIO()
        inner = MagicMock(spec=PlainTextAdapter)
        adapter = LoggingAdapter(inner, log_file)
        return adapter, inner, log_file

    def test_on_tasks_initialized(self):
        adapter, inner, log_file = self._make_adapter()
        tasks = [Task("ds", {}, "file.grib")]
        adapter.on_tasks_initialized(tasks, {"skipped.grib"})
        assert "tasks initialized" in log_file.getvalue()
        assert "1 total" in log_file.getvalue()
        assert "1 cached" in log_file.getvalue()
        inner.on_tasks_initialized.assert_called_once_with(tasks, {"skipped.grib"})

    def test_on_task_cancelled(self):
        adapter, inner, log_file = self._make_adapter()
        adapter.on_task_cancelled(0)
        assert "cancelled" in log_file.getvalue()
        inner.on_task_cancelled.assert_called_once_with(0)

    def test_on_task_file_size(self):
        adapter, inner, log_file = self._make_adapter()
        adapter.on_task_file_size(0, 1024)
        assert "file size: 1024" in log_file.getvalue()
        inner.on_task_file_size.assert_called_once_with(0, 1024)

    def test_on_task_server_timestamps(self):
        adapter, inner, log_file = self._make_adapter()
        adapter.on_task_server_timestamps(0, "2024-01-01", "2024-01-02", "2024-01-03")
        log = log_file.getvalue()
        assert "server timestamps" in log
        assert "created=2024-01-01" in log
        inner.on_task_server_timestamps.assert_called_once_with(
            0, "2024-01-01", "2024-01-02", "2024-01-03"
        )

    def test_on_task_dataset_title(self):
        adapter, inner, log_file = self._make_adapter()
        adapter.on_task_dataset_title(0, "ERA5 hourly")
        assert "dataset: ERA5 hourly" in log_file.getvalue()
        inner.on_task_dataset_title.assert_called_once_with(0, "ERA5 hourly")

    def test_on_task_request_labels(self):
        adapter, inner, log_file = self._make_adapter()
        labels = {"year": "2024"}
        adapter.on_task_request_labels(0, labels)
        assert "labels:" in log_file.getvalue()
        inner.on_task_request_labels.assert_called_once_with(0, labels)

    def test_on_task_hook_started(self):
        adapter, inner, log_file = self._make_adapter()
        adapter.on_task_hook_started(0, "gzip file.grib")
        assert "post-hook started" in log_file.getvalue()
        assert "gzip file.grib" in log_file.getvalue()
        inner.on_task_hook_started.assert_called_once_with(0, "gzip file.grib")

    def test_on_task_hook_finished_success(self):
        adapter, inner, log_file = self._make_adapter()
        adapter.on_task_hook_finished(0, True)
        assert "post-hook finished: ok" in log_file.getvalue()
        inner.on_task_hook_finished.assert_called_once_with(0, True, "")

    def test_on_task_hook_finished_failure(self):
        adapter, inner, log_file = self._make_adapter()
        adapter.on_task_hook_finished(0, False, "exit code 1")
        log = log_file.getvalue()
        assert "FAILED" in log
        assert "exit code 1" in log
        inner.on_task_hook_finished.assert_called_once_with(0, False, "exit code 1")

    def test_on_server_stats_update(self):
        adapter, inner, log_file = self._make_adapter()
        adapter.on_server_stats_update(5, 10, 3, "OK")
        log = log_file.getvalue()
        assert "server stats" in log
        assert "queued=10" in log
        assert "running=5" in log
        assert "users=3" in log
        assert "status=OK" in log
        inner.on_server_stats_update.assert_called_once_with(5, 10, 3, "OK")


class TestTextualAdapter:
    """Tests for TextualAdapter — verifies correct Message posting via mock app."""

    def _make_adapter(self):
        mock_app = MagicMock()
        adapter = TextualAdapter(mock_app)
        return adapter, mock_app

    def _posted_messages(self, mock_app):
        """Extract the Message objects posted via call_from_thread."""
        return [
            c[0][1]  # call_from_thread(post_message, msg) → msg is second arg
            for c in mock_app.call_from_thread.call_args_list
        ]

    def test_on_tasks_initialized(self):
        from cdsswarm.textual_app import TasksInitialized

        adapter, app = self._make_adapter()
        tasks = [Task("ds", {}, "file.grib")]
        adapter.on_tasks_initialized(tasks, {"skipped.grib"})
        msgs = self._posted_messages(app)
        assert len(msgs) == 1
        assert isinstance(msgs[0], TasksInitialized)
        assert msgs[0].tasks == tasks
        assert msgs[0].skipped_targets == {"skipped.grib"}

    def test_on_task_started(self):
        from cdsswarm.textual_app import FileActive, WorkerStarted

        adapter, app = self._make_adapter()
        task = Task("my-dataset", {"year": "2024"}, "/path/to/file.grib")
        adapter.on_task_started(0, task)
        msgs = self._posted_messages(app)
        assert len(msgs) == 2
        assert isinstance(msgs[0], WorkerStarted)
        assert msgs[0].worker_id == 0
        assert msgs[0].filename == "file.grib"
        assert msgs[0].dataset == "my-dataset"
        assert msgs[0].request == {"year": "2024"}
        assert msgs[0].target == "/path/to/file.grib"
        assert isinstance(msgs[1], FileActive)
        assert msgs[1].target == "/path/to/file.grib"
        assert msgs[1].worker_id == 0

    def test_on_task_message_with_status(self):
        from cdsswarm.textual_app import WorkerCdsStatus, WorkerMessage

        adapter, app = self._make_adapter()
        adapter.on_task_message(1, "Request is queued")
        msgs = self._posted_messages(app)
        assert len(msgs) == 2
        assert isinstance(msgs[0], WorkerCdsStatus)
        assert msgs[0].worker_id == 1
        assert msgs[0].cds_status == WorkerStatus.ACCEPTED
        assert isinstance(msgs[1], WorkerMessage)
        assert msgs[1].message == "Request is queued"

    def test_on_task_message_no_status(self):
        from cdsswarm.textual_app import WorkerMessage

        adapter, app = self._make_adapter()
        adapter.on_task_message(2, "some plain message")
        msgs = self._posted_messages(app)
        assert len(msgs) == 1
        assert isinstance(msgs[0], WorkerMessage)
        assert msgs[0].message == "some plain message"

    def test_on_task_completed_success(self):
        from cdsswarm.textual_app import (
            FileCompleted,
            WorkerCdsStatus,
            WorkerFinished,
            WorkerMessage,
        )

        adapter, app = self._make_adapter()
        task = Task("ds", {}, "file.grib")
        adapter.on_task_completed(0, task, success=True)
        msgs = self._posted_messages(app)
        assert len(msgs) == 4
        assert isinstance(msgs[0], WorkerCdsStatus)
        assert msgs[0].cds_status == WorkerStatus.SUCCESSFUL
        assert isinstance(msgs[1], WorkerMessage)
        assert "Completed" in msgs[1].message
        assert isinstance(msgs[2], WorkerFinished)
        assert msgs[2].success is True
        assert isinstance(msgs[3], FileCompleted)
        assert msgs[3].success is True

    def test_on_task_completed_failure(self):
        from cdsswarm.textual_app import (
            FileCompleted,
            WorkerCdsStatus,
            WorkerFinished,
            WorkerMessage,
        )

        adapter, app = self._make_adapter()
        task = Task("ds", {}, "file.grib")
        adapter.on_task_completed(0, task, success=False, error="timeout")
        msgs = self._posted_messages(app)
        assert len(msgs) == 4
        assert isinstance(msgs[0], WorkerCdsStatus)
        assert msgs[0].cds_status == WorkerStatus.FAILED
        assert isinstance(msgs[1], WorkerMessage)
        assert "timeout" in msgs[1].message
        assert isinstance(msgs[2], WorkerFinished)
        assert msgs[2].success is False
        assert msgs[2].error == "timeout"
        assert isinstance(msgs[3], FileCompleted)
        assert msgs[3].success is False
        assert msgs[3].error == "timeout"

    def test_on_progress_update(self):
        from cdsswarm.textual_app import ProgressUpdate

        adapter, app = self._make_adapter()
        adapter.on_progress_update(5, 10, 2)
        msgs = self._posted_messages(app)
        assert len(msgs) == 1
        assert isinstance(msgs[0], ProgressUpdate)
        assert msgs[0].completed == 5
        assert msgs[0].total == 10
        assert msgs[0].skipped == 2

    def test_on_task_request_id(self):
        from cdsswarm.textual_app import WorkerRequestId

        adapter, app = self._make_adapter()
        adapter.on_task_request_id(3, "abc-123")
        msgs = self._posted_messages(app)
        assert len(msgs) == 1
        assert isinstance(msgs[0], WorkerRequestId)
        assert msgs[0].request_id == "abc-123"

    def test_on_task_progress(self):
        from cdsswarm.textual_app import WorkerProgress

        adapter, app = self._make_adapter()
        adapter.on_task_progress(0, 1024, 4096)
        msgs = self._posted_messages(app)
        assert len(msgs) == 1
        assert isinstance(msgs[0], WorkerProgress)
        assert msgs[0].downloaded == 1024
        assert msgs[0].total == 4096

    def test_on_task_cancelled(self):
        from cdsswarm.textual_app import WorkerCancelled

        adapter, app = self._make_adapter()
        adapter.on_task_cancelled(1)
        msgs = self._posted_messages(app)
        assert len(msgs) == 1
        assert isinstance(msgs[0], WorkerCancelled)
        assert msgs[0].worker_id == 1

    def test_on_task_file_size(self):
        from cdsswarm.textual_app import WorkerFileSize

        adapter, app = self._make_adapter()
        adapter.on_task_file_size(0, 95418)
        msgs = self._posted_messages(app)
        assert len(msgs) == 1
        assert isinstance(msgs[0], WorkerFileSize)
        assert msgs[0].file_size == 95418

    def test_on_task_server_timestamps(self):
        from cdsswarm.textual_app import WorkerServerTimestamps

        adapter, app = self._make_adapter()
        adapter.on_task_server_timestamps(0, "2024-01-01", "2024-01-02", "2024-01-03")
        msgs = self._posted_messages(app)
        assert len(msgs) == 1
        assert isinstance(msgs[0], WorkerServerTimestamps)
        assert msgs[0].created == "2024-01-01"
        assert msgs[0].started == "2024-01-02"
        assert msgs[0].finished == "2024-01-03"

    def test_on_task_dataset_title(self):
        from cdsswarm.textual_app import WorkerDatasetTitle

        adapter, app = self._make_adapter()
        adapter.on_task_dataset_title(0, "ERA5 hourly")
        msgs = self._posted_messages(app)
        assert len(msgs) == 1
        assert isinstance(msgs[0], WorkerDatasetTitle)
        assert msgs[0].title == "ERA5 hourly"

    def test_on_task_request_labels(self):
        from cdsswarm.textual_app import WorkerRequestLabels

        adapter, app = self._make_adapter()
        labels = {"Variable": "Temperature", "Year": "2024"}
        adapter.on_task_request_labels(0, labels)
        msgs = self._posted_messages(app)
        assert len(msgs) == 1
        assert isinstance(msgs[0], WorkerRequestLabels)
        assert msgs[0].labels == labels

    def test_on_task_hook_started(self):
        from cdsswarm.textual_app import WorkerMessage

        adapter, app = self._make_adapter()
        adapter.on_task_hook_started(0, "gzip file.grib")
        msgs = self._posted_messages(app)
        assert len(msgs) == 1
        assert isinstance(msgs[0], WorkerMessage)
        assert "post-hook" in msgs[0].message.lower()
        assert "gzip file.grib" in msgs[0].message

    def test_on_task_hook_finished_success(self):
        adapter, app = self._make_adapter()
        adapter.on_task_hook_finished(0, True)
        # Success is silent for TextualAdapter
        msgs = self._posted_messages(app)
        assert len(msgs) == 0

    def test_on_task_hook_finished_failure(self):
        from cdsswarm.textual_app import WorkerMessage

        adapter, app = self._make_adapter()
        adapter.on_task_hook_finished(0, False, "exit code 1")
        msgs = self._posted_messages(app)
        assert len(msgs) == 1
        assert isinstance(msgs[0], WorkerMessage)
        assert "failed" in msgs[0].message.lower()
        assert "exit code 1" in msgs[0].message

    def test_on_global_message(self):
        from cdsswarm.textual_app import GlobalMessage

        adapter, app = self._make_adapter()
        adapter.on_global_message("All downloads completed")
        msgs = self._posted_messages(app)
        assert len(msgs) == 1
        assert isinstance(msgs[0], GlobalMessage)
        assert msgs[0].message == "All downloads completed"

    def test_on_server_stats_update(self):
        from cdsswarm.textual_app import ServerStatsUpdate

        adapter, app = self._make_adapter()
        adapter.on_server_stats_update(5, 10, 3, "OK")
        msgs = self._posted_messages(app)
        assert len(msgs) == 1
        assert isinstance(msgs[0], ServerStatsUpdate)
        assert msgs[0].server_running == 5
        assert msgs[0].server_queued == 10
        assert msgs[0].running_users == 3
        assert msgs[0].system_status == "OK"

    def test_post_ignores_runtime_error_when_app_stopped(self):
        """_post silently ignores RuntimeError when the app is no longer running."""
        adapter, app = self._make_adapter()
        app.call_from_thread.side_effect = RuntimeError("App is not running")
        # Should not raise
        adapter.on_global_message("message after shutdown")
