"""Tests for session state persistence."""

import json
import os
import tempfile
from unittest.mock import patch

import pytest

from cdsswarm.core import Task
from cdsswarm.state import SessionState, session_path


@pytest.fixture
def tmp_dir():
    with tempfile.TemporaryDirectory() as d:
        yield d


def _make_tasks(tmp_dir, count=3):
    return [
        Task(
            dataset="reanalysis-era5-single-levels",
            request={"variable": [f"var_{i}"], "year": ["2024"]},
            target=os.path.join(tmp_dir, f"output_{i}.grib"),
        )
        for i in range(count)
    ]


class TestSessionPath:
    def test_deterministic(self, tmp_dir):
        """Same inputs produce the same path."""
        req = os.path.join(tmp_dir, "requests.json")
        open(req, "w").close()
        p1 = session_path(req)
        p2 = session_path(req)
        assert p1 == p2

    def test_different_files(self, tmp_dir):
        """Different request files produce different paths."""
        a = os.path.join(tmp_dir, "a.json")
        b = os.path.join(tmp_dir, "b.json")
        open(a, "w").close()
        open(b, "w").close()
        assert session_path(a) != session_path(b)

    def test_different_output_dirs(self, tmp_dir):
        """Different output_dirs produce different paths."""
        req = os.path.join(tmp_dir, "requests.json")
        open(req, "w").close()
        assert session_path(req, "dir_a") != session_path(req, "dir_b")

    def test_respects_xdg_cache_home(self, tmp_dir):
        """session_path uses $XDG_CACHE_HOME when set."""
        req = os.path.join(tmp_dir, "requests.json")
        open(req, "w").close()
        custom_cache = os.path.join(tmp_dir, "my_cache")
        with patch.dict(os.environ, {"XDG_CACHE_HOME": custom_cache}):
            p = session_path(req)
        assert p.startswith(os.path.join(custom_cache, "cdsswarm", "sessions"))

    def test_default_cache_dir(self, tmp_dir):
        """Without XDG_CACHE_HOME, uses ~/.cache."""
        req = os.path.join(tmp_dir, "requests.json")
        open(req, "w").close()
        with patch.dict(os.environ, {}, clear=True):
            p = session_path(req)
        assert ".cache/cdsswarm/sessions" in p

    def test_path_ends_with_json(self, tmp_dir):
        req = os.path.join(tmp_dir, "requests.json")
        open(req, "w").close()
        assert session_path(req).endswith(".json")


class TestSessionState:
    def test_new_creates_all_pending(self, tmp_dir):
        tasks = _make_tasks(tmp_dir, count=3)
        session = SessionState.new("requests.json", tasks)
        assert len(session.tasks) == 3
        for ts in session.tasks.values():
            assert ts.status == "pending"
            assert ts.dataset == "reanalysis-era5-single-levels"

    def test_new_records_settings(self, tmp_dir):
        tasks = _make_tasks(tmp_dir, count=1)
        session = SessionState.new(
            "requests.json", tasks, settings={"workers": 4, "max_retries": 3}
        )
        assert session.settings["workers"] == 4

    def test_new_resolves_request_file(self, tmp_dir):
        tasks = _make_tasks(tmp_dir, count=1)
        session = SessionState.new("requests.json", tasks)
        assert os.path.isabs(session.request_file)

    def test_save_load_roundtrip(self, tmp_dir):
        tasks = _make_tasks(tmp_dir, count=2)
        session = SessionState.new("requests.json", tasks, settings={"workers": 4})
        session.mark_completed(tasks[0].target, 100.0, 200.0, 5000, request_id="rid-1")

        path = os.path.join(tmp_dir, "session.json")
        session.save(path)
        loaded = SessionState.load(path)

        assert loaded.version == 1
        assert loaded.request_file == session.request_file
        assert loaded.settings == {"workers": 4}
        assert len(loaded.tasks) == 2
        ts0 = loaded.tasks[tasks[0].target]
        assert ts0.status == "completed"
        assert ts0.start_time == 100.0
        assert ts0.end_time == 200.0
        assert ts0.file_size == 5000
        assert ts0.cds_request_id == "rid-1"
        ts1 = loaded.tasks[tasks[1].target]
        assert ts1.status == "pending"

    def test_atomic_write_no_tmp_leftover(self, tmp_dir):
        """Atomic write cleans up the .tmp file."""
        tasks = _make_tasks(tmp_dir, count=1)
        session = SessionState.new("requests.json", tasks)
        path = os.path.join(tmp_dir, "session.json")
        session.save(path)

        assert os.path.isfile(path)
        assert not os.path.exists(path + ".tmp")

    def test_save_creates_directories(self, tmp_dir):
        tasks = _make_tasks(tmp_dir, count=1)
        session = SessionState.new("requests.json", tasks)
        path = os.path.join(tmp_dir, "a", "b", "c", "session.json")
        session.save(path)
        assert os.path.isfile(path)

    def test_mark_completed(self, tmp_dir):
        tasks = _make_tasks(tmp_dir, count=1)
        session = SessionState.new("requests.json", tasks)
        target = tasks[0].target
        session.mark_completed(target, 10.0, 20.0, 1024, request_id="r1")

        ts = session.tasks[target]
        assert ts.status == "completed"
        assert ts.error == ""
        assert ts.start_time == 10.0
        assert ts.end_time == 20.0
        assert ts.file_size == 1024
        assert ts.cds_request_id == "r1"

    def test_mark_failed(self, tmp_dir):
        tasks = _make_tasks(tmp_dir, count=1)
        session = SessionState.new("requests.json", tasks)
        target = tasks[0].target
        session.mark_failed(target, "timeout", 10.0, 20.0, request_id="r2")

        ts = session.tasks[target]
        assert ts.status == "failed"
        assert ts.error == "timeout"
        assert ts.cds_request_id == "r2"

    def test_mark_unknown_target_is_noop(self, tmp_dir):
        tasks = _make_tasks(tmp_dir, count=1)
        session = SessionState.new("requests.json", tasks)
        session.mark_completed("nonexistent.grib", 0.0, 0.0, 0)
        session.mark_failed("nonexistent.grib", "err", 0.0, 0.0)
        # No error raised

    def test_set_request_id(self, tmp_dir):
        tasks = _make_tasks(tmp_dir, count=1)
        session = SessionState.new("requests.json", tasks)
        target = tasks[0].target
        session.set_request_id(target, "new-rid")
        assert session.tasks[target].cds_request_id == "new-rid"

    def test_set_request_id_unknown_target(self, tmp_dir):
        tasks = _make_tasks(tmp_dir, count=1)
        session = SessionState.new("requests.json", tasks)
        session.set_request_id("unknown.grib", "rid")  # No error

    def test_pending_targets_all_pending(self, tmp_dir):
        tasks = _make_tasks(tmp_dir, count=3)
        session = SessionState.new("requests.json", tasks)
        pending = session.pending_targets()
        assert len(pending) == 3

    def test_pending_targets_some_done(self, tmp_dir):
        tasks = _make_tasks(tmp_dir, count=3)
        session = SessionState.new("requests.json", tasks)
        # Complete first task and create the file
        target0 = tasks[0].target
        with open(target0, "w") as f:
            f.write("data")
        session.mark_completed(target0, 10.0, 20.0, 1024)

        pending = session.pending_targets()
        assert target0 not in pending
        assert len(pending) == 2

    def test_pending_targets_file_deleted_redownloads(self, tmp_dir):
        """Completed task whose file is missing shows as pending."""
        tasks = _make_tasks(tmp_dir, count=1)
        session = SessionState.new("requests.json", tasks)
        target = tasks[0].target
        session.mark_completed(target, 10.0, 20.0, 1024)
        # File does NOT exist on disk
        pending = session.pending_targets()
        assert target in pending

    def test_reuse_map_returns_ids_for_pending(self, tmp_dir):
        tasks = _make_tasks(tmp_dir, count=2)
        session = SessionState.new("requests.json", tasks)
        session.set_request_id(tasks[0].target, "rid-0")
        session.set_request_id(tasks[1].target, "rid-1")

        reuse = session.reuse_map()
        assert reuse[tasks[0].target] == "rid-0"
        assert reuse[tasks[1].target] == "rid-1"

    def test_reuse_map_excludes_completed(self, tmp_dir):
        tasks = _make_tasks(tmp_dir, count=2)
        session = SessionState.new("requests.json", tasks)
        session.set_request_id(tasks[0].target, "rid-0")
        session.set_request_id(tasks[1].target, "rid-1")
        # Complete first task and create file
        with open(tasks[0].target, "w") as f:
            f.write("data")
        session.mark_completed(tasks[0].target, 10.0, 20.0, 1024)

        reuse = session.reuse_map()
        assert tasks[0].target not in reuse
        assert reuse[tasks[1].target] == "rid-1"

    def test_reuse_map_empty_ids_excluded(self, tmp_dir):
        tasks = _make_tasks(tmp_dir, count=1)
        session = SessionState.new("requests.json", tasks)
        # No request ID set
        reuse = session.reuse_map()
        assert reuse == {}

    def test_load_corrupt_file_raises(self, tmp_dir):
        path = os.path.join(tmp_dir, "bad.json")
        with open(path, "w") as f:
            f.write("{not valid json")
        with pytest.raises(json.JSONDecodeError):
            SessionState.load(path)

    def test_load_wrong_version_raises(self, tmp_dir):
        path = os.path.join(tmp_dir, "v99.json")
        with open(path, "w") as f:
            json.dump({"version": 99, "tasks": {}}, f)
        with pytest.raises(ValueError, match="Unsupported session version"):
            SessionState.load(path)

    def test_load_missing_optional_fields_defaults(self, tmp_dir):
        """Loading a file with missing optional fields uses defaults."""
        path = os.path.join(tmp_dir, "minimal.json")
        with open(path, "w") as f:
            json.dump(
                {
                    "version": 1,
                    "tasks": {
                        "out.grib": {
                            "dataset": "ds",
                            "request": {},
                            "status": "pending",
                        }
                    },
                },
                f,
            )
        loaded = SessionState.load(path)
        assert loaded.request_file == ""
        assert loaded.started == ""
        assert loaded.settings == {}
        ts = loaded.tasks["out.grib"]
        assert ts.error == ""
        assert ts.cds_request_id == ""
        assert ts.start_time == 0.0
        assert ts.end_time == 0.0
        assert ts.file_size == 0

    def test_load_not_object_raises(self, tmp_dir):
        """Loading a file that is not a JSON object raises."""
        path = os.path.join(tmp_dir, "array.json")
        with open(path, "w") as f:
            json.dump([1, 2, 3], f)
        with pytest.raises(ValueError, match="expected object"):
            SessionState.load(path)

    def test_pending_targets_failed_included(self, tmp_dir):
        """Failed tasks are included in pending_targets."""
        tasks = _make_tasks(tmp_dir, count=1)
        session = SessionState.new("requests.json", tasks)
        session.mark_failed(tasks[0].target, "err", 10.0, 20.0)
        pending = session.pending_targets()
        assert tasks[0].target in pending

    def test_clear_stale_request_ids_clears_pending_and_failed(self, tmp_dir):
        """clear_stale_request_ids clears IDs for pending and failed tasks."""
        tasks = _make_tasks(tmp_dir, count=3)
        session = SessionState.new("requests.json", tasks)
        session.set_request_id(tasks[0].target, "rid-0")
        session.set_request_id(tasks[1].target, "rid-1")
        session.set_request_id(tasks[2].target, "rid-2")
        session.mark_failed(tasks[1].target, "dismissed", 10.0, 20.0)

        cleared = session.clear_stale_request_ids()
        assert cleared == 3  # all non-completed
        for ts in session.tasks.values():
            assert ts.cds_request_id == ""

    def test_clear_stale_request_ids_preserves_completed(self, tmp_dir):
        """clear_stale_request_ids does not touch completed tasks."""
        tasks = _make_tasks(tmp_dir, count=2)
        session = SessionState.new("requests.json", tasks)
        session.set_request_id(tasks[0].target, "rid-0")
        session.set_request_id(tasks[1].target, "rid-1")
        with open(tasks[0].target, "w") as f:
            f.write("data")
        session.mark_completed(tasks[0].target, 10.0, 20.0, 1024)

        cleared = session.clear_stale_request_ids()
        assert cleared == 1  # only the pending one
        assert session.tasks[tasks[0].target].cds_request_id == "rid-0"
        assert session.tasks[tasks[1].target].cds_request_id == ""

    def test_clear_stale_request_ids_empty_when_nothing_to_clear(self, tmp_dir):
        """clear_stale_request_ids returns 0 when no IDs to clear."""
        tasks = _make_tasks(tmp_dir, count=1)
        session = SessionState.new("requests.json", tasks)
        assert session.clear_stale_request_ids() == 0
