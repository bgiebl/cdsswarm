"""Tests for CLI and request file loading."""

import io
import json
import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest

from cdsswarm.cli import _resolve_mode, _run_script, load_requests, main
from cdsswarm.core import Result, Task


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
        from cdsswarm.exceptions import RequestFileError

        path = os.path.join(tmp_dir, "bad.json")
        with open(path, "w") as f:
            json.dump({"foo": "bar"}, f)

        with pytest.raises(RequestFileError, match="Unrecognized format"):
            load_requests(path)


class TestCLIParsing:
    def test_default_args(self):
        from cdsswarm.cli import _build_parser

        parser = _build_parser()
        args = parser.parse_args(["requests.json"])
        assert args.requests_file == "requests.json"
        assert args.workers is None
        assert args.mode is None
        assert args.no_skip is None
        assert args.reuse is None
        assert args.max_retries is None
        assert args.output_dir is None
        assert args.log is None
        assert args.summary is None
        assert not args.dry_run

    def test_custom_args(self):
        from cdsswarm.cli import _build_parser

        parser = _build_parser()
        args = parser.parse_args(
            [
                "my_requests.yaml",
                "-w",
                "8",
                "-m",
                "script",
                "--no-skip",
                "--dry-run",
            ]
        )
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

    def test_output_dir_flag(self):
        from cdsswarm.cli import _build_parser

        parser = _build_parser()
        args = parser.parse_args(["f.json", "--output-dir", "data/cds/"])
        assert args.output_dir == "data/cds/"

    def test_log_flag(self):
        from cdsswarm.cli import _build_parser

        parser = _build_parser()
        args = parser.parse_args(["f.json", "--log", "run.log"])
        assert args.log == "run.log"

    def test_summary_flag(self):
        from cdsswarm.cli import _build_parser

        parser = _build_parser()
        args = parser.parse_args(["f.json", "--summary", "report.json"])
        assert args.summary == "report.json"

    def test_version(self, capsys):
        from cdsswarm.cli import _build_parser

        parser = _build_parser()
        with pytest.raises(SystemExit, match="0"):
            parser.parse_args(["--version"])


class TestConfigIntegration:
    """Test that CLI main() integrates with config files."""

    def test_config_values_used(self, tmp_dir):
        """Config file values are used when no CLI flag given."""
        from unittest.mock import patch

        from cdsswarm.cli import main

        requests_file = os.path.join(tmp_dir, "requests.json")
        with open(requests_file, "w") as f:
            json.dump([{"dataset": "ds", "request": {}, "target": "out.grib"}], f)

        cfg_file = os.path.join(tmp_dir, ".cdsswarm.toml")
        with open(cfg_file, "w") as f:
            f.write("workers = 12\n")

        with (
            patch(
                "cdsswarm.config.USER_CONFIG_PATH",
                __import__("pathlib").Path(tmp_dir) / "nope.toml",
            ),
            patch(
                "cdsswarm.config.Path.cwd",
                return_value=__import__("pathlib").Path(tmp_dir),
            ),
            pytest.raises(SystemExit, match="0"),
        ):
            main([requests_file, "--dry-run"])

    def test_cli_overrides_config(self, tmp_dir):
        """CLI flags override config file values."""
        from unittest.mock import patch

        from cdsswarm.cli import main

        requests_file = os.path.join(tmp_dir, "requests.json")
        with open(requests_file, "w") as f:
            json.dump([{"dataset": "ds", "request": {}, "target": "out.grib"}], f)

        cfg_file = os.path.join(tmp_dir, ".cdsswarm.toml")
        with open(cfg_file, "w") as f:
            f.write("workers = 12\n")

        with (
            patch(
                "cdsswarm.config.USER_CONFIG_PATH",
                __import__("pathlib").Path(tmp_dir) / "nope.toml",
            ),
            patch(
                "cdsswarm.config.Path.cwd",
                return_value=__import__("pathlib").Path(tmp_dir),
            ),
            pytest.raises(SystemExit, match="0"),
        ):
            main([requests_file, "--dry-run", "-w", "2"])

    def test_output_dir_prepends_to_targets(self, tmp_dir, capsys):
        """--output-dir prepends directory to relative target paths."""
        from unittest.mock import patch

        from cdsswarm.cli import main

        requests_file = os.path.join(tmp_dir, "requests.json")
        with open(requests_file, "w") as f:
            json.dump([{"dataset": "ds", "request": {}, "target": "out.grib"}], f)

        with (
            patch(
                "cdsswarm.config.USER_CONFIG_PATH",
                __import__("pathlib").Path(tmp_dir) / "nope.toml",
            ),
            patch(
                "cdsswarm.config.Path.cwd",
                return_value=__import__("pathlib").Path(tmp_dir),
            ),
            pytest.raises(SystemExit, match="0"),
        ):
            main([requests_file, "--dry-run", "--output-dir", "data/cds"])

        output = capsys.readouterr().out
        assert "data/cds/out.grib" in output

    def test_output_dir_rejects_path_traversal(self, tmp_dir, capsys):
        """--output-dir rejects targets that escape the output directory."""
        from unittest.mock import patch

        from cdsswarm.cli import main

        requests_file = os.path.join(tmp_dir, "requests.json")
        with open(requests_file, "w") as f:
            json.dump(
                [{"dataset": "ds", "request": {}, "target": "../../etc/passwd"}], f
            )

        with (
            patch(
                "cdsswarm.config.USER_CONFIG_PATH",
                __import__("pathlib").Path(tmp_dir) / "nope.toml",
            ),
            patch(
                "cdsswarm.config.Path.cwd",
                return_value=__import__("pathlib").Path(tmp_dir),
            ),
            pytest.raises(SystemExit, match="1"),
        ):
            main([requests_file, "--dry-run", "--output-dir", "data/cds"])

        err = capsys.readouterr().err
        assert "escapes output directory" in err

    def test_output_dir_from_config(self, tmp_dir, capsys):
        """output-dir from config file is applied."""
        from unittest.mock import patch

        from cdsswarm.cli import main

        requests_file = os.path.join(tmp_dir, "requests.json")
        with open(requests_file, "w") as f:
            json.dump([{"dataset": "ds", "request": {}, "target": "out.grib"}], f)

        cfg_file = os.path.join(tmp_dir, ".cdsswarm.toml")
        with open(cfg_file, "w") as f:
            f.write('output-dir = "my/output"\n')

        with (
            patch(
                "cdsswarm.config.USER_CONFIG_PATH",
                __import__("pathlib").Path(tmp_dir) / "nope.toml",
            ),
            patch(
                "cdsswarm.config.Path.cwd",
                return_value=__import__("pathlib").Path(tmp_dir),
            ),
            pytest.raises(SystemExit, match="0"),
        ):
            main([requests_file, "--dry-run"])

        output = capsys.readouterr().out
        assert "my/output/out.grib" in output


def _patch_config(tmp_dir):
    """Context manager to isolate config resolution from real config files."""
    import pathlib

    return (
        patch(
            "cdsswarm.config.USER_CONFIG_PATH",
            pathlib.Path(tmp_dir) / "nope.toml",
        ),
        patch(
            "cdsswarm.config.Path.cwd",
            return_value=pathlib.Path(tmp_dir),
        ),
    )


class TestResolveMode:
    def test_interactive_passthrough(self):
        assert _resolve_mode("interactive") == "interactive"

    def test_script_passthrough(self):
        assert _resolve_mode("script") == "script"

    def test_auto_tty(self):
        with patch("cdsswarm.cli.sys.stdout") as mock_stdout:
            mock_stdout.isatty.return_value = True
            assert _resolve_mode("auto") == "interactive"

    def test_auto_no_tty(self):
        with patch("cdsswarm.cli.sys.stdout") as mock_stdout:
            mock_stdout.isatty.return_value = False
            assert _resolve_mode("auto") == "script"


class TestMainErrorPaths:
    def test_file_not_found(self, capsys):
        with pytest.raises(SystemExit, match="1"):
            main(["/nonexistent_file_that_does_not_exist.json"])
        err = capsys.readouterr().err
        assert "file not found" in err.lower()

    def test_load_error(self, tmp_dir, capsys):
        path = os.path.join(tmp_dir, "bad.json")
        with open(path, "w") as f:
            json.dump({"foo": "bar"}, f)

        cfg_patches = _patch_config(tmp_dir)
        with cfg_patches[0], cfg_patches[1], pytest.raises(SystemExit, match="1"):
            main([path])
        err = capsys.readouterr().err
        assert "Error" in err

    def test_empty_tasks(self, tmp_dir, capsys):
        path = os.path.join(tmp_dir, "empty.json")
        with open(path, "w") as f:
            json.dump([], f)

        cfg_patches = _patch_config(tmp_dir)
        with cfg_patches[0], cfg_patches[1], pytest.raises(SystemExit, match="1"):
            main([path])
        err = capsys.readouterr().err
        assert "No download tasks" in err

    def test_config_resolve_error(self, tmp_dir, capsys):
        path = os.path.join(tmp_dir, "requests.json")
        with open(path, "w") as f:
            json.dump([{"dataset": "ds", "request": {}, "target": "out.grib"}], f)

        with (
            patch(
                "cdsswarm.config.resolve_settings", side_effect=ValueError("bad config")
            ),
            pytest.raises(SystemExit, match="1"),
        ):
            main([path])
        err = capsys.readouterr().err
        assert "Config error" in err

    def test_no_skip_flag(self, tmp_dir, capsys):
        path = os.path.join(tmp_dir, "requests.json")
        with open(path, "w") as f:
            json.dump([{"dataset": "ds", "request": {}, "target": "out.grib"}], f)

        cfg_patches = _patch_config(tmp_dir)
        with (
            cfg_patches[0],
            cfg_patches[1],
            pytest.raises(SystemExit, match="0"),
        ):
            main([path, "--no-skip", "--dry-run"])


class TestRunScript:
    @patch("cdsswarm.cli.SwarmDownloader")
    def test_basic_run(self, mock_cls):
        tasks = [Task("ds", {}, "out.grib")]
        mock_instance = MagicMock()
        mock_instance.run.return_value = [Result(task=tasks[0], success=True)]
        mock_cls.return_value = mock_instance

        result = _run_script(tasks, num_workers=2, skip_existing=True)

        mock_cls.assert_called_once()
        call_kwargs = mock_cls.call_args[1]
        assert call_kwargs["num_workers"] == 2
        assert call_kwargs["skip_existing"] is True
        mock_instance.run.assert_called_once()
        assert result is not None

    @patch("cdsswarm.cli.SwarmDownloader")
    def test_with_log_file(self, mock_cls):
        from cdsswarm.adapters import LoggingAdapter

        tasks = [Task("ds", {}, "out.grib")]
        mock_instance = MagicMock()
        mock_instance.run.return_value = [Result(task=tasks[0], success=True)]
        mock_cls.return_value = mock_instance

        log_file = io.StringIO()
        _run_script(tasks, num_workers=1, skip_existing=False, log_file=log_file)

        # The adapter arg should be a LoggingAdapter when log_file is provided
        call_kwargs = mock_cls.call_args[1]
        assert isinstance(call_kwargs["adapter"], LoggingAdapter)


class TestMainDownloadFlow:
    def _write_tasks(self, tmp_dir):
        path = os.path.join(tmp_dir, "requests.json")
        with open(path, "w") as f:
            json.dump([{"dataset": "ds", "request": {}, "target": "out.grib"}], f)
        return path

    def test_script_success_exits_0(self, tmp_dir, capsys):
        path = self._write_tasks(tmp_dir)
        task = Task("ds", {}, "out.grib")
        results = [Result(task=task, success=True, start_time=100.0, end_time=200.0)]

        cfg_patches = _patch_config(tmp_dir)
        with (
            cfg_patches[0],
            cfg_patches[1],
            patch("cdsswarm.cli._resolve_mode", return_value="script"),
            patch("cdsswarm.cli._run_script", return_value=results),
            pytest.raises(SystemExit, match="0"),
        ):
            main([path])

        out = capsys.readouterr().out
        assert "Summary" in out

    def test_script_failure_exits_1(self, tmp_dir, capsys):
        path = self._write_tasks(tmp_dir)
        task = Task("ds", {}, "out.grib")
        results = [Result(task=task, success=False, error="timeout")]

        cfg_patches = _patch_config(tmp_dir)
        with (
            cfg_patches[0],
            cfg_patches[1],
            patch("cdsswarm.cli._resolve_mode", return_value="script"),
            patch("cdsswarm.cli._run_script", return_value=results),
            pytest.raises(SystemExit, match="1"),
        ):
            main([path])

    def test_none_results_exits_1(self, tmp_dir):
        path = self._write_tasks(tmp_dir)

        cfg_patches = _patch_config(tmp_dir)
        with (
            cfg_patches[0],
            cfg_patches[1],
            patch("cdsswarm.cli._resolve_mode", return_value="script"),
            patch("cdsswarm.cli._run_script", return_value=None),
            pytest.raises(SystemExit, match="1"),
        ):
            main([path])

    def test_export_summary_called(self, tmp_dir, capsys):
        path = self._write_tasks(tmp_dir)
        task = Task("ds", {}, "out.grib")
        results = [Result(task=task, success=True, start_time=100.0, end_time=200.0)]
        summary_file = os.path.join(tmp_dir, "report.json")

        cfg_patches = _patch_config(tmp_dir)
        with (
            cfg_patches[0],
            cfg_patches[1],
            patch("cdsswarm.cli._resolve_mode", return_value="script"),
            patch("cdsswarm.cli._run_script", return_value=results),
            pytest.raises(SystemExit, match="0"),
        ):
            main([path, "--summary", summary_file])

        assert os.path.isfile(summary_file)
        with open(summary_file) as f:
            data = json.load(f)
        assert data["totals"]["tasks_total"] == 1

    def test_log_file_closed(self, tmp_dir):
        path = self._write_tasks(tmp_dir)
        task = Task("ds", {}, "out.grib")
        results = [Result(task=task, success=True, start_time=100.0, end_time=200.0)]
        log_path = os.path.join(tmp_dir, "run.log")

        cfg_patches = _patch_config(tmp_dir)
        with (
            cfg_patches[0],
            cfg_patches[1],
            patch("cdsswarm.cli._resolve_mode", return_value="script"),
            patch("cdsswarm.cli._run_script", return_value=results),
            pytest.raises(SystemExit, match="0"),
        ):
            main([path, "--log", log_path])

        # Log file should exist and be closed (writing should succeed normally)
        assert os.path.isfile(log_path)
