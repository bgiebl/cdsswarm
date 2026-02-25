# Getting Started

## Installation

Install from PyPI:

```bash
pip install cdsswarm
```

For YAML request file support:

```bash
pip install "cdsswarm[yaml]"
```

For development (tests, linting, type checking):

```bash
git clone https://github.com/bgiebl/cdsswarm.git
cd cdsswarm
pip install -e ".[dev]"
```

## Prerequisites

You need a valid CDS API configuration file at `~/.cdsapirc`:

```
url: https://cds.climate.copernicus.eu/api
key: <your-personal-access-token>
```

See the [CDS API documentation](https://cds.climate.copernicus.eu/how-to-api) for setup instructions.

## First Download

### 1. Create a request file

Save the following as `requests.json`:

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

### 2. Run the download

```bash
cdsswarm requests.json --workers 4
```

If your terminal supports it, the interactive TUI will launch automatically. Otherwise cdsswarm falls back to script mode with plain-text output.

### 3. Check the results

Downloaded files appear in the current directory (or in `--output-dir` if specified). If a download is interrupted, just rerun the same command — cdsswarm resumes from where it left off.

## What's Next

- [CLI Reference](cli.md) — all commands and options
- [Configuration](configuration.md) — config files and request file formats
- [Python API](python-api.md) — use cdsswarm as a library
- [TUI](tui.md) — interactive terminal UI guide
