"""Tests for CDS utility functions."""

from cdsswarm._cds_utils import parse_cds_status, parse_request_id


class TestParseCdsStatus:
    def test_old_api_queued(self):
        assert parse_cds_status("Request is queued") == "accepted"

    def test_old_api_running(self):
        assert parse_cds_status("Request is running") == "running"

    def test_old_api_completed(self):
        assert parse_cds_status("Request is completed") == "successful"

    def test_old_api_failed(self):
        assert parse_cds_status("Request is failed") == "failed"

    def test_new_api_accepted(self):
        assert parse_cds_status("status has been updated to accepted") == "accepted"

    def test_new_api_successful(self):
        assert parse_cds_status("status has been updated to successful") == "successful"

    def test_no_match(self):
        assert parse_cds_status("some random message") is None

    def test_unknown_state(self):
        assert parse_cds_status("Request is unknown_state") is None


class TestParseRequestId:
    def test_old_api_format(self):
        msg = "Request ID is abc-123-def, sleep 10"
        assert parse_request_id(msg) == "abc-123-def"

    def test_new_api_format(self):
        msg = "Request ID is 550e8400-e29b-41d4-a716-446655440000"
        assert parse_request_id(msg) == "550e8400-e29b-41d4-a716-446655440000"

    def test_no_match(self):
        assert parse_request_id("no request id here") is None
