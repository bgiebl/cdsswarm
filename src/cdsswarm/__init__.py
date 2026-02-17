"""cdsswarm — Concurrent CDS API downloader with TUI and script mode.

Usage as a Python library:

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
"""

from importlib.metadata import version as _pkg_version

from .adapters import PlainTextAdapter
from .core import Result, SwarmDownloader, Task
from .exceptions import ChecksumMismatchError, ConfigError, RequestFileError
from .generate import expand_template
from .summary import build_summary

__version__ = _pkg_version("cdsswarm")
__all__ = [
    "Task",
    "Result",
    "build_summary",
    "download",
    "expand_template",
    "ChecksumMismatchError",
    "ConfigError",
    "RequestFileError",
    "__version__",
]


def download(
    tasks: list[Task],
    num_workers: int = 4,
    skip_existing: bool = True,
    reuse_jobs: bool = True,
    max_retries: int = 3,
    on_message=None,
    post_hook: str = "",
) -> list[Result]:
    """Download multiple CDS API requests concurrently.

    Args:
        tasks: List of Task objects specifying what to download.
        num_workers: Number of parallel download workers.
        skip_existing: Skip tasks whose target file already exists.
        reuse_jobs: Reuse existing CDS jobs with matching parameters.
        max_retries: Max retry attempts per task (1 to disable retries).
        on_message: Optional callback ``fn(message: str)`` for status messages.
        post_hook: Shell command to run after each successful download.
            Placeholders: {file} (output path), {dataset} (dataset name).

    Returns:
        List of Result objects. Returns empty list if interrupted.
    """
    adapter = PlainTextAdapter(write_fn=on_message)
    downloader = SwarmDownloader(
        tasks=tasks,
        adapter=adapter,
        num_workers=num_workers,
        skip_existing=skip_existing,
        reuse_jobs=reuse_jobs,
        max_retries=max_retries,
        post_hook=post_hook,
    )
    results = downloader.run()
    return results if results is not None else []
