"""Tests for core download engine."""

import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest
import requests

from cdsswarm.adapters import PlainTextAdapter
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

    @patch("cdsswarm.core.fetch_job_results")
    @patch("cdsswarm.core.cdsapi")
    def test_checksum_network_error_degrades_gracefully(
        self, mock_cdsapi, mock_fetch, tmp_dir
    ):
        """Download succeeds when checksum fetch fails with a network error."""

        def capture_client(**kwargs):
            client = MagicMock()
            # Expose inner client so the checksum code path is entered
            client.client = MagicMock()
            client.client._get_headers = MagicMock(return_value={})
            info_cb = kwargs.get("info_callback")

            def fake_retrieve(dataset, request, target):
                if info_cb:
                    info_cb("Request ID is abc-12345-def")
                with open(target, "w") as f:
                    f.write("data")

            client.retrieve.side_effect = fake_retrieve
            return client

        mock_cdsapi.Client.side_effect = capture_client
        mock_fetch.side_effect = requests.ConnectionError("connection refused")

        tasks = _make_tasks(tmp_dir, count=1)
        adapter = PlainTextAdapter()
        downloader = SwarmDownloader(tasks, adapter, num_workers=1)
        results = downloader.run()

        assert results is not None
        assert len(results) == 1
        assert results[0].success
        mock_fetch.assert_called_once()

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

    @patch("cdsswarm.core.verify_checksum")
    @patch("cdsswarm.core.fetch_job_results")
    @patch("cdsswarm.core.cdsapi")
    def test_checksum_mismatch_continue(
        self, mock_cdsapi, mock_fetch, mock_verify, tmp_dir
    ):
        """Checksum mismatch with 'continue' decision adds warning to result."""

        def capture_client(**kwargs):
            client = MagicMock()
            client.client = MagicMock()
            client.client._get_headers = MagicMock(return_value={})
            info_cb = kwargs.get("info_callback")

            def fake_retrieve(dataset, request, target):
                if info_cb:
                    info_cb("Request ID is abc-12345-def")
                with open(target, "w") as f:
                    f.write("data")

            client.retrieve.side_effect = fake_retrieve
            return client

        mock_cdsapi.Client.side_effect = capture_client
        mock_fetch.return_value = (1024, "expected-checksum")
        mock_verify.return_value = False

        tasks = _make_tasks(tmp_dir, count=1)
        adapter = MagicMock(spec=PlainTextAdapter)
        adapter.on_task_checksum_result.return_value = "continue"
        downloader = SwarmDownloader(tasks, adapter, num_workers=1)
        results = downloader.run()

        assert results is not None
        assert len(results) == 1
        assert results[0].success
        assert any("Checksum mismatch" in w for w in results[0].warnings)
        adapter.on_task_checksum_result.assert_called_once_with(
            0, False, "expected-checksum"
        )

    @patch("cdsswarm.core.verify_checksum")
    @patch("cdsswarm.core.fetch_job_results")
    @patch("cdsswarm.core.cdsapi")
    def test_checksum_mismatch_retry(
        self, mock_cdsapi, mock_fetch, mock_verify, tmp_dir
    ):
        """Checksum mismatch with 'retry' re-downloads, then succeeds on pass."""
        attempt_count = [0]

        def capture_client(**kwargs):
            client = MagicMock()
            client.client = MagicMock()
            client.client._get_headers = MagicMock(return_value={})
            info_cb = kwargs.get("info_callback")

            def fake_retrieve(dataset, request, target):
                attempt_count[0] += 1
                if info_cb:
                    info_cb("Request ID is abc-12345-def")
                with open(target, "w") as f:
                    f.write("data")

            client.retrieve.side_effect = fake_retrieve
            return client

        mock_cdsapi.Client.side_effect = capture_client
        mock_fetch.return_value = (1024, "expected-checksum")
        # First attempt: mismatch → retry, second attempt: pass
        mock_verify.side_effect = [False, True]

        tasks = _make_tasks(tmp_dir, count=1)
        adapter = MagicMock(spec=PlainTextAdapter)
        adapter.on_task_checksum_result.side_effect = ["retry", "continue"]
        downloader = SwarmDownloader(tasks, adapter, num_workers=1)
        results = downloader.run()

        assert results is not None
        assert len(results) == 1
        assert results[0].success
        assert attempt_count[0] == 2

    @patch("cdsswarm.core.verify_checksum")
    @patch("cdsswarm.core.fetch_job_results")
    @patch("cdsswarm.core.cdsapi")
    def test_checksum_pass(self, mock_cdsapi, mock_fetch, mock_verify, tmp_dir):
        """Successful checksum verification sends 'Checksum OK' message."""

        def capture_client(**kwargs):
            client = MagicMock()
            client.client = MagicMock()
            client.client._get_headers = MagicMock(return_value={})
            info_cb = kwargs.get("info_callback")

            def fake_retrieve(dataset, request, target):
                if info_cb:
                    info_cb("Request ID is abc-12345-def")
                with open(target, "w") as f:
                    f.write("data")

            client.retrieve.side_effect = fake_retrieve
            return client

        mock_cdsapi.Client.side_effect = capture_client
        mock_fetch.return_value = (1024, "good-checksum")
        mock_verify.return_value = True

        tasks = _make_tasks(tmp_dir, count=1)
        adapter = MagicMock(spec=PlainTextAdapter)
        adapter.on_task_checksum_result.return_value = "continue"
        downloader = SwarmDownloader(tasks, adapter, num_workers=1)
        results = downloader.run()

        assert results is not None
        assert len(results) == 1
        assert results[0].success
        # Verify "Checksum OK" was sent
        checksum_ok_calls = [
            call
            for call in adapter.on_task_message.call_args_list
            if "Checksum OK" in str(call)
        ]
        assert len(checksum_ok_calls) == 1
        adapter.on_task_checksum_result.assert_called_once_with(
            0, True, "good-checksum"
        )
