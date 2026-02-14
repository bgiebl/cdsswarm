"""Post-run summary: build, print, and export download results."""

from __future__ import annotations

import csv
import datetime
import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .core import Result


def _format_duration(seconds: float) -> str:
    """Format seconds into human-readable duration like '1h23m45s'."""
    if seconds <= 0:
        return "\u2014"
    total = int(seconds)
    h, remainder = divmod(total, 3600)
    m, s = divmod(remainder, 60)
    if h > 0:
        return f"{h}h{m:02d}m{s:02d}s"
    if m > 0:
        return f"{m}m{s:02d}s"
    return f"{s}s"


def _format_bytes(size: int) -> str:
    """Format byte count into human-readable size like '1.5 GB'."""
    if size <= 0:
        return "\u2014"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            if unit == "B":
                return f"{size} {unit}"
            return f"{size:.1f} {unit}"
        size = size / 1024
    return f"{size:.1f} TB"


def build_summary(
    results: list[Result],
    wall_start: float,
    wall_end: float,
) -> dict:
    """Build a structured summary dict from download results.

    Args:
        results: List of Result objects from SwarmDownloader.run().
        wall_start: time.time() when the run started.
        wall_end: time.time() when the run finished.

    Returns:
        Dict with 'totals' and 'tasks' keys.
    """
    ok = sum(1 for r in results if r.success)
    failed = sum(1 for r in results if not r.success)
    total_size = sum(r.file_size for r in results)
    wall_duration = wall_end - wall_start

    tasks = []
    for r in results:
        duration = r.end_time - r.start_time if r.start_time > 0 else 0.0
        tasks.append(
            {
                "target": r.task.target,
                "status": "ok" if r.success else "FAILED",
                "error": r.error,
                "warnings": list(r.warnings),
                "duration": round(duration, 2),
                "file_size": r.file_size,
                "start_time": r.start_time,
                "end_time": r.end_time,
            }
        )

    return {
        "totals": {
            "wall_duration": round(wall_duration, 2),
            "tasks_ok": ok,
            "tasks_failed": failed,
            "tasks_total": len(results),
            "total_size": total_size,
            "started": datetime.datetime.fromtimestamp(wall_start).isoformat(
                timespec="seconds"
            )
            if wall_start > 0
            else "",
            "finished": datetime.datetime.fromtimestamp(wall_end).isoformat(
                timespec="seconds"
            )
            if wall_end > 0
            else "",
        },
        "tasks": tasks,
    }


def print_summary(
    results: list[Result],
    wall_start: float,
    wall_end: float,
    write_fn=None,
):
    """Print a human-readable summary table.

    Args:
        results: List of Result objects.
        wall_start: time.time() when the run started.
        wall_end: time.time() when the run finished.
        write_fn: Callable for output (default: print).
    """
    write = write_fn or print
    summary = build_summary(results, wall_start, wall_end)
    totals = summary["totals"]
    tasks = summary["tasks"]

    sep = "=" * 80
    write(sep)
    write("Summary")
    write(sep)
    write(f"  Total time:    {_format_duration(totals['wall_duration'])}")
    write(
        f"  Tasks:         {totals['tasks_total']} "
        f"({totals['tasks_ok']} ok, {totals['tasks_failed']} failed)"
    )
    write(f"  Total size:    {_format_bytes(totals['total_size'])}")
    write(f"  Started:       {totals['started']}")
    write(f"  Finished:      {totals['finished']}")
    write("")

    # Per-task table
    hdr_target = "Target"
    hdr_status = "Status"
    hdr_dur = "Duration"
    hdr_size = "Size"
    write(f"  {hdr_target:<40} {hdr_status:<10} {hdr_dur:<12} {hdr_size:<12}")
    write(f"  {'-' * 40} {'-' * 10} {'-' * 12} {'-' * 12}")
    for t in tasks:
        label = t["target"]
        # Show only filename for readability
        if "/" in label or "\\" in label:
            import os

            label = os.path.basename(label)
        dur = _format_duration(t["duration"])
        size = _format_bytes(t["file_size"])
        write(f"  {label:<40} {t['status']:<10} {dur:<12} {size:<12}")

    # Errors section
    errors = [t for t in tasks if t["status"] == "FAILED"]
    if errors:
        write("")
        write("  Errors:")
        for t in errors:
            label = t["target"]
            if "/" in label or "\\" in label:
                import os

                label = os.path.basename(label)
            write(f"    {label}: {t['error']}")

    # Warnings section
    warned = [t for t in tasks if t.get("warnings")]
    if warned:
        write("")
        write("  Warnings:")
        for t in warned:
            label = t["target"]
            if "/" in label or "\\" in label:
                import os

                label = os.path.basename(label)
            for w in t["warnings"]:
                write(f"    {label}: {w}")

    write(sep)


def export_summary(
    results: list[Result],
    wall_start: float,
    wall_end: float,
    path: str,
):
    """Export summary to a file. Format is determined by extension.

    Supported: .json, .csv

    Args:
        results: List of Result objects.
        wall_start: time.time() when the run started.
        wall_end: time.time() when the run finished.
        path: Output file path (.json or .csv).

    Raises:
        ValueError: If the file extension is not supported.
    """
    summary = build_summary(results, wall_start, wall_end)

    if path.endswith(".json"):
        with open(path, "w") as f:
            json.dump(summary, f, indent=2)
    elif path.endswith(".csv"):
        with open(path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(
                [
                    "target",
                    "status",
                    "error",
                    "warnings",
                    "duration",
                    "file_size",
                    "start_time",
                    "end_time",
                ]
            )
            for t in summary["tasks"]:
                writer.writerow(
                    [
                        t["target"],
                        t["status"],
                        t["error"],
                        "; ".join(t.get("warnings", [])),
                        t["duration"],
                        t["file_size"],
                        t["start_time"],
                        t["end_time"],
                    ]
                )
    else:
        raise ValueError(f"Unsupported summary format: {path!r} (use .json or .csv)")
