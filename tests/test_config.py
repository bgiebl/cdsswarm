"""Tests for config file support."""

from pathlib import Path
from unittest.mock import patch

import pytest

from cdsswarm.config import (
    DEFAULTS,
    _load_toml,
    _normalize_key,
    _validate_config,
    load_config,
    resolve_settings,
)


class TestNormalizeKey:
    def test_hyphen_to_underscore(self):
        assert _normalize_key("max-retries") == "max_retries"

    def test_no_hyphens(self):
        assert _normalize_key("workers") == "workers"

    def test_multiple_hyphens(self):
        assert _normalize_key("skip-existing") == "skip_existing"

    def test_already_underscore(self):
        assert _normalize_key("output_dir") == "output_dir"


class TestLoadToml:
    def test_missing_file(self):
        assert _load_toml(Path("/nonexistent/.cdsswarm.toml")) == {}

    def test_valid_file(self, tmp_path):
        cfg = tmp_path / ".cdsswarm.toml"
        cfg.write_text('workers = 8\nmax-retries = 5\nmode = "script"\n')
        result = _load_toml(cfg)
        assert result == {"workers": 8, "max_retries": 5, "mode": "script"}

    def test_unknown_keys_ignored(self, tmp_path):
        cfg = tmp_path / ".cdsswarm.toml"
        cfg.write_text('workers = 2\nunknown-key = "foo"\n')
        result = _load_toml(cfg)
        assert result == {"workers": 2}
        assert "unknown_key" not in result

    def test_booleans(self, tmp_path):
        cfg = tmp_path / ".cdsswarm.toml"
        cfg.write_text("reuse = false\nskip-existing = true\n")
        result = _load_toml(cfg)
        assert result == {"reuse": False, "skip_existing": True}

    def test_string_value(self, tmp_path):
        cfg = tmp_path / ".cdsswarm.toml"
        cfg.write_text('output-dir = "data/cds/"\n')
        result = _load_toml(cfg)
        assert result == {"output_dir": "data/cds/"}


class TestValidateConfig:
    def test_valid_config(self):
        _validate_config({"workers": 4, "mode": "script"}, "test")

    def test_wrong_type(self):
        with pytest.raises(ValueError, match="must be int"):
            _validate_config({"workers": "four"}, "test")

    def test_invalid_mode(self):
        with pytest.raises(ValueError, match="must be one of"):
            _validate_config({"mode": "turbo"}, "test")

    def test_workers_below_one(self):
        with pytest.raises(ValueError, match="must be >= 1"):
            _validate_config({"workers": 0}, "test")

    def test_max_retries_below_one(self):
        with pytest.raises(ValueError, match="must be >= 1"):
            _validate_config({"max_retries": 0}, "test")


class TestLoadConfig:
    def test_no_files(self, tmp_path):
        with (
            patch("cdsswarm.config.USER_CONFIG_PATH", tmp_path / "nope.toml"),
            patch("cdsswarm.config.Path.cwd", return_value=tmp_path),
        ):
            result = load_config()
        assert result == {}

    def test_user_only(self, tmp_path):
        user_cfg = tmp_path / "user.toml"
        user_cfg.write_text("workers = 16\n")
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        with (
            patch("cdsswarm.config.USER_CONFIG_PATH", user_cfg),
            patch("cdsswarm.config.Path.cwd", return_value=project_dir),
        ):
            result = load_config()
        assert result == {"workers": 16}

    def test_project_overrides_user(self, tmp_path):
        user_cfg = tmp_path / "user.toml"
        user_cfg.write_text("workers = 16\nreuse = false\n")
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        project_cfg = project_dir / ".cdsswarm.toml"
        project_cfg.write_text("workers = 2\n")
        with (
            patch("cdsswarm.config.USER_CONFIG_PATH", user_cfg),
            patch("cdsswarm.config.Path.cwd", return_value=project_dir),
        ):
            result = load_config()
        assert result["workers"] == 2
        assert result["reuse"] is False

    def test_invalid_config_raises(self, tmp_path):
        user_cfg = tmp_path / "user.toml"
        user_cfg.write_text('workers = "bad"\n')
        with (
            patch("cdsswarm.config.USER_CONFIG_PATH", user_cfg),
            patch("cdsswarm.config.Path.cwd", return_value=tmp_path),
        ):
            with pytest.raises(ValueError, match="must be int"):
                load_config()


class TestResolveSettings:
    def test_all_defaults(self, tmp_path):
        with (
            patch("cdsswarm.config.USER_CONFIG_PATH", tmp_path / "nope.toml"),
            patch("cdsswarm.config.Path.cwd", return_value=tmp_path),
        ):
            result = resolve_settings({})
        assert result == DEFAULTS

    def test_config_overrides_defaults(self, tmp_path):
        user_cfg = tmp_path / "user.toml"
        user_cfg.write_text("workers = 12\n")
        with (
            patch("cdsswarm.config.USER_CONFIG_PATH", user_cfg),
            patch("cdsswarm.config.Path.cwd", return_value=tmp_path),
        ):
            result = resolve_settings({})
        assert result["workers"] == 12
        assert result["mode"] == "auto"  # default preserved

    def test_cli_overrides_config(self, tmp_path):
        user_cfg = tmp_path / "user.toml"
        user_cfg.write_text("workers = 12\n")
        with (
            patch("cdsswarm.config.USER_CONFIG_PATH", user_cfg),
            patch("cdsswarm.config.Path.cwd", return_value=tmp_path),
        ):
            result = resolve_settings({"workers": 1})
        assert result["workers"] == 1

    def test_cli_none_falls_through(self, tmp_path):
        user_cfg = tmp_path / "user.toml"
        user_cfg.write_text("workers = 12\n")
        with (
            patch("cdsswarm.config.USER_CONFIG_PATH", user_cfg),
            patch("cdsswarm.config.Path.cwd", return_value=tmp_path),
        ):
            result = resolve_settings({"workers": None, "mode": "script"})
        assert result["workers"] == 12
        assert result["mode"] == "script"

    def test_full_cascade(self, tmp_path):
        user_cfg = tmp_path / "user.toml"
        user_cfg.write_text('workers = 16\nmode = "script"\nreuse = false\n')
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        project_cfg = project_dir / ".cdsswarm.toml"
        project_cfg.write_text("workers = 4\n")
        with (
            patch("cdsswarm.config.USER_CONFIG_PATH", user_cfg),
            patch("cdsswarm.config.Path.cwd", return_value=project_dir),
        ):
            result = resolve_settings({"workers": None, "mode": "interactive"})
        # workers: default=4, user=16, project=4, CLI=None → 4 (project)
        assert result["workers"] == 4
        # mode: default=auto, user=script, project=-, CLI=interactive → interactive
        assert result["mode"] == "interactive"
        # reuse: default=True, user=False, project=-, CLI=- → False (user)
        assert result["reuse"] is False
        # max_retries: default only
        assert result["max_retries"] == 3
