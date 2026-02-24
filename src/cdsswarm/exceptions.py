"""Custom exception types for cdsswarm."""


class ConfigError(ValueError):
    """Invalid configuration value in config file or CLI flags."""


class RequestFileError(ValueError):
    """Invalid or unrecognized request file format."""
