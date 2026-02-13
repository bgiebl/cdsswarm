#!/usr/bin/env python3
"""Demo: preview the htop-style TUI with simulated downloads (no API calls).

Usage:
    python examples/demo_tui.py
    python examples/demo_tui.py --workers 8 --tasks 20
"""

import argparse
import curses
import random
import threading
import time
import uuid

from cdsswarm.tui import CursesTUI

# Simulated filenames
FILENAMES = [
    "era5_t2m_2024_{:02d}.grib",
    "era5_wind_2024_{:02d}.grib",
    "era5_precip_2024_{:02d}.nc",
    "era5_pressure_2024_{:02d}.grib",
    "cerra_soil_2023_{:02d}.grib",
    "era5_radiation_2024_{:02d}.nc",
]

# Simulated datasets and request params
DATASETS = [
    "reanalysis-era5-single-levels",
    "reanalysis-era5-pressure-levels",
    "reanalysis-cerra-single-levels",
]

VARIABLES = [
    "2m_temperature",
    "10m_u_component_of_wind",
    "total_precipitation",
    "surface_pressure",
    "soil_temperature_level_1",
    "surface_net_solar_radiation",
]

# Heavy request params that exceed 4 lines in the info panel
HEAVY_PARAMS = {
    "product_type": "reanalysis",
    "variable": [
        "2m_temperature",
        "10m_u_component_of_wind",
        "10m_v_component_of_wind",
        "total_precipitation",
        "surface_pressure",
        "mean_sea_level_pressure",
        "soil_temperature_level_1",
        "surface_net_solar_radiation",
        "skin_temperature",
        "geopotential",
        "relative_humidity",
        "specific_humidity",
        "temperature",
        "u_component_of_wind",
    ],
    "pressure_level": [
        "1000",
        "975",
        "950",
        "925",
        "900",
        "850",
        "800",
        "700",
        "600",
        "500",
        "400",
        "300",
        "250",
        "200",
        "150",
        "100",
    ],
    "year": ["2023", "2024"],
    "month": ["01", "02", "03", "04", "05", "06"],
    "day": [f"{d:02d}" for d in range(1, 32)],
    "time": ["00:00", "06:00", "12:00", "18:00"],
    "format": "grib",
    "area": [60, -10, 35, 30],
}


def _simulate_downloads(tui, num_workers, num_tasks, stop_event):
    """Simulate download activity by driving the TUI directly.

    Phases per task mirror the real CDS lifecycle:
      0: accepted  — request queued on the CDS server
      1: running   — server is processing the request
      2: successful — data is ready, download can start
      3: downloading — transferring bytes
      4: done       — file saved locally (or failed)
    """
    tasks_done = 0
    tui.update_progress(0, num_tasks, 0)
    tui.set_status_line(f"Downloading {num_tasks} files ({num_workers} workers)")

    # Queue of tasks to process
    task_queue = []
    for i in range(num_tasks):
        tpl = random.choice(FILENAMES)
        fname = tpl.format(random.randint(1, 12))
        size = random.randint(50 * 1024**2, 8 * 1024**3)  # 50 MB - 8 GB
        request_id = str(uuid.uuid4())
        dataset = random.choice(DATASETS)
        if i < 3:
            # First 3 tasks get heavy params (>4 lines in info panel)
            request_params = HEAVY_PARAMS
        else:
            variable = random.choice(VARIABLES)
            month = random.randint(1, 12)
            request_params = {
                "product_type": "reanalysis",
                "variable": variable,
                "year": "2024",
                "month": f"{month:02d}",
                "day": [f"{d:02d}" for d in range(1, 32)],
                "time": "00:00",
                "format": "grib",
            }
        target = f"/data/downloads/{dataset}/{fname}"
        task_queue.append((fname, size, request_id, dataset, request_params, target))

    active = {}  # worker_id -> state dict
    task_idx = 0

    def _assign(wid):
        nonlocal task_idx
        if task_idx >= num_tasks:
            return False
        fname, size, rid, dataset, params, target = task_queue[task_idx]
        task_idx += 1
        tui.clear_worker_log(wid)
        tui.set_worker_filename(wid, fname)
        tui.set_worker_cds_status(wid, "accepted")
        tui.set_worker_request_id(wid, rid)
        tui.set_worker_task_info(wid, dataset, params, target)
        tui.append_worker_log(wid, f"Started: {fname}")
        tui.append_worker_log(wid, f"Request ID is {rid}")
        active[wid] = {
            "fname": fname,
            "size": size,
            "rid": rid,
            "dl_bytes": 0,
            "phase": 0,
            "phase_ticks": 0,
        }
        return True

    for wid in range(num_workers):
        _assign(wid)

    while not stop_event.is_set() and (active or task_idx < num_tasks):
        time.sleep(0.35)
        if stop_event.is_set():
            break

        for wid in list(active):
            state = active[wid]
            phase = state["phase"]
            state["phase_ticks"] += 1

            if phase == 0 and state["phase_ticks"] >= random.randint(5, 15):
                # accepted -> running (server processing)
                state["phase"] = 1
                state["phase_ticks"] = 0
                tui.set_worker_cds_status(wid, "running")
                tui.append_worker_log(wid, "status has been updated to running")

            elif phase == 1 and state["phase_ticks"] >= random.randint(10, 30):
                # running -> successful (data ready on server)
                state["phase"] = 2
                state["phase_ticks"] = 0
                tui.set_worker_cds_status(wid, "successful")
                tui.append_worker_log(wid, "status has been updated to successful")
                tui.append_worker_log(wid, "Request is completed, starting download")

            elif phase == 2 and state["phase_ticks"] >= random.randint(2, 5):
                # successful -> downloading (transfer begins)
                state["phase"] = 3
                state["phase_ticks"] = 0
                tui.append_worker_log(wid, "Starting download...")

            elif phase == 3:
                # Downloading: increment bytes (slower chunks for longer runtime)
                chunk = random.randint(state["size"] // 80, state["size"] // 30)
                state["dl_bytes"] = min(state["dl_bytes"] + chunk, state["size"])
                tui.update_worker_progress(wid, state["dl_bytes"], state["size"])
                done_mb = state["dl_bytes"] / (1024 * 1024)
                total_mb = state["size"] / (1024 * 1024)
                pct = int(state["dl_bytes"] * 100 / state["size"])
                tui.append_worker_log(
                    wid, f"Downloading: {done_mb:.0f}/{total_mb:.0f} MB ({pct}%)"
                )

                if state["dl_bytes"] >= state["size"]:
                    # Download finished — small chance of failure
                    if random.random() < 0.1:
                        tui.set_worker_cds_status(wid, "failed")
                        tui.append_worker_log(wid, "Error: connection timeout")
                    else:
                        tui.append_worker_log(wid, f"Completed: {state['fname']}")
                    tui.set_worker_finished(wid)
                    tasks_done += 1
                    tui.update_progress(tasks_done, num_tasks, 0)
                    # Enter cooldown phase so finished state is visible
                    state["phase"] = 4
                    state["phase_ticks"] = 0

            elif phase == 4 and state["phase_ticks"] >= random.randint(8, 15):
                # Cooldown finished — assign next task
                del active[wid]
                _assign(wid)

    if not stop_event.is_set():
        failed = sum(
            1 for wid in range(num_workers) if tui._worker_cds_status[wid] == "failed"
        )
        if failed:
            tui.set_status_line(f"Done ({failed} failed). Press any key to exit...")
        else:
            tui.set_status_line("All downloads completed! Press any key to exit...")


def main():
    parser = argparse.ArgumentParser(description="Demo the cdsswarm TUI")
    parser.add_argument("-w", "--workers", type=int, default=4)
    parser.add_argument("-t", "--tasks", type=int, default=50)
    args = parser.parse_args()

    tui = CursesTUI(num_workers=args.workers, title="cdsswarm demo (simulated)")
    stop_event = threading.Event()

    def _main(stdscr):
        tui.start(stdscr)

        sim_thread = threading.Thread(
            target=_simulate_downloads,
            args=(tui, args.workers, args.tasks, stop_event),
            daemon=True,
        )
        sim_thread.start()

        stdscr.timeout(200)
        try:
            while sim_thread.is_alive():
                try:
                    key = stdscr.getch()
                except curses.error:
                    continue
                if key == -1:
                    tui.refresh()
                    continue
                if key == ord("q"):
                    stop_event.set()
                    tui.set_status_line("Stopping...")
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
            stop_event.set()

        sim_thread.join(timeout=5)
        if not stop_event.is_set():
            tui.refresh()
            stdscr.timeout(-1)
            stdscr.getch()

    curses.wrapper(_main)


if __name__ == "__main__":
    main()
