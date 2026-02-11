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

from .adapters import PlainTextAdapter
from .core import Result, SwarmDownloader, Task

__all__ = ["Task", "Result", "download"]


def download(
    tasks: list[Task],
    num_workers: int = 4,
    skip_existing: bool = True,
    on_message=None,
) -> list[Result]:
    """Download multiple CDS API requests concurrently.

    Args:
        tasks: List of Task objects specifying what to download.
        num_workers: Number of parallel download workers.
        skip_existing: Skip tasks whose target file already exists.
        on_message: Optional callback ``fn(message: str)`` for status messages.

    Returns:
        List of Result objects. Returns empty list if interrupted.
    """
    adapter = PlainTextAdapter(write_fn=on_message)
    downloader = SwarmDownloader(
        tasks=tasks,
        adapter=adapter,
        num_workers=num_workers,
        skip_existing=skip_existing,
    )
    results = downloader.run()
    return results if results is not None else []
