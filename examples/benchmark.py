#!/usr/bin/env python3
"""Benchmark cdsswarm with different worker counts.

Runs the same request file with varying parallelism and produces a
comparison table with wall-clock times and speedup factors.

Two modes:
  - End-to-end (default): each run submits fresh CDS requests. Measures
    the real-world speedup from overlapping queue wait + processing +
    download. More expensive (N × trials API calls).
  - Download-only (--reuse): a warmup run primes the CDS cache, then
    each benchmark run downloads pre-processed results. Measures pure
    download parallelism. Cheaper but less representative.

Usage:
    python examples/benchmark.py examples/era5_temperature.json
    python examples/benchmark.py requests.json -w 1,2,4,8 --trials 3
    python examples/benchmark.py requests.json -o benchmark_results.json
    python examples/benchmark.py requests.json --reuse   # download-only mode
"""

import argparse
import datetime
import json
import os
import shutil
import statistics
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import cdsswarm
from cdsswarm.cli import load_requests


def _format_duration(seconds: float) -> str:
    if seconds <= 0:
        return "—"
    total = int(seconds)
    h, remainder = divmod(total, 3600)
    m, s = divmod(remainder, 60)
    if h > 0:
        return f"{h}h{m:02d}m{s:02d}s"
    if m > 0:
        return f"{m}m{s:02d}s"
    return f"{s}s"


def _format_size(size: int) -> str:
    fsize = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if fsize < 1024 or unit == "TB":
            if unit == "B":
                return f"{int(fsize)} {unit}"
            return f"{fsize:.1f} {unit}"
        fsize /= 1024
    return f"{fsize:.1f} TB"


def _check_unique_basenames(tasks: list[cdsswarm.Task]) -> None:
    """Abort if any tasks share a basename (temp dir would collide)."""
    basenames: dict[str, str] = {}
    for t in tasks:
        base = os.path.basename(t.target)
        if base in basenames:
            print(
                f"Error: duplicate basename '{base}' in targets:\n"
                f"  {basenames[base]}\n  {t.target}\n"
                f"Benchmark requires unique filenames.",
                file=sys.stderr,
            )
            sys.exit(1)
        basenames[base] = t.target


def run_trial(
    tasks: list[cdsswarm.Task],
    num_workers: int,
    output_dir: str,
    reuse_jobs: bool = False,
) -> dict:
    """Run one benchmark trial and return timing results."""
    adjusted = [
        cdsswarm.Task(
            dataset=t.dataset,
            request=dict(t.request),
            target=os.path.join(output_dir, os.path.basename(t.target)),
        )
        for t in tasks
    ]

    start = time.time()
    results = cdsswarm.download(
        adjusted,
        num_workers=num_workers,
        skip_existing=False,
        reuse_jobs=reuse_jobs,
        max_retries=3,
    )
    elapsed = time.time() - start

    return {
        "workers": num_workers,
        "elapsed": round(elapsed, 2),
        "ok": sum(1 for r in results if r.success),
        "failed": sum(1 for r in results if not r.success),
        "total_size": sum(r.file_size for r in results),
    }


def warmup(tasks: list[cdsswarm.Task], output_dir: str) -> None:
    """Download all files once to prime the CDS cache."""
    adjusted = [
        cdsswarm.Task(
            dataset=t.dataset,
            request=dict(t.request),
            target=os.path.join(output_dir, os.path.basename(t.target)),
        )
        for t in tasks
    ]
    # Use several workers to speed up the warmup
    workers = min(4, len(tasks))
    cdsswarm.download(
        adjusted,
        num_workers=workers,
        skip_existing=False,
        reuse_jobs=True,
        max_retries=3,
    )


def main():
    parser = argparse.ArgumentParser(
        description="Benchmark cdsswarm with different worker counts",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  %(prog)s examples/era5_temperature.json\n"
            "  %(prog)s requests.json -w 1,2,4,8 --trials 3\n"
            "  %(prog)s requests.json --reuse -o results.json\n"
        ),
    )
    parser.add_argument("requests_file", help="JSON/YAML request file")
    parser.add_argument(
        "-w",
        "--workers",
        default="1,2,4",
        help="Comma-separated worker counts to test (default: 1,2,4)",
    )
    parser.add_argument(
        "--trials",
        type=int,
        default=1,
        help="Trials per worker count; median is reported (default: 1)",
    )
    parser.add_argument(
        "-o",
        "--output",
        metavar="FILE",
        help="Save raw results to JSON file",
    )
    parser.add_argument(
        "--reuse",
        action="store_true",
        help="Download-only mode: warmup first, then benchmark with reuse",
    )
    args = parser.parse_args()

    worker_counts = sorted(int(w.strip()) for w in args.workers.split(","))
    tasks = load_requests(args.requests_file)
    if not tasks:
        print("Error: no tasks in request file.", file=sys.stderr)
        sys.exit(1)
    _check_unique_basenames(tasks)

    mode_label = "download-only (reuse)" if args.reuse else "end-to-end"
    total_runs = len(worker_counts) * args.trials
    total_requests = total_runs * len(tasks)

    print(
        f"Benchmark: {len(tasks)} tasks × {len(worker_counts)} configs × "
        f"{args.trials} trial(s) = {total_runs} runs"
    )
    print(f"Mode: {mode_label}")
    if not args.reuse:
        print(f"CDS requests: {total_requests} total (each run submits fresh requests)")
    print(f"Request file: {args.requests_file}")
    print(f"Started: {datetime.datetime.now().astimezone().strftime('%Y-%m-%d %H:%M')}")
    print()

    # Warmup phase for reuse mode
    if args.reuse:
        print("--- Warmup: priming CDS cache ---")
        tmpdir = tempfile.mkdtemp(prefix="cdsswarm_bench_warmup_")
        try:
            warmup(tasks, tmpdir)
            print("    Cache primed.")
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)
        print()

    all_results: dict[int, list[dict]] = {}
    interrupted = False

    for w in worker_counts:
        if interrupted:
            break
        all_results[w] = []
        for trial_num in range(1, args.trials + 1):
            label = f"workers={w}"
            if args.trials > 1:
                label += f" (trial {trial_num}/{args.trials})"
            print(f"--- {label} ---")

            tmpdir = tempfile.mkdtemp(prefix="cdsswarm_bench_")
            try:
                result = run_trial(tasks, w, tmpdir, reuse_jobs=args.reuse)
                all_results[w].append(result)
                total = result["ok"] + result["failed"]
                print(
                    f"    {_format_duration(result['elapsed'])} | "
                    f"{result['ok']}/{total} ok | "
                    f"{_format_size(result['total_size'])}"
                )
            except KeyboardInterrupt:
                print("\nInterrupted.")
                interrupted = True
                break
            finally:
                shutil.rmtree(tmpdir, ignore_errors=True)
            print()

    # Need at least the baseline to produce a table
    if not all_results.get(worker_counts[0]):
        print("No results collected.")
        sys.exit(1)

    # --- Results table ---
    print()
    print("=" * 60)
    print("Results")
    print("=" * 60)

    baseline_times = [t["elapsed"] for t in all_results[worker_counts[0]]]
    baseline = statistics.median(baseline_times)

    has_range = args.trials > 1
    header = f"  {'Workers':<10} {'Wall time':<12} {'Speedup':<10}"
    if has_range:
        header += f"{'Min':<12} {'Max':<12}"
    print(header)
    print(f"  {'-' * 10} {'-' * 12} {'-' * 10}", end="")
    if has_range:
        print(f"{'-' * 12} {'-' * 12}", end="")
    print()

    for w in worker_counts:
        trials = all_results.get(w, [])
        if not trials:
            continue
        times = [t["elapsed"] for t in trials]
        median = statistics.median(times)
        speedup = baseline / median if median > 0 else 0
        row = f"  {w:<10} {_format_duration(median):<12} {speedup:.1f}x"
        if has_range:
            row += f"      {_format_duration(min(times)):<12} {_format_duration(max(times)):<12}"
        print(row)

    first = all_results[worker_counts[0]][0]
    total_tasks = first["ok"] + first["failed"]
    print()
    print(f"  Tasks: {total_tasks} ({first['ok']} ok, {first['failed']} failed)")
    print(f"  Total size: {_format_size(first['total_size'])}")
    print(
        f"  Finished: {datetime.datetime.now().astimezone().strftime('%Y-%m-%d %H:%M')}"
    )
    print("=" * 60)

    # Markdown table for README
    print()
    print("Markdown for README:")
    print()
    print("| Workers | Wall time | Speedup |")
    print("|---------|-----------|---------|")
    for w in worker_counts:
        trials = all_results.get(w, [])
        if not trials:
            continue
        median = statistics.median(t["elapsed"] for t in trials)
        speedup = baseline / median if median > 0 else 0
        print(f"| {w} | {_format_duration(median)} | {speedup:.1f}x |")
    print()

    # JSON export
    if args.output:
        export = {
            "request_file": os.path.abspath(args.requests_file),
            "num_tasks": len(tasks),
            "trials": args.trials,
            "reuse": args.reuse,
            "timestamp": datetime.datetime.now()
            .astimezone()
            .isoformat(timespec="seconds"),
            "runs": {
                str(w): all_results[w] for w in worker_counts if all_results.get(w)
            },
        }
        with open(args.output, "w") as f:
            json.dump(export, f, indent=2)
        print(f"Results saved to {args.output}")


if __name__ == "__main__":
    main()
