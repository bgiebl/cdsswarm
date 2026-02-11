"""Command-line interface for cdsswarm."""

from __future__ import annotations

import argparse
import curses
import json
import os
import shutil
import sys

from .adapters import CursesAdapter, PlainTextAdapter
from .core import Result, SwarmDownloader, Task
from .tui import CursesTUI


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
                print(
                    "Error: PyYAML is required for YAML files. "
                    "Install it with: pip install cdsswarm[yaml]",
                    file=sys.stderr,
                )
                sys.exit(1)
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

    print(f"Error: unrecognized format in {path}", file=sys.stderr)
    sys.exit(1)


def _resolve_mode(mode: str) -> str:
    if mode != "auto":
        return mode
    if not sys.stdout.isatty():
        return "script"
    size = shutil.get_terminal_size()
    if size.lines < CursesTUI.MIN_HEIGHT or size.columns < CursesTUI.MIN_WIDTH:
        return "script"
    return "interactive"


def _run_interactive(tasks: list[Task], num_workers: int, skip_existing: bool):
    """Launch curses TUI and run downloads inside it."""
    tui = CursesTUI(num_workers=num_workers)
    interrupted = False
    results: list[Result] | None = None

    def _main(stdscr):
        nonlocal interrupted, results
        tui.start(stdscr)
        adapter = CursesAdapter(tui)
        downloader = SwarmDownloader(
            tasks=tasks,
            adapter=adapter,
            num_workers=num_workers,
            skip_existing=skip_existing,
        )
        results = downloader.run()
        if results is None:
            interrupted = True
            tui.set_status_line("Cancelled. Press any key to exit...")
            tui.refresh()
            stdscr.nodelay(False)
            stdscr.getch()
        else:
            failed = sum(1 for r in results if not r.success)
            if failed:
                tui.set_status_line(f"Done ({failed} failed). Press any key to exit...")
            else:
                tui.set_status_line("Done! Press any key to exit...")
            tui.refresh()
            stdscr.nodelay(False)
            stdscr.getch()

    curses.wrapper(_main)
    if interrupted:
        os._exit(1)
    return results


def _run_script(tasks: list[Task], num_workers: int, skip_existing: bool):
    """Run downloads with plain text output."""
    adapter = PlainTextAdapter()
    downloader = SwarmDownloader(
        tasks=tasks,
        adapter=adapter,
        num_workers=num_workers,
        skip_existing=skip_existing,
    )
    return downloader.run()


def main(argv: list[str] | None = None):
    parser = argparse.ArgumentParser(
        prog="cdsswarm",
        description="Concurrent CDS API downloader with TUI",
    )
    parser.add_argument(
        "requests_file",
        help="JSON or YAML file with download requests",
    )
    parser.add_argument(
        "-w", "--workers",
        type=int,
        default=4,
        help="Number of parallel download workers (default: 4)",
    )
    parser.add_argument(
        "-m", "--mode",
        choices=["interactive", "script", "auto"],
        default="auto",
        help="Display mode (default: auto)",
    )
    parser.add_argument(
        "--no-skip",
        action="store_true",
        help="Re-download files that already exist",
    )

    args = parser.parse_args(argv)

    if not os.path.isfile(args.requests_file):
        print(f"Error: file not found: {args.requests_file}", file=sys.stderr)
        sys.exit(1)

    tasks = load_requests(args.requests_file)
    if not tasks:
        print("No download tasks found in the requests file.", file=sys.stderr)
        sys.exit(1)

    mode = _resolve_mode(args.mode)
    skip_existing = not args.no_skip

    if mode == "interactive":
        results = _run_interactive(tasks, args.workers, skip_existing)
    else:
        results = _run_script(tasks, args.workers, skip_existing)

    if results is None:
        sys.exit(1)
    if any(not r.success for r in results):
        sys.exit(1)
    sys.exit(0)
