# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/).

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

[0.2.0]: https://github.com/bgiebl/cdsswarm/compare/v0.1.7...v0.2.0
[0.1.7]: https://github.com/bgiebl/cdsswarm/compare/v0.1.6...v0.1.7
[0.1.6]: https://github.com/bgiebl/cdsswarm/compare/v0.1.5...v0.1.6
[0.1.5]: https://github.com/bgiebl/cdsswarm/compare/v0.1.4...v0.1.5
