"""Tests for custom exception types."""

from cdsswarm.exceptions import ChecksumMismatchError, ConfigError, RequestFileError


class TestExceptionHierarchy:
    """Custom exceptions are subclasses of ValueError where appropriate."""

    def test_config_error_is_value_error(self):
        exc = ConfigError("bad config")
        assert isinstance(exc, ValueError)

    def test_request_file_error_is_value_error(self):
        exc = RequestFileError("bad file")
        assert isinstance(exc, ValueError)

    def test_checksum_mismatch_error_is_exception(self):
        exc = ChecksumMismatchError("/tmp/data.grib", "abc123")
        assert isinstance(exc, Exception)
        assert not isinstance(exc, ValueError)


class TestChecksumMismatchError:
    def test_attributes(self):
        exc = ChecksumMismatchError("/tmp/data.grib", "abc123")
        assert exc.path == "/tmp/data.grib"
        assert exc.expected == "abc123"

    def test_message(self):
        exc = ChecksumMismatchError("/tmp/data.grib", "abc123")
        assert "data.grib" in str(exc)
        assert "abc123" in str(exc)


class TestPublicImports:
    """Exception types are importable from the top-level package."""

    def test_import_from_package(self):
        import cdsswarm

        assert cdsswarm.ConfigError is ConfigError
        assert cdsswarm.RequestFileError is RequestFileError
        assert cdsswarm.ChecksumMismatchError is ChecksumMismatchError
