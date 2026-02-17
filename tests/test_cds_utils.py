"""Tests for CDS utility functions."""

from unittest.mock import MagicMock, patch

from cdsswarm._cds_utils import (
    _dict_key,
    _scan_jobs,
    cancel_cds_request,
    find_reusable_jobs,
    install_progress_router,
    normalize_request,
    parse_cds_status,
    parse_request_id,
    uninstall_progress_router,
)
from cdsswarm.status import WorkerStatus


class TestParseCdsStatus:
    def test_old_api_queued(self):
        assert parse_cds_status("Request is queued") is WorkerStatus.ACCEPTED

    def test_old_api_running(self):
        assert parse_cds_status("Request is running") is WorkerStatus.RUNNING

    def test_old_api_completed(self):
        assert parse_cds_status("Request is completed") is WorkerStatus.SUCCESSFUL

    def test_old_api_failed(self):
        assert parse_cds_status("Request is failed") is WorkerStatus.FAILED

    def test_new_api_accepted(self):
        assert (
            parse_cds_status("status has been updated to accepted")
            is WorkerStatus.ACCEPTED
        )

    def test_new_api_successful(self):
        assert (
            parse_cds_status("status has been updated to successful")
            is WorkerStatus.SUCCESSFUL
        )

    def test_no_match(self):
        assert parse_cds_status("some random message") is None

    def test_unknown_state(self):
        assert parse_cds_status("Request is unknown_state") is None


class TestNormalizeRequest:
    def test_scalar_to_list(self):
        assert normalize_request({"year": "2024"}) == {"year": ["2024"]}

    def test_int_to_string(self):
        assert normalize_request({"area": [55, 5]}) == {"area": ["55", "5"]}

    def test_list_order_preserved(self):
        result = normalize_request({"area": [55, -10, 30, 40]})
        assert result == {"area": ["55", "-10", "30", "40"]}

    def test_already_normalized(self):
        inp = {"variable": ["2m_temperature"], "year": ["2024"]}
        assert normalize_request(inp) == {
            "variable": ["2m_temperature"],
            "year": ["2024"],
        }

    def test_empty_dict(self):
        assert normalize_request({}) == {}

    def test_keys_sorted(self):
        result = normalize_request({"z": "1", "a": "2"})
        assert list(result.keys()) == ["a", "z"]

    def test_mixed_types(self):
        result = normalize_request({"year": "2024", "month": ["01", "02"], "day": 15})
        assert result == {
            "day": ["15"],
            "month": ["01", "02"],
            "year": ["2024"],
        }


class TestParseRequestId:
    def test_old_api_format(self):
        msg = "Request ID is abc-123-def, sleep 10"
        assert parse_request_id(msg) == "abc-123-def"

    def test_new_api_format(self):
        msg = "Request ID is 550e8400-e29b-41d4-a716-446655440000"
        assert parse_request_id(msg) == "550e8400-e29b-41d4-a716-446655440000"

    def test_no_match(self):
        assert parse_request_id("no request id here") is None


class TestCancelCdsRequest:
    def test_old_style_client(self):
        """Old cdsapi client uses DELETE /tasks/{id}."""
        client = MagicMock(spec=["url", "session", "verify"])
        client.url = "https://cds.example.com/api/v2"
        client.verify = True
        mock_response = MagicMock()
        client.session.delete.return_value = mock_response

        cancel_cds_request(client, "task-123")

        client.session.delete.assert_called_once_with(
            "https://cds.example.com/api/v2/tasks/task-123",
            verify=True,
            timeout=10,
        )
        mock_response.raise_for_status.assert_called_once()

    @patch("cdsswarm._cds_utils.http_requests")
    def test_new_style_client(self, mock_http):
        """New CADS client uses POST /jobs/delete."""
        import sys

        inner = MagicMock()
        inner.url = "https://cds.example.com"
        inner._get_headers.return_value = {"Authorization": "Bearer token"}
        inner.verify = True

        client = MagicMock()
        client.client = inner

        mock_response = MagicMock()
        mock_session = MagicMock()
        mock_session.post.return_value = mock_response
        mock_http.Session.return_value = mock_session

        # Patch sys.modules so `from ecmwf.datastores import config` succeeds
        ecmwf_config = MagicMock()
        ecmwf_config.SUPPORTED_API_VERSION = "v1"
        ecmwf_ds = MagicMock()
        ecmwf_ds.config = ecmwf_config
        ecmwf_mod = MagicMock()
        ecmwf_mod.datastores = ecmwf_ds
        with patch.dict(
            sys.modules,
            {
                "ecmwf": ecmwf_mod,
                "ecmwf.datastores": ecmwf_ds,
                "ecmwf.datastores.config": ecmwf_config,
            },
        ):
            cancel_cds_request(client, "job-456")

        mock_session.post.assert_called_once()
        call_args = mock_session.post.call_args
        assert "/jobs/delete" in call_args[0][0]
        assert call_args[1]["json"] == {"job_ids": ["job-456"]}


class TestInstallProgressRouter:
    def test_patches_and_restores_tqdm(self):
        """install_progress_router patches tqdm, uninstall restores it."""
        import threading
        import tqdm

        adapter = MagicMock()
        worker_id_map = {}
        lock = threading.Lock()

        original_tqdm = tqdm.tqdm
        router_state = install_progress_router(adapter, worker_id_map, lock)

        assert router_state  # Should not be empty
        assert "patches" in router_state
        assert "devnull" in router_state
        assert len(router_state["patches"]) > 0

        # tqdm.tqdm should be patched
        assert tqdm.tqdm is not original_tqdm

        uninstall_progress_router(router_state)

        # tqdm.tqdm should be restored
        assert tqdm.tqdm is original_tqdm
        # devnull should be closed
        assert router_state["devnull"].closed

    def test_uninstall_empty_state(self):
        """uninstall_progress_router handles empty/None state."""
        uninstall_progress_router({})
        uninstall_progress_router(None)  # type: ignore

    def test_progress_tqdm_routes_updates(self):
        """_ProgressTqdm routes download progress through the adapter."""
        import threading
        import tqdm

        adapter = MagicMock()
        tid = threading.current_thread().ident
        worker_id_map = {tid: 5}
        lock = threading.Lock()

        router_state = install_progress_router(adapter, worker_id_map, lock)
        try:
            # tqdm.tqdm is now _ProgressTqdm
            bar = tqdm.tqdm(total=100 * 1024 * 1024)
            bar.update(50 * 1024 * 1024)  # 50%
            bar.close()

            adapter.on_task_message.assert_called()
            adapter.on_task_progress.assert_called()
            # Check worker_id was resolved from map
            progress_call = adapter.on_task_progress.call_args
            assert progress_call[0][0] == 5  # worker_id
        finally:
            uninstall_progress_router(router_state)

    def test_progress_tqdm_deduplicates_same_pct(self):
        """_ProgressTqdm skips update when percentage hasn't changed."""
        import threading
        import tqdm

        adapter = MagicMock()
        lock = threading.Lock()

        router_state = install_progress_router(adapter, {}, lock)
        try:
            bar = tqdm.tqdm(total=1000)
            # Two small updates that stay at 0%
            bar.update(1)
            bar.update(1)
            bar.close()

            # on_task_progress should be called only once for 0%
            assert adapter.on_task_progress.call_count == 1
        finally:
            uninstall_progress_router(router_state)

    def test_progress_tqdm_no_total(self):
        """_ProgressTqdm with no total doesn't crash on update."""
        import threading
        import tqdm

        adapter = MagicMock()
        lock = threading.Lock()

        router_state = install_progress_router(adapter, {}, lock)
        try:
            bar = tqdm.tqdm(total=0)
            bar.update(100)
            bar.close()

            adapter.on_task_progress.assert_not_called()
        finally:
            uninstall_progress_router(router_state)

    def test_uninstall_setattr_exception(self):
        """uninstall_progress_router handles setattr exceptions gracefully."""

        class FrozenModule:
            def __setattr__(self, name, value):
                raise AttributeError("frozen")

        state = {
            "patches": [(FrozenModule(), "tqdm", None)],
            "devnull": MagicMock(),
        }
        # Should not raise
        uninstall_progress_router(state)

    def test_uninstall_close_exception(self):
        """uninstall_progress_router handles devnull close exception."""
        devnull = MagicMock()
        devnull.close.side_effect = OSError("already closed")
        state = {"patches": [], "devnull": devnull}
        # Should not raise
        uninstall_progress_router(state)

    def test_tqdm_not_importable(self):
        """Returns empty dict when tqdm is not available."""
        import sys

        tqdm_mod = sys.modules.get("tqdm")
        try:
            sys.modules["tqdm"] = None  # type: ignore
            # Re-import to test the ImportError path
            import cdsswarm._cds_utils as utils_mod
            import threading

            result = utils_mod.install_progress_router(
                MagicMock(), {}, threading.Lock()
            )
            assert result == {}
        finally:
            if tqdm_mod is not None:
                sys.modules["tqdm"] = tqdm_mod
            else:
                del sys.modules["tqdm"]

    def test_progress_router_skips_none_modules(self):
        """install_progress_router skips sys.modules entries that are None."""
        import sys
        import threading

        adapter = MagicMock()
        lock = threading.Lock()

        # Insert a None module under cdsapi namespace
        key = "cdsapi._test_none_sentinel"
        try:
            sys.modules[key] = None  # type: ignore
            router_state = install_progress_router(adapter, {}, lock)
            # Should not crash — just skips None modules
            assert isinstance(router_state, dict)
        finally:
            sys.modules.pop(key, None)
            uninstall_progress_router(router_state)

    def test_install_progress_router_setattr_exception(self):
        """install_progress_router handles modules where setattr raises."""
        import sys
        import threading
        import types

        adapter = MagicMock()
        lock = threading.Lock()

        # Create a module-like object that raises on setattr for tqdm attr
        frozen = types.ModuleType("cdsapi._frozen_test")
        import tqdm

        frozen.tqdm = tqdm.tqdm  # Has the original tqdm reference

        class FrozenMeta(type(frozen)):
            def __setattr__(self, name, value):
                if name == "tqdm":
                    raise AttributeError("frozen")
                super().__setattr__(name, value)

        frozen.__class__ = FrozenMeta

        key = "cdsapi._frozen_test"
        try:
            sys.modules[key] = frozen
            router_state = install_progress_router(adapter, {}, lock)
            # Should not crash
            assert isinstance(router_state, dict)
        finally:
            sys.modules.pop(key, None)
            uninstall_progress_router(router_state)


class TestFindReusableJobs:
    def test_old_client_returns_empty(self):
        """Old-style client (no .client attr) returns empty dict."""
        client = MagicMock(spec=["url", "session"])
        tasks = [MagicMock(dataset="ds", request={"year": "2024"}, target="out.grib")]
        result = find_reusable_jobs(client, tasks)
        assert result == {}

    def test_scan_jobs_api_failure(self):
        """get_jobs() raising does not crash, reuse_map stays empty."""
        inner = MagicMock()
        inner.get_jobs.side_effect = RuntimeError("API error")
        reuse_map = {}
        _scan_jobs(inner, "successful", 200, {"ds"}, {}, reuse_map)
        assert reuse_map == {}

    def test_scan_jobs_matches_task(self):
        """Job with matching processID and request populates reuse_map."""
        norm = normalize_request({"year": "2024", "variable": "temp"})
        key = ("ds", _dict_key(norm))
        needed = {key: ["out.grib"]}

        job_response = MagicMock()
        job_response._json_dict = {"jobs": [{"processID": "ds", "jobID": "job-abc"}]}

        remote = MagicMock()
        remote.request = {"year": "2024", "variable": "temp"}

        inner = MagicMock()
        inner.get_jobs.return_value = job_response
        inner.get_remote.return_value = remote

        reuse_map = {}
        _scan_jobs(inner, "successful", 200, {"ds"}, needed, reuse_map)

        assert reuse_map == {"out.grib": "job-abc"}
        assert key not in needed  # consumed

    def test_scan_jobs_skips_wrong_dataset(self):
        """Job with non-matching processID is skipped."""
        norm = normalize_request({"year": "2024"})
        key = ("ds", _dict_key(norm))
        needed = {key: ["out.grib"]}

        job_response = MagicMock()
        job_response._json_dict = {
            "jobs": [{"processID": "other-ds", "jobID": "job-abc"}]
        }

        inner = MagicMock()
        inner.get_jobs.return_value = job_response

        reuse_map = {}
        _scan_jobs(inner, "successful", 200, {"ds"}, needed, reuse_map)

        assert reuse_map == {}
        assert key in needed  # not consumed

    def test_scan_jobs_get_remote_failure(self):
        """get_remote() raising for one job skips it, no crash."""
        norm = normalize_request({"year": "2024"})
        key = ("ds", _dict_key(norm))
        needed = {key: ["out.grib"]}

        job_response = MagicMock()
        job_response._json_dict = {"jobs": [{"processID": "ds", "jobID": "job-abc"}]}

        inner = MagicMock()
        inner.get_jobs.return_value = job_response
        inner.get_remote.side_effect = RuntimeError("not found")

        reuse_map = {}
        _scan_jobs(inner, "successful", 200, {"ds"}, needed, reuse_map)

        assert reuse_map == {}

    def test_scan_jobs_skips_no_job_id(self):
        """Job dict with no jobID is skipped."""
        norm = normalize_request({"year": "2024"})
        key = ("ds", _dict_key(norm))
        needed = {key: ["out.grib"]}

        job_response = MagicMock()
        job_response._json_dict = {
            "jobs": [{"processID": "ds"}]  # no jobID
        }

        inner = MagicMock()
        inner.get_jobs.return_value = job_response

        reuse_map = {}
        _scan_jobs(inner, "successful", 200, {"ds"}, needed, reuse_map)

        assert reuse_map == {}


class TestFindReusableJobsScanException:
    @patch("cdsswarm._cds_utils._scan_jobs")
    def test_scan_exception_continues_to_next_status(self, mock_scan):
        """Exception in _scan_jobs propagates to find_reusable_jobs except block."""
        # First call (successful) raises, second (running) succeeds, third (accepted)
        mock_scan.side_effect = [
            RuntimeError("scan failed"),
            None,
            None,
        ]

        inner = MagicMock()
        inner.get_remote = MagicMock()
        client = MagicMock()
        client.client = inner

        task = MagicMock(dataset="ds", request={"year": "2024"}, target="out.grib")
        result = find_reusable_jobs(client, [task])
        # Should not crash, just returns empty (mock doesn't populate reuse_map)
        assert result == {}
        # _scan_jobs was called multiple times (exception didn't stop iteration)
        assert mock_scan.call_count >= 2

    @patch("cdsswarm._cds_utils._scan_jobs")
    def test_early_break_when_all_matched(self, mock_scan):
        """find_reusable_jobs breaks early when all tasks are matched."""

        def populate_reuse_map(inner, status, limit, datasets, needed, reuse_map):
            # Consume all needed entries on first call
            for key in list(needed):
                targets = needed.pop(key)
                for t in targets:
                    reuse_map[t] = "job-matched"

        mock_scan.side_effect = populate_reuse_map

        inner = MagicMock()
        inner.get_remote = MagicMock()
        client = MagicMock()
        client.client = inner

        task = MagicMock(dataset="ds", request={"year": "2024"}, target="out.grib")
        result = find_reusable_jobs(client, [task])
        assert result == {"out.grib": "job-matched"}
        # Should stop after first status since all tasks matched
        assert mock_scan.call_count == 1


class TestCancelImportError:
    def test_no_ecmwf_datastores_falls_back_to_old_style(self):
        """When ecmwf.datastores is not importable, falls through to old-style DELETE."""
        import sys

        client = MagicMock(spec=["url", "session", "verify"])
        client.url = "https://cds.example.com/api/v2"
        client.verify = True
        mock_response = MagicMock()
        client.session.delete.return_value = mock_response

        with patch.dict(
            sys.modules,
            {
                "ecmwf": None,
                "ecmwf.datastores": None,
                "ecmwf.datastores.config": None,
            },
        ):
            cancel_cds_request(client, "task-999")

        client.session.delete.assert_called_once_with(
            "https://cds.example.com/api/v2/tasks/task-999",
            verify=True,
            timeout=10,
        )
        mock_response.raise_for_status.assert_called_once()
