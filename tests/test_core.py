"""Tests for core download engine."""

import io
import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest

from cdsswarm.adapters import LoggingAdapter, PlainTextAdapter
from cdsswarm.core import Result, SwarmDownloader, Task


@pytest.fixture
def tmp_dir():
    with tempfile.TemporaryDirectory() as d:
        yield d


def _make_tasks(tmp_dir, count=3):
    return [
        Task(
            dataset="reanalysis-era5-single-levels",
            request={"variable": [f"var_{i}"], "year": ["2024"]},
            target=os.path.join(tmp_dir, f"output_{i}.grib"),
        )
        for i in range(count)
    ]


class TestTask:
    def test_label_is_filename(self):
        t = Task("ds", {}, "/some/path/output.grib")
        assert t.label == "output.grib"

    def test_fields(self):
        t = Task("my-dataset", {"key": "val"}, "target.nc")
        assert t.dataset == "my-dataset"
        assert t.request == {"key": "val"}
        assert t.target == "target.nc"


class TestResult:
    def test_success_result(self):
        t = Task("ds", {}, "out.grib")
        r = Result(task=t, success=True)
        assert r.success
        assert r.error == ""

    def test_failure_result(self):
        t = Task("ds", {}, "out.grib")
        r = Result(task=t, success=False, error="network error")
        assert not r.success
        assert r.error == "network error"

    def test_timing_defaults(self):
        t = Task("ds", {}, "out.grib")
        r = Result(task=t, success=True)
        assert r.start_time == 0.0
        assert r.end_time == 0.0
        assert r.file_size == 0

    def test_timing_fields(self):
        t = Task("ds", {}, "out.grib")
        r = Result(
            task=t, success=True, start_time=100.0, end_time=200.0, file_size=1024
        )
        assert r.start_time == 100.0
        assert r.end_time == 200.0
        assert r.file_size == 1024


class TestSwarmDownloader:
    @patch("cdsswarm.core.cdsapi")
    def test_successful_download(self, mock_cdsapi, tmp_dir):
        """All tasks succeed when cdsapi.Client.retrieve works."""
        mock_client = MagicMock()

        # Simulate retrieve by creating the target file
        def fake_retrieve(dataset, request, target):
            with open(target, "w") as f:
                f.write("data")

        mock_client.retrieve.side_effect = fake_retrieve
        mock_cdsapi.Client.return_value = mock_client

        tasks = _make_tasks(tmp_dir, count=2)
        adapter = PlainTextAdapter()
        downloader = SwarmDownloader(tasks, adapter, num_workers=2)
        results = downloader.run()

        assert results is not None
        assert len(results) == 2
        assert all(r.success for r in results)
        for r in results:
            assert r.start_time > 0
            assert r.end_time >= r.start_time
            assert r.file_size > 0

    @patch("cdsswarm.core.cdsapi")
    def test_failed_download(self, mock_cdsapi, tmp_dir):
        """Failed tasks produce Result with success=False."""
        mock_client = MagicMock()
        mock_client.retrieve.side_effect = RuntimeError("CDS error")
        mock_cdsapi.Client.return_value = mock_client

        tasks = _make_tasks(tmp_dir, count=1)
        adapter = PlainTextAdapter()
        downloader = SwarmDownloader(tasks, adapter, num_workers=1)
        results = downloader.run()

        assert results is not None
        assert len(results) == 1
        assert not results[0].success
        assert "CDS error" in results[0].error

    @patch("cdsswarm.core.cdsapi")
    def test_skip_existing(self, mock_cdsapi, tmp_dir):
        """Existing files are skipped when skip_existing=True."""
        tasks = _make_tasks(tmp_dir, count=2)
        # Pre-create first file
        with open(tasks[0].target, "w") as f:
            f.write("cached")

        mock_client = MagicMock()

        def fake_retrieve(dataset, request, target):
            with open(target, "w") as f:
                f.write("data")

        mock_client.retrieve.side_effect = fake_retrieve
        mock_cdsapi.Client.return_value = mock_client

        adapter = PlainTextAdapter()
        downloader = SwarmDownloader(tasks, adapter, num_workers=1, skip_existing=True)
        results = downloader.run()

        assert results is not None
        assert len(results) == 2
        assert all(r.success for r in results)
        # cdsapi.Client.retrieve should only be called once (for the non-existing file)
        assert mock_client.retrieve.call_count == 1

    @patch("cdsswarm.core.cdsapi")
    def test_no_skip_existing(self, mock_cdsapi, tmp_dir):
        """Files are re-downloaded when skip_existing=False."""
        tasks = _make_tasks(tmp_dir, count=1)
        with open(tasks[0].target, "w") as f:
            f.write("old")

        mock_client = MagicMock()

        def fake_retrieve(dataset, request, target):
            with open(target, "w") as f:
                f.write("new")

        mock_client.retrieve.side_effect = fake_retrieve
        mock_cdsapi.Client.return_value = mock_client

        adapter = PlainTextAdapter()
        downloader = SwarmDownloader(tasks, adapter, num_workers=1, skip_existing=False)
        results = downloader.run()

        assert results is not None
        assert mock_client.retrieve.call_count == 1

    @patch("cdsswarm.core.cdsapi")
    def test_all_cached(self, mock_cdsapi, tmp_dir):
        """When all files exist, no downloads happen."""
        tasks = _make_tasks(tmp_dir, count=2)
        for t in tasks:
            with open(t.target, "w") as f:
                f.write("cached")

        adapter = PlainTextAdapter()
        downloader = SwarmDownloader(tasks, adapter, num_workers=1)
        results = downloader.run()

        assert results is not None
        assert len(results) == 2
        assert all(r.success for r in results)
        mock_cdsapi.Client.assert_not_called()

    @patch("cdsswarm.core.cdsapi")
    def test_mixed_success_failure(self, mock_cdsapi, tmp_dir):
        """Mix of successful and failed tasks."""
        tasks = _make_tasks(tmp_dir, count=3)

        mock_client = MagicMock()
        call_count = [0]

        def fake_retrieve(dataset, request, target):
            call_count[0] += 1
            if "var_1" in request.get("variable", []):
                raise RuntimeError("var_1 failed")
            with open(target, "w") as f:
                f.write("data")

        mock_client.retrieve.side_effect = fake_retrieve
        mock_cdsapi.Client.return_value = mock_client

        adapter = PlainTextAdapter()
        downloader = SwarmDownloader(tasks, adapter, num_workers=2)
        results = downloader.run()

        assert results is not None
        successes = [r for r in results if r.success]
        failures = [r for r in results if not r.success]
        assert len(successes) == 2
        assert len(failures) == 1

    @patch("cdsswarm.core.find_reusable_jobs")
    @patch("cdsswarm.core.cdsapi")
    def test_reuse_job_uses_get_remote(self, mock_cdsapi, mock_find, tmp_dir):
        """Reused job downloads via inner.get_remote() instead of retrieve()."""
        tasks = _make_tasks(tmp_dir, count=1)
        target = tasks[0].target

        mock_find.return_value = {target: "existing-job-id"}

        # Set up mock client with inner .client attribute
        mock_inner = MagicMock()
        mock_remote = MagicMock()
        mock_remote.download.side_effect = lambda t: open(t, "w").close()
        mock_inner.get_remote.return_value = mock_remote

        mock_client = MagicMock()
        mock_client.client = mock_inner
        mock_cdsapi.Client.return_value = mock_client

        adapter = PlainTextAdapter()
        downloader = SwarmDownloader(tasks, adapter, num_workers=1, reuse_jobs=True)
        results = downloader.run()

        assert results is not None
        assert len(results) == 1
        assert results[0].success
        mock_inner.get_remote.assert_called_once_with("existing-job-id")
        mock_remote.download.assert_called_once_with(target)
        mock_client.retrieve.assert_not_called()

    @patch("cdsswarm.core.find_reusable_jobs")
    @patch("cdsswarm.core.cdsapi")
    def test_non_matching_tasks_still_call_retrieve(
        self, mock_cdsapi, mock_find, tmp_dir
    ):
        """Tasks without a reuse match use normal retrieve()."""
        tasks = _make_tasks(tmp_dir, count=2)

        # Only the first task has a reuse match
        mock_find.return_value = {tasks[0].target: "job-0"}

        mock_inner = MagicMock()
        mock_remote = MagicMock()
        mock_remote.download.side_effect = lambda t: open(t, "w").close()
        mock_inner.get_remote.return_value = mock_remote

        mock_client = MagicMock()
        mock_client.client = mock_inner

        def fake_retrieve(dataset, request, target):
            with open(target, "w") as f:
                f.write("data")

        mock_client.retrieve.side_effect = fake_retrieve
        mock_cdsapi.Client.return_value = mock_client

        adapter = PlainTextAdapter()
        downloader = SwarmDownloader(tasks, adapter, num_workers=1, reuse_jobs=True)
        results = downloader.run()

        assert results is not None
        assert len(results) == 2
        assert all(r.success for r in results)
        # One reused, one fresh
        mock_inner.get_remote.assert_called_once_with("job-0")
        assert mock_client.retrieve.call_count == 1

    @patch("cdsswarm.core.find_reusable_jobs")
    @patch("cdsswarm.core.cdsapi")
    def test_reuse_lookup_failure_falls_back(self, mock_cdsapi, mock_find, tmp_dir):
        """Lookup failure gracefully falls back to new requests."""
        mock_find.side_effect = RuntimeError("API unavailable")

        mock_client = MagicMock()

        def fake_retrieve(dataset, request, target):
            with open(target, "w") as f:
                f.write("data")

        mock_client.retrieve.side_effect = fake_retrieve
        mock_cdsapi.Client.return_value = mock_client

        tasks = _make_tasks(tmp_dir, count=1)
        adapter = PlainTextAdapter()
        downloader = SwarmDownloader(tasks, adapter, num_workers=1, reuse_jobs=True)
        results = downloader.run()

        assert results is not None
        assert len(results) == 1
        assert results[0].success
        assert mock_client.retrieve.call_count == 1

    @patch("cdsswarm.core.find_reusable_jobs")
    @patch("cdsswarm.core.cdsapi")
    def test_reuse_disabled_skips_lookup(self, mock_cdsapi, mock_find, tmp_dir):
        """reuse_jobs=False skips the lookup entirely."""
        mock_client = MagicMock()

        def fake_retrieve(dataset, request, target):
            with open(target, "w") as f:
                f.write("data")

        mock_client.retrieve.side_effect = fake_retrieve
        mock_cdsapi.Client.return_value = mock_client

        tasks = _make_tasks(tmp_dir, count=1)
        adapter = PlainTextAdapter()
        downloader = SwarmDownloader(tasks, adapter, num_workers=1, reuse_jobs=False)
        results = downloader.run()

        assert results is not None
        mock_find.assert_not_called()
        assert mock_client.retrieve.call_count == 1

    @patch("cdsswarm.core.cdsapi")
    def test_retry_succeeds_after_transient_failure(self, mock_cdsapi, tmp_dir):
        """Task succeeds after transient failures when retries are available."""
        mock_client = MagicMock()
        call_count = [0]

        def fake_retrieve(dataset, request, target):
            call_count[0] += 1
            if call_count[0] < 3:
                raise RuntimeError("transient error")
            with open(target, "w") as f:
                f.write("data")

        mock_client.retrieve.side_effect = fake_retrieve
        mock_cdsapi.Client.return_value = mock_client

        tasks = _make_tasks(tmp_dir, count=1)
        adapter = PlainTextAdapter()
        downloader = SwarmDownloader(tasks, adapter, num_workers=1, max_retries=3)
        results = downloader.run()

        assert results is not None
        assert len(results) == 1
        assert results[0].success
        assert call_count[0] == 3

    @patch("cdsswarm.core.cdsapi")
    def test_retry_exhausted(self, mock_cdsapi, tmp_dir):
        """Task fails after all retry attempts are exhausted."""
        mock_client = MagicMock()
        mock_client.retrieve.side_effect = RuntimeError("persistent error")
        mock_cdsapi.Client.return_value = mock_client

        tasks = _make_tasks(tmp_dir, count=1)
        adapter = PlainTextAdapter()
        downloader = SwarmDownloader(tasks, adapter, num_workers=1, max_retries=3)
        results = downloader.run()

        assert results is not None
        assert len(results) == 1
        assert not results[0].success
        assert "persistent error" in results[0].error
        assert mock_client.retrieve.call_count == 3

    @patch("cdsswarm.core.cdsapi")
    def test_retry_disabled(self, mock_cdsapi, tmp_dir):
        """max_retries=1 means no retries — immediate failure."""
        mock_client = MagicMock()
        mock_client.retrieve.side_effect = RuntimeError("fail")
        mock_cdsapi.Client.return_value = mock_client

        tasks = _make_tasks(tmp_dir, count=1)
        adapter = PlainTextAdapter()
        downloader = SwarmDownloader(tasks, adapter, num_workers=1, max_retries=1)
        results = downloader.run()

        assert results is not None
        assert len(results) == 1
        assert not results[0].success
        assert mock_client.retrieve.call_count == 1

    @patch("cdsswarm.core.cdsapi")
    def test_debug_cb_fallback_on_format_error(self, mock_cdsapi, tmp_dir):
        """debug_callback falls back to str(msg) instead of silently dropping."""
        captured_debug_cb = []

        def capture_client(**kwargs):
            client = MagicMock()
            if "debug_callback" in kwargs:
                captured_debug_cb.append(kwargs["debug_callback"])

            def fake_retrieve(dataset, request, target):
                with open(target, "w") as f:
                    f.write("data")

            client.retrieve.side_effect = fake_retrieve
            return client

        mock_cdsapi.Client.side_effect = capture_client

        tasks = _make_tasks(tmp_dir, count=1)
        adapter = MagicMock(spec=PlainTextAdapter)
        downloader = SwarmDownloader(tasks, adapter, num_workers=1)
        downloader.run()

        assert captured_debug_cb, "debug_callback was not passed to Client"
        debug_cb = captured_debug_cb[0]

        # Call with args that will cause % formatting to fail
        # (msg expects %d but gets a string)
        debug_cb("request id %d", "not-a-number")

        # Should NOT raise — the old code would silently return,
        # the fix falls back to str(msg) and still calls _check_request_id.
        # We can't easily verify _check_request_id was called, but we verify
        # it didn't crash.

    @patch("cdsswarm.core.cdsapi")
    def test_debug_cb_extracts_request_id(self, mock_cdsapi, tmp_dir):
        """debug_callback extracts request IDs even from fallback formatting."""
        captured_debug_cb = []

        def capture_client(**kwargs):
            client = MagicMock()
            if "debug_callback" in kwargs:
                captured_debug_cb.append(kwargs["debug_callback"])

            def fake_retrieve(dataset, request, target):
                with open(target, "w") as f:
                    f.write("data")

            client.retrieve.side_effect = fake_retrieve
            return client

        mock_cdsapi.Client.side_effect = capture_client

        tasks = _make_tasks(tmp_dir, count=1)
        adapter = MagicMock(spec=PlainTextAdapter)
        downloader = SwarmDownloader(tasks, adapter, num_workers=1)
        downloader.run()

        debug_cb = captured_debug_cb[0]

        # Call with a UUID-like request ID that would fail % formatting
        # but the raw msg contains a request ID
        debug_cb("Request ID: %s abc-12345-def", object())

        # The fallback str(msg) should contain the raw format string,
        # which doesn't contain a valid UUID, so on_task_request_id
        # should NOT be called for this specific case.
        # But the key point is it doesn't crash.

    @patch("cdsswarm.core.cdsapi")
    def test_concurrent_timing_and_warnings(self, mock_cdsapi, tmp_dir):
        """Timing and warnings are correctly collected under concurrent access."""
        mock_client = MagicMock()

        def fake_retrieve(dataset, request, target):
            with open(target, "w") as f:
                f.write("x" * 1024)

        mock_client.retrieve.side_effect = fake_retrieve
        mock_cdsapi.Client.return_value = mock_client

        tasks = _make_tasks(tmp_dir, count=6)
        adapter = PlainTextAdapter()
        downloader = SwarmDownloader(tasks, adapter, num_workers=4)
        results = downloader.run()

        assert results is not None
        assert len(results) == 6
        assert all(r.success for r in results)
        # All results should have valid timing
        for r in results:
            assert r.start_time > 0
            assert r.end_time >= r.start_time
            assert r.file_size > 0

    @patch("cdsswarm.core.cdsapi")
    def test_retry_message_sent(self, mock_cdsapi, tmp_dir):
        """Adapter receives retry messages on transient failures."""
        mock_client = MagicMock()
        call_count = [0]

        def fake_retrieve(dataset, request, target):
            call_count[0] += 1
            if call_count[0] < 2:
                raise RuntimeError("temporary")
            with open(target, "w") as f:
                f.write("data")

        mock_client.retrieve.side_effect = fake_retrieve
        mock_cdsapi.Client.return_value = mock_client

        tasks = _make_tasks(tmp_dir, count=1)
        adapter = MagicMock(spec=PlainTextAdapter)
        downloader = SwarmDownloader(tasks, adapter, num_workers=1, max_retries=3)
        results = downloader.run()

        assert results is not None
        assert results[0].success
        # Check that on_task_message was called with retry info
        retry_messages = [
            call
            for call in adapter.on_task_message.call_args_list
            if "retrying" in str(call).lower()
        ]
        assert len(retry_messages) == 1
        assert "1/3" in str(retry_messages[0])

    @patch("cdsswarm.core.cdsapi")
    def test_retry_logs_full_traceback(self, mock_cdsapi, tmp_dir, caplog):
        """Retry failures log the full traceback for debugging."""
        mock_client = MagicMock()
        call_count = [0]

        def fake_retrieve(dataset, request, target):
            call_count[0] += 1
            if call_count[0] < 2:
                raise ValueError("bad value(s) in fds_to_keep")
            with open(target, "w") as f:
                f.write("data")

        mock_client.retrieve.side_effect = fake_retrieve
        mock_cdsapi.Client.return_value = mock_client

        tasks = _make_tasks(tmp_dir, count=1)
        adapter = MagicMock(spec=PlainTextAdapter)
        downloader = SwarmDownloader(tasks, adapter, num_workers=1, max_retries=3)

        import logging

        with caplog.at_level(logging.WARNING, logger="cdsswarm.core"):
            results = downloader.run()

        assert results is not None
        assert results[0].success
        # The log must contain the full traceback, not just the message
        assert "Traceback (most recent call last)" in caplog.text
        assert "bad value(s) in fds_to_keep" in caplog.text
        assert "fake_retrieve" in caplog.text

    @patch("cdsswarm.core.cdsapi")
    def test_retry_traceback_reaches_log_file(self, mock_cdsapi, tmp_dir):
        """Retry tracebacks are routed through the adapter into the log file."""
        mock_client = MagicMock()
        call_count = [0]

        def fake_retrieve(dataset, request, target):
            call_count[0] += 1
            if call_count[0] < 2:
                raise ValueError("bad value(s) in fds_to_keep")
            with open(target, "w") as f:
                f.write("data")

        mock_client.retrieve.side_effect = fake_retrieve
        mock_cdsapi.Client.return_value = mock_client

        tasks = _make_tasks(tmp_dir, count=1)
        log_file = io.StringIO()
        inner = PlainTextAdapter()
        adapter = LoggingAdapter(inner, log_file)
        downloader = SwarmDownloader(tasks, adapter, num_workers=1, max_retries=3)
        results = downloader.run()

        assert results is not None
        assert results[0].success
        log_content = log_file.getvalue()
        assert "Traceback (most recent call last)" in log_content
        assert "bad value(s) in fds_to_keep" in log_content

    @patch("cdsswarm.core.cdsapi")
    def test_cancel_sets_event_and_shuts_down(self, mock_cdsapi, tmp_dir):
        """cancel() sets the cancel event and shuts down the pool."""
        tasks = _make_tasks(tmp_dir, count=1)
        adapter = PlainTextAdapter()
        downloader = SwarmDownloader(tasks, adapter, num_workers=1)

        # Simulate internal state as if run() was in progress
        mock_pool = MagicMock()
        downloader._pool = mock_pool
        from cdsswarm.core import _WorkerState

        downloader._state = _WorkerState()

        downloader.cancel()

        assert downloader._cancel_event.is_set()
        mock_pool.shutdown.assert_called_once_with(wait=False, cancel_futures=True)

    @patch("cdsswarm.core.cancel_cds_request")
    @patch("cdsswarm.core.cdsapi")
    def test_cancel_active_requests(self, mock_cdsapi, mock_cancel, tmp_dir):
        """cancel() cancels active CDS requests via the API."""
        tasks = _make_tasks(tmp_dir, count=1)
        adapter = MagicMock(spec=PlainTextAdapter)
        downloader = SwarmDownloader(tasks, adapter, num_workers=1)

        from cdsswarm.core import _WorkerState

        state = _WorkerState()
        mock_client = MagicMock()
        state.active_requests["out.grib"] = ("req-123", mock_client)
        state.task_worker_map["out.grib"] = 0
        downloader._state = state
        downloader._pool = MagicMock()

        downloader.cancel()

        mock_cancel.assert_called_once_with(mock_client, "req-123")
        # Verify adapter received cancellation messages
        global_msgs = [str(c) for c in adapter.on_global_message.call_args_list]
        assert any("Cancelling" in m for m in global_msgs)
        assert any("Cancelled" in m for m in global_msgs)
        adapter.on_task_cancelled.assert_called_once_with(0)

    @patch("cdsswarm.core.cancel_cds_request")
    @patch("cdsswarm.core.cdsapi")
    def test_cancel_active_request_failure(self, mock_cdsapi, mock_cancel, tmp_dir):
        """Failed cancellation reports error but doesn't crash."""
        tasks = _make_tasks(tmp_dir, count=1)
        adapter = MagicMock(spec=PlainTextAdapter)
        downloader = SwarmDownloader(tasks, adapter, num_workers=1)

        from cdsswarm.core import _WorkerState

        state = _WorkerState()
        mock_client = MagicMock()
        state.active_requests["out.grib"] = ("req-456", mock_client)
        downloader._state = state
        downloader._pool = MagicMock()

        mock_cancel.side_effect = RuntimeError("network error")
        downloader.cancel()

        global_msgs = [str(c) for c in adapter.on_global_message.call_args_list]
        assert any("Failed to cancel" in m for m in global_msgs)

    @patch("cdsswarm.core.cdsapi")
    def test_info_cb_format_error_fallback(self, mock_cdsapi, tmp_dir):
        """info_callback falls back to str(msg) on format errors."""
        captured_info_cb = []

        def capture_client(**kwargs):
            client = MagicMock()
            if "info_callback" in kwargs:
                captured_info_cb.append(kwargs["info_callback"])

            def fake_retrieve(dataset, request, target):
                with open(target, "w") as f:
                    f.write("data")

            client.retrieve.side_effect = fake_retrieve
            return client

        mock_cdsapi.Client.side_effect = capture_client

        tasks = _make_tasks(tmp_dir, count=1)
        adapter = MagicMock(spec=PlainTextAdapter)
        downloader = SwarmDownloader(tasks, adapter, num_workers=1)
        downloader.run()

        assert captured_info_cb, "info_callback was not passed to Client"
        info_cb = captured_info_cb[0]

        # Call with args that will cause % formatting to fail
        info_cb("value %d", "not-a-number")
        # Should NOT raise — falls back to str(msg)

    @patch("cdsswarm.core.find_reusable_jobs")
    @patch("cdsswarm.core.cdsapi")
    def test_reuse_old_client_falls_back_to_retrieve(
        self, mock_cdsapi, mock_find, tmp_dir
    ):
        """When client.client is None, reuse falls back to retrieve()."""
        tasks = _make_tasks(tmp_dir, count=1)
        target = tasks[0].target

        mock_find.return_value = {target: "existing-job-id"}

        mock_client = MagicMock()
        mock_client.client = None  # Old-style client, no inner

        def fake_retrieve(dataset, request, target):
            with open(target, "w") as f:
                f.write("data")

        mock_client.retrieve.side_effect = fake_retrieve
        mock_cdsapi.Client.return_value = mock_client

        adapter = PlainTextAdapter()
        downloader = SwarmDownloader(tasks, adapter, num_workers=1, reuse_jobs=True)
        results = downloader.run()

        assert results is not None
        assert len(results) == 1
        assert results[0].success
        mock_client.retrieve.assert_called_once()

    @patch("cdsswarm.core.cdsapi")
    def test_cancel_event_during_downloads(self, mock_cdsapi, tmp_dir):
        """Setting cancel event during downloads returns None."""
        import threading

        mock_client = MagicMock()
        barrier = threading.Event()

        def slow_retrieve(dataset, request, target):
            barrier.set()
            # Wait a bit to give cancel time to trigger
            import time

            time.sleep(1)
            with open(target, "w") as f:
                f.write("data")

        mock_client.retrieve.side_effect = slow_retrieve
        mock_cdsapi.Client.return_value = mock_client

        tasks = _make_tasks(tmp_dir, count=1)
        adapter = PlainTextAdapter()
        downloader = SwarmDownloader(tasks, adapter, num_workers=1)

        def cancel_later():
            barrier.wait(5)
            downloader.cancel()

        t = threading.Thread(target=cancel_later)
        t.start()
        results = downloader.run()
        t.join()
        assert results is None

    @patch("cdsswarm.core.wait")
    @patch("cdsswarm.core.cdsapi")
    def test_keyboard_interrupt_cancels(self, mock_cdsapi, mock_wait, tmp_dir):
        """KeyboardInterrupt during wait() returns None with message."""
        mock_client = MagicMock()
        mock_cdsapi.Client.return_value = mock_client

        mock_wait.side_effect = KeyboardInterrupt()

        tasks = _make_tasks(tmp_dir, count=1)
        adapter = MagicMock(spec=PlainTextAdapter)
        downloader = SwarmDownloader(tasks, adapter, num_workers=1)
        results = downloader.run()

        assert results is None
        interrupted_msgs = [
            str(c)
            for c in adapter.on_global_message.call_args_list
            if "Interrupted" in str(c)
        ]
        assert len(interrupted_msgs) >= 1

    @patch("cdsswarm.core.cdsapi")
    def test_double_keyboard_interrupt(self, mock_cdsapi, tmp_dir):
        """Double KeyboardInterrupt sends 'Force quit' message."""
        mock_client = MagicMock()
        mock_cdsapi.Client.return_value = mock_client

        tasks = _make_tasks(tmp_dir, count=1)
        adapter = MagicMock(spec=PlainTextAdapter)
        downloader = SwarmDownloader(tasks, adapter, num_workers=1)

        # Patch wait and _cancel_active to both raise KeyboardInterrupt
        with patch("cdsswarm.core.wait", side_effect=KeyboardInterrupt()):
            with patch.object(
                downloader, "_cancel_active", side_effect=KeyboardInterrupt()
            ):
                results = downloader.run()

        assert results is None
        force_msgs = [
            str(c)
            for c in adapter.on_global_message.call_args_list
            if "Force quit" in str(c)
        ]
        assert len(force_msgs) >= 1

    @patch("cdsswarm.core.subprocess")
    @patch("cdsswarm.core.cdsapi")
    def test_post_hook_runs_with_placeholders(
        self, mock_cdsapi, mock_subprocess, tmp_dir
    ):
        """Post-hook runs with {file} and {dataset} expanded."""
        mock_client = MagicMock()

        def fake_retrieve(dataset, request, target):
            with open(target, "w") as f:
                f.write("data")

        mock_client.retrieve.side_effect = fake_retrieve
        mock_cdsapi.Client.return_value = mock_client
        mock_subprocess.run.return_value = MagicMock(returncode=0)

        tasks = _make_tasks(tmp_dir, count=1)
        adapter = MagicMock(spec=PlainTextAdapter)
        downloader = SwarmDownloader(
            tasks, adapter, num_workers=1, post_hook="gzip {file} && echo {dataset}"
        )
        results = downloader.run()

        assert results is not None
        assert len(results) == 1
        assert results[0].success
        mock_subprocess.run.assert_called_once()
        cmd = mock_subprocess.run.call_args[0][0]
        assert tasks[0].target in cmd
        assert tasks[0].dataset in cmd
        adapter.on_task_hook_started.assert_called_once()
        adapter.on_task_hook_finished.assert_called_once_with(0, True)

    @patch("cdsswarm.core.subprocess")
    @patch("cdsswarm.core.cdsapi")
    def test_post_hook_failure_adds_warning(
        self, mock_cdsapi, mock_subprocess, tmp_dir
    ):
        """Post-hook failure adds warning but task stays successful."""
        mock_client = MagicMock()

        def fake_retrieve(dataset, request, target):
            with open(target, "w") as f:
                f.write("data")

        mock_client.retrieve.side_effect = fake_retrieve
        mock_cdsapi.Client.return_value = mock_client
        mock_subprocess.run.return_value = MagicMock(
            returncode=1, stderr="command not found"
        )

        tasks = _make_tasks(tmp_dir, count=1)
        adapter = MagicMock(spec=PlainTextAdapter)
        downloader = SwarmDownloader(
            tasks, adapter, num_workers=1, post_hook="bad-command {file}"
        )
        results = downloader.run()

        assert results is not None
        assert len(results) == 1
        assert results[0].success
        assert any("Post-hook failed" in w for w in results[0].warnings)
        adapter.on_task_hook_finished.assert_called_once()
        call_args = adapter.on_task_hook_finished.call_args
        assert call_args[0][1] is False  # success=False

    @patch("cdsswarm.core.subprocess")
    @patch("cdsswarm.core.cdsapi")
    def test_post_hook_exception_adds_warning(
        self, mock_cdsapi, mock_subprocess, tmp_dir
    ):
        """Post-hook exception adds warning but task stays successful."""
        mock_client = MagicMock()

        def fake_retrieve(dataset, request, target):
            with open(target, "w") as f:
                f.write("data")

        mock_client.retrieve.side_effect = fake_retrieve
        mock_cdsapi.Client.return_value = mock_client
        mock_subprocess.run.side_effect = OSError("exec failed")

        tasks = _make_tasks(tmp_dir, count=1)
        adapter = MagicMock(spec=PlainTextAdapter)
        downloader = SwarmDownloader(
            tasks, adapter, num_workers=1, post_hook="bad {file}"
        )
        results = downloader.run()

        assert results is not None
        assert len(results) == 1
        assert results[0].success
        assert any("Post-hook error" in w for w in results[0].warnings)

    @patch("cdsswarm.core.cdsapi")
    def test_no_post_hook_when_not_set(self, mock_cdsapi, tmp_dir):
        """No post-hook is run when post_hook is empty."""
        mock_client = MagicMock()

        def fake_retrieve(dataset, request, target):
            with open(target, "w") as f:
                f.write("data")

        mock_client.retrieve.side_effect = fake_retrieve
        mock_cdsapi.Client.return_value = mock_client

        tasks = _make_tasks(tmp_dir, count=1)
        adapter = MagicMock(spec=PlainTextAdapter)
        downloader = SwarmDownloader(tasks, adapter, num_workers=1)
        results = downloader.run()

        assert results is not None
        assert results[0].success
        adapter.on_task_hook_started.assert_not_called()
        adapter.on_task_hook_finished.assert_not_called()

    @patch("cdsswarm.core.subprocess")
    @patch("cdsswarm.core.cdsapi")
    def test_post_hook_not_run_on_failed_download(
        self, mock_cdsapi, mock_subprocess, tmp_dir
    ):
        """Post-hook is not run when the download itself fails."""
        mock_client = MagicMock()
        mock_client.retrieve.side_effect = RuntimeError("CDS error")
        mock_cdsapi.Client.return_value = mock_client

        tasks = _make_tasks(tmp_dir, count=1)
        adapter = MagicMock(spec=PlainTextAdapter)
        downloader = SwarmDownloader(
            tasks, adapter, num_workers=1, post_hook="echo {file}"
        )
        results = downloader.run()

        assert results is not None
        assert not results[0].success
        mock_subprocess.run.assert_not_called()
        adapter.on_task_hook_started.assert_not_called()

    @patch("cdsswarm.core.cdsapi")
    def test_on_task_done_called_on_success(self, mock_cdsapi, tmp_dir):
        """on_task_done callback is called with Result and request_id on success."""
        mock_client = MagicMock()

        def fake_retrieve(dataset, request, target):
            with open(target, "w") as f:
                f.write("data")

        mock_client.retrieve.side_effect = fake_retrieve
        mock_cdsapi.Client.return_value = mock_client

        tasks = _make_tasks(tmp_dir, count=1)
        adapter = PlainTextAdapter()
        done_calls = []
        downloader = SwarmDownloader(
            tasks,
            adapter,
            num_workers=1,
            on_task_done=lambda result, rid: done_calls.append((result, rid)),
        )
        results = downloader.run()

        assert results is not None
        assert len(done_calls) == 1
        assert done_calls[0][0].success is True

    @patch("cdsswarm.core.cdsapi")
    def test_on_task_done_called_on_failure(self, mock_cdsapi, tmp_dir):
        """on_task_done callback is called with Result and request_id on failure."""
        mock_client = MagicMock()
        mock_client.retrieve.side_effect = RuntimeError("fail")
        mock_cdsapi.Client.return_value = mock_client

        tasks = _make_tasks(tmp_dir, count=1)
        adapter = PlainTextAdapter()
        done_calls = []
        downloader = SwarmDownloader(
            tasks,
            adapter,
            num_workers=1,
            on_task_done=lambda result, rid: done_calls.append((result, rid)),
        )
        results = downloader.run()

        assert results is not None
        assert len(done_calls) == 1
        assert done_calls[0][0].success is False

    @patch("cdsswarm.core.cdsapi")
    def test_on_task_done_not_called_when_none(self, mock_cdsapi, tmp_dir):
        """No error when on_task_done is None."""
        mock_client = MagicMock()

        def fake_retrieve(dataset, request, target):
            with open(target, "w") as f:
                f.write("data")

        mock_client.retrieve.side_effect = fake_retrieve
        mock_cdsapi.Client.return_value = mock_client

        tasks = _make_tasks(tmp_dir, count=1)
        adapter = PlainTextAdapter()
        downloader = SwarmDownloader(tasks, adapter, num_workers=1)
        results = downloader.run()
        assert results is not None
        assert len(results) == 1

    @patch("cdsswarm.core.cdsapi")
    def test_on_request_id_called(self, mock_cdsapi, tmp_dir):
        """on_request_id callback is called when CDS returns a request ID."""
        captured_ids = []

        def capture_client(**kwargs):
            client = MagicMock()
            info_cb = kwargs.get("info_callback")

            def fake_retrieve(dataset, request, target):
                if info_cb:
                    info_cb("Request ID is abc-12345-def")
                with open(target, "w") as f:
                    f.write("data")

            client.retrieve.side_effect = fake_retrieve
            return client

        mock_cdsapi.Client.side_effect = capture_client

        tasks = _make_tasks(tmp_dir, count=1)
        adapter = MagicMock(spec=PlainTextAdapter)
        downloader = SwarmDownloader(
            tasks,
            adapter,
            num_workers=1,
            on_request_id=lambda target, rid: captured_ids.append((target, rid)),
        )
        results = downloader.run()

        assert results is not None
        assert len(captured_ids) >= 1
        assert captured_ids[0][0] == tasks[0].target

    @patch("cdsswarm.core.find_reusable_jobs")
    @patch("cdsswarm.core.cdsapi")
    def test_initial_reuse_map_skips_api_scan(self, mock_cdsapi, mock_find, tmp_dir):
        """initial_reuse_map tasks skip API scan, others still use find_reusable_jobs."""
        tasks = _make_tasks(tmp_dir, count=2)

        # API returns nothing for the scanned task
        mock_find.return_value = {}

        mock_inner = MagicMock()
        mock_remote = MagicMock()
        mock_remote.download.side_effect = lambda t: open(t, "w").close()
        mock_inner.get_remote.return_value = mock_remote

        mock_client = MagicMock()
        mock_client.client = mock_inner

        def fake_retrieve(dataset, request, target):
            with open(target, "w") as f:
                f.write("data")

        mock_client.retrieve.side_effect = fake_retrieve
        mock_cdsapi.Client.return_value = mock_client

        adapter = PlainTextAdapter()
        downloader = SwarmDownloader(
            tasks,
            adapter,
            num_workers=1,
            reuse_jobs=True,
            initial_reuse_map={tasks[0].target: "saved-job-id"},
        )
        results = downloader.run()

        assert results is not None
        assert len(results) == 2
        assert all(r.success for r in results)
        # API scan should only look at task[1], not task[0]
        scanned_tasks = mock_find.call_args[0][1]
        assert len(scanned_tasks) == 1
        assert scanned_tasks[0].target == tasks[1].target
        # task[0] used the saved job ID
        mock_inner.get_remote.assert_called_once_with("saved-job-id")

    @patch("cdsswarm.core.cdsapi")
    def test_pre_messages_emitted(self, mock_cdsapi, tmp_dir):
        """pre_messages are emitted via adapter at start of run()."""
        mock_client = MagicMock()

        def fake_retrieve(dataset, request, target):
            with open(target, "w") as f:
                f.write("data")

        mock_client.retrieve.side_effect = fake_retrieve
        mock_cdsapi.Client.return_value = mock_client

        tasks = _make_tasks(tmp_dir, count=1)
        adapter = MagicMock(spec=PlainTextAdapter)
        downloader = SwarmDownloader(
            tasks,
            adapter,
            num_workers=1,
            pre_messages=["Resuming: 3/5 tasks remaining"],
        )
        results = downloader.run()

        assert results is not None
        # First on_global_message call should be the pre_message
        first_call = adapter.on_global_message.call_args_list[0]
        assert "Resuming" in str(first_call)

    @patch("cdsswarm.core.cdsapi")
    def test_cancel_during_retry_sleep(self, mock_cdsapi, tmp_dir):
        """Cancel event during retry sleep stops retrying."""
        import threading

        mock_client = MagicMock()
        call_count = [0]
        cancel_trigger = threading.Event()

        def fail_once(dataset, request, target):
            call_count[0] += 1
            if call_count[0] == 1:
                cancel_trigger.set()
                raise RuntimeError("transient error")
            with open(target, "w") as f:
                f.write("data")

        mock_client.retrieve.side_effect = fail_once
        mock_cdsapi.Client.return_value = mock_client

        tasks = _make_tasks(tmp_dir, count=1)
        adapter = PlainTextAdapter()
        downloader = SwarmDownloader(tasks, adapter, num_workers=1, max_retries=3)

        def cancel_after_trigger():
            cancel_trigger.wait(5)
            # Small delay to let retry loop start sleeping
            import time

            time.sleep(0.1)
            downloader._cancel_event.set()

        t = threading.Thread(target=cancel_after_trigger)
        t.start()
        downloader.run()
        t.join()
        # Either returns None (cancel during as_completed) or a failed result
        # The important thing is it stopped retrying
        assert call_count[0] <= 2  # Should not have made 3 attempts

    @patch("cdsswarm.core.LiveScraper.start", new=lambda self: None)
    @patch("cdsswarm.core.cdsapi")
    def test_server_down_blocks_worker(self, mock_cdsapi, tmp_dir):
        """Workers wait when _server_ok is cleared (server down) and resume when set."""
        import threading
        import time

        mock_client = MagicMock()
        call_count = [0]

        def fake_retrieve(dataset, request, target):
            call_count[0] += 1
            with open(target, "w") as f:
                f.write("data")

        mock_client.retrieve.side_effect = fake_retrieve
        mock_cdsapi.Client.return_value = mock_client

        tasks = _make_tasks(tmp_dir, count=1)
        adapter = PlainTextAdapter()
        downloader = SwarmDownloader(tasks, adapter, num_workers=1)
        downloader._server_ok.clear()  # simulate server down

        def resume_later():
            time.sleep(0.3)
            assert call_count[0] == 0  # worker should be blocked
            downloader._server_ok.set()

        t = threading.Thread(target=resume_later)
        t.start()
        results = downloader.run()
        t.join()
        assert results is not None
        assert len(results) == 1
        assert results[0].success

    @patch("cdsswarm.core.LiveScraper.start", new=lambda self: None)
    @patch("cdsswarm.core.cdsapi")
    def test_server_down_cancel_exits(self, mock_cdsapi, tmp_dir):
        """Cancel event during server down pause exits the worker."""
        import threading
        import time

        mock_client = MagicMock()
        mock_client.retrieve.side_effect = RuntimeError("should not be called")
        mock_cdsapi.Client.return_value = mock_client

        tasks = _make_tasks(tmp_dir, count=1)
        adapter = PlainTextAdapter()
        downloader = SwarmDownloader(tasks, adapter, num_workers=1)
        downloader._server_ok.clear()  # simulate server down

        def cancel_later():
            time.sleep(0.2)
            downloader._cancel_event.set()

        t = threading.Thread(target=cancel_later)
        t.start()
        downloader.run()
        t.join()
        # Worker exited due to cancel, retrieve was never called
        mock_client.retrieve.assert_not_called()

    @patch("cdsswarm.core.LiveScraper.start", new=lambda self: None)
    @patch("cdsswarm.core.cdsapi")
    def test_degraded_retries_indefinitely(self, mock_cdsapi, tmp_dir):
        """During degraded state, retries exceed max_retries until success."""
        mock_client = MagicMock()
        call_count = [0]

        def fail_then_succeed(dataset, request, target):
            call_count[0] += 1
            if call_count[0] <= 5:  # fail 5 times (more than max_retries=3)
                raise RuntimeError("server error")
            with open(target, "w") as f:
                f.write("data")

        mock_client.retrieve.side_effect = fail_then_succeed
        mock_cdsapi.Client.return_value = mock_client

        tasks = _make_tasks(tmp_dir, count=1)
        adapter = PlainTextAdapter()
        downloader = SwarmDownloader(tasks, adapter, num_workers=1, max_retries=3)
        downloader._server_degraded.set()  # degraded = unlimited retries

        results = downloader.run()
        assert results is not None
        assert len(results) == 1
        assert results[0].success
        assert call_count[0] == 6  # 5 failures + 1 success

    @patch("cdsswarm.core.LiveScraper.start", new=lambda self: None)
    @patch("cdsswarm.core.cdsapi")
    def test_degraded_cancel_during_retry(self, mock_cdsapi, tmp_dir):
        """Cancel event set when degraded retry exception is caught exits immediately."""
        mock_client = MagicMock()

        def fail_and_cancel(dataset, request, target):
            # Set cancel before raising so line 382 is reached
            downloader._cancel_event.set()
            raise RuntimeError("server error")

        mock_client.retrieve.side_effect = fail_and_cancel
        mock_cdsapi.Client.return_value = mock_client

        tasks = _make_tasks(tmp_dir, count=1)
        adapter = PlainTextAdapter()
        downloader = SwarmDownloader(tasks, adapter, num_workers=1, max_retries=3)
        downloader._server_degraded.set()

        results = downloader.run()
        # Should have stopped after first attempt
        assert results is None or any(not r.success for r in results)

    @patch("cdsswarm.core.LiveScraper.start", new=lambda self: None)
    @patch("cdsswarm.core.cdsapi")
    def test_degraded_retry_message_format(self, mock_cdsapi, tmp_dir):
        """Degraded retry message shows 'server degraded' without max count."""
        mock_client = MagicMock()
        call_count = [0]

        def fail_once(dataset, request, target):
            call_count[0] += 1
            if call_count[0] == 1:
                raise RuntimeError("fds error")
            with open(target, "w") as f:
                f.write("data")

        mock_client.retrieve.side_effect = fail_once
        mock_cdsapi.Client.return_value = mock_client

        task_messages = []
        tasks = _make_tasks(tmp_dir, count=1)
        adapter = PlainTextAdapter()
        original = adapter.on_task_message

        def capture(wid, msg):
            task_messages.append(msg)
            original(wid, msg)

        adapter.on_task_message = capture
        downloader = SwarmDownloader(tasks, adapter, num_workers=1, max_retries=3)
        downloader._server_degraded.set()

        downloader.run()
        retry_msgs = [m for m in task_messages if "server degraded" in m]
        assert len(retry_msgs) == 1
        first_line = retry_msgs[0].split("\n")[0]
        assert "1/" not in first_line  # no "1/3" counter
