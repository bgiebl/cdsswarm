"""Tests for output adapters."""

from unittest.mock import MagicMock

from cdsswarm.adapters import CursesAdapter, PlainTextAdapter
from cdsswarm.core import Task


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

    def test_started_is_silent(self):
        messages = []
        adapter = PlainTextAdapter(write_fn=messages.append)
        task = Task("ds", {}, "file.grib")
        adapter.on_task_started(0, task)
        assert len(messages) == 0

    def test_message_is_silent(self):
        messages = []
        adapter = PlainTextAdapter(write_fn=messages.append)
        adapter.on_task_message(0, "some CDS log line")
        assert len(messages) == 0

    def test_checksum_pass_is_silent(self):
        messages = []
        adapter = PlainTextAdapter(write_fn=messages.append)
        result = adapter.on_task_checksum_result(0, True, "abc123")
        assert result == "continue"
        assert len(messages) == 0

    def test_checksum_mismatch_prints_warning(self):
        messages = []
        adapter = PlainTextAdapter(write_fn=messages.append)
        result = adapter.on_task_checksum_result(0, False, "abc123")
        assert result == "continue"
        assert any("WARNING" in m and "checksum" in m.lower() for m in messages)
        assert any("abc123" in m for m in messages)

    def test_qos_update_prints(self):
        messages = []
        adapter = PlainTextAdapter(write_fn=messages.append)
        adapter.on_qos_update(5220, 400, 400)
        assert any("5220 queued" in m for m in messages)
        assert any("400/400 running" in m for m in messages)

    def test_qos_update_zeros_silent(self):
        messages = []
        adapter = PlainTextAdapter(write_fn=messages.append)
        adapter.on_qos_update(0, 0, 0)
        assert len(messages) == 0


class TestCursesAdapterNewCallbacks:
    def test_on_task_server_progress(self):
        tui = MagicMock()
        adapter = CursesAdapter(tui)
        adapter.on_task_server_progress(0, 72)
        tui.set_worker_server_progress.assert_called_once_with(0, 72)

    def test_on_task_file_size(self):
        tui = MagicMock()
        adapter = CursesAdapter(tui)
        adapter.on_task_file_size(0, 95418)
        tui.set_worker_file_size.assert_called_once_with(0, 95418)

    def test_on_task_checksum_pass(self):
        tui = MagicMock()
        adapter = CursesAdapter(tui)
        result = adapter.on_task_checksum_result(0, True, "abc")
        tui.set_worker_checksum_result.assert_called_once_with(0, True)
        tui.show_checksum_dialog.assert_not_called()
        assert result == "continue"

    def test_on_task_checksum_fail_opens_dialog(self):
        tui = MagicMock()
        tui.show_checksum_dialog.return_value = "retry"
        adapter = CursesAdapter(tui)
        result = adapter.on_task_checksum_result(0, False, "abc")
        tui.set_worker_checksum_result.assert_called_once_with(0, False)
        tui.show_checksum_dialog.assert_called_once_with(0, "abc")
        assert result == "retry"

    def test_on_task_server_timestamps(self):
        tui = MagicMock()
        adapter = CursesAdapter(tui)
        adapter.on_task_server_timestamps(0, "a", "b", "c")
        tui.set_worker_server_timestamps.assert_called_once_with(0, "a", "b", "c")

    def test_on_task_dataset_title(self):
        tui = MagicMock()
        adapter = CursesAdapter(tui)
        adapter.on_task_dataset_title(0, "ERA5 data")
        tui.set_worker_dataset_title.assert_called_once_with(0, "ERA5 data")

    def test_on_task_request_labels(self):
        tui = MagicMock()
        adapter = CursesAdapter(tui)
        labels = {"Variable": "Temperature"}
        adapter.on_task_request_labels(0, labels)
        tui.set_worker_request_labels.assert_called_once_with(0, labels)

    def test_on_qos_update(self):
        tui = MagicMock()
        adapter = CursesAdapter(tui)
        adapter.on_qos_update(5220, 400, 400)
        tui.set_qos_data.assert_called_once_with(5220, 400, 400)
