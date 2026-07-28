#!/usr/bin/env python3
"""Demo: preview the CLI script mode output with simulated downloads (no API calls).

Usage:
    python examples/demo_script.py
    python examples/demo_script.py --workers 8 --tasks 20
    python examples/demo_script.py --script          # no color, for piping/CI
"""

import argparse
import random
import time
import uuid

from cdsswarm.adapters import PlainTextAdapter
from cdsswarm.core import Result, Task
from cdsswarm.summary import print_summary

FILENAMES = [
    "era5_t2m_2024_{:02d}.grib",
    "era5_wind_2024_{:02d}.grib",
    "era5_precip_2024_{:02d}.nc",
    "era5_pressure_2024_{:02d}.grib",
    "cerra_soil_2023_{:02d}.grib",
    "era5_radiation_2024_{:02d}.nc",
]

DATASETS = [
    "reanalysis-era5-single-levels",
    "reanalysis-era5-pressure-levels",
    "reanalysis-cerra-single-levels",
]


def main():
    parser = argparse.ArgumentParser(description="Demo the cdsswarm script mode output")
    parser.add_argument("-w", "--workers", type=int, default=4)
    parser.add_argument("-t", "--tasks", type=int, default=12)
    parser.add_argument(
        "-s",
        "--script",
        action="store_true",
        help="Script mode: no colors, suitable for piping or CI",
    )
    args = parser.parse_args()

    num_workers = args.workers
    num_tasks = args.tasks
    adapter = PlainTextAdapter(
        interactive=False,
        use_color=False if args.script else None,
    )

    # Build task list
    task_queue = []
    for i in range(num_tasks):
        tpl = random.choice(FILENAMES)
        fname = tpl.format(random.randint(1, 12))
        dataset = random.choice(DATASETS)
        target = f"/data/downloads/{dataset}/{fname}"
        size = random.randint(50 * 1024**2, 8 * 1024**3)
        rid = str(uuid.uuid4())
        task = Task(
            dataset=dataset, request={"variable": "2m_temperature"}, target=target
        )
        task_queue.append((task, size, rid))

    num_cached = min(2, num_tasks)
    pending = num_tasks - num_cached
    adapter.on_global_message(f"{num_cached}/{num_tasks} files already exist, skipping")
    adapter.on_global_message(f"Downloading {pending} files ({num_workers} workers)")
    adapter.on_progress_update(0, pending, num_cached)

    active = {}
    task_idx = num_cached
    tasks_done = 0
    results: list[Result] = []

    # Skipped tasks count as successful
    for i in range(num_cached):
        results.append(Result(task=task_queue[i][0], success=True))

    wall_start = time.time()

    def assign(wid):
        nonlocal task_idx
        if task_idx >= num_tasks:
            return False
        task, size, rid = task_queue[task_idx]
        task_idx += 1
        adapter.on_task_started(wid, task)
        adapter.on_task_message(wid, "Request is queued")
        adapter.on_task_request_id(wid, rid)
        active[wid] = {
            "task": task,
            "size": size,
            "rid": rid,
            "phase": 0,  # 0=queued, 1=running, 2=successful, 3=downloading
            "ticks": 0,
            "dl_bytes": 0,
            "start_time": time.time(),
        }
        return True

    for wid in range(min(num_workers, pending)):
        assign(wid)

    cancelled = False
    while active:
        try:
            time.sleep(0.15)
        except KeyboardInterrupt:
            adapter.on_global_message("Interrupted — cancelling CDS requests...")
            for wid in list(active):
                adapter.on_global_message(f"  Cancelled {active[wid]['rid']}")
            cancelled = True
            break

        for wid in list(active):
            state = active[wid]
            state["ticks"] += 1
            phase = state["phase"]

            if phase == 0:
                # Queued — send duplicate status to show deduplication
                adapter.on_task_message(wid, "Request is queued")
                if state["ticks"] >= random.randint(4, 10):
                    state["phase"] = 1
                    state["ticks"] = 0
                    adapter.on_task_message(wid, "Request is running")

            elif phase == 1:
                adapter.on_task_message(wid, "Request is running")
                if state["ticks"] >= random.randint(3, 8):
                    state["phase"] = 2
                    state["ticks"] = 0
                    adapter.on_task_message(
                        wid, "status has been updated to successful"
                    )

            elif phase == 2:
                if state["ticks"] >= random.randint(1, 3):
                    state["phase"] = 3
                    state["ticks"] = 0

            elif phase == 3:
                chunk = random.randint(state["size"] // 40, state["size"] // 15)
                state["dl_bytes"] = min(state["dl_bytes"] + chunk, state["size"])
                adapter.on_task_progress(wid, state["dl_bytes"], state["size"])

                if state["dl_bytes"] >= state["size"]:
                    tasks_done += 1
                    adapter.on_progress_update(tasks_done, pending, num_cached)
                    failed = random.random() < 0.08
                    error = "connection timeout" if failed else ""
                    success = not failed
                    if success:
                        adapter.on_task_completed(wid, state["task"], True)
                    else:
                        adapter.on_task_completed(wid, state["task"], False, error)
                    results.append(
                        Result(
                            task=state["task"],
                            success=success,
                            error=error,
                            start_time=state["start_time"],
                            end_time=time.time(),
                            file_size=state["size"],
                            warnings=[],
                        )
                    )
                    del active[wid]
                    assign(wid)

    wall_end = time.time()
    if cancelled:
        adapter.on_global_message("Downloads cancelled.")
    else:
        adapter.on_global_message("All downloads completed successfully.")
    print()
    print_summary(results, wall_start, wall_end)


if __name__ == "__main__":
    main()
