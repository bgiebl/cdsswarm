# Configuration

## Configuration File

Settings can be stored in a `.cdsswarm.toml` file instead of passing CLI flags every time. CLI flags always take precedence.

| Location | Scope |
|---|---|
| `~/.cdsswarm.toml` | User-global defaults |
| `.cdsswarm.toml` (working directory) | Project-level overrides |

Precedence order (highest to lowest):

1. **CLI flags** — always win
2. **Project config** — `.cdsswarm.toml` in the working directory
3. **User config** — `~/.cdsswarm.toml`
4. **Built-in defaults**

### Example

```toml
workers = 8
max-retries = 5
mode = "script"
output-dir = "/data/downloads"
post-hook = "gzip {file}"
```

All CLI flags are supported as config keys (use hyphens, e.g. `max-retries`, `post-hook`).

### All Config Keys

| Key | Type | Default | Description |
|---|---|---|---|
| `workers` | int | `4` | Number of parallel download workers |
| `mode` | string | `"auto"` | Display mode: `interactive`, `script`, or `auto` |
| `skip-existing` | bool | `true` | Skip files that already exist on disk |
| `resume` | bool | `true` | Resume interrupted sessions |
| `reuse` | bool | `true` | Reuse existing CDS jobs with matching parameters |
| `max-retries` | int | `3` | Max retry attempts per task (1 to disable) |
| `output-dir` | string | | Prepend directory to relative target paths |
| `log` | string | | Write timestamped log to a file |
| `summary` | string | | Export summary as JSON or CSV |
| `post-hook` | string | | Shell command after each successful download |

## Request File Formats

### List Format

Each entry specifies its own dataset:

```json
[
  {
    "dataset": "reanalysis-era5-single-levels",
    "request": {
      "product_type": ["reanalysis"],
      "variable": ["2m_temperature"],
      "year": ["2024"],
      "month": ["01"],
      "day": ["01"],
      "time": ["12:00"],
      "data_format": "grib"
    },
    "target": "output1.grib"
  },
  {
    "dataset": "reanalysis-era5-pressure-levels",
    "request": {
      "product_type": ["reanalysis"],
      "variable": ["geopotential"],
      "pressure_level": ["500"],
      "year": ["2024"],
      "month": ["01"],
      "day": ["01"],
      "time": ["12:00"],
      "data_format": "grib"
    },
    "target": "output2.grib"
  }
]
```

### Compact Format

Share a dataset across all requests:

```json
{
  "dataset": "reanalysis-era5-single-levels",
  "requests": [
    {
      "request": {
        "product_type": ["reanalysis"],
        "variable": ["2m_temperature"],
        "year": ["2024"],
        "month": ["01"],
        "day": ["01"],
        "time": ["12:00"],
        "data_format": "grib"
      },
      "target": "output1.grib"
    },
    {
      "request": {
        "product_type": ["reanalysis"],
        "variable": ["total_precipitation"],
        "year": ["2024"],
        "month": ["01"],
        "day": ["01"],
        "time": ["12:00"],
        "data_format": "grib"
      },
      "target": "output2.grib"
    }
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
