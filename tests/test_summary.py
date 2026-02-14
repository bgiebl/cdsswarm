"""Tests for summary module."""

import csv
import json
import os
import tempfile

import pytest

from cdsswarm.core import Result, Task
from cdsswarm.summary import (
    _format_bytes,
    _format_duration,
    build_summary,
    export_summary,
    print_summary,
)


class TestFormatDuration:
    def test_zero(self):
        assert _format_duration(0) == "\u2014"

    def test_negative(self):
        assert _format_duration(-5) == "\u2014"

    def test_seconds(self):
        assert _format_duration(12) == "12s"

    def test_minutes_seconds(self):
        assert _format_duration(330) == "5m30s"

    def test_hours_minutes_seconds(self):
        assert _format_duration(5025) == "1h23m45s"

    def test_exact_minute(self):
        assert _format_duration(60) == "1m00s"

    def test_exact_hour(self):
        assert _format_duration(3600) == "1h00m00s"


class TestFormatBytes:
    def test_zero(self):
        assert _format_bytes(0) == "\u2014"

    def test_negative(self):
        assert _format_bytes(-1) == "\u2014"

    def test_bytes(self):
        assert _format_bytes(500) == "500 B"

    def test_kilobytes(self):
        result = _format_bytes(1536)
        assert "KB" in result

    def test_megabytes(self):
        result = _format_bytes(5 * 1024 * 1024)
        assert "MB" in result

    def test_gigabytes(self):
        result = _format_bytes(int(1.5 * 1024**3))
        assert "GB" in result


def _make_result(target, success=True, error="", start=100.0, end=200.0, size=1024):
    task = Task("dataset", {"var": "temp"}, target)
    return Result(
        task=task,
        success=success,
        error=error,
        start_time=start,
        end_time=end,
        file_size=size,
    )


class TestBuildSummary:
    def test_all_success(self):
        results = [
            _make_result("a.grib", size=1000),
            _make_result("b.grib", size=2000),
        ]
        s = build_summary(results, 100.0, 200.0)
        assert s["totals"]["tasks_ok"] == 2
        assert s["totals"]["tasks_failed"] == 0
        assert s["totals"]["tasks_total"] == 2
        assert s["totals"]["total_size"] == 3000
        assert s["totals"]["wall_duration"] == 100.0
        assert len(s["tasks"]) == 2

    def test_mixed(self):
        results = [
            _make_result("a.grib"),
            _make_result("b.grib", success=False, error="timeout", size=0),
        ]
        s = build_summary(results, 100.0, 200.0)
        assert s["totals"]["tasks_ok"] == 1
        assert s["totals"]["tasks_failed"] == 1

    def test_empty(self):
        s = build_summary([], 100.0, 200.0)
        assert s["totals"]["tasks_total"] == 0
        assert s["totals"]["total_size"] == 0
        assert s["tasks"] == []

    def test_timing_calculations(self):
        r = _make_result("a.grib", start=100.0, end=145.0)
        s = build_summary([r], 100.0, 200.0)
        assert s["tasks"][0]["duration"] == 45.0

    def test_timestamps_in_totals(self):
        s = build_summary([], 1705353060.0, 1705358085.0)
        assert s["totals"]["started"] != ""
        assert s["totals"]["finished"] != ""


class TestPrintSummary:
    def test_output_contains_header(self):
        results = [_make_result("output.grib")]
        lines = []
        print_summary(results, 100.0, 200.0, write_fn=lines.append)
        text = "\n".join(lines)
        assert "Summary" in text
        assert "=" * 60 in text

    def test_output_contains_task_rows(self):
        results = [
            _make_result("a.grib"),
            _make_result("b.grib", success=False, error="fail"),
        ]
        lines = []
        print_summary(results, 100.0, 200.0, write_fn=lines.append)
        text = "\n".join(lines)
        assert "a.grib" in text
        assert "b.grib" in text
        assert "FAILED" in text

    def test_output_contains_error_section(self):
        results = [_make_result("bad.grib", success=False, error="Connection timeout")]
        lines = []
        print_summary(results, 100.0, 200.0, write_fn=lines.append)
        text = "\n".join(lines)
        assert "Errors:" in text
        assert "Connection timeout" in text

    def test_no_error_section_when_all_ok(self):
        results = [_make_result("good.grib")]
        lines = []
        print_summary(results, 100.0, 200.0, write_fn=lines.append)
        text = "\n".join(lines)
        assert "Errors:" not in text


class TestExportSummary:
    def test_json_roundtrip(self):
        results = [_make_result("a.grib"), _make_result("b.grib")]
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "report.json")
            export_summary(results, 100.0, 200.0, path)
            with open(path) as f:
                data = json.load(f)
            assert data["totals"]["tasks_total"] == 2
            assert len(data["tasks"]) == 2

    def test_csv_roundtrip(self):
        results = [_make_result("a.grib"), _make_result("b.grib")]
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "report.csv")
            export_summary(results, 100.0, 200.0, path)
            with open(path) as f:
                reader = csv.reader(f)
                rows = list(reader)
            assert rows[0] == [
                "target",
                "status",
                "error",
                "warnings",
                "duration",
                "file_size",
                "start_time",
                "end_time",
            ]
            assert len(rows) == 3  # header + 2 tasks

    def test_bad_extension_raises(self):
        results = [_make_result("a.grib")]
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "report.txt")
            with pytest.raises(ValueError, match="Unsupported summary format"):
                export_summary(results, 100.0, 200.0, path)
