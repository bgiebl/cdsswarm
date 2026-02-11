"""Tests for core download engine."""

import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest

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
