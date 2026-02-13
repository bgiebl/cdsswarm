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
    size = shutil.get_terminal_size()
    if size.lines < CursesTUI.MIN_HEIGHT or size.columns < CursesTUI.MIN_WIDTH:
        return "script"
    return "interactive"


def _run_interactive(
    tasks: list[Task],
    num_workers: int,
    skip_existing: bool,
    reuse_jobs: bool = True,
    max_retries: int = 3,
):
    """Launch curses TUI and run downloads inside it."""
    tui = CursesTUI(num_workers=num_workers)
    results: list[Result] | None = None

    def _main(stdscr):
        nonlocal results
        tui.start(stdscr)
        adapter = CursesAdapter(tui)
        downloader = SwarmDownloader(
            tasks=tasks,
            adapter=adapter,
            num_workers=num_workers,
            skip_existing=skip_existing,
            reuse_jobs=reuse_jobs,
            max_retries=max_retries,
        )

        import threading as _thr

        download_thread = _thr.Thread(
            target=_run_download, args=(downloader,), daemon=True
        )
        download_thread.start()

        # Input loop: poll at 200ms intervals so Running column ticks
        stdscr.timeout(200)
        try:
            while download_thread.is_alive():
                try:
                    key = stdscr.getch()
                except curses.error:
                    continue
                if key == -1:
                    # Timeout — refresh to tick the Running column
                    tui.refresh()
                    continue
                if tui.handle_checksum_key(key):
                    continue
                if key == ord("q"):
                    downloader.cancel()
                    tui.set_status_line("Cancelling...")
                    break
                elif key == curses.KEY_RESIZE:
                    tui.handle_resize()
                elif key == curses.KEY_UP:
                    tui.select_up()
                elif key == curses.KEY_DOWN:
                    tui.select_down()
                elif key in (ord("\n"), ord("\r"), curses.KEY_ENTER):
                    if tui._view_mode == "logs":
                        tui.close_log_view()
                    else:
                        tui.open_log_view()
                elif key == ord("a"):
                    if tui._view_mode == "params":
                        tui.close_fullscreen_view()
                    elif tui._view_mode == "table":
                        tui.open_params_view()
                elif key == 27:  # Escape
                    tui.close_fullscreen_view()
                elif key == curses.KEY_PPAGE:
                    tui.page_up()
                elif key == curses.KEY_NPAGE:
                    tui.page_down()
                elif key == curses.KEY_HOME:
                    tui.log_home()
                elif key == curses.KEY_END:
                    tui.log_end()
                elif key == curses.KEY_MOUSE:
                    try:
                        mouse_event = curses.getmouse()
                        tui.handle_mouse(mouse_event)
                    except curses.error:
                        pass
        except KeyboardInterrupt:
            downloader.cancel()
            tui.set_status_line("Interrupted \u2014 cancelling...")

        download_thread.join(timeout=10)
        results = _download_result[0]

        if results is None:
            tui.set_status_line("Cancelled. Press any key to exit...")
        else:
            failed = sum(1 for r in results if not r.success)
            if failed:
                tui.set_status_line(f"Done ({failed} failed). Press any key to exit...")
            else:
                tui.set_status_line("Done! Press any key to exit...")
        tui.refresh()
        stdscr.timeout(-1)
        stdscr.getch()

    _download_result: list = [None]

    def _run_download(downloader):
        _download_result[0] = downloader.run()

    curses.wrapper(_main)
    return results


def _run_script(
    tasks: list[Task],
    num_workers: int,
    skip_existing: bool,
    reuse_jobs: bool = True,
    max_retries: int = 3,
):
    """Run downloads with plain text output."""
    adapter = PlainTextAdapter()
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
        default=4,
        help="Number of parallel download workers (default: 4)",
    )
    parser.add_argument(
        "-m",
        "--mode",
        choices=["interactive", "script", "auto"],
        default="auto",
        help="Display mode (default: auto)",
    )
    parser.add_argument(
        "--no-skip",
        action="store_true",
        help="Re-download files that already exist",
    )
    parser.add_argument(
        "--reuse",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Reuse existing CDS jobs with matching parameters (default: enabled)",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=3,
        help="Max retry attempts per task (default: 3, 1 to disable)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be downloaded without actually downloading",
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

    if args.dry_run:
        print(f"{'Target':<50} {'Dataset':<40} {'Exists'}")
        print("-" * 95)
        for t in tasks:
            exists = "skip" if os.path.isfile(t.target) else "download"
            print(f"{t.target:<50} {t.dataset:<40} {exists}")
        print(f"\n{len(tasks)} task(s) total")
        sys.exit(0)

    mode = _resolve_mode(args.mode)
    skip_existing = not args.no_skip

    if mode == "interactive":
        results = _run_interactive(
            tasks, args.workers, skip_existing, args.reuse, args.max_retries
        )
    else:
        results = _run_script(
            tasks, args.workers, skip_existing, args.reuse, args.max_retries
        )

    if results is None:
        sys.exit(1)
    if any(not r.success for r in results):
        sys.exit(1)
    sys.exit(0)
