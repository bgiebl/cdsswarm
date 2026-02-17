"""Tests for the CDS metadata module."""

import hashlib
import os
import sys
import tempfile
import threading
import time
from unittest.mock import MagicMock, patch

import requests as http_requests

from cdsswarm._cds_metadata import (
    JobMetadata,
    MetadataPoller,
    QoSData,
    _parse_job_metadata,
    _parse_qos,
    compute_file_hash,
    compute_md5,
    fetch_job_metadata,
    fetch_job_results,
    parse_multihash,
    verify_checksum,
)


class TestParsJobMetadata:
    def test_full_response(self):
        data = {
            "jobID": "test-123",
            "progress": 72,
            "created": "2024-01-01T00:00:00Z",
            "started": "2024-01-01T00:05:00Z",
            "finished": "",
            "metadata": {
                "datasetMetadata": {
                    "title": "ERA5 hourly data on single levels",
                },
                "request": {
                    "labels": {"Variable": "2m temperature", "Year": "2024"},
                },
                "results": {
                    "asset": {
                        "value": {
                            "file:size": 95418,
                            "file:checksum": "abc123",
                        }
                    }
                },
            },
        }
        meta = _parse_job_metadata(data, "test-123")
        assert meta.job_id == "test-123"
        assert meta.progress == 72
        assert meta.created == "2024-01-01T00:00:00Z"
        assert meta.started == "2024-01-01T00:05:00Z"
        assert meta.finished == ""
        assert meta.dataset_title == "ERA5 hourly data on single levels"
        assert meta.request_labels == {"Variable": "2m temperature", "Year": "2024"}
        assert meta.file_size == 95418
        assert meta.file_checksum == "abc123"

    def test_empty_metadata(self):
        data = {"jobID": "test-456"}
        meta = _parse_job_metadata(data, "test-456")
        assert meta.job_id == "test-456"
        assert meta.progress == 0
        assert meta.created == ""
        assert meta.dataset_title == ""
        assert meta.request_labels == {}
        assert meta.file_size == 0
        assert meta.file_checksum == ""

    def test_null_values(self):
        data = {
            "progress": None,
            "created": None,
            "started": None,
            "metadata": {
                "datasetMetadata": {"title": None},
                "request": {"labels": None},
            },
        }
        meta = _parse_job_metadata(data, "x")
        assert meta.progress == 0
        assert meta.created == ""
        assert meta.started == ""
        assert meta.dataset_title == ""
        assert meta.request_labels == {}


class TestParseQos:
    def test_valid_qos(self):
        data = {
            "metadata": {
                "qos": {
                    "status": {
                        "limit": [
                            {
                                "info": "Max requests is 400",
                                "queued": 5220,
                                "running": 400,
                                "conclusion": "400",
                            }
                        ]
                    }
                }
            }
        }
        qos = _parse_qos(data)
        assert qos.queued == 5220
        assert qos.running == 400
        assert qos.limit == 400

    def test_empty_qos(self):
        data = {"metadata": {"qos": {"status": {}}}}
        qos = _parse_qos(data)
        assert qos.queued == 0
        assert qos.running == 0
        assert qos.limit == 0

    def test_no_metadata(self):
        data = {}
        qos = _parse_qos(data)
        assert qos.queued == 0
        assert qos.running == 0

    def test_empty_limit_array(self):
        data = {"metadata": {"qos": {"status": {"limit": []}}}}
        qos = _parse_qos(data)
        assert qos.queued == 0

    def test_non_numeric_conclusion(self):
        data = {
            "metadata": {
                "qos": {
                    "status": {
                        "limit": [{"queued": 10, "running": 5, "conclusion": "abc"}]
                    }
                }
            }
        }
        qos = _parse_qos(data)
        assert qos.queued == 10
        assert qos.running == 5
        assert qos.limit == 0


class TestComputeMd5:
    def test_known_hash(self):
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(b"hello world")
            f.flush()
            path = f.name
        try:
            result = compute_md5(path)
            expected = hashlib.md5(b"hello world").hexdigest()
            assert result == expected
        finally:
            os.unlink(path)

    def test_empty_file(self):
        with tempfile.NamedTemporaryFile(delete=False) as f:
            path = f.name
        try:
            result = compute_md5(path)
            expected = hashlib.md5(b"").hexdigest()
            assert result == expected
        finally:
            os.unlink(path)


class TestParseMultihash:
    def test_sha256(self):
        digest = hashlib.sha256(b"hello").digest()
        mh_hex = "1220" + digest.hex()
        algo, parsed_digest = parse_multihash(mh_hex)
        assert algo == "sha256"
        assert parsed_digest == digest

    def test_sha1(self):
        digest = hashlib.sha1(b"hello").digest()
        mh_hex = "1114" + digest.hex()
        algo, parsed_digest = parse_multihash(mh_hex)
        assert algo == "sha1"
        assert parsed_digest == digest

    def test_md5_varint(self):
        """MD5 code 0xd5 requires two-byte varint encoding."""
        digest = hashlib.md5(b"hello").digest()
        # 0xd5 as unsigned varint = bytes d5 01; length 16 = 10
        mh_hex = "d50110" + digest.hex()
        algo, parsed_digest = parse_multihash(mh_hex)
        assert algo == "md5"
        assert parsed_digest == digest

    def test_unsupported_code(self):
        import pytest

        with pytest.raises(ValueError, match="unsupported"):
            parse_multihash("ff0400000000")


class TestComputeFileHash:
    def test_sha256(self):
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(b"test data")
            path = f.name
        try:
            result = compute_file_hash(path, "sha256")
            assert result == hashlib.sha256(b"test data").digest()
        finally:
            os.unlink(path)


class TestVerifyChecksum:
    def test_pass_sha256_multihash(self):
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(b"test data")
            f.flush()
            path = f.name
        try:
            digest = hashlib.sha256(b"test data").digest()
            expected = "1220" + digest.hex()
            assert verify_checksum(path, expected) is True
        finally:
            os.unlink(path)

    def test_fail_sha256_multihash(self):
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(b"test data")
            f.flush()
            path = f.name
        try:
            wrong_digest = hashlib.sha256(b"other data").digest()
            expected = "1220" + wrong_digest.hex()
            assert verify_checksum(path, expected) is False
        finally:
            os.unlink(path)

    def test_fallback_bare_md5(self):
        """Falls back to bare MD5 hex for non-multihash strings."""
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(b"test data")
            f.flush()
            path = f.name
        try:
            expected = hashlib.md5(b"test data").hexdigest()
            assert verify_checksum(path, expected) is True
        finally:
            os.unlink(path)

    def test_fail_bare_md5(self):
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(b"test data")
            f.flush()
            path = f.name
        try:
            assert verify_checksum(path, "wrong_hash") is False
        finally:
            os.unlink(path)


class TestFetchJobMetadata:
    @patch("cdsswarm._cds_metadata.http_requests")
    def test_url_construction(self, mock_requests):
        inner = MagicMock()
        inner.url = "https://cds.example.com/api"
        inner.verify = True
        inner._get_headers.return_value = {"Authorization": "Bearer token"}
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"jobID": "test-id", "progress": 50}
        mock_requests.get.return_value = mock_resp

        with patch("cdsswarm._cds_metadata.ds_config", create=True):
            meta, qos = fetch_job_metadata(inner, "test-id")

        call_url = mock_requests.get.call_args[0][0]
        assert "jobs/test-id" in call_url
        assert qos is None
        assert meta.progress == 50

    @patch("cdsswarm._cds_metadata.http_requests")
    def test_with_qos_and_request(self, mock_requests):
        inner = MagicMock()
        inner.url = "https://cds.example.com/api"
        inner.verify = True
        inner._get_headers.return_value = {}
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "jobID": "test-id",
            "metadata": {
                "qos": {
                    "status": {
                        "limit": [{"queued": 100, "running": 50, "conclusion": "400"}]
                    }
                },
                "request": {"labels": {"Variable": "Temperature"}},
            },
        }
        mock_requests.get.return_value = mock_resp

        meta, qos = fetch_job_metadata(
            inner, "test-id", include_qos=True, include_request=True
        )
        call_url = mock_requests.get.call_args[0][0]
        assert "qos=true" in call_url
        assert "request=true" in call_url
        assert qos is not None
        assert qos.queued == 100
        assert meta.request_labels == {"Variable": "Temperature"}


class TestFetchJobResults:
    @patch("cdsswarm._cds_metadata.http_requests")
    def test_parses_results(self, mock_requests):
        inner = MagicMock()
        inner.url = "https://cds.example.com/api"
        inner.verify = True
        inner._get_headers.return_value = {}
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "asset": {
                "value": {
                    "file:size": 95418,
                    "file:checksum": "abc123def",
                }
            }
        }
        mock_requests.get.return_value = mock_resp

        size, checksum = fetch_job_results(inner, "job-id")
        assert size == 95418
        assert checksum == "abc123def"

    @patch("cdsswarm._cds_metadata.http_requests")
    def test_missing_fields(self, mock_requests):
        inner = MagicMock()
        inner.url = "https://cds.example.com/api"
        inner.verify = True
        inner._get_headers.return_value = {}
        mock_resp = MagicMock()
        mock_resp.json.return_value = {}
        mock_requests.get.return_value = mock_resp

        size, checksum = fetch_job_results(inner, "job-id")
        assert size == 0
        assert checksum == ""


class TestMetadataPoller:
    def test_start_stop(self):
        adapter = MagicMock()
        state = MagicMock()
        state.lock = threading.Lock()
        state.active_requests = {}
        state.task_worker_map = {}
        cancel = threading.Event()
        poller = MetadataPoller(adapter, state, cancel, poll_interval=0.05)
        poller.start()
        assert poller._thread is not None
        assert poller._thread.is_alive()
        cancel.set()
        poller._thread.join(timeout=2)
        assert not poller._thread.is_alive()

    def test_register_client(self):
        adapter = MagicMock()
        state = MagicMock()
        cancel = threading.Event()
        poller = MetadataPoller(adapter, state, cancel)

        # No inner client
        mock_client1 = MagicMock(spec=[])
        del mock_client1.client  # ensure no .client attr
        poller.register_client(mock_client1)
        assert poller._inner_client is None

        # With inner client
        mock_inner = MagicMock()
        mock_inner._get_headers = MagicMock()
        mock_client2 = MagicMock()
        mock_client2.client = mock_inner
        poller.register_client(mock_client2)
        assert poller._inner_client is mock_inner

        # Second registration is ignored
        mock_inner2 = MagicMock()
        mock_inner2._get_headers = MagicMock()
        mock_client3 = MagicMock()
        mock_client3.client = mock_inner2
        poller.register_client(mock_client3)
        assert poller._inner_client is mock_inner  # Still first

    @patch("cdsswarm._cds_metadata.fetch_job_metadata")
    def test_poll_fires_callbacks(self, mock_fetch):
        adapter = MagicMock()
        state = MagicMock()
        state.lock = threading.Lock()
        state.active_requests = {"target.grib": ("rid-1", MagicMock())}
        state.task_worker_map = {"target.grib": 0}
        cancel = threading.Event()

        meta = JobMetadata(
            job_id="rid-1",
            progress=50,
            dataset_title="ERA5",
            request_labels={"Variable": "T"},
            file_size=1000,
        )
        qos = QoSData(queued=100, running=50, limit=400)
        mock_fetch.return_value = (meta, qos)

        poller = MetadataPoller(adapter, state, cancel, poll_interval=0.05)
        inner = MagicMock()
        inner._get_headers = MagicMock()
        poller._inner_client = inner

        poller._poll_once(inner)

        adapter.on_task_server_progress.assert_called_with(0, 50)
        adapter.on_task_file_size.assert_called_with(0, 1000)
        adapter.on_task_dataset_title.assert_called_with(0, "ERA5")
        adapter.on_task_request_labels.assert_called_with(0, {"Variable": "T"})
        adapter.on_qos_update.assert_called_with(100, 50, 400)

    @patch("cdsswarm._cds_metadata.fetch_job_metadata")
    def test_poll_skips_unchanged(self, mock_fetch):
        adapter = MagicMock()
        state = MagicMock()
        state.lock = threading.Lock()
        state.active_requests = {"target.grib": ("rid-1", MagicMock())}
        state.task_worker_map = {"target.grib": 0}
        cancel = threading.Event()

        meta = JobMetadata(job_id="rid-1", progress=50)
        mock_fetch.return_value = (meta, None)

        poller = MetadataPoller(adapter, state, cancel)
        inner = MagicMock()
        poller._inner_client = inner

        poller._poll_once(inner)
        adapter.on_task_server_progress.assert_called_once()

        # Poll again with same data — should not fire again
        adapter.reset_mock()
        poller._poll_once(inner)
        adapter.on_task_server_progress.assert_not_called()

    @patch("cdsswarm._cds_metadata.fetch_job_metadata")
    def test_poll_handles_exception(self, mock_fetch):
        adapter = MagicMock()
        state = MagicMock()
        state.lock = threading.Lock()
        state.active_requests = {"target.grib": ("rid-1", MagicMock())}
        state.task_worker_map = {"target.grib": 0}
        cancel = threading.Event()

        mock_fetch.side_effect = RuntimeError("network error")

        poller = MetadataPoller(adapter, state, cancel)
        inner = MagicMock()
        poller._inner_client = inner

        # Should not raise
        poller._poll_once(inner)
        adapter.on_task_server_progress.assert_not_called()

    def test_poll_skips_when_no_client(self):
        adapter = MagicMock()
        state = MagicMock()
        state.lock = threading.Lock()
        cancel = threading.Event()
        poller = MetadataPoller(adapter, state, cancel, poll_interval=0.05)
        poller.start()
        time.sleep(0.15)
        cancel.set()
        poller._thread.join(timeout=2)
        # No client registered, so no callbacks should have fired
        adapter.on_task_server_progress.assert_not_called()

    @patch("cdsswarm._cds_metadata.fetch_job_metadata")
    def test_run_polls_with_registered_client(self, mock_fetch):
        """_run loop calls _poll_once when inner client is registered."""
        adapter = MagicMock()
        state = MagicMock()
        state.lock = threading.Lock()
        state.active_requests = {"target.grib": ("rid-1", MagicMock())}
        state.task_worker_map = {"target.grib": 0}
        cancel = threading.Event()

        meta = JobMetadata(job_id="rid-1", progress=50)
        mock_fetch.return_value = (meta, None)

        poller = MetadataPoller(adapter, state, cancel, poll_interval=0.05)
        # Register inner client before starting
        mock_inner = MagicMock()
        mock_inner._get_headers = MagicMock()
        mock_client = MagicMock()
        mock_client.client = mock_inner
        poller.register_client(mock_client)

        poller.start()
        time.sleep(0.15)
        cancel.set()
        poller._thread.join(timeout=2)

        # _poll_once was called via _run with the registered inner client
        mock_fetch.assert_called()
        adapter.on_task_server_progress.assert_called()

    def test_poll_once_no_active_requests(self):
        """Empty active_requests → early return, no fetch calls."""
        adapter = MagicMock()
        state = MagicMock()
        state.lock = threading.Lock()
        state.active_requests = {}
        state.task_worker_map = {}
        cancel = threading.Event()
        poller = MetadataPoller(adapter, state, cancel)
        inner = MagicMock()

        with patch("cdsswarm._cds_metadata.fetch_job_metadata") as mock_fetch:
            poller._poll_once(inner)
            mock_fetch.assert_not_called()

    def test_poll_once_missing_worker_mapping(self):
        """Target in active_requests but not in task_worker_map → skipped."""
        adapter = MagicMock()
        state = MagicMock()
        state.lock = threading.Lock()
        state.active_requests = {"target.grib": ("rid-1", MagicMock())}
        state.task_worker_map = {}  # No mapping for target.grib
        cancel = threading.Event()
        poller = MetadataPoller(adapter, state, cancel)
        inner = MagicMock()

        with patch("cdsswarm._cds_metadata.fetch_job_metadata") as mock_fetch:
            poller._poll_once(inner)
            mock_fetch.assert_not_called()

    def test_poll_once_request_exception(self):
        """fetch_job_metadata raises RequestException → no crash, continues."""
        adapter = MagicMock()
        state = MagicMock()
        state.lock = threading.Lock()
        state.active_requests = {"target.grib": ("rid-1", MagicMock())}
        state.task_worker_map = {"target.grib": 0}
        cancel = threading.Event()
        poller = MetadataPoller(adapter, state, cancel)
        inner = MagicMock()

        with patch(
            "cdsswarm._cds_metadata.fetch_job_metadata",
            side_effect=http_requests.RequestException("connection refused"),
        ):
            poller._poll_once(inner)  # Should not raise

        adapter.on_task_server_progress.assert_not_called()


class TestFetchImportFallback:
    def test_fetch_job_metadata_no_ecmwf(self):
        """When ecmwf.datastores is not importable, api_version defaults to v1."""
        with patch.dict(
            sys.modules,
            {
                "ecmwf": None,
                "ecmwf.datastores": None,
                "ecmwf.datastores.config": None,
            },
        ):
            inner = MagicMock()
            inner.url = "https://cds.example.com/api"
            inner.verify = True
            inner._get_headers.return_value = {}
            mock_resp = MagicMock()
            mock_resp.json.return_value = {"jobID": "test-id"}
            with patch("cdsswarm._cds_metadata.http_requests") as mock_req:
                mock_req.get.return_value = mock_resp
                meta, qos = fetch_job_metadata(inner, "test-id")
            url = mock_req.get.call_args[0][0]
            assert "/v1/" in url

    def test_fetch_job_results_no_ecmwf(self):
        """When ecmwf.datastores is not importable, api_version defaults to v1."""
        with patch.dict(
            sys.modules,
            {
                "ecmwf": None,
                "ecmwf.datastores": None,
                "ecmwf.datastores.config": None,
            },
        ):
            inner = MagicMock()
            inner.url = "https://cds.example.com/api"
            inner.verify = True
            inner._get_headers.return_value = {}
            mock_resp = MagicMock()
            mock_resp.json.return_value = {}
            with patch("cdsswarm._cds_metadata.http_requests") as mock_req:
                mock_req.get.return_value = mock_resp
                size, checksum = fetch_job_results(inner, "test-id")
            url = mock_req.get.call_args[0][0]
            assert "/v1/" in url
