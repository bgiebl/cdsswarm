"""Tests for CDS utility functions."""

from unittest.mock import MagicMock, patch

from cdsswarm._cds_utils import (
    cancel_cds_request,
    install_progress_router,
    normalize_request,
    parse_cds_status,
    parse_request_id,
    uninstall_progress_router,
)


class TestParseCdsStatus:
    def test_old_api_queued(self):
        assert parse_cds_status("Request is queued") == "accepted"

    def test_old_api_running(self):
        assert parse_cds_status("Request is running") == "running"

    def test_old_api_completed(self):
        assert parse_cds_status("Request is completed") == "successful"

    def test_old_api_failed(self):
        assert parse_cds_status("Request is failed") == "failed"

    def test_new_api_accepted(self):
        assert parse_cds_status("status has been updated to accepted") == "accepted"

    def test_new_api_successful(self):
        assert parse_cds_status("status has been updated to successful") == "successful"

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
        assert normalize_request(inp) == {"variable": ["2m_temperature"], "year": ["2024"]}

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
        with patch.dict(sys.modules, {
            "ecmwf": ecmwf_mod,
            "ecmwf.datastores": ecmwf_ds,
            "ecmwf.datastores.config": ecmwf_config,
        }):
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

    def test_tqdm_not_importable(self):
        """Returns empty dict when tqdm is not available."""
        import sys
        tqdm_mod = sys.modules.get("tqdm")
        try:
            sys.modules["tqdm"] = None  # type: ignore
            # Re-import to test the ImportError path
            from importlib import reload
            import cdsswarm._cds_utils as utils_mod
            import threading
            result = utils_mod.install_progress_router(MagicMock(), {}, threading.Lock())
            assert result == {}
        finally:
            if tqdm_mod is not None:
                sys.modules["tqdm"] = tqdm_mod
            else:
                del sys.modules["tqdm"]
