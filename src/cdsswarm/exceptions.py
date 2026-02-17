"""Custom exception types for cdsswarm."""


class ConfigError(ValueError):
    """Invalid configuration value in config file or CLI flags."""


class RequestFileError(ValueError):
    """Invalid or unrecognized request file format."""


class ChecksumMismatchError(Exception):
    """Download checksum does not match the expected value."""

    def __init__(self, path: str, expected: str):
        self.path = path
        self.expected = expected
        super().__init__(f"Checksum mismatch for '{path}' (expected {expected})")
