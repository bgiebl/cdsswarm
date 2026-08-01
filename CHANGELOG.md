# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]

## [0.3.0] - 2026-08-01

### Removed
- `--ignore-warnings` CLI flag and the `ignore-warnings` config key. Both had been no-ops since checksum verification was removed in 0.1.6 — that dialog was the only interactive warning prompt. An `ignore-warnings` key left in `.cdsswarm.toml` is ignored, not an error.
- `interactive` parameter of `PlainTextAdapter`. It backed the same removed dialog and was never read. Callers constructing the adapter directly must drop the keyword argument.

### Changed
- Adopt the ruff 0.16 default rule set rather than restricting the selection; blind `except` handlers narrowed to concrete exception types where the raise set is knowable
- Bump GitHub Actions dependencies (deploy-pages 5, configure-pages 6, upload-pages-artifact 5, codecov-action 6)

## [0.2.3] - 2026-03-03

### Changed
- Replace `open(os.devnull)` with no-op `_NullWriter` in tqdm progress router to avoid holding a real file descriptor across worker threads ([#6](https://github.com/bgiebl/cdsswarm/issues/6))
- Improve cancellation responsiveness by replacing `as_completed` with a polling wait loop
- `a` (request parameters) now works on the Files tab, showing the parameters for the selected file

### Fixed
- Route retry tracebacks through the output adapter so they reach the log files in TUI mode ([#7](https://github.com/bgiebl/cdsswarm/issues/7))

## [0.2.2] - 2026-02-25

### Added
- Full traceback logging for failed download attempts before a retry

## [0.2.1] - 2026-02-25

### Added
- Server-aware worker pause/resume: workers pause when CDS server is down and retry with exponential backoff when degraded
- W-State column in TUI worker table showing worker state (active/paused/retrying)
- Server status display in MeterBar footer with blinking status dot and reason from ECMWF API
- File size display for cached (skipped) files in TUI Files tab via `os.path.getsize`
- Documentation site (MkDocs) with CLI, configuration, Python API, and TUI guides

## [0.2.0] - 2026-02-25

### Added
- Shell completion for bash and zsh via `cdsswarm completion bash|zsh` (uses `shtab`)
- Benchmark script (`examples/benchmark.py`)
- Python 3.14 support
- Mypy type checking with pre-commit hook
- Ruff linter added to dev dependencies
- Changelog link in project metadata

### Changed
- Declared package as typed (`Typing :: Typed` classifier)
- Added `OS Independent` classifier

## [0.1.7] - 2026-02-25

### Added
- CDS server status display in TUI (queue position, estimated wait time)
- Status bar footer in TUI with overall progress and ETA

### Changed
- Removed progress column from TUI worker table (replaced by status bar)

### Fixed
- Fix task state not persisting correctly on resume ([#6](https://github.com/bgiebl/cdsswarm/issues/6))
- Fix user status not updating correctly in TUI
- Fix server status info parsing for new CDS API responses
- Fix failed task status not displaying properly

## [0.1.6] - 2026-02-24

### Added
- Automatic logging: all runs write to `$XDG_STATE_HOME/cdsswarm/logs/` with log rotation (keeps last 10)

### Removed
- Checksum verification (removed due to unreliable CDS API checksums)

## [0.1.5] - 2026-02-24

### Added
- `cdsswarm cancel` subcommand for cancelling active CDS requests
- Project logo

### Fixed
- Fix `cdsswarm generate` failing when given a single-element list instead of a plain object ([#5](https://github.com/bgiebl/cdsswarm/issues/5))

### Changed
- Bump GitHub Actions dependencies (checkout v6, setup-python v6, upload-artifact v6, download-artifact v7)

[Unreleased]: https://github.com/bgiebl/cdsswarm/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/bgiebl/cdsswarm/compare/v0.2.3...v0.3.0
[0.2.3]: https://github.com/bgiebl/cdsswarm/compare/v0.2.2...v0.2.3
[0.2.2]: https://github.com/bgiebl/cdsswarm/compare/v0.2.1...v0.2.2
[0.2.1]: https://github.com/bgiebl/cdsswarm/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/bgiebl/cdsswarm/compare/v0.1.7...v0.2.0
[0.1.7]: https://github.com/bgiebl/cdsswarm/compare/v0.1.6...v0.1.7
[0.1.6]: https://github.com/bgiebl/cdsswarm/compare/v0.1.5...v0.1.6
[0.1.5]: https://github.com/bgiebl/cdsswarm/compare/v0.1.4...v0.1.5
