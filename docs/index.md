# ![cdsswarm](img/logo.svg){ width="42" style="vertical-align: middle" } cdsswarm

Concurrent [CDS API](https://cds.climate.copernicus.eu/) downloader with an interactive Textual TUI and script mode.

Submit multiple CDS API requests and download them in parallel with a configurable number of workers. Monitor progress through an interactive terminal UI with an htop-style worker table, or run headless in script mode for CI/cron jobs.

![TUI demo](https://raw.githubusercontent.com/bgiebl/cdsswarm/main/img/demo.gif)

## Features

- **Parallel downloads** — configurable number of concurrent workers
- **Interactive TUI** — htop-style worker table with live progress, ETA, and server queue info
- **Script mode** — plain-text output for CI, cron jobs, and headless environments
- **Request generation** — expand templates into hundreds of tasks via Cartesian product
- **Session resume** — interrupted downloads pick up where they left off
- **Post-download hooks** — run shell commands (compress, convert, upload) after each file
- **Configuration file** — store defaults in `.cdsswarm.toml` instead of repeating CLI flags
- **Cancel requests** — clean up active CDS jobs from the command line
- **Python API** — use as a library with `cdsswarm.download()`

## Quick Start

### Install

```bash
pip install cdsswarm
```

### Create a request file

```json
[
  {
    "dataset": "reanalysis-era5-single-levels",
    "request": {
      "product_type": ["reanalysis"],
      "variable": ["2m_temperature"],
      "year": ["2024"],
      "month": ["01"],
      "day": ["01", "02", "03"],
      "time": ["12:00"],
      "data_format": "grib"
    },
    "target": "temperature_jan.grib"
  }
]
```

### Download

```bash
cdsswarm requests.json --workers 4
```

### Or use the Python API

```python
import cdsswarm

tasks = [
    cdsswarm.Task(
        dataset="reanalysis-era5-single-levels",
        request={
            "product_type": ["reanalysis"],
            "variable": ["2m_temperature"],
            "year": ["2024"],
            "month": ["01"],
            "day": ["01"],
            "time": ["12:00"],
            "data_format": "grib",
        },
        target="temperature.grib",
    ),
]

results = cdsswarm.download(tasks, num_workers=4)
```

See the [Getting Started](getting-started.md) guide for a full walkthrough.
