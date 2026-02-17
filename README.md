# cdsswarm

[![CI](https://github.com/bgiebl/cdsswarm/actions/workflows/ci.yml/badge.svg)](https://github.com/bgiebl/cdsswarm/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/bgiebl/cdsswarm/branch/main/graph/badge.svg)](https://codecov.io/gh/bgiebl/cdsswarm)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](https://opensource.org/licenses/MIT)

Concurrent [CDS API](https://cds.climate.copernicus.eu/) downloader with an interactive Textual TUI and script mode.

Submit multiple CDS API requests and download them in parallel with a configurable number of workers. Monitor progress through an interactive terminal UI with an htop-style worker table, or run headless in script mode for CI/cron jobs.

![Workers tab](https://raw.githubusercontent.com/bgiebl/cdsswarm/main/img/tui_screenshot_workers.png)
![Files tab](https://raw.githubusercontent.com/bgiebl/cdsswarm/main/img/tui_screenshot.png)

## Installation

```bash
pip install cdsswarm
```

For YAML request file support:

```bash
pip install "cdsswarm[yaml]"
```

For development (tests, pre-commit):

```bash
git clone https://github.com/bgiebl/cdsswarm.git
cd cdsswarm
pip install -e ".[dev]"
```

## Prerequisites

A valid CDS API configuration file at `~/.cdsapirc`:

```
url: https://cds.climate.copernicus.eu/api
key: <your-uid>:<your-api-key>
```

See the [CDS API documentation](https://cds.climate.copernicus.eu/how-to-api) for setup instructions.

## Quick Start

### Command Line

Create a request file `requests.json`:

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
  },
  {
    "dataset": "reanalysis-era5-single-levels",
    "request": {
      "product_type": ["reanalysis"],
      "variable": ["total_precipitation"],
      "year": ["2024"],
      "month": ["01"],
      "day": ["01", "02", "03"],
      "time": ["12:00"],
      "data_format": "grib"
    },
    "target": "precipitation_jan.grib"
  }
]
```

Run with 4 workers:

```bash
cdsswarm requests.json --workers 4
```

### Python API

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
            "day": ["01", "02", "03"],
            "time": ["12:00"],
            "data_format": "grib",
        },
        target="temperature_jan.grib",
    ),
    cdsswarm.Task(
        dataset="reanalysis-era5-single-levels",
        request={
            "product_type": ["reanalysis"],
            "variable": ["total_precipitation"],
            "year": ["2024"],
            "month": ["01"],
            "day": ["01", "02", "03"],
            "time": ["12:00"],
            "data_format": "grib",
        },
        target="precipitation_jan.grib",
    ),
]

results = cdsswarm.download(tasks, num_workers=4)

for r in results:
    if r.success:
        print(f"Downloaded {r.task.target}")
    else:
        print(f"Failed {r.task.target}: {r.error}")
```

## CLI Reference

```
usage: cdsswarm [-h] [--version] [-w WORKERS] [-m {interactive,script,auto}]
                [--no-skip] [--reuse | --no-reuse] [--max-retries MAX_RETRIES]
                [--output-dir OUTPUT_DIR] [--dry-run] [--log FILE]
                [--summary FILE]
                requests_file
```

| Argument | Description |
|---|---|
| `requests_file` | Path to a JSON or YAML file with download requests |
| `-w`, `--workers` | Number of parallel download workers (default: 4) |
| `-m`, `--mode` | Display mode: `interactive` (TUI), `script` (plain text), or `auto` (default) |
| `--no-skip` | Re-download files that already exist on disk |
| `--reuse` / `--no-reuse` | Reuse existing CDS jobs with matching parameters (default: enabled) |
| `--max-retries` | Max retry attempts per task (default: 3, 1 to disable) |
| `--output-dir` | Prepend directory to relative target paths |
| `--dry-run` | Show what would be downloaded without actually downloading |
| `--log FILE` | Write timestamped log to a file |
| `--summary FILE` | Export summary as JSON (`.json`) or CSV (`.csv`) |

In `auto` mode, the TUI is used when stdout is a TTY; otherwise it falls back to script mode.

## Request File Format

### List format

Each entry specifies its own dataset:

```json
[
  {
    "dataset": "reanalysis-era5-single-levels",
    "request": { ... },
    "target": "output1.grib"
  },
  {
    "dataset": "reanalysis-era5-pressure-levels",
    "request": { ... },
    "target": "output2.grib"
  }
]
```

### Compact format

Share a dataset across all requests:

```json
{
  "dataset": "reanalysis-era5-single-levels",
  "requests": [
    { "request": { ... }, "target": "output1.grib" },
    { "request": { ... }, "target": "output2.grib" }
  ]
}
```

### YAML

Both formats also work in YAML (requires `pip install cdsswarm[yaml]`):

```yaml
dataset: reanalysis-era5-single-levels
requests:
  - request:
      product_type: [reanalysis]
      variable: [2m_temperature]
      year: ["2024"]
      month: ["01"]
      day: ["01"]
      time: ["12:00"]
      data_format: grib
    target: temperature.grib
```

The `request` dict accepts the same parameters as `cdsapi.Client.retrieve()`.

## Python API Reference

### `cdsswarm.Task(dataset, request, target)`

A single CDS API download request.

| Field | Type | Description |
|---|---|---|
| `dataset` | `str` | CDS dataset name (e.g. `"reanalysis-era5-single-levels"`) |
| `request` | `dict` | Request parameters, same format as `cdsapi.Client.retrieve()` |
| `target` | `str` | Local file path to save the downloaded data |

### `cdsswarm.download(tasks, num_workers=4, skip_existing=True, reuse_jobs=False, max_retries=3, on_message=None)`

Download multiple CDS API requests concurrently.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `tasks` | `list[Task]` | required | List of download tasks |
| `num_workers` | `int` | `4` | Number of parallel workers |
| `skip_existing` | `bool` | `True` | Skip files that already exist |
| `reuse_jobs` | `bool` | `True` | Reuse existing CDS jobs with matching parameters |
| `max_retries` | `int` | `3` | Max retry attempts per task (1 to disable) |
| `on_message` | `callable` | `None` | Callback `fn(message: str)` for status updates |

Returns a `list[Result]`. Returns an empty list if interrupted by `KeyboardInterrupt`.

### `cdsswarm.Result`

| Field | Type | Description |
|---|---|---|
| `task` | `Task` | The original task |
| `success` | `bool` | Whether the download succeeded |
| `error` | `str` | Error message (empty on success) |

## TUI

The interactive TUI (terminal user interface) is built with [Textual](https://textual.textualize.io/) and is available via the CLI only. It shows an htop-style `DataTable` with one row per worker:

```
W  │Status      │Prog │Filename          │Started  │Elapsed  │Size    │DL %   │Request ID
0  │ running    │72%  │era5_2024_01.grib │22:31:24 │2h30m05s │15.2 GB│48%    │af1e2306-28c3...
1  │ successful │100% │era5_2024_02.nc   │22:31:25 │1h15m00s │8.1 GB │100% ✓ │b2f4a891-...
```

The layout has two tabs (Workers and Files), an info panel above the table, and a progress footer with an overall progress bar and ETA.

**Key bindings:**

| Key | Action |
|---|---|
| `q` | Quit |
| `t` / `Tab` | Switch tab |
| `Enter` | Open scrollable log for the selected worker |
| `a` | Show full request parameters |
| `Esc` | Dismiss screen / go back |
| `Ctrl+C` | Cancel — in-flight CDS API requests are cancelled on the server |

## Running Tests

```bash
pip install -e ".[dev]"
pytest -v
```

## License

MIT
