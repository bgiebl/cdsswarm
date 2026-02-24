"""Tests for CLI and request file loading."""

import io
import json
import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest

from cdsswarm.cli import (
    _MultiWriter,
    _resolve_mode,
    _rotate_logs,
    _run_script,
    load_requests,
    main,
)
from cdsswarm.core import Result, Task
from cdsswarm.state import SessionState, session_path


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

    def test_yaml_import_error(self, tmp_dir):
        """YAML file raises ImportError when PyYAML is not installed."""
        import sys

        path = os.path.join(tmp_dir, "requests.yaml")
        with open(path, "w") as f:
            f.write("- dataset: ds\n  request: {}\n  target: out.grib\n")

        with patch.dict(sys.modules, {"yaml": None}):
            with pytest.raises(ImportError, match="PyYAML is required"):
                load_requests(path)

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

    def test_post_hook_flag(self):
        from cdsswarm.cli import _build_parser

        parser = _build_parser()
        args = parser.parse_args(["f.json", "--post-hook", "gzip {file}"])
        assert args.post_hook == "gzip {file}"

    def test_post_hook_default_none(self):
        from cdsswarm.cli import _build_parser

        parser = _build_parser()
        args = parser.parse_args(["f.json"])
        assert args.post_hook is None

    def test_resume_flag(self):
        from cdsswarm.cli import _build_parser

        parser = _build_parser()
        args = parser.parse_args(["f.json", "--resume"])
        assert args.resume is True
        args = parser.parse_args(["f.json", "--no-resume"])
        assert args.resume is False

    def test_resume_default_none(self):
        from cdsswarm.cli import _build_parser

        parser = _build_parser()
        args = parser.parse_args(["f.json"])
        assert args.resume is None

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
    def test_with_post_hook(self, mock_cls):
        tasks = [Task("ds", {}, "out.grib")]
        mock_instance = MagicMock()
        mock_instance.run.return_value = [Result(task=tasks[0], success=True)]
        mock_cls.return_value = mock_instance

        _run_script(tasks, num_workers=1, skip_existing=True, post_hook="gzip {file}")

        call_kwargs = mock_cls.call_args[1]
        assert call_kwargs["post_hook"] == "gzip {file}"

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

    def test_interactive_mode_calls_run_interactive(self, tmp_dir):
        """Interactive mode calls _run_interactive and exits based on results."""
        path = self._write_tasks(tmp_dir)
        task = Task("ds", {}, "out.grib")
        results = [Result(task=task, success=True, start_time=100.0, end_time=200.0)]

        cfg_patches = _patch_config(tmp_dir)
        with (
            cfg_patches[0],
            cfg_patches[1],
            patch("cdsswarm.cli._resolve_mode", return_value="interactive"),
            patch("cdsswarm.cli._run_interactive", return_value=results) as mock_ri,
            pytest.raises(SystemExit, match="0"),
        ):
            main([path])

        mock_ri.assert_called_once()


class TestRunInteractive:
    def _capture_lazy(self, MockApp, download_results=None):
        """Helper: configure MockApp to capture the lazy downloader."""
        captured = []

        def capture_app(**kwargs):
            captured.append(kwargs.get("downloader"))
            mock_app = MagicMock()
            mock_app.download_results = download_results
            return mock_app

        MockApp.side_effect = capture_app
        return captured

    def test_basic(self):
        """_run_interactive creates CdsswarmApp and calls app.run()."""
        from cdsswarm.cli import _run_interactive

        tasks = [Task("ds", {}, "out.grib")]

        with patch("cdsswarm.textual_app.CdsswarmApp") as MockApp:
            mock_app = MagicMock()
            mock_app.download_results = [Result(task=tasks[0], success=True)]
            MockApp.return_value = mock_app

            result = _run_interactive(tasks, num_workers=2, skip_existing=True)

            MockApp.assert_called_once()
            mock_app.run.assert_called_once()
            assert result == mock_app.download_results

    def test_lazy_downloader_run_creates_real(self):
        """Calling lazy.run() creates SwarmDownloader and delegates run()."""
        from cdsswarm.cli import _run_interactive

        tasks = [Task("ds", {}, "out.grib")]

        with (
            patch("cdsswarm.textual_app.CdsswarmApp") as MockApp,
            patch("cdsswarm.cli.SwarmDownloader") as MockSD,
        ):
            captured = self._capture_lazy(MockApp)
            mock_sd_instance = MagicMock()
            mock_sd_instance.run.return_value = [Result(task=tasks[0], success=True)]
            MockSD.return_value = mock_sd_instance

            _run_interactive(tasks, num_workers=2, skip_existing=True)

            lazy = captured[0]
            result = lazy.run()

            MockSD.assert_called_once()
            call_kw = MockSD.call_args[1]
            assert call_kw["num_workers"] == 2
            assert call_kw["skip_existing"] is True
            mock_sd_instance.run.assert_called_once()
            assert result == mock_sd_instance.run.return_value

    def test_lazy_downloader_run_with_log_file(self):
        """Calling lazy.run() with log_file wraps adapter in LoggingAdapter."""
        from cdsswarm.adapters import LoggingAdapter
        from cdsswarm.cli import _run_interactive

        tasks = [Task("ds", {}, "out.grib")]
        log_file = io.StringIO()

        with (
            patch("cdsswarm.textual_app.CdsswarmApp") as MockApp,
            patch("cdsswarm.cli.SwarmDownloader") as MockSD,
        ):
            captured = self._capture_lazy(MockApp)
            MockSD.return_value = MagicMock()

            _run_interactive(
                tasks, num_workers=1, skip_existing=False, log_file=log_file
            )

            lazy = captured[0]
            lazy.run()

            call_kw = MockSD.call_args[1]
            assert isinstance(call_kw["adapter"], LoggingAdapter)

    def test_lazy_downloader_cancel_before_run(self):
        """Calling cancel() before run() on lazy downloader doesn't crash."""
        from cdsswarm.cli import _run_interactive

        tasks = [Task("ds", {}, "out.grib")]

        with patch("cdsswarm.textual_app.CdsswarmApp") as MockApp:
            captured = self._capture_lazy(MockApp)
            _run_interactive(tasks, num_workers=1, skip_existing=False)

            lazy = captured[0]
            lazy.cancel()  # No-op, _real is None

    def test_lazy_downloader_cancel_after_run(self):
        """Calling cancel() after run() delegates to _real.cancel()."""
        from cdsswarm.cli import _run_interactive

        tasks = [Task("ds", {}, "out.grib")]

        with (
            patch("cdsswarm.textual_app.CdsswarmApp") as MockApp,
            patch("cdsswarm.cli.SwarmDownloader") as MockSD,
        ):
            captured = self._capture_lazy(MockApp)
            mock_sd_instance = MagicMock()
            MockSD.return_value = mock_sd_instance

            _run_interactive(tasks, num_workers=1, skip_existing=False)

            lazy = captured[0]
            lazy.run()  # Creates _real
            lazy.cancel()  # Should delegate to _real.cancel()

            mock_sd_instance.cancel.assert_called_once()


class TestMainArgvDefault:
    def test_main_uses_sysargv_when_none(self, tmp_dir, capsys):
        """main() falls back to sys.argv[1:] when argv is None."""
        requests_file = os.path.join(tmp_dir, "requests.json")
        with open(requests_file, "w") as f:
            json.dump([{"dataset": "ds", "request": {}, "target": "out.grib"}], f)

        cfg_patches = _patch_config(tmp_dir)
        with (
            cfg_patches[0],
            cfg_patches[1],
            patch("cdsswarm.cli.sys.argv", ["cdsswarm", requests_file, "--dry-run"]),
            pytest.raises(SystemExit, match="0"),
        ):
            main(None)


class TestCmdGenerate:
    """CLI integration tests for ``cdsswarm generate``."""

    def _write_template(self, tmp_dir, data, name="template.json"):
        path = os.path.join(tmp_dir, name)
        with open(path, "w") as f:
            json.dump(data, f)
        return path

    def _template(self):
        return {
            "dataset": "reanalysis-era5-single-levels",
            "request": {
                "variable": ["2m_temperature", "total_precipitation"],
                "year": ["2023", "2024"],
                "day": ["01"],
                "time": ["12:00"],
            },
            "target": "output/{variable}_{year}.grib",
            "split_by": ["variable", "year"],
        }

    def test_generate_stdout(self, tmp_dir, capsys):
        path = self._write_template(tmp_dir, self._template())

        with pytest.raises(SystemExit, match="0"):
            main(["generate", path])

        out = capsys.readouterr().out
        data = json.loads(out)
        assert data["dataset"] == "reanalysis-era5-single-levels"
        assert len(data["requests"]) == 4  # 2 vars x 2 years

    def test_generate_output_file(self, tmp_dir, capsys):
        path = self._write_template(tmp_dir, self._template())
        out_path = os.path.join(tmp_dir, "requests.json")

        with pytest.raises(SystemExit, match="0"):
            main(["generate", path, "-o", out_path])

        assert os.path.isfile(out_path)
        with open(out_path) as f:
            data = json.load(f)
        assert len(data["requests"]) == 4

        err = capsys.readouterr().err
        assert "4 task(s)" in err

    def test_generate_split_by_override(self, tmp_dir, capsys):
        template = self._template()
        template["target"] = "output/{year}.grib"
        path = self._write_template(tmp_dir, template)

        with pytest.raises(SystemExit, match="0"):
            main(["generate", path, "--split-by", "year"])

        out = capsys.readouterr().out
        data = json.loads(out)
        assert len(data["requests"]) == 2  # only year split

    def test_generate_dry_run(self, tmp_dir, capsys):
        path = self._write_template(tmp_dir, self._template())

        with pytest.raises(SystemExit, match="0"):
            main(["generate", path, "--dry-run"])

        out = capsys.readouterr().out
        assert "4 task(s)" in out
        assert "output/2m_temperature_2023.grib" in out

    def test_generate_file_not_found(self, capsys):
        with pytest.raises(SystemExit, match="1"):
            main(["generate", "/nonexistent_template.json"])

        err = capsys.readouterr().err
        assert "file not found" in err.lower()

    def test_generate_invalid_template(self, tmp_dir, capsys):
        path = self._write_template(tmp_dir, {"foo": "bar"})

        with pytest.raises(SystemExit, match="1"):
            main(["generate", path])

        err = capsys.readouterr().err
        assert "Error" in err

    def test_generate_unwraps_single_element_list(self, tmp_dir, capsys):
        """A single-element list ``[{...}]`` is auto-unwrapped with a warning."""
        path = self._write_template(tmp_dir, [self._template()])

        with pytest.raises(SystemExit, match="0"):
            main(["generate", path])

        captured = capsys.readouterr()
        assert "Warning" in captured.err
        assert "single JSON object" in captured.err

        data = json.loads(captured.out)
        assert len(data["requests"]) == 4

    def test_generate_rejects_multi_element_list(self, tmp_dir, capsys):
        """A list with multiple elements is rejected with an error."""
        path = self._write_template(tmp_dir, [self._template(), self._template()])

        with pytest.raises(SystemExit, match="1"):
            main(["generate", path])

        err = capsys.readouterr().err
        assert "Error" in err
        assert "single JSON object" in err

    def test_generate_rejects_non_dict_template(self, tmp_dir, capsys):
        """A bare string or number in the template file is rejected."""
        path = self._write_template(tmp_dir, "not a dict")

        with pytest.raises(SystemExit, match="1"):
            main(["generate", path])

        err = capsys.readouterr().err
        assert "Error" in err
        assert "single JSON object" in err

    def test_existing_dry_run_still_works(self, tmp_dir, capsys):
        """Existing ``cdsswarm requests.json --dry-run`` still works."""
        requests_file = os.path.join(tmp_dir, "requests.json")
        with open(requests_file, "w") as f:
            json.dump([{"dataset": "ds", "request": {}, "target": "out.grib"}], f)

        cfg_patches = _patch_config(tmp_dir)
        with cfg_patches[0], cfg_patches[1], pytest.raises(SystemExit, match="0"):
            main([requests_file, "--dry-run"])

        out = capsys.readouterr().out
        assert "1 task(s) total" in out

    def test_generate_yaml_template(self, tmp_dir, capsys):
        pytest.importorskip("yaml")
        import yaml

        template = self._template()
        path = os.path.join(tmp_dir, "template.yaml")
        with open(path, "w") as f:
            yaml.dump(template, f)

        with pytest.raises(SystemExit, match="0"):
            main(["generate", path])

        out = capsys.readouterr().out
        data = json.loads(out)
        assert len(data["requests"]) == 4

    def test_generate_yaml_import_error(self, tmp_dir, capsys):
        """YAML template raises ImportError when PyYAML is not installed."""
        import sys as _sys

        path = os.path.join(tmp_dir, "template.yaml")
        with open(path, "w") as f:
            f.write("dataset: ds\n")

        with patch.dict(_sys.modules, {"yaml": None}):
            with pytest.raises(SystemExit, match="1"):
                main(["generate", path])

        err = capsys.readouterr().err
        assert "PyYAML is required" in err

    def test_generate_invalid_json(self, tmp_dir, capsys):
        """Malformed JSON template reports error."""
        path = os.path.join(tmp_dir, "bad.json")
        with open(path, "w") as f:
            f.write("{not valid json")

        with pytest.raises(SystemExit, match="1"):
            main(["generate", path])

        err = capsys.readouterr().err
        assert "Error" in err


class TestSessionResume:
    """Tests for the --resume / --no-resume session resume feature."""

    def _write_tasks(self, tmp_dir, count=2):
        data = [
            {
                "dataset": "reanalysis-era5-single-levels",
                "request": {"variable": [f"var_{i}"], "year": ["2024"]},
                "target": os.path.join(tmp_dir, f"output_{i}.grib"),
            }
            for i in range(count)
        ]
        path = os.path.join(tmp_dir, "requests.json")
        with open(path, "w") as f:
            json.dump(data, f)
        return path, [Task(d["dataset"], d["request"], d["target"]) for d in data]

    def test_fresh_run_creates_state_file(self, tmp_dir, capsys):
        """A fresh run creates a session state file."""
        path, tasks = self._write_tasks(tmp_dir, count=1)
        task = tasks[0]
        results = [Result(task=task, success=True, start_time=100.0, end_time=200.0)]

        # Point session dir into tmp_dir
        cache_dir = os.path.join(tmp_dir, "cache")
        # Compute expected path under the same env override
        with patch.dict(os.environ, {"XDG_CACHE_HOME": cache_dir}):
            sp = session_path(path, "")

        cfg_patches = _patch_config(tmp_dir)
        with (
            cfg_patches[0],
            cfg_patches[1],
            patch.dict(os.environ, {"XDG_CACHE_HOME": cache_dir}),
            patch("cdsswarm.cli._resolve_mode", return_value="script"),
            patch("cdsswarm.cli._run_script", return_value=results),
            pytest.raises(SystemExit, match="0"),
        ):
            main([path])

        assert os.path.isfile(sp)

    def test_resume_filters_completed_tasks(self, tmp_dir, capsys):
        """On resume, completed tasks are filtered out."""
        path, tasks = self._write_tasks(tmp_dir, count=2)
        cache_dir = os.path.join(tmp_dir, "cache")

        # Create a state file with first task completed
        with patch.dict(os.environ, {"XDG_CACHE_HOME": cache_dir}):
            sp = session_path(path, "")
        session = SessionState.new(path, tasks)
        # Mark first task completed and create the file
        with open(tasks[0].target, "w") as f:
            f.write("data")
        session.mark_completed(tasks[0].target, 10.0, 20.0, 1024)
        session.save(sp)

        # Now run — should only pass the second task
        captured_tasks = []

        def fake_run_script(tasks_arg, *a, **kw):
            captured_tasks.extend(tasks_arg)
            return [
                Result(task=t, success=True, start_time=100.0, end_time=200.0)
                for t in tasks_arg
            ]

        cfg_patches = _patch_config(tmp_dir)
        with (
            cfg_patches[0],
            cfg_patches[1],
            patch.dict(os.environ, {"XDG_CACHE_HOME": cache_dir}),
            patch("cdsswarm.cli._resolve_mode", return_value="script"),
            patch("cdsswarm.cli._run_script", side_effect=fake_run_script),
            pytest.raises(SystemExit, match="0"),
        ):
            main([path])

        assert len(captured_tasks) == 1
        assert captured_tasks[0].target == tasks[1].target

        out = capsys.readouterr().out
        assert "Resuming" in out or "Summary" in out

    def test_no_resume_ignores_state_file(self, tmp_dir, capsys):
        """--no-resume starts fresh even if state file exists."""
        path, tasks = self._write_tasks(tmp_dir, count=2)
        cache_dir = os.path.join(tmp_dir, "cache")

        # Create a state file with first task completed
        with patch.dict(os.environ, {"XDG_CACHE_HOME": cache_dir}):
            sp = session_path(path, "")
        session = SessionState.new(path, tasks)
        with open(tasks[0].target, "w") as f:
            f.write("data")
        session.mark_completed(tasks[0].target, 10.0, 20.0, 1024)
        session.save(sp)

        captured_tasks = []

        def fake_run_script(tasks_arg, *a, **kw):
            captured_tasks.extend(tasks_arg)
            return [
                Result(task=t, success=True, start_time=100.0, end_time=200.0)
                for t in tasks_arg
            ]

        cfg_patches = _patch_config(tmp_dir)
        with (
            cfg_patches[0],
            cfg_patches[1],
            patch.dict(os.environ, {"XDG_CACHE_HOME": cache_dir}),
            patch("cdsswarm.cli._resolve_mode", return_value="script"),
            patch("cdsswarm.cli._run_script", side_effect=fake_run_script),
            pytest.raises(SystemExit, match="0"),
        ):
            main([path, "--no-resume"])

        # Both tasks should be passed since we're starting fresh
        assert len(captured_tasks) == 2

    def test_corrupt_state_file_starts_fresh(self, tmp_dir, capsys):
        """Corrupt state file triggers a warning and starts fresh."""
        path, tasks = self._write_tasks(tmp_dir, count=1)
        cache_dir = os.path.join(tmp_dir, "cache")

        with patch.dict(os.environ, {"XDG_CACHE_HOME": cache_dir}):
            sp = session_path(path, "")
        os.makedirs(os.path.dirname(sp), exist_ok=True)
        with open(sp, "w") as f:
            f.write("{corrupt json!!")

        captured_tasks = []

        def fake_run_script(tasks_arg, *a, **kw):
            captured_tasks.extend(tasks_arg)
            return [
                Result(task=t, success=True, start_time=100.0, end_time=200.0)
                for t in tasks_arg
            ]

        cfg_patches = _patch_config(tmp_dir)
        with (
            cfg_patches[0],
            cfg_patches[1],
            patch.dict(os.environ, {"XDG_CACHE_HOME": cache_dir}),
            patch("cdsswarm.cli._resolve_mode", return_value="script"),
            patch("cdsswarm.cli._run_script", side_effect=fake_run_script),
            pytest.raises(SystemExit, match="0"),
        ):
            main([path])

        err = capsys.readouterr().err
        assert "corrupt session file" in err.lower() or "Warning" in err
        assert len(captured_tasks) == 1

    def test_all_completed_exits_0(self, tmp_dir, capsys):
        """When all tasks already completed, exit 0 with message."""
        path, tasks = self._write_tasks(tmp_dir, count=1)
        cache_dir = os.path.join(tmp_dir, "cache")

        with patch.dict(os.environ, {"XDG_CACHE_HOME": cache_dir}):
            sp = session_path(path, "")
        session = SessionState.new(path, tasks)
        with open(tasks[0].target, "w") as f:
            f.write("data")
        session.mark_completed(tasks[0].target, 10.0, 20.0, 1024)
        session.save(sp)

        cfg_patches = _patch_config(tmp_dir)
        with (
            cfg_patches[0],
            cfg_patches[1],
            patch.dict(os.environ, {"XDG_CACHE_HOME": cache_dir}),
            pytest.raises(SystemExit, match="0"),
        ):
            main([path])

        out = capsys.readouterr().out
        assert "already completed" in out.lower()

    def test_resume_notification_message(self, tmp_dir, capsys):
        """Resume emits notification message via pre_messages."""
        path, tasks = self._write_tasks(tmp_dir, count=2)
        cache_dir = os.path.join(tmp_dir, "cache")

        with patch.dict(os.environ, {"XDG_CACHE_HOME": cache_dir}):
            sp = session_path(path, "")
        session = SessionState.new(path, tasks)
        with open(tasks[0].target, "w") as f:
            f.write("data")
        session.mark_completed(tasks[0].target, 10.0, 20.0, 1024)
        session.save(sp)

        captured_pre = []

        def fake_run_script(tasks_arg, *a, **kw):
            if kw.get("pre_messages"):
                captured_pre.extend(kw["pre_messages"])
            return [
                Result(task=t, success=True, start_time=100.0, end_time=200.0)
                for t in tasks_arg
            ]

        cfg_patches = _patch_config(tmp_dir)
        with (
            cfg_patches[0],
            cfg_patches[1],
            patch.dict(os.environ, {"XDG_CACHE_HOME": cache_dir}),
            patch("cdsswarm.cli._resolve_mode", return_value="script"),
            patch("cdsswarm.cli._run_script", side_effect=fake_run_script),
            pytest.raises(SystemExit, match="0"),
        ):
            main([path])

        assert len(captured_pre) == 1
        assert "Resuming" in captured_pre[0]
        assert "1/2" in captured_pre[0]
        assert "--no-resume" in captured_pre[0]

    def test_callbacks_update_state_file(self, tmp_dir, capsys):
        """on_task_done and on_request_id callbacks update the state file."""
        path, tasks = self._write_tasks(tmp_dir, count=2)
        cache_dir = os.path.join(tmp_dir, "cache")

        with patch.dict(os.environ, {"XDG_CACHE_HOME": cache_dir}):
            sp = session_path(path, "")

        def fake_run_script(tasks_arg, *a, **kw):
            on_done = kw.get("on_task_done")
            on_rid = kw.get("on_request_id")
            results = []
            for t in tasks_arg:
                if on_rid:
                    on_rid(t.target, f"rid-{t.label}")
                r = Result(
                    task=t, success=True, start_time=100.0, end_time=200.0, file_size=42
                )
                results.append(r)
                if on_done:
                    on_done(r, f"rid-{t.label}")
            return results

        cfg_patches = _patch_config(tmp_dir)
        with (
            cfg_patches[0],
            cfg_patches[1],
            patch.dict(os.environ, {"XDG_CACHE_HOME": cache_dir}),
            patch("cdsswarm.cli._resolve_mode", return_value="script"),
            patch("cdsswarm.cli._run_script", side_effect=fake_run_script),
            pytest.raises(SystemExit, match="0"),
        ):
            main([path])

        # Verify state file was updated by the callbacks
        loaded = SessionState.load(sp)
        for t in tasks:
            ts = loaded.tasks[t.target]
            assert ts.status == "completed"
            assert ts.cds_request_id == f"rid-{t.label}"
            assert ts.file_size == 42

    def test_callback_marks_failure(self, tmp_dir, capsys):
        """on_task_done callback records failures in state file."""
        path, tasks = self._write_tasks(tmp_dir, count=1)
        cache_dir = os.path.join(tmp_dir, "cache")

        with patch.dict(os.environ, {"XDG_CACHE_HOME": cache_dir}):
            sp = session_path(path, "")

        def fake_run_script(tasks_arg, *a, **kw):
            on_done = kw.get("on_task_done")
            t = tasks_arg[0]
            r = Result(
                task=t, success=False, error="timeout", start_time=1.0, end_time=2.0
            )
            if on_done:
                on_done(r, "rid-fail")
            return [r]

        cfg_patches = _patch_config(tmp_dir)
        with (
            cfg_patches[0],
            cfg_patches[1],
            patch.dict(os.environ, {"XDG_CACHE_HOME": cache_dir}),
            patch("cdsswarm.cli._resolve_mode", return_value="script"),
            patch("cdsswarm.cli._run_script", side_effect=fake_run_script),
            pytest.raises(SystemExit, match="1"),
        ):
            main([path])

        loaded = SessionState.load(sp)
        ts = loaded.tasks[tasks[0].target]
        assert ts.status == "failed"
        assert ts.error == "timeout"
        assert ts.cds_request_id == "rid-fail"

    def test_resume_merges_new_tasks(self, tmp_dir, capsys):
        """New tasks in regenerated request file are merged into session."""
        # Create initial session with 1 task
        path, tasks = self._write_tasks(tmp_dir, count=1)
        cache_dir = os.path.join(tmp_dir, "cache")

        with patch.dict(os.environ, {"XDG_CACHE_HOME": cache_dir}):
            sp = session_path(path, "")
        session = SessionState.new(path, tasks)
        session.save(sp)

        # Now regenerate request file with 2 tasks (adds a new one)
        data = [
            {
                "dataset": "reanalysis-era5-single-levels",
                "request": {"variable": [f"var_{i}"], "year": ["2024"]},
                "target": os.path.join(tmp_dir, f"output_{i}.grib"),
            }
            for i in range(2)
        ]
        with open(path, "w") as f:
            json.dump(data, f)

        captured_tasks = []

        def fake_run_script(tasks_arg, *a, **kw):
            captured_tasks.extend(tasks_arg)
            return [
                Result(task=t, success=True, start_time=100.0, end_time=200.0)
                for t in tasks_arg
            ]

        cfg_patches = _patch_config(tmp_dir)
        with (
            cfg_patches[0],
            cfg_patches[1],
            patch.dict(os.environ, {"XDG_CACHE_HOME": cache_dir}),
            patch("cdsswarm.cli._resolve_mode", return_value="script"),
            patch("cdsswarm.cli._run_script", side_effect=fake_run_script),
            pytest.raises(SystemExit, match="0"),
        ):
            main([path])

        # Both tasks should be pending (original was pending, new was merged)
        assert len(captured_tasks) == 2
        # State file should have both tasks
        loaded = SessionState.load(sp)
        assert len(loaded.tasks) == 2


class TestCancelParserAndHelp:
    """Tests for cdsswarm cancel argument parsing."""

    def test_cancel_parser_defaults(self):
        from cdsswarm.cli import _build_cancel_parser

        parser = _build_cancel_parser()
        args = parser.parse_args([])
        assert args.request_ids == []
        assert args.yes is False

    def test_cancel_parser_with_ids(self):
        from cdsswarm.cli import _build_cancel_parser

        parser = _build_cancel_parser()
        args = parser.parse_args(["abc-123", "def-456"])
        assert args.request_ids == ["abc-123", "def-456"]

    def test_cancel_parser_yes_flag(self):
        from cdsswarm.cli import _build_cancel_parser

        parser = _build_cancel_parser()
        args = parser.parse_args(["-y"])
        assert args.yes is True
        args = parser.parse_args(["--yes"])
        assert args.yes is True

    def test_cancel_help(self):
        """cdsswarm cancel --help exits 0."""
        with pytest.raises(SystemExit, match="0"):
            main(["cancel", "--help"])


class TestCmdCancelSpecificIds:
    """Tests for cdsswarm cancel <id1> <id2> ..."""

    def test_cancel_specific_ids_with_yes(self, capsys):
        """Cancel specific IDs with --yes skips prompt."""
        mock_client = MagicMock()

        with (
            patch("cdsapi.Client", return_value=mock_client),
            patch("cdsswarm._cds_utils.cancel_cds_request") as mock_cancel,
            pytest.raises(SystemExit, match="0"),
        ):
            main(["cancel", "abc-123", "def-456", "--yes"])

        assert mock_cancel.call_count == 2
        mock_cancel.assert_any_call(mock_client, "abc-123")
        mock_cancel.assert_any_call(mock_client, "def-456")

        out = capsys.readouterr().out
        assert "Cancelled abc-123" in out
        assert "Cancelled def-456" in out
        assert "2/2" in out

    def test_cancel_specific_ids_confirmed(self, capsys):
        """Cancel specific IDs with user confirmation."""
        mock_client = MagicMock()

        with (
            patch("cdsapi.Client", return_value=mock_client),
            patch("cdsswarm._cds_utils.cancel_cds_request"),
            patch("builtins.input", return_value="y"),
            pytest.raises(SystemExit, match="0"),
        ):
            main(["cancel", "abc-123"])

        out = capsys.readouterr().out
        assert "abc-123" in out

    def test_cancel_specific_ids_denied(self, capsys):
        """Declining confirmation aborts cancellation."""
        with (
            patch("cdsapi.Client", return_value=MagicMock()),
            patch("cdsswarm._cds_utils.cancel_cds_request") as mock_cancel,
            patch("builtins.input", return_value="n"),
            pytest.raises(SystemExit, match="0"),
        ):
            main(["cancel", "abc-123"])

        mock_cancel.assert_not_called()
        out = capsys.readouterr().out
        assert "Aborted" in out

    def test_cancel_specific_ids_eof(self, capsys):
        """EOFError on confirmation prompt aborts."""
        with (
            patch("cdsapi.Client", return_value=MagicMock()),
            patch("builtins.input", side_effect=EOFError),
            pytest.raises(SystemExit, match="1"),
        ):
            main(["cancel", "abc-123"])

        out = capsys.readouterr().out
        assert "Aborted" in out

    def test_cancel_specific_ids_keyboard_interrupt(self, capsys):
        """KeyboardInterrupt on confirmation prompt aborts."""
        with (
            patch("cdsapi.Client", return_value=MagicMock()),
            patch("builtins.input", side_effect=KeyboardInterrupt),
            pytest.raises(SystemExit, match="1"),
        ):
            main(["cancel", "abc-123"])

        out = capsys.readouterr().out
        assert "Aborted" in out

    def test_cancel_partial_failure(self, capsys):
        """One failed cancellation reports partial success and exits 1."""
        mock_client = MagicMock()

        def side_effect(client, rid):
            if rid == "bad-id":
                raise RuntimeError("not found")

        with (
            patch("cdsapi.Client", return_value=mock_client),
            patch("cdsswarm._cds_utils.cancel_cds_request", side_effect=side_effect),
            pytest.raises(SystemExit, match="1"),
        ):
            main(["cancel", "good-id", "bad-id", "--yes"])

        out = capsys.readouterr().out
        assert "1/2" in out


class TestCmdCancelAllJobs:
    """Tests for cdsswarm cancel (no IDs, list all active)."""

    def test_old_api_no_ids_errors(self, capsys):
        """Old API client without IDs prints error."""
        mock_client = MagicMock(spec=["url", "session", "verify"])

        with (
            patch("cdsapi.Client", return_value=mock_client),
            pytest.raises(SystemExit, match="1"),
        ):
            main(["cancel"])

        err = capsys.readouterr().err
        assert "ecmwf-datastores" in err
        assert "cdsswarm cancel <id1>" in err

    def test_no_active_jobs(self, capsys):
        """No active jobs prints message and exits 0."""
        inner = MagicMock()
        inner.get_remote = MagicMock()
        mock_client = MagicMock()
        mock_client.client = inner

        with (
            patch("cdsapi.Client", return_value=mock_client),
            patch("cdsswarm._cds_utils.list_active_jobs", return_value=[]),
            pytest.raises(SystemExit, match="0"),
        ):
            main(["cancel"])

        out = capsys.readouterr().out
        assert "No active requests found" in out

    def test_cancel_all_confirmed(self, capsys):
        """Cancel all active jobs after user confirmation."""
        inner = MagicMock()
        inner.get_remote = MagicMock()
        mock_client = MagicMock()
        mock_client.client = inner

        jobs = [
            {
                "job_id": "job-1",
                "status": "accepted",
                "dataset": "era5",
                "created": "2024-01-01",
            },
            {
                "job_id": "job-2",
                "status": "running",
                "dataset": "era5",
                "created": "2024-01-02",
            },
        ]

        with (
            patch("cdsapi.Client", return_value=mock_client),
            patch("cdsswarm._cds_utils.list_active_jobs", return_value=jobs),
            patch("cdsswarm._cds_utils.cancel_cds_requests") as mock_bulk,
            patch("builtins.input", return_value="y"),
            pytest.raises(SystemExit, match="0"),
        ):
            main(["cancel"])

        mock_bulk.assert_called_once_with(mock_client, ["job-1", "job-2"])
        out = capsys.readouterr().out
        assert "2 active request(s)" in out
        assert "job-1" in out
        assert "job-2" in out
        assert "Cancelled 2" in out

    def test_cancel_all_with_yes(self, capsys):
        """--yes skips confirmation when cancelling all."""
        inner = MagicMock()
        inner.get_remote = MagicMock()
        mock_client = MagicMock()
        mock_client.client = inner

        jobs = [
            {"job_id": "job-1", "status": "running", "dataset": "ds", "created": ""},
        ]

        with (
            patch("cdsapi.Client", return_value=mock_client),
            patch("cdsswarm._cds_utils.list_active_jobs", return_value=jobs),
            patch("cdsswarm._cds_utils.cancel_cds_requests") as mock_bulk,
            pytest.raises(SystemExit, match="0"),
        ):
            main(["cancel", "--yes"])

        mock_bulk.assert_called_once_with(mock_client, ["job-1"])

    def test_cancel_all_denied(self, capsys):
        """Declining cancellation of all jobs aborts."""
        inner = MagicMock()
        inner.get_remote = MagicMock()
        mock_client = MagicMock()
        mock_client.client = inner

        jobs = [
            {"job_id": "job-1", "status": "running", "dataset": "ds", "created": ""},
        ]

        with (
            patch("cdsapi.Client", return_value=mock_client),
            patch("cdsswarm._cds_utils.list_active_jobs", return_value=jobs),
            patch("cdsswarm._cds_utils.cancel_cds_requests") as mock_bulk,
            patch("builtins.input", return_value="n"),
            pytest.raises(SystemExit, match="0"),
        ):
            main(["cancel"])

        mock_bulk.assert_not_called()
        out = capsys.readouterr().out
        assert "Aborted" in out

    def test_cancel_all_eof(self, capsys):
        """EOFError on cancel-all prompt aborts."""
        inner = MagicMock()
        inner.get_remote = MagicMock()
        mock_client = MagicMock()
        mock_client.client = inner

        jobs = [
            {"job_id": "job-1", "status": "running", "dataset": "ds", "created": ""},
        ]

        with (
            patch("cdsapi.Client", return_value=mock_client),
            patch("cdsswarm._cds_utils.list_active_jobs", return_value=jobs),
            patch("builtins.input", side_effect=EOFError),
            pytest.raises(SystemExit, match="1"),
        ):
            main(["cancel"])

        out = capsys.readouterr().out
        assert "Aborted" in out

    def test_cancel_all_keyboard_interrupt(self, capsys):
        """KeyboardInterrupt on cancel-all prompt aborts."""
        inner = MagicMock()
        inner.get_remote = MagicMock()
        mock_client = MagicMock()
        mock_client.client = inner

        jobs = [
            {"job_id": "job-1", "status": "running", "dataset": "ds", "created": ""},
        ]

        with (
            patch("cdsapi.Client", return_value=mock_client),
            patch("cdsswarm._cds_utils.list_active_jobs", return_value=jobs),
            patch("builtins.input", side_effect=KeyboardInterrupt),
            pytest.raises(SystemExit, match="1"),
        ):
            main(["cancel"])

    def test_cancel_all_api_error(self, capsys):
        """Bulk cancel API error reports failure and exits 1."""
        inner = MagicMock()
        inner.get_remote = MagicMock()
        mock_client = MagicMock()
        mock_client.client = inner

        jobs = [
            {"job_id": "job-1", "status": "running", "dataset": "ds", "created": ""},
        ]

        with (
            patch("cdsapi.Client", return_value=mock_client),
            patch("cdsswarm._cds_utils.list_active_jobs", return_value=jobs),
            patch(
                "cdsswarm._cds_utils.cancel_cds_requests",
                side_effect=RuntimeError("server error"),
            ),
            pytest.raises(SystemExit, match="1"),
        ):
            main(["cancel", "--yes"])

        err = capsys.readouterr().err
        assert "server error" in err


class TestMultiWriter:
    def test_write_to_multiple(self):
        a = io.StringIO()
        b = io.StringIO()
        w = _MultiWriter(a, b)
        w.write("hello")
        assert a.getvalue() == "hello"
        assert b.getvalue() == "hello"

    def test_flush(self):
        a = MagicMock()
        b = MagicMock()
        w = _MultiWriter(a, b)
        w.flush()
        a.flush.assert_called_once()
        b.flush.assert_called_once()


class TestRotateLogs:
    def test_keeps_last_n(self, tmp_dir):
        log_dir = os.path.join(tmp_dir, "logs")
        os.makedirs(log_dir)
        # Create 12 log files with staggered mtimes
        for i in range(12):
            p = os.path.join(log_dir, f"run_2026-01-{i + 1:02d}_00.00.00.log")
            with open(p, "w") as f:
                f.write(f"log {i}")
            os.utime(p, (i, i))

        _rotate_logs(log_dir, keep=10)

        remaining = sorted(os.listdir(log_dir))
        assert len(remaining) == 10
        # Oldest two should be gone
        assert "run_2026-01-01_00.00.00.log" not in remaining
        assert "run_2026-01-02_00.00.00.log" not in remaining

    def test_fewer_than_keep(self, tmp_dir):
        log_dir = os.path.join(tmp_dir, "logs")
        os.makedirs(log_dir)
        for i in range(3):
            p = os.path.join(log_dir, f"run_2026-01-{i + 1:02d}_00.00.00.log")
            with open(p, "w") as f:
                f.write("")
        _rotate_logs(log_dir, keep=10)
        assert len(os.listdir(log_dir)) == 3

    def test_ignores_non_matching_files(self, tmp_dir):
        log_dir = os.path.join(tmp_dir, "logs")
        os.makedirs(log_dir)
        # Non-matching file should be left alone
        other = os.path.join(log_dir, "other.txt")
        with open(other, "w") as f:
            f.write("")
        for i in range(12):
            p = os.path.join(log_dir, f"run_2026-01-{i + 1:02d}_00.00.00.log")
            with open(p, "w") as f:
                f.write("")
            os.utime(p, (i, i))
        _rotate_logs(log_dir, keep=10)
        # 10 run_*.log + 1 other.txt
        assert len(os.listdir(log_dir)) == 11
        assert os.path.isfile(other)


class TestAutoLog:
    def _write_tasks(self, tmp_dir):
        path = os.path.join(tmp_dir, "requests.json")
        with open(path, "w") as f:
            json.dump([{"dataset": "ds", "request": {}, "target": "out.grib"}], f)
        return path

    def test_auto_log_created(self, tmp_dir):
        """A run_*.log file is created in $XDG_STATE_HOME/cdsswarm/logs/."""
        path = self._write_tasks(tmp_dir)
        task = Task("ds", {}, "out.grib")
        results = [Result(task=task, success=True, start_time=100.0, end_time=200.0)]
        state_home = os.path.join(tmp_dir, "state")

        cfg_patches = _patch_config(tmp_dir)
        with (
            cfg_patches[0],
            cfg_patches[1],
            patch.dict(os.environ, {"XDG_STATE_HOME": state_home}),
            patch("cdsswarm.cli._resolve_mode", return_value="script"),
            patch("cdsswarm.cli._run_script", return_value=results),
            pytest.raises(SystemExit, match="0"),
        ):
            main([path])

        log_dir = os.path.join(state_home, "cdsswarm", "logs")
        logs = [f for f in os.listdir(log_dir) if f.startswith("run_")]
        assert len(logs) == 1

    def test_auto_log_rotation(self, tmp_dir):
        """Old auto-log files are rotated after a run."""
        path = self._write_tasks(tmp_dir)
        task = Task("ds", {}, "out.grib")
        results = [Result(task=task, success=True, start_time=100.0, end_time=200.0)]
        state_home = os.path.join(tmp_dir, "state")
        log_dir = os.path.join(state_home, "cdsswarm", "logs")
        os.makedirs(log_dir)

        # Pre-create 12 old log files
        for i in range(12):
            p = os.path.join(log_dir, f"run_2020-01-{i + 1:02d}_00.00.00.log")
            with open(p, "w") as f:
                f.write("")
            os.utime(p, (i, i))

        cfg_patches = _patch_config(tmp_dir)
        with (
            cfg_patches[0],
            cfg_patches[1],
            patch.dict(os.environ, {"XDG_STATE_HOME": state_home}),
            patch("cdsswarm.cli._resolve_mode", return_value="script"),
            patch("cdsswarm.cli._run_script", return_value=results),
            pytest.raises(SystemExit, match="0"),
        ):
            main([path])

        logs = [f for f in os.listdir(log_dir) if f.startswith("run_")]
        assert len(logs) == 10

    def test_auto_log_with_user_log(self, tmp_dir):
        """When --log is given, content appears in both auto-log and user file."""
        path = self._write_tasks(tmp_dir)
        task = Task("ds", {}, "out.grib")
        results = [Result(task=task, success=True, start_time=100.0, end_time=200.0)]
        state_home = os.path.join(tmp_dir, "state")
        user_log_path = os.path.join(tmp_dir, "user.log")

        def fake_run_script(tasks, workers, skip, reuse, retries, **kw):
            log_file = kw.get("log_file")
            if log_file:
                log_file.write("test log line\n")
                log_file.flush()
            return results

        cfg_patches = _patch_config(tmp_dir)
        with (
            cfg_patches[0],
            cfg_patches[1],
            patch.dict(os.environ, {"XDG_STATE_HOME": state_home}),
            patch("cdsswarm.cli._resolve_mode", return_value="script"),
            patch("cdsswarm.cli._run_script", side_effect=fake_run_script),
            pytest.raises(SystemExit, match="0"),
        ):
            main([path, "--log", user_log_path])

        # User log should contain the line
        assert os.path.isfile(user_log_path)
        with open(user_log_path) as f:
            assert "test log line" in f.read()

        # Auto-log should also contain the line
        log_dir = os.path.join(state_home, "cdsswarm", "logs")
        auto_logs = [f for f in os.listdir(log_dir) if f.startswith("run_")]
        assert len(auto_logs) == 1
        with open(os.path.join(log_dir, auto_logs[0])) as f:
            assert "test log line" in f.read()
