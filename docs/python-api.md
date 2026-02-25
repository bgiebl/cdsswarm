# Python API

cdsswarm can be used as a Python library for programmatic downloads.

## Basic Usage

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

## Reference

### `cdsswarm.Task`

A single CDS API download request (dataclass).

| Field | Type | Description |
|---|---|---|
| `dataset` | `str` | CDS dataset name (e.g. `"reanalysis-era5-single-levels"`) |
| `request` | `dict` | Request parameters, same format as `cdsapi.Client.retrieve()` |
| `target` | `str` | Local file path to save the downloaded data |

### `cdsswarm.Result`

Result of a single download (dataclass).

| Field | Type | Description |
|---|---|---|
| `task` | `Task` | The original task |
| `success` | `bool` | Whether the download succeeded |
| `error` | `str` | Error message (empty on success) |

### `cdsswarm.download()`

```python
cdsswarm.download(
    tasks: list[Task],
    num_workers: int = 4,
    skip_existing: bool = True,
    reuse_jobs: bool = True,
    max_retries: int = 3,
    on_message: Callable[[str], None] | None = None,
    post_hook: str = "",
) -> list[Result]
```

Download multiple CDS API requests concurrently.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `tasks` | `list[Task]` | required | List of download tasks |
| `num_workers` | `int` | `4` | Number of parallel workers |
| `skip_existing` | `bool` | `True` | Skip files that already exist |
| `reuse_jobs` | `bool` | `True` | Reuse existing CDS jobs with matching parameters |
| `max_retries` | `int` | `3` | Max retry attempts per task (1 to disable) |
| `on_message` | `callable` | `None` | Callback `fn(message: str)` for status updates |
| `post_hook` | `str` | `""` | Shell command to run after each successful download (`{file}`, `{dataset}`) |

Returns a `list[Result]`. Returns an empty list if interrupted by `KeyboardInterrupt`.

### `cdsswarm.expand_template()`

```python
cdsswarm.expand_template(
    template: dict,
    split_by: list[str] | None = None,
) -> list[Task]
```

Expand a template dict into a list of `Task` objects via Cartesian product of the `split_by` dimensions. If `split_by` is `None`, uses the template's `split_by` field.

```python
import cdsswarm

template = {
    "dataset": "reanalysis-era5-single-levels",
    "request": {
        "product_type": ["reanalysis"],
        "variable": ["2m_temperature", "total_precipitation"],
        "year": ["2023", "2024"],
        "month": ["01", "02", "03"],
        "day": ["01"],
        "time": ["12:00"],
        "data_format": "grib",
    },
    "target": "output/{variable}_{year}_{month}.grib",
    "split_by": ["variable", "year", "month"],
}

tasks = cdsswarm.expand_template(template)
# 2 × 2 × 3 = 12 tasks
```

### `cdsswarm.build_summary()`

```python
cdsswarm.build_summary(results: list[Result]) -> dict
```

Build a summary dict from download results, suitable for JSON export.

### Exception Types

#### `cdsswarm.ConfigError`

Raised for invalid configuration values in config files or CLI flags. Subclass of `ValueError`.

#### `cdsswarm.RequestFileError`

Raised for invalid or unrecognized request file formats. Subclass of `ValueError`.
