"""Command-line interface for cdsswarm."""

from __future__ import annotations

import argparse
import curses
import json
import os
import shutil
import sys
import time

from .adapters import CursesAdapter, LoggingAdapter, PlainTextAdapter
from .core import Result, SwarmDownloader, Task
from .summary import export_summary, print_summary
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
    log_file=None,
):
    """Launch curses TUI and run downloads inside it."""
    tui = CursesTUI(num_workers=num_workers)
    results: list[Result] | None = None

    def _main(stdscr):
        nonlocal results
        tui.start(stdscr)

        # Redirect stdout/stderr to /dev/null while curses is active.
        # Libraries (cdsapi, urllib3, ecmwf-datastores) may write directly
        # to stdout/stderr via loggers or print(), which corrupts the
        # curses display.  All meaningful output goes through the adapter.
        _saved_stdout = sys.stdout
        _saved_stderr = sys.stderr
        _devnull = open(os.devnull, "w")
        sys.stdout = _devnull
        sys.stderr = _devnull

        try:
            adapter = CursesAdapter(tui)
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

            import threading as _thr

            download_thread = _thr.Thread(
                target=_run_download, args=(downloader,), daemon=True
            )
            download_thread.start()

            def _process_key(key):
                """Handle a single key press. Returns True to quit."""
                if tui.handle_checksum_key(key):
                    return False
                if key == ord("q"):
                    downloader.cancel()
                    tui.set_status_line("Cancelling...")
                    return True
                elif key == 9:  # Tab
                    if tui._view_mode == "table":
                        tui.toggle_tab()
                elif key == curses.KEY_RESIZE:
                    tui.handle_resize()
                elif key == curses.KEY_UP:
                    tui.select_up()
                elif key == curses.KEY_DOWN:
                    tui.select_down()
                elif key in (ord("\n"), ord("\r"), curses.KEY_ENTER):
                    if tui._view_mode == "logs":
                        tui.close_log_view()
                    elif tui._active_tab == "workers":
                        tui.open_log_view()
                elif key == ord("a"):
                    if tui._view_mode == "params":
                        tui.close_fullscreen_view()
                    elif tui._view_mode == "table" and tui._active_tab == "workers":
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
                return False

            # Input loop: poll at ~20fps so elapsed column ticks smoothly
            stdscr.timeout(50)
            try:
                while download_thread.is_alive():
                    try:
                        key = stdscr.getch()
                    except curses.error:
                        key = -1
                    if key != -1:
                        if _process_key(key):
                            break
                        # Drain any remaining buffered keys before refreshing
                        stdscr.nodelay(True)
                        while True:
                            try:
                                k = stdscr.getch()
                            except curses.error:
                                break
                            if k == -1:
                                break
                            if _process_key(k):
                                break
                        stdscr.timeout(50)
                    tui.refresh()
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
                    tui.set_status_line(
                        f"Done ({failed} failed). Press any key to exit..."
                    )
                else:
                    tui.set_status_line("Done! Press any key to exit...")
            tui.refresh()
            stdscr.timeout(-1)
            stdscr.getch()
        finally:
            sys.stdout = _saved_stdout
            sys.stderr = _saved_stderr
            _devnull.close()

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
    log_file=None,
):
    """Run downloads with plain text output."""
    adapter = PlainTextAdapter()
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
