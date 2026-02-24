"""Tests for custom exception types."""

from cdsswarm.exceptions import ConfigError, RequestFileError


class TestExceptionHierarchy:
    """Custom exceptions are subclasses of ValueError where appropriate."""

    def test_config_error_is_value_error(self):
        exc = ConfigError("bad config")
        assert isinstance(exc, ValueError)

    def test_request_file_error_is_value_error(self):
        exc = RequestFileError("bad file")
        assert isinstance(exc, ValueError)


class TestPublicImports:
    """Exception types are importable from the top-level package."""

    def test_import_from_package(self):
        import cdsswarm

        assert cdsswarm.ConfigError is ConfigError
        assert cdsswarm.RequestFileError is RequestFileError
