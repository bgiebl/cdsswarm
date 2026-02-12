"""Tests for the public Python API."""

import os
import tempfile
from unittest.mock import MagicMock, patch

import cdsswarm
from cdsswarm import Result, Task, download


class TestPublicAPI:
    def test_task_exported(self):
        assert hasattr(cdsswarm, "Task")

    def test_result_exported(self):
        assert hasattr(cdsswarm, "Result")

    def test_download_exported(self):
        assert hasattr(cdsswarm, "download")
        assert callable(cdsswarm.download)

    @patch("cdsswarm.core.cdsapi")
    def test_download_basic(self, mock_cdsapi):
        """download() returns a list of Results."""
        with tempfile.TemporaryDirectory() as tmp:
            mock_client = MagicMock()

            def fake_retrieve(dataset, request, target):
                with open(target, "w") as f:
                    f.write("data")

            mock_client.retrieve.side_effect = fake_retrieve
            mock_cdsapi.Client.return_value = mock_client

            tasks = [
                Task("ds", {"variable": ["t2m"]}, os.path.join(tmp, "out.grib")),
            ]
            results = download(tasks, num_workers=1)

            assert len(results) == 1
            assert results[0].success
            assert isinstance(results[0], Result)

    @patch("cdsswarm.core.cdsapi")
    def test_download_with_callback(self, mock_cdsapi):
        """download() routes messages through on_message callback."""
        with tempfile.TemporaryDirectory() as tmp:
            mock_client = MagicMock()

            def fake_retrieve(dataset, request, target):
                with open(target, "w") as f:
                    f.write("data")

            mock_client.retrieve.side_effect = fake_retrieve
            mock_cdsapi.Client.return_value = mock_client

            messages = []
            tasks = [
                Task("ds", {"variable": ["t2m"]}, os.path.join(tmp, "out.grib")),
            ]
            results = download(tasks, num_workers=1, on_message=messages.append)

            assert len(results) == 1
            assert any("Downloading" in m or "completed" in m.lower() for m in messages)

    @patch("cdsswarm.core.cdsapi")
    def test_download_all_cached(self, mock_cdsapi):
        """download() returns success for all cached files without calling CDS."""
        with tempfile.TemporaryDirectory() as tmp:
            target = os.path.join(tmp, "cached.grib")
            with open(target, "w") as f:
                f.write("existing data")

            tasks = [Task("ds", {}, target)]
            results = download(tasks, num_workers=1)

            assert len(results) == 1
            assert results[0].success
            mock_cdsapi.Client.assert_not_called()

    @patch("cdsswarm.core.cdsapi")
    def test_download_failure_returns_error(self, mock_cdsapi):
        """download() returns Result with error on failure."""
        with tempfile.TemporaryDirectory() as tmp:
            mock_client = MagicMock()
            mock_client.retrieve.side_effect = RuntimeError("API down")
            mock_cdsapi.Client.return_value = mock_client

            tasks = [Task("ds", {}, os.path.join(tmp, "out.grib"))]
            results = download(tasks, num_workers=1)

            assert len(results) == 1
            assert not results[0].success
            assert "API down" in results[0].error
