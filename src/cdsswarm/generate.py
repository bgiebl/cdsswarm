"""Template expansion for generating request files."""

from __future__ import annotations

import copy
import itertools
import re

from .exceptions import RequestFileError


def _extract_placeholders(target_template: str) -> set[str]:
    """Parse ``{...}`` placeholders from a target template string."""
    return set(re.findall(r"\{(\w+)\}", target_template))


def validate_template(template: dict, split_by: list[str]) -> None:
    """Validate a template dict, raising *RequestFileError* on problems."""
    for key in ("dataset", "request", "target"):
        if key not in template:
            raise RequestFileError(f"Template missing required key: '{key}'")

    request = template["request"]
    if not isinstance(request, dict):
        raise RequestFileError("Template 'request' must be a mapping")

    for field in split_by:
        if field not in request:
            raise RequestFileError(f"split_by field '{field}' not found in request")
        if not isinstance(request[field], list):
            raise RequestFileError(
                f"split_by field '{field}' must be a list, got {type(request[field]).__name__}"
            )

    placeholders = _extract_placeholders(template["target"])
    for ph in placeholders:
        if ph not in split_by:
            raise RequestFileError(
                f"Target placeholder '{{{ph}}}' is not in split_by fields: {split_by}"
            )


def expand_template(template: dict, split_by: list[str] | None = None) -> dict:
    """Expand a template into a compact-format request dict.

    Args:
        template: Template dict with ``dataset``, ``request``, ``target``,
            and optionally ``split_by``.
        split_by: Override for ``template["split_by"]``. If *None*, uses the
            value from the template (defaults to empty list).

    Returns:
        Compact-format dict ``{"dataset": ..., "requests": [...]}``.
    """
    if split_by is None:
        split_by = template.get("split_by", [])

    validate_template(template, split_by)

    request = template["request"]
    target_template = template["target"]
    dataset = template["dataset"]

    if not split_by:
        return {
            "dataset": dataset,
            "requests": [
                {"request": copy.deepcopy(request), "target": target_template}
            ],
        }

    # Build dimension values: [(key, [val1, val2, ...]), ...]
    dimensions = [(key, request[key]) for key in split_by]
    dim_keys = [k for k, _ in dimensions]
    dim_values = [v for _, v in dimensions]

    requests = []
    seen_targets: set[str] = set()
    for combo in itertools.product(*dim_values):
        mapping = dict(zip(dim_keys, combo))

        task_request = copy.deepcopy(request)
        for key, value in mapping.items():
            task_request[key] = [value]

        target = target_template.format(**mapping)

        if target in seen_targets:
            raise RequestFileError(f"Duplicate target after expansion: '{target}'")
        seen_targets.add(target)

        requests.append({"request": task_request, "target": target})

    return {"dataset": dataset, "requests": requests}
