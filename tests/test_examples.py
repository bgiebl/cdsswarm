"""Smoke tests for the scripts in examples/.

The examples call the library API directly but live outside the package, so
nothing else covers them: CI runs ``mypy src/cdsswarm/`` only, and even adding
``examples/`` there would not help — ``PlainTextAdapter.__init__`` has an
unannotated parameter, so mypy treats it as untyped and skips checking calls
to it. Only running the code catches a stale call site.
"""

import importlib.util
import pathlib
import sys
import time

import pytest

EXAMPLES_DIR = pathlib.Path(__file__).resolve().parent.parent / "examples"

EXAMPLES = sorted(EXAMPLES_DIR.glob("*.py"))


def _load(path: pathlib.Path):
    """Import an example by path without executing its ``main()`` guard."""
    spec = importlib.util.spec_from_file_location(f"_example_{path.stem}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestExamplesImport:
    @pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.name)
    def test_imports(self, path):
        """Every example imports against the current public API."""
        _load(path)


class TestDemoScript:
    def test_full_run(self, monkeypatch, capsys):
        """demo_script drives the whole adapter surface without raising.

        Regression guard for the ``PlainTextAdapter(interactive=...)`` call
        that survived removal of the parameter: constructing the adapter is
        the first thing main() does, so a stale kwarg is a TypeError here.
        """
        monkeypatch.setattr(time, "sleep", lambda *_a, **_kw: None)
        monkeypatch.setattr(sys, "argv", ["demo_script.py", "-t", "3", "-w", "2", "-s"])

        _load(EXAMPLES_DIR / "demo_script.py").main()

        out = capsys.readouterr().out
        assert "Traceback" not in out
        assert "Summary" in out
