#!/usr/bin/env python3
"""Demo: preview the Textual TUI with simulated downloads (no API calls).

Usage:
    python examples/demo_textual.py
    python examples/demo_textual.py --workers 8 --tasks 20
"""

import argparse
import random
import time
import uuid

from textual import work

from cdsswarm.core import Task
from cdsswarm.status import WorkerStatus
from cdsswarm.textual_app import (
    CdsswarmApp,
    FileActive,
    FileCompleted,
    GlobalMessage,
    ProgressUpdate,
    QosUpdate,
    ServerStatsUpdate,
    TasksInitialized,
    WorkerCdsStatus,
    WorkerFinished,
    WorkerMessage,
    WorkerProgress,
    WorkerRequestId,
    WorkerRequestLabels,
    WorkerServerTimestamps,
    WorkerStarted,
    WorkerDatasetTitle,
    WorkerFileSize,
)

# Simulated filenames
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

DATASET_TITLES = {
    "reanalysis-era5-single-levels": "ERA5 hourly data on single levels from 1940 to present",
    "reanalysis-era5-pressure-levels": "ERA5 hourly data on pressure levels from 1940 to present",
    "reanalysis-cerra-single-levels": "CERRA sub-daily regional reanalysis data for Europe",
}

VARIABLES = [
    "2m_temperature",
    "10m_u_component_of_wind",
    "total_precipitation",
    "surface_pressure",
    "soil_temperature_level_1",
    "surface_net_solar_radiation",
]

VARIABLE_LABELS = {
    "2m_temperature": "2m temperature",
    "10m_u_component_of_wind": "10m u-component of wind",
    "total_precipitation": "Total precipitation",
    "surface_pressure": "Surface pressure",
    "soil_temperature_level_1": "Soil temperature level 1",
    "surface_net_solar_radiation": "Surface net solar radiation",
}

HEAVY_PARAMS = {
    "product_type": "reanalysis",
    "variable": [
        "2m_temperature",
        "10m_u_component_of_wind",
        "10m_v_component_of_wind",
        "total_precipitation",
        "surface_pressure",
        "mean_sea_level_pressure",
    ],
    "pressure_level": ["1000", "975", "950", "925", "900", "850"],
    "year": ["2023", "2024"],
    "month": ["01", "02", "03", "04", "05", "06"],
    "day": [f"{d:02d}" for d in range(1, 32)],
    "time": ["00:00", "06:00", "12:00", "18:00"],
    "format": "grib",
    "area": [60, -10, 35, 30],
}

HEAVY_LABELS = {
    "Product type": "Reanalysis",
    "Variable": "2m temperature, 10m u-component of wind, ...",
    "Pressure level": "1000, 975, 950, 925, 900, 850 hPa",
    "Year": "2023, 2024",
    "Month": "January to June",
    "Day": "01 to 31",
    "Time": "00:00, 06:00, 12:00, 18:00",
    "Data format": "GRIB",
    "Sub-region extracted": "N:60\u00b0 W:-10\u00b0 S:35\u00b0 E:30\u00b0",
}

MONTH_NAMES = [
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
]


class DemoApp(CdsswarmApp):
    """Demo app that simulates downloads without API calls."""

    def __init__(self, num_workers: int, num_tasks: int) -> None:
        super().__init__(num_workers=num_workers, title="cdsswarm demo (simulated)")
        self.num_tasks = num_tasks

    def on_mount(self) -> None:
        self._run_simulation()

    @work(thread=True)
    def _run_simulation(self) -> None:
        """Simulate download activity by posting messages."""
        import threading

        stop_event = threading.Event()
        num_workers = self.num_workers
        num_tasks = self.num_tasks

        # Build task list
        task_queue = []
        all_task_objects = []
        for i in range(num_tasks):
            tpl = random.choice(FILENAMES)
            fname = tpl.format(random.randint(1, 12))
            size = random.randint(50 * 1024**2, 8 * 1024**3)
            request_id = str(uuid.uuid4())
            dataset = random.choice(DATASETS)
            if i < 3:
                request_params = HEAVY_PARAMS
                request_labels = HEAVY_LABELS
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
                request_labels = {
                    "Product type": "Reanalysis",
                    "Variable": VARIABLE_LABELS.get(variable, variable),
                    "Year": "2024",
                    "Month": MONTH_NAMES[month - 1],
                    "Day": "01 to 31",
                    "Time": "00:00",
                    "Data format": "GRIB",
                }
            target = f"/data/downloads/{dataset}/{fname}"
            task_queue.append(
                (
                    fname,
                    size,
                    request_id,
                    dataset,
                    request_params,
                    request_labels,
                    target,
                )
            )
            all_task_objects.append(
                Task(dataset=dataset, request=request_params, target=target)
            )

        # Initialize with some cached entries
        num_cached = min(3, num_tasks)
        skipped_targets = {all_task_objects[i].target for i in range(num_cached)}

        def _post(msg):
            self.call_from_thread(self.post_message, msg)

        _post(TasksInitialized(all_task_objects, skipped_targets))
        _post(ProgressUpdate(0, num_tasks - num_cached, num_cached))
        _post(GlobalMessage(f"Downloading {num_tasks} files ({num_workers} workers)"))
        _post(QosUpdate(random.randint(3000, 6000), random.randint(380, 400), 400))
        _post(
            ServerStatsUpdate(
                random.randint(400, 500),
                random.randint(3000, 5000),
                random.randint(300, 450),
                "Ok",
            )
        )

        active = {}
        task_idx = 0
        tasks_done = 0
        time.monotonic()

        def _assign(wid):
            nonlocal task_idx
            if task_idx >= num_tasks:
                return False
            fname, size, rid, dataset, params, labels, target = task_queue[task_idx]
            task_idx += 1
            _post(WorkerStarted(wid, fname, dataset, params, target))
            _post(WorkerCdsStatus(wid, WorkerStatus.ACCEPTED))
            _post(WorkerRequestId(wid, rid))
            _post(WorkerDatasetTitle(wid, DATASET_TITLES.get(dataset, "")))
            _post(WorkerRequestLabels(wid, labels))
            _post(FileActive(target, wid))
            now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            _post(WorkerServerTimestamps(wid, now, "", ""))
            _post(WorkerMessage(wid, f"Started: {fname}"))
            _post(WorkerMessage(wid, f"Request ID is {rid}"))
            active[wid] = {
                "fname": fname,
                "size": size,
                "rid": rid,
                "target": target,
                "dl_bytes": 0,
                "phase": 0,
                "phase_ticks": 0,
                "server_created": now,
            }
            return True

        for wid in range(num_workers):
            _assign(wid)

        while not stop_event.is_set() and (active or task_idx < num_tasks):
            time.sleep(0.35)
            if stop_event.is_set():
                break

            if random.random() < 0.1:
                _post(
                    QosUpdate(
                        random.randint(3000, 6000),
                        random.randint(380, 400),
                        400,
                    )
                )
                _post(
                    ServerStatsUpdate(
                        random.randint(400, 500),
                        random.randint(3000, 5000),
                        random.randint(300, 450),
                        random.choice(["Ok", "Ok", "Ok", "Degraded"]),
                    )
                )

            for wid in list(active):
                state = active[wid]
                phase = state["phase"]
                state["phase_ticks"] += 1

                if phase == 0 and state["phase_ticks"] >= random.randint(5, 15):
                    state["phase"] = 1
                    state["phase_ticks"] = 0
                    _post(WorkerCdsStatus(wid, WorkerStatus.RUNNING))
                    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                    _post(
                        WorkerServerTimestamps(
                            wid,
                            state["server_created"],
                            now,
                            "",
                        )
                    )
                    _post(WorkerMessage(wid, "status has been updated to running"))

                elif phase == 1 and state["phase_ticks"] >= random.randint(8, 20):
                    state["phase"] = 2
                    state["phase_ticks"] = 0
                    _post(WorkerCdsStatus(wid, WorkerStatus.SUCCESSFUL))
                    _post(WorkerFileSize(wid, state["size"]))
                    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                    _post(
                        WorkerServerTimestamps(
                            wid,
                            state["server_created"],
                            state.get("server_started", ""),
                            now,
                        )
                    )
                    _post(WorkerMessage(wid, "status has been updated to successful"))

                elif phase == 2 and state["phase_ticks"] >= random.randint(2, 5):
                    state["phase"] = 3
                    state["phase_ticks"] = 0
                    _post(WorkerMessage(wid, "Starting download..."))

                elif phase == 3:
                    chunk = random.randint(state["size"] // 80, state["size"] // 30)
                    state["dl_bytes"] = min(state["dl_bytes"] + chunk, state["size"])
                    _post(WorkerProgress(wid, state["dl_bytes"], state["size"]))
                    pct = int(state["dl_bytes"] * 100 / state["size"])
                    _post(
                        WorkerMessage(
                            wid,
                            f"Downloading: {state['dl_bytes'] / (1024 * 1024):.0f}/"
                            f"{state['size'] / (1024 * 1024):.0f} MB ({pct}%)",
                        )
                    )

                    if state["dl_bytes"] >= state["size"]:
                        if random.random() < 0.1:
                            _post(WorkerCdsStatus(wid, WorkerStatus.FAILED))
                            _post(WorkerMessage(wid, "Error: connection timeout"))
                            _post(
                                FileCompleted(
                                    state["target"], False, "connection timeout"
                                )
                            )
                            _post(WorkerFinished(wid, False, "connection timeout"))
                        else:
                            _post(WorkerMessage(wid, f"Completed: {state['fname']}"))
                            _post(FileCompleted(state["target"], True))
                            _post(WorkerFinished(wid))
                        tasks_done += 1
                        _post(
                            ProgressUpdate(
                                tasks_done, num_tasks - num_cached, num_cached
                            )
                        )
                        state["phase"] = 4
                        state["phase_ticks"] = 0

                elif phase == 4 and state["phase_ticks"] >= random.randint(8, 15):
                    del active[wid]
                    _assign(wid)

        if not stop_event.is_set():
            _post(GlobalMessage("All downloads completed! Press q to exit..."))


def main():
    parser = argparse.ArgumentParser(description="Demo the cdsswarm Textual TUI")
    parser.add_argument("-w", "--workers", type=int, default=4)
    parser.add_argument("-t", "--tasks", type=int, default=50)
    args = parser.parse_args()

    app = DemoApp(num_workers=args.workers, num_tasks=args.tasks)
    app.run()


if __name__ == "__main__":
    main()
