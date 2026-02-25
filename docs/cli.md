# CLI Reference

## `cdsswarm`

Download CDS API requests concurrently with multiple workers.

```
cdsswarm [-h] [--version] [-w WORKERS] [-m {interactive,script,auto}]
         [--no-skip] [--resume | --no-resume] [--reuse | --no-reuse]
         [--max-retries MAX_RETRIES] [--output-dir OUTPUT_DIR]
         [--dry-run] [--ignore-warnings] [--log FILE] [--summary FILE]
         [--post-hook CMD]
         requests_file
```

| Argument | Description |
|---|---|
| `requests_file` | Path to a JSON or YAML file with download requests |
| `-w`, `--workers` | Number of parallel download workers (default: 4) |
| `-m`, `--mode` | Display mode: `interactive` (TUI), `script` (plain text), or `auto` (default) |
| `--no-skip` | Re-download files that already exist on disk |
| `--resume` / `--no-resume` | Resume an interrupted session if state file exists (default: enabled) |
| `--reuse` / `--no-reuse` | Reuse existing CDS jobs with matching parameters (default: enabled) |
| `--max-retries` | Max retry attempts per task (default: 3, 1 to disable) |
| `--output-dir` | Prepend directory to relative target paths |
| `--dry-run` | Show what would be downloaded without actually downloading |
| `--ignore-warnings` | Auto-continue on warnings without prompting |
| `--log FILE` | Write timestamped log to a file |
| `--summary FILE` | Export summary as JSON (`.json`) or CSV (`.csv`) |
| `--post-hook CMD` | Shell command to run after each successful download |

### Display Modes

In `auto` mode, the TUI is used when stdout is a TTY; otherwise it falls back to script mode.

**Interactive (TUI)** — an htop-style terminal UI with live worker status, progress bars, and keyboard navigation. See [TUI](tui.md) for details.

**Script** — plain-text output suitable for logging, piping, and non-interactive environments.

### Post-Download Hooks

The `--post-hook` option runs a shell command after each file is successfully downloaded. Use `{file}` and `{dataset}` as placeholders:

```bash
# Compress each file after download
cdsswarm requests.json --post-hook "gzip {file}"

# Convert GRIB to NetCDF with CDO
cdsswarm requests.json --post-hook "cdo -f nc copy {file} {file}.nc"

# Upload to S3
cdsswarm requests.json --post-hook "aws s3 cp {file} s3://my-bucket/cds/"
```

Hook failures produce a warning but do not mark the download as failed — the file is already on disk.

### Session Resume

cdsswarm automatically saves session state after each task completes. If a download session is interrupted (e.g. by `Ctrl+C` or a network failure), rerunning the same command picks up where it left off — completed tasks are skipped and failed/pending tasks are retried.

State files are stored in `~/.cache/cdsswarm/sessions/` (or `$XDG_CACHE_HOME`), keyed by request file path and output directory. Run logs are automatically saved to `~/.local/state/cdsswarm/logs/` (or `$XDG_STATE_HOME`).

```bash
cdsswarm requests.json -w 4             # interrupted — 50 of 100 tasks done
cdsswarm requests.json -w 4             # resumes from task 51
cdsswarm requests.json -w 4 --no-resume # force a fresh start
```

### Dry Run

The `--dry-run` flag shows what would be downloaded without making any API calls:

```bash
cdsswarm requests.json --dry-run
```

This is useful for verifying your request file before starting a long download session.

## `cdsswarm generate`

Expand a template file into a full request file using Cartesian product expansion.

```
cdsswarm generate [-h] [--split-by SPLIT_BY] [-o FILE] [--dry-run]
                  template_file
```

| Argument | Description |
|---|---|
| `template_file` | Path to template JSON/YAML file |
| `--split-by FIELDS` | Override the template's `split_by` (comma-separated) |
| `-o`, `--output FILE` | Output file path (default: stdout) |
| `--dry-run` | Show task count and target filenames without writing output |

The template file must contain a **single JSON object** (not a list). If you pass a single-element list `[{...}]`, it will be auto-unwrapped with a warning.

A template looks like a single request with a `split_by` field that lists which dimensions to expand:

```json
{
  "dataset": "reanalysis-era5-single-levels",
  "request": {
    "product_type": ["reanalysis"],
    "variable": ["2m_temperature", "total_precipitation"],
    "year": ["2023", "2024"],
    "month": ["01", "02", "03"],
    "day": ["01", "02", "03"],
    "time": ["12:00"],
    "data_format": "grib"
  },
  "target": "output/{variable}_{year}_{month}.grib",
  "split_by": ["variable", "year", "month"]
}
```

This generates 2 × 2 × 3 = 12 separate tasks, one for each combination of variable, year, and month. Non-split fields (`day`, `time`, etc.) are shared across all tasks. The `{placeholder}` syntax in `target` fills in each combination's values.

## `cdsswarm cancel`

Cancel active CDS API requests on the server.

```
cdsswarm cancel [-h] [-y] [request_ids ...]
```

| Argument | Description |
|---|---|
| `request_ids` | Specific request IDs to cancel (omit to cancel all active) |
| `-y`, `--yes` | Skip confirmation prompt |

When no IDs are given, cdsswarm queries the CDS server for all active (accepted/running) requests and presents them for confirmation before cancelling. This requires the new CADS API (`ecmwf-datastores`). With the old `cdsapi`, you must provide specific request IDs.

## `cdsswarm completion`

Print a shell completion script for bash or zsh.

```
cdsswarm completion [-h] {bash,zsh}
```

| Argument | Description |
|---|---|
| `{bash,zsh}` | Shell type to generate completions for |

See [Getting Started](getting-started.md#shell-completion) for installation instructions.
