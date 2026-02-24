"""Tests for LoggingAdapter."""

import io
import re
from unittest.mock import MagicMock

from cdsswarm.adapters import LoggingAdapter, OutputAdapter
from cdsswarm.core import Task


def _make_adapter():
    inner = MagicMock(spec=OutputAdapter)
    log_file = io.StringIO()
    adapter = LoggingAdapter(inner, log_file)
    return adapter, inner, log_file


def _make_task():
    return Task("dataset", {"var": "temp"}, "output.grib")


class TestDelegation:
    def test_on_task_started_delegates(self):
        adapter, inner, _ = _make_adapter()
        task = _make_task()
        adapter.on_task_started(0, task)
        inner.on_task_started.assert_called_once_with(0, task)

    def test_on_task_message_delegates(self):
        adapter, inner, _ = _make_adapter()
        adapter.on_task_message(1, "hello")
        inner.on_task_message.assert_called_once_with(1, "hello")

    def test_on_task_completed_delegates(self):
        adapter, inner, _ = _make_adapter()
        task = _make_task()
        adapter.on_task_completed(0, task, True)
        inner.on_task_completed.assert_called_once_with(0, task, True, "")

    def test_on_progress_update_delegates(self):
        adapter, inner, _ = _make_adapter()
        adapter.on_progress_update(5, 10, 2)
        inner.on_progress_update.assert_called_once_with(5, 10, 2)

    def test_on_global_message_delegates(self):
        adapter, inner, _ = _make_adapter()
        adapter.on_global_message("Starting download")
        inner.on_global_message.assert_called_once_with("Starting download")

    def test_on_task_progress_delegates(self):
        adapter, inner, _ = _make_adapter()
        adapter.on_task_progress(0, 1024, 4096)
        inner.on_task_progress.assert_called_once_with(0, 1024, 4096)

    def test_on_task_cancelled_delegates(self):
        adapter, inner, _ = _make_adapter()
        adapter.on_task_cancelled(0)
        inner.on_task_cancelled.assert_called_once_with(0)

    def test_on_task_request_id_delegates(self):
        adapter, inner, _ = _make_adapter()
        adapter.on_task_request_id(0, "abc-123")
        inner.on_task_request_id.assert_called_once_with(0, "abc-123")

    def test_on_task_hook_started_delegates(self):
        adapter, inner, _ = _make_adapter()
        adapter.on_task_hook_started(0, "gzip file.grib")
        inner.on_task_hook_started.assert_called_once_with(0, "gzip file.grib")

    def test_on_task_hook_finished_delegates(self):
        adapter, inner, _ = _make_adapter()
        adapter.on_task_hook_finished(0, False, "exit code 1")
        inner.on_task_hook_finished.assert_called_once_with(0, False, "exit code 1")


class TestLogging:
    def test_timestamp_format(self):
        adapter, _, log_file = _make_adapter()
        adapter.on_global_message("test message")
        content = log_file.getvalue()
        # Expect [YYYY-MM-DD HH:MM:SS] prefix
        assert re.match(r"\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\]", content)

    def test_task_started_logged(self):
        adapter, _, log_file = _make_adapter()
        task = _make_task()
        adapter.on_task_started(0, task)
        assert "started" in log_file.getvalue()
        assert "output.grib" in log_file.getvalue()

    def test_task_message_logged(self):
        adapter, _, log_file = _make_adapter()
        adapter.on_task_message(1, "status: running")
        assert "status: running" in log_file.getvalue()

    def test_task_completed_logged(self):
        adapter, _, log_file = _make_adapter()
        task = _make_task()
        adapter.on_task_completed(0, task, False, "network error")
        content = log_file.getvalue()
        assert "FAILED" in content
        assert "network error" in content

    def test_progress_not_logged(self):
        adapter, _, log_file = _make_adapter()
        adapter.on_task_progress(0, 1024, 4096)
        assert log_file.getvalue() == ""

    def test_hook_started_logged(self):
        adapter, _, log_file = _make_adapter()
        adapter.on_task_hook_started(0, "gzip file.grib")
        content = log_file.getvalue()
        assert "post-hook started" in content
        assert "gzip file.grib" in content

    def test_hook_finished_logged(self):
        adapter, _, log_file = _make_adapter()
        adapter.on_task_hook_finished(0, False, "exit code 1")
        content = log_file.getvalue()
        assert "FAILED" in content
        assert "exit code 1" in content

    def test_multiple_events(self):
        adapter, _, log_file = _make_adapter()
        task = _make_task()
        adapter.on_task_started(0, task)
        adapter.on_task_message(0, "processing")
        adapter.on_global_message("all done")
        lines = log_file.getvalue().strip().split("\n")
        assert len(lines) == 3
