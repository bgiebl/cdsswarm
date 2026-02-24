"""Command-line interface for cdsswarm."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

import glob as _glob

from .adapters import LoggingAdapter, PlainTextAdapter, TextualAdapter
from .core import SwarmDownloader, Task
from .exceptions import RequestFileError
from .summary import export_summary, print_summary


def _log_dir() -> str:
    """Return $XDG_STATE_HOME/cdsswarm/logs/."""
    base = os.environ.get("XDG_STATE_HOME") or os.path.join(
        os.path.expanduser("~"), ".local", "state"
    )
    return os.path.join(base, "cdsswarm", "logs")


def _auto_log_path() -> str:
    """Generate a timestamped log filename."""
    ts = time.strftime("%Y-%m-%d_%H.%M.%S")
    return os.path.join(_log_dir(), f"run_{ts}.log")


def _rotate_logs(directory: str, keep: int = 10):
    """Keep the last *keep* log files, delete the rest."""
    pattern = os.path.join(directory, "run_*.log")
    logs = sorted(_glob.glob(pattern), key=os.path.getmtime)
    for old in logs[:-keep]:
        os.remove(old)


class _MultiWriter:
    """Write to multiple file objects simultaneously."""

    def __init__(self, *writers):
        self._writers = writers

    def write(self, s):
        for w in self._writers:
            w.write(s)

    def flush(self):
        for w in self._writers:
            w.flush()


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

    from .exceptions import RequestFileError

    raise RequestFileError(f"Unrecognized format in {path}")


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
    post_hook: str = "",
    on_task_done=None,
    on_request_id=None,
    initial_reuse_map=None,
    pre_messages=None,
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
                    post_hook=post_hook,
                    on_task_done=on_task_done,
                    on_request_id=on_request_id,
                    initial_reuse_map=initial_reuse_map,
                    pre_messages=pre_messages,
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
    post_hook: str = "",
    on_task_done=None,
    on_request_id=None,
    initial_reuse_map=None,
    pre_messages=None,
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
        post_hook=post_hook,
        on_task_done=on_task_done,
        on_request_id=on_request_id,
        initial_reuse_map=initial_reuse_map,
        pre_messages=pre_messages,
    )
    return downloader.run()


def _build_parser() -> argparse.ArgumentParser:
    from . import __version__

    parser = argparse.ArgumentParser(
        prog="cdsswarm",
        description="Concurrent CDS API downloader with TUI",
        epilog=(
            "subcommands:\n"
            "  generate   Expand a template into a request file\n"
            "  cancel     Cancel active CDS API requests\n"
            "\n"
            "Run 'cdsswarm <subcommand> --help' for subcommand usage."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
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
        "--resume",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Resume interrupted session if state file exists (default: enabled)",
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
        help="Auto-continue on warnings without prompting",
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
    parser.add_argument(
        "--post-hook",
        default=None,
        metavar="CMD",
        help="Shell command to run after each successful download. "
        "Placeholders: {file} (output path), {dataset} (dataset name)",
    )
    return parser


def _load_template(path: str) -> dict:
    """Load a generate template from a JSON or YAML file."""
    with open(path) as f:
        if path.endswith((".yaml", ".yml")):
            try:
                import yaml
            except ImportError:
                raise ImportError(
                    "PyYAML is required for YAML files. "
                    "Install it with: pip install cdsswarm[yaml]"
                )
            return yaml.safe_load(f)
        else:
            return json.load(f)


def _build_generate_parser() -> argparse.ArgumentParser:
    """Build the argument parser for ``cdsswarm generate``."""
    parser = argparse.ArgumentParser(
        prog="cdsswarm generate",
        description="Expand a template into a request file",
    )
    parser.add_argument(
        "template_file",
        help="Path to template JSON/YAML file",
    )
    parser.add_argument(
        "--split-by",
        default=None,
        help="Comma-separated split fields (overrides template's split_by)",
    )
    parser.add_argument(
        "-o",
        "--output",
        default=None,
        metavar="FILE",
        help="Output file path (default: stdout)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show task count and targets without writing output",
    )
    return parser


def _cmd_generate(argv: list[str]) -> None:
    """Handle ``cdsswarm generate ...``."""
    from .generate import expand_template

    parser = _build_generate_parser()
    args = parser.parse_args(argv)

    if not os.path.isfile(args.template_file):
        print(f"Error: file not found: {args.template_file}", file=sys.stderr)
        sys.exit(1)

    try:
        template = _load_template(args.template_file)
    except (ImportError, ValueError, json.JSONDecodeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    if isinstance(template, list):
        if len(template) == 1 and isinstance(template[0], dict):
            print(
                "Warning: template file contains a JSON list. "
                "The generate command expects a single JSON object. "
                "Unwrapping the first element.",
                file=sys.stderr,
            )
            template = template[0]
        else:
            print(
                "Error: template must be a single JSON object, not a list. "
                "See 'cdsswarm generate --help' for the expected format.",
                file=sys.stderr,
            )
            sys.exit(1)
    elif not isinstance(template, dict):
        print(
            "Error: template must be a single JSON object. "
            "See 'cdsswarm generate --help' for the expected format.",
            file=sys.stderr,
        )
        sys.exit(1)

    split_by = None
    if args.split_by is not None:
        split_by = [s.strip() for s in args.split_by.split(",")]

    try:
        result = expand_template(template, split_by=split_by)
    except RequestFileError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    if args.dry_run:
        requests = result["requests"]
        print(f"{len(requests)} task(s) would be generated:\n")
        for r in requests:
            print(f"  {r['target']}")
        sys.exit(0)

    output_json = json.dumps(result, indent=2)

    if args.output:
        with open(args.output, "w") as f:
            f.write(output_json + "\n")
        print(
            f"Wrote {len(result['requests'])} task(s) to {args.output}",
            file=sys.stderr,
        )
    else:
        print(output_json)

    sys.exit(0)


def _build_cancel_parser() -> argparse.ArgumentParser:
    """Build the argument parser for ``cdsswarm cancel``."""
    parser = argparse.ArgumentParser(
        prog="cdsswarm cancel",
        description="Cancel active CDS API requests",
    )
    parser.add_argument(
        "request_ids",
        nargs="*",
        help="Specific request IDs to cancel (omit to cancel all active)",
    )
    parser.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help="Skip confirmation prompt",
    )
    return parser


def _cmd_cancel(argv: list[str]) -> None:
    """Handle ``cdsswarm cancel ...``."""
    import cdsapi

    from ._cds_utils import (
        cancel_cds_request,
        cancel_cds_requests,
        list_active_jobs,
    )

    parser = _build_cancel_parser()
    args = parser.parse_args(argv)

    client = cdsapi.Client(quiet=True, progress=False)

    if args.request_ids:
        # Cancel specific IDs directly
        ids_to_cancel = args.request_ids
        if not args.yes:
            print(f"Will cancel {len(ids_to_cancel)} request(s):\n")
            for rid in ids_to_cancel:
                print(f"  {rid}")
            print()
            try:
                answer = input("Proceed? [y/N] ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                print("\nAborted.")
                sys.exit(1)
            if answer not in ("y", "yes"):
                print("Aborted.")
                sys.exit(0)

        failed = 0
        for rid in ids_to_cancel:
            try:
                cancel_cds_request(client, rid)
                print(f"  Cancelled {rid}")
            except Exception as exc:
                print(f"  Failed to cancel {rid}: {exc}", file=sys.stderr)
                failed += 1

        total = len(ids_to_cancel)
        print(f"\n{total - failed}/{total} request(s) cancelled.")
        sys.exit(1 if failed else 0)

    # No specific IDs — list and cancel all active jobs
    inner = getattr(client, "client", None)
    if inner is None or not hasattr(inner, "get_remote"):
        print(
            "Error: listing active requests requires the new CADS API "
            "(ecmwf-datastores).\n"
            "Provide specific request IDs instead: cdsswarm cancel <id1> <id2> ...",
            file=sys.stderr,
        )
        sys.exit(1)

    jobs = list_active_jobs(client)
    if not jobs:
        print("No active requests found.")
        sys.exit(0)

    print(f"Found {len(jobs)} active request(s):\n")
    print(f"  {'Request ID':<40} {'Dataset':<35} {'Status':<12} {'Created'}")
    print(f"  {'-' * 40} {'-' * 35} {'-' * 12} {'-' * 20}")
    for job in jobs:
        print(
            f"  {job['job_id']:<40} {job['dataset']:<35} "
            f"{job['status']:<12} {job['created']}"
        )
    print()

    if not args.yes:
        try:
            answer = input("Cancel all? [y/N] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\nAborted.")
            sys.exit(1)
        if answer not in ("y", "yes"):
            print("Aborted.")
            sys.exit(0)

    ids_to_cancel = [job["job_id"] for job in jobs]
    try:
        cancel_cds_requests(client, ids_to_cancel)
        print(f"Cancelled {len(ids_to_cancel)} request(s).")
    except Exception as exc:
        print(f"Error cancelling requests: {exc}", file=sys.stderr)
        sys.exit(1)

    sys.exit(0)


def main(argv: list[str] | None = None):
    if argv is None:
        argv = sys.argv[1:]

    # Dispatch to subcommands before regular parsing.
    if argv and argv[0] == "generate":
        return _cmd_generate(argv[1:])
    if argv and argv[0] == "cancel":
        return _cmd_cancel(argv[1:])

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
        "resume": args.resume,
        "reuse": args.reuse,
        "max_retries": args.max_retries,
        "ignore_warnings": args.ignore_warnings if args.ignore_warnings else None,
        "output_dir": args.output_dir,
        "log": args.log,
        "summary": args.summary,
        "post_hook": args.post_hook,
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
        resolved_base = os.path.realpath(output_dir)
        for task in tasks:
            if not os.path.isabs(task.target):
                joined = os.path.join(output_dir, task.target)
                resolved = os.path.realpath(joined)
                if (
                    not resolved.startswith(resolved_base + os.sep)
                    and resolved != resolved_base
                ):
                    print(
                        f"Error: target '{task.target}' escapes output directory '{output_dir}'",
                        file=sys.stderr,
                    )
                    sys.exit(1)
                task.target = joined

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
    resume = settings["resume"]
    reuse = settings["reuse"]
    max_retries = settings["max_retries"]
    ignore_warnings = settings["ignore_warnings"]
    log_path = settings["log"]
    summary_path = settings["summary"]
    post_hook = settings["post_hook"]

    # --- Session resume logic ---
    from .state import SessionState, session_path

    state_path = session_path(args.requests_file, output_dir)
    session = None
    saved_reuse: dict[str, str] = {}
    pre_messages: list[str] = []

    if resume and os.path.isfile(state_path):
        try:
            session = SessionState.load(state_path)
        except (ValueError, json.JSONDecodeError) as exc:
            print(
                f"Warning: corrupt session file, starting fresh ({exc})",
                file=sys.stderr,
            )
            session = None

    if session is not None:
        # Merge new tasks not in state file
        current_targets = {t.target for t in tasks}
        for task in tasks:
            if task.target not in session.tasks:
                from .state import TaskState

                session.tasks[task.target] = TaskState(
                    dataset=task.dataset, request=dict(task.request)
                )
        # Filter tasks to pending targets intersected with current task list
        pending_set = set(session.pending_targets()) & current_targets
        if not pending_set:
            print("All tasks already completed (from previous session).")
            sys.exit(0)
        tasks = [t for t in tasks if t.target in pending_set]
        session.clear_stale_request_ids()
        saved_reuse = session.reuse_map()
        total = len(session.tasks)
        remaining = len(tasks)
        pre_messages.append(
            f"Resuming: {remaining}/{total} tasks remaining "
            f"(use --no-resume for a clean start)"
        )
        session.save(state_path)
    else:
        session = SessionState.new(
            args.requests_file,
            tasks,
            settings={
                "workers": workers,
                "max_retries": max_retries,
                "output_dir": output_dir,
            },
        )
        session.save(state_path)

    def _on_task_done(result, request_id):
        if result.success:
            session.mark_completed(
                result.task.target,
                result.start_time,
                result.end_time,
                result.file_size,
                request_id=request_id,
            )
        else:
            session.mark_failed(
                result.task.target,
                result.error,
                result.start_time,
                result.end_time,
                request_id=request_id,
            )
        session.save(state_path)

    def _on_request_id(target, request_id):
        session.set_request_id(target, request_id)
        session.save(state_path)

    # Always-on auto-log
    auto_dir = _log_dir()
    os.makedirs(auto_dir, exist_ok=True)
    auto_log = open(_auto_log_path(), "w")

    # Combine with --log FILE if given
    user_log = open(log_path, "a") if log_path else None
    if user_log:
        log_file = _MultiWriter(auto_log, user_log)
    else:
        log_file = auto_log

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
                post_hook=post_hook,
                on_task_done=_on_task_done,
                on_request_id=_on_request_id,
                initial_reuse_map=saved_reuse,
                pre_messages=pre_messages,
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
                post_hook=post_hook,
                on_task_done=_on_task_done,
                on_request_id=_on_request_id,
                initial_reuse_map=saved_reuse,
                pre_messages=pre_messages,
            )
        wall_end = time.time()
    finally:
        auto_log.close()
        if user_log:
            user_log.close()
        _rotate_logs(auto_dir)

    if results is None:
        session.save(state_path)
        sys.exit(1)

    print_summary(results, wall_start, wall_end)
    if summary_path:
        export_summary(results, wall_start, wall_end, summary_path)

    if any(not r.success for r in results):
        sys.exit(1)
    sys.exit(0)
