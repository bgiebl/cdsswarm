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

    def test_checksum_mismatch_bold_orange(self):
        messages = []
        adapter = PlainTextAdapter(write_fn=messages.append, use_color=True)
        adapter.on_task_checksum_result(0, False, "abc123")
        # bold orange: \033[1;38;5;208m
        assert "\033[1;38;5;208m" in messages[0]

    def test_checksum_mismatch_no_color(self):
        messages = []
        adapter = PlainTextAdapter(write_fn=messages.append, use_color=False)
        adapter.on_task_checksum_result(0, False, "abc123")
        assert "\033" not in messages[0]
        assert "WARNING" in messages[0]

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

    def test_qos_update_deduplicated(self):
        messages = []
        adapter = PlainTextAdapter(write_fn=messages.append)
        adapter.on_qos_update(100, 20, 20)
        adapter.on_qos_update(100, 20, 20)
        adapter.on_qos_update(100, 20, 20)
        assert len(messages) == 1

    def test_qos_update_change_printed(self):
        messages = []
        adapter = PlainTextAdapter(write_fn=messages.append)
        adapter.on_qos_update(100, 20, 20)
        adapter.on_qos_update(80, 20, 20)
        assert len(messages) == 2

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
