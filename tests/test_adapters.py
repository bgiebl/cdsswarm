"""Tests for output adapters."""

from cdsswarm.adapters import PlainTextAdapter
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
