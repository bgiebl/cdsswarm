"""Config file support for cdsswarm (.cdsswarm.toml)."""

from __future__ import annotations

from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore[no-redef]

# Single source of truth for all default values.
DEFAULTS: dict[str, object] = {
    "workers": 4,
    "max_retries": 3,
    "resume": True,
    "reuse": True,
    "skip_existing": True,
    "ignore_warnings": False,
    "mode": "auto",
    "output_dir": "",
    "log": "",
    "summary": "",
    "post_hook": "",
}

_VALID_KEYS = set(DEFAULTS)

USER_CONFIG_PATH = Path.home() / ".cdsswarm.toml"
PROJECT_CONFIG_NAME = ".cdsswarm.toml"

_TYPE_CHECKS: dict[str, type | tuple[type, ...]] = {
    "workers": int,
    "max_retries": int,
    "resume": bool,
    "reuse": bool,
    "skip_existing": bool,
    "ignore_warnings": bool,
    "mode": str,
    "output_dir": str,
    "log": str,
    "summary": str,
    "post_hook": str,
}

_VALUE_CHECKS: dict[str, object] = {
    "mode": ("auto", "interactive", "script"),
}


def _normalize_key(key: str) -> str:
    """Convert TOML hyphenated key to Python underscore key."""
    return key.replace("-", "_")


def _load_toml(path: Path) -> dict[str, object]:
    """Read a TOML file and return normalized, filtered config dict."""
    if not path.is_file():
        return {}
    with open(path, "rb") as f:
        raw = tomllib.load(f)
    return {
        _normalize_key(k): v for k, v in raw.items() if _normalize_key(k) in _VALID_KEYS
    }


def _validate_config(config: dict[str, object], source: str) -> None:
    """Validate types and values. Raises ConfigError on problems."""
    from .exceptions import ConfigError

    for key, value in config.items():
        expected = _TYPE_CHECKS.get(key)
        if expected and not isinstance(value, expected):
            raise ConfigError(
                f"{source}: '{key}' must be {expected.__name__}, "
                f"got {type(value).__name__}"
            )
        allowed = _VALUE_CHECKS.get(key)
        if allowed and value not in allowed:
            raise ConfigError(
                f"{source}: '{key}' must be one of {allowed}, got '{value}'"
            )
        if key == "workers" and isinstance(value, int) and value < 1:
            raise ConfigError(f"{source}: 'workers' must be >= 1, got {value}")
        if key == "max_retries" and isinstance(value, int) and value < 1:
            raise ConfigError(f"{source}: 'max_retries' must be >= 1, got {value}")


def load_config() -> dict[str, object]:
    """Load and merge user-global and project-local config files.

    Project-local values override user-global values.
    """
    user_cfg = _load_toml(USER_CONFIG_PATH)
    if user_cfg:
        _validate_config(user_cfg, str(USER_CONFIG_PATH))

    project_path = Path.cwd() / PROJECT_CONFIG_NAME
    project_cfg = _load_toml(project_path)
    if project_cfg:
        _validate_config(project_cfg, str(project_path))

    merged = {**user_cfg, **project_cfg}
    return merged


def resolve_settings(cli_args: dict[str, object]) -> dict[str, object]:
    """Three-tier merge: CLI (non-None) > config files > defaults.

    Args:
        cli_args: Dict of CLI argument values. Keys with None values are
            treated as "not provided" and fall through to config/defaults.

    Returns:
        Fully resolved settings dict with no None values.
    """
    config = load_config()
    cli_overrides = {k: v for k, v in cli_args.items() if v is not None}
    resolved = {**DEFAULTS, **config, **cli_overrides}
    return resolved
