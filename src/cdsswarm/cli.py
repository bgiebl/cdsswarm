"""Command-line interface for cdsswarm."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

from .adapters import LoggingAdapter, PlainTextAdapter, TextualAdapter
from .core import SwarmDownloader, Task
from .summary import export_summary, print_summary


def load_requests(path: str) -> list[Task]:
    """Load download tasks from a JSON or YAML file.

    Supported formats:

    List format (JSON/YAML):
        [
            {
                "dataset": "reanalysis-era5-single-levels",
                "request": { ... },
                "target": "output.grib"
            }
        ]

    Compact format — shared dataset (JSON/YAML):
        {
            "dataset": "reanalysis-era5-single-levels",
            "requests": [
                { "request": { ... }, "target": "output.grib" }
            ]
        }
    """
    with open(path) as f:
        if path.endswith((".yaml", ".yml")):
            try:
                import yaml
            except ImportError:
                raise ImportError(
                    "PyYAML is required for YAML files. "
                    "Install it with: pip install cdsswarm[yaml]"
                )
            data = yaml.safe_load(f)
        else:
            data = json.load(f)

    if isinstance(data, list):
        return [
            Task(
                dataset=item["dataset"],
                request=item["request"],
                target=item["target"],
            )
            for item in data
        ]

    if isinstance(data, dict) and "requests" in data:
        dataset = data["dataset"]
        return [
            Task(
                dataset=dataset,
                request=item["request"],
                target=item["target"],
            )
            for item in data["requests"]
        ]

    raise ValueError(f"Unrecognized format in {path}")


def _resolve_mode(mode: str) -> str:
    if mode != "auto":
        return mode
    if not sys.stdout.isatty():
        return "script"
    return "interactive"


def _run_interactive(
    tasks: list[Task],
    num_workers: int,
    skip_existing: bool,
    reuse_jobs: bool = True,
    max_retries: int = 3,
    log_file=None,
):
    """Launch Textual TUI and run downloads inside it."""
    from .textual_app import CdsswarmApp

    adapter_holder: list = [None]

    # Create the downloader first (adapter will be set before run())
    class _LazyDownloader:
        """Wrapper that creates the real downloader once the adapter exists."""

        def __init__(self):
            self._real = None
            self._cancel_event = None

        def _ensure(self, adapter):
            if self._real is None:
                actual_adapter = adapter
                if log_file:
                    actual_adapter = LoggingAdapter(adapter, log_file)
                self._real = SwarmDownloader(
                    tasks=tasks,
                    adapter=actual_adapter,
                    num_workers=num_workers,
                    skip_existing=skip_existing,
                    reuse_jobs=reuse_jobs,
                    max_retries=max_retries,
                )

        def run(self):
            adapter = adapter_holder[0]
            self._ensure(adapter)
            return self._real.run()

        def cancel(self):
            if self._real is not None:
                self._real.cancel()

    lazy = _LazyDownloader()
    app = CdsswarmApp(
        num_workers=num_workers,
        downloader=lazy,
    )
    adapter = TextualAdapter(app)
    adapter_holder[0] = adapter

    app.run()
    return app.download_results


def _run_script(
    tasks: list[Task],
    num_workers: int,
    skip_existing: bool,
    reuse_jobs: bool = True,
    max_retries: int = 3,
    log_file=None,
    ignore_warnings: bool = False,
):
    """Run downloads with plain text output."""
    adapter = PlainTextAdapter(interactive=not ignore_warnings)
    if log_file:
        adapter = LoggingAdapter(adapter, log_file)
    downloader = SwarmDownloader(
        tasks=tasks,
        adapter=adapter,
        num_workers=num_workers,
        skip_existing=skip_existing,
        reuse_jobs=reuse_jobs,
        max_retries=max_retries,
    )
    return downloader.run()


def _build_parser() -> argparse.ArgumentParser:
    from . import __version__

    parser = argparse.ArgumentParser(
        prog="cdsswarm",
        description="Concurrent CDS API downloader with TUI",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    parser.add_argument(
        "requests_file",
        help="JSON or YAML file with download requests",
    )
    parser.add_argument(
        "-w",
        "--workers",
        type=int,
        default=None,
        help="Number of parallel download workers (default: 4)",
    )
    parser.add_argument(
        "-m",
        "--mode",
        choices=["interactive", "script", "auto"],
        default=None,
        help="Display mode (default: auto)",
    )
    parser.add_argument(
        "--no-skip",
        action="store_true",
        default=None,
        help="Re-download files that already exist",
    )
    parser.add_argument(
        "--reuse",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Reuse existing CDS jobs with matching parameters (default: enabled)",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=None,
        help="Max retry attempts per task (default: 3, 1 to disable)",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Prepend directory to relative target paths",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be downloaded without actually downloading",
    )
    parser.add_argument(
        "--ignore-warnings",
        action="store_true",
        default=None,
        help="Auto-continue on warnings (e.g. checksum mismatch) without prompting",
    )
    parser.add_argument(
        "--log",
        default=None,
        metavar="FILE",
        help="Write timestamped log to FILE",
    )
    parser.add_argument(
        "--summary",
        default=None,
        metavar="FILE",
        help="Export summary as JSON (.json) or CSV (.csv)",
    )
    return parser


def main(argv: list[str] | None = None):
    parser = _build_parser()
    args = parser.parse_args(argv)

    if not os.path.isfile(args.requests_file):
        print(f"Error: file not found: {args.requests_file}", file=sys.stderr)
        sys.exit(1)

    try:
        tasks = load_requests(args.requests_file)
    except (ImportError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    if not tasks:
        print("No download tasks found in the requests file.", file=sys.stderr)
        sys.exit(1)

    # Build CLI overrides (only explicitly provided flags).
    # --no-skip is inverted: CLI flag means skip_existing=False.
    cli_overrides: dict[str, object] = {
        "workers": args.workers,
        "mode": args.mode,
        "reuse": args.reuse,
        "max_retries": args.max_retries,
        "ignore_warnings": args.ignore_warnings if args.ignore_warnings else None,
        "output_dir": args.output_dir,
        "log": args.log,
        "summary": args.summary,
    }
    if args.no_skip is True:
        cli_overrides["skip_existing"] = False

    from .config import resolve_settings

    try:
        settings = resolve_settings(cli_overrides)
    except ValueError as exc:
        print(f"Config error: {exc}", file=sys.stderr)
        sys.exit(1)

    # Apply output_dir: prepend to relative target paths.
    output_dir = settings["output_dir"]
    if output_dir:
        for task in tasks:
            if not os.path.isabs(task.target):
                task.target = os.path.join(output_dir, task.target)

    if args.dry_run:
        print(f"{'Target':<50} {'Dataset':<40} {'Exists'}")
        print("-" * 95)
        for t in tasks:
            exists = "skip" if os.path.isfile(t.target) else "download"
            print(f"{t.target:<50} {t.dataset:<40} {exists}")
        print(f"\n{len(tasks)} task(s) total")
        sys.exit(0)

    mode = _resolve_mode(settings["mode"])
    skip_existing = settings["skip_existing"]
    workers = settings["workers"]
    reuse = settings["reuse"]
    max_retries = settings["max_retries"]
    ignore_warnings = settings["ignore_warnings"]
    log_path = settings["log"]
    summary_path = settings["summary"]

    log_file = open(log_path, "a") if log_path else None
    try:
        wall_start = time.time()
        if mode == "interactive":
            results = _run_interactive(
                tasks,
                workers,
                skip_existing,
                reuse,
                max_retries,
                log_file=log_file,
            )
        else:
            results = _run_script(
                tasks,
                workers,
                skip_existing,
                reuse,
                max_retries,
                log_file=log_file,
                ignore_warnings=ignore_warnings,
            )
        wall_end = time.time()
    finally:
        if log_file:
            log_file.close()

    if results is None:
        sys.exit(1)

    print_summary(results, wall_start, wall_end)
    if summary_path:
        export_summary(results, wall_start, wall_end, summary_path)

    if any(not r.success for r in results):
        sys.exit(1)
    sys.exit(0)
