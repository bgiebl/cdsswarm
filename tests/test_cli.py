"""Tests for CLI and request file loading."""

import json
import os
import tempfile

import pytest

from cdsswarm.cli import load_requests
from cdsswarm.core import Task


@pytest.fixture
def tmp_dir():
    with tempfile.TemporaryDirectory() as d:
        yield d


class TestLoadRequests:
    def test_list_format(self, tmp_dir):
        data = [
            {
                "dataset": "reanalysis-era5-single-levels",
                "request": {"variable": ["2m_temperature"], "year": ["2024"]},
                "target": "temp.grib",
            },
            {
                "dataset": "reanalysis-era5-pressure-levels",
                "request": {"variable": ["geopotential"], "year": ["2023"]},
                "target": "geopot.grib",
            },
        ]
        path = os.path.join(tmp_dir, "requests.json")
        with open(path, "w") as f:
            json.dump(data, f)

        tasks = load_requests(path)
        assert len(tasks) == 2
        assert isinstance(tasks[0], Task)
        assert tasks[0].dataset == "reanalysis-era5-single-levels"
        assert tasks[0].request["variable"] == ["2m_temperature"]
        assert tasks[0].target == "temp.grib"
        assert tasks[1].dataset == "reanalysis-era5-pressure-levels"

    def test_compact_format(self, tmp_dir):
        data = {
            "dataset": "reanalysis-era5-single-levels",
            "requests": [
                {
                    "request": {"variable": ["2m_temperature"]},
                    "target": "temp.grib",
                },
                {
                    "request": {"variable": ["total_precipitation"]},
                    "target": "precip.grib",
                },
            ],
        }
        path = os.path.join(tmp_dir, "requests.json")
        with open(path, "w") as f:
            json.dump(data, f)

        tasks = load_requests(path)
        assert len(tasks) == 2
        assert tasks[0].dataset == "reanalysis-era5-single-levels"
        assert tasks[1].dataset == "reanalysis-era5-single-levels"
        assert tasks[0].target == "temp.grib"
        assert tasks[1].target == "precip.grib"

    def test_yaml_format(self, tmp_dir):
        pytest.importorskip("yaml")
        import yaml

        data = [
            {
                "dataset": "reanalysis-era5-single-levels",
                "request": {"variable": ["2m_temperature"]},
                "target": "temp.grib",
            },
        ]
        path = os.path.join(tmp_dir, "requests.yaml")
        with open(path, "w") as f:
            yaml.dump(data, f)

        tasks = load_requests(path)
        assert len(tasks) == 1
        assert tasks[0].dataset == "reanalysis-era5-single-levels"

    def test_invalid_format(self, tmp_dir):
        path = os.path.join(tmp_dir, "bad.json")
        with open(path, "w") as f:
            json.dump({"foo": "bar"}, f)

        with pytest.raises(ValueError, match="Unrecognized format"):
            load_requests(path)


class TestCLIParsing:
    def test_default_args(self):
        from cdsswarm.cli import _build_parser

        parser = _build_parser()
        args = parser.parse_args(["requests.json"])
        assert args.requests_file == "requests.json"
        assert args.workers == 4
        assert args.mode == "auto"
        assert not args.no_skip
        assert args.reuse is True
        assert not args.dry_run

    def test_custom_args(self):
        from cdsswarm.cli import _build_parser

        parser = _build_parser()
        args = parser.parse_args([
            "my_requests.yaml", "-w", "8", "-m", "script", "--no-skip", "--dry-run",
        ])
        assert args.requests_file == "my_requests.yaml"
        assert args.workers == 8
        assert args.mode == "script"
        assert args.no_skip
        assert args.dry_run

    def test_mode_choices(self):
        from cdsswarm.cli import _build_parser

        parser = _build_parser()
        # Valid choices should work
        for mode in ("interactive", "script", "auto"):
            args = parser.parse_args(["f.json", "-m", mode])
            assert args.mode == mode
        # Invalid choice should error
        with pytest.raises(SystemExit):
            parser.parse_args(["f.json", "-m", "invalid"])

    def test_reuse_flag(self):
        from cdsswarm.cli import _build_parser

        parser = _build_parser()
        args = parser.parse_args(["f.json", "--no-reuse"])
        assert args.reuse is False
        args = parser.parse_args(["f.json", "--reuse"])
        assert args.reuse is True

    def test_version(self, capsys):
        from cdsswarm.cli import _build_parser

        parser = _build_parser()
        with pytest.raises(SystemExit, match="0"):
            parser.parse_args(["--version"])
