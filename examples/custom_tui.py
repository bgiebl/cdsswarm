#!/usr/bin/env python3
"""Example: build tasks programmatically and run them with the TUI.

Usage:
    python examples/custom_tui.py

Downloads ERA5 2m temperature for Germany, one request per month (Jan-Jun 2024),
using the curses TUI to monitor progress. Requires CDS API credentials in
~/.cdsapirc.
"""

import curses
import os
import sys

from cdsswarm.adapters import CursesAdapter
from cdsswarm.core import SwarmDownloader, Task
from cdsswarm.tui import CursesTUI

NUM_WORKERS = 4
OUTPUT_DIR = "output"
AREA = [55, 5, 47, 16]  # Germany bbox: [N, W, S, E]
MONTHS = range(1, 7)  # Jan-Jun


def build_tasks():
    tasks = []
    for month in MONTHS:
        tasks.append(
            Task(
                dataset="reanalysis-era5-single-levels",
                request={
                    "product_type": ["reanalysis"],
                    "variable": ["2m_temperature"],
                    "year": ["2024"],
                    "month": [f"{month:02d}"],
                    "day": [f"{d:02d}" for d in range(1, 32)],
                    "time": ["12:00"],
                    "area": AREA,
                    "data_format": "grib",
                },
                target=os.path.join(OUTPUT_DIR, f"t2m_2024_{month:02d}.grib"),
            )
        )
    return tasks


def main():
    import threading

    tasks = build_tasks()
    tui = CursesTUI(num_workers=NUM_WORKERS)
    download_result = [None]

    def _run_download(downloader):
        download_result[0] = downloader.run()

    def _main(stdscr):
        tui.start(stdscr)
        adapter = CursesAdapter(tui)
        downloader = SwarmDownloader(
            tasks=tasks,
            adapter=adapter,
            num_workers=NUM_WORKERS,
        )

        download_thread = threading.Thread(
            target=_run_download,
            args=(downloader,),
            daemon=True,
        )
        download_thread.start()

        stdscr.timeout(200)
        try:
            while download_thread.is_alive():
                try:
                    key = stdscr.getch()
                except curses.error:
                    continue
                if key == -1:
                    tui.refresh()
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
                    tui.toggle_expand()
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
        results = download_result[0]

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

    curses.wrapper(_main)
    results = download_result[0]
    if results is None or any(not r.success for r in results):
        sys.exit(1)


if __name__ == "__main__":
    main()
