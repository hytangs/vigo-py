#!/usr/bin/env python3
"""Validate shipped notebooks and Python documentation examples."""

from __future__ import annotations

import json
from pathlib import Path

PYTHON_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = PYTHON_ROOT
NOTEBOOK_ROOT = PYTHON_ROOT / "notebooks"
PACKAGE_README = PYTHON_ROOT / "README.md"
EXPECTED_NOTEBOOKS = {
    "01_build_network.ipynb",
    "02_route_and_batch.ipynb",
    "03_one_to_many.ipynb",
    "04_isochrone.ipynb",
}
FORBIDDEN_API_MARKERS = {
    ".rows": "batch and one-to-many results are list-compatible; iterate or index them directly",
    "row.id": "TravelTime exposes destination_id, not id",
    "cutoff_minutes=": "isochrone uses the cutoffs_minutes sequence",
    "leg.geometry": "Leg exposes coordinates or to_dict(), not geometry",
}
REQUIRED_API_MARKERS = {
    "01_build_network.ipynb": (
        "with vigo_router.TransportNetwork(",
        "build_summary",
    ),
    "02_route_and_batch.ipynb": (
        "with vigo_router.open_network(",
        "network.route(",
        "network.route_batch(",
        "batch[0]",
    ),
    "03_one_to_many.ipynb": (
        "with vigo_router.open_network(",
        "network.route_many(",
        "row.destination_id",
        "for row in result",
    ),
    "04_isochrone.ipynb": (
        "with vigo_router.open_network(",
        "network.isochrone(",
        "cutoffs_minutes=",
        "to_geojson()",
    ),
}
PYTHON_GUIDE_MARKERS = (
    "with vigo_router.open_network(",
    "leg.coordinates",
    "batch[0]",
    "row.destination_id",
    "cutoffs_minutes=",
    "VigoCliError",
)
DOCUMENTATION_CONTRACTS = {
    PACKAGE_README: PYTHON_GUIDE_MARKERS,
}


def source_text(cell: dict[str, object]) -> str:
    source = cell.get("source", "")
    if isinstance(source, list):
        return "".join(str(line) for line in source)
    return str(source)


def reject_stale_api(source: str, label: str) -> None:
    for marker, guidance in FORBIDDEN_API_MARKERS.items():
        if marker in source:
            raise SystemExit(f"{label}: stale API marker {marker!r}; {guidance}")


def python_fences(markdown: str, label: str) -> list[str]:
    blocks: list[str] = []
    current: list[str] | None = None
    for line in markdown.splitlines(keepends=True):
        marker = line.strip()
        if current is None and marker == "```python":
            current = []
        elif current is not None and marker == "```":
            blocks.append("".join(current))
            current = None
        elif current is not None:
            current.append(line)
    if current is not None:
        raise SystemExit(f"{label}: unterminated Python code fence")
    return blocks


def main() -> None:
    discovered = {path.name for path in NOTEBOOK_ROOT.glob("*.ipynb")}
    if discovered != EXPECTED_NOTEBOOKS:
        missing = sorted(EXPECTED_NOTEBOOKS - discovered)
        extra = sorted(discovered - EXPECTED_NOTEBOOKS)
        raise SystemExit(f"notebook set mismatch; missing={missing}, extra={extra}")

    code_cells = 0
    for path in sorted(NOTEBOOK_ROOT.glob("*.ipynb")):
        notebook = json.loads(path.read_text(encoding="utf-8"))
        if notebook.get("nbformat") != 4:
            raise SystemExit(f"{path.name}: expected notebook format 4")
        cells = notebook.get("cells")
        if not isinstance(cells, list) or not cells:
            raise SystemExit(f"{path.name}: notebook has no cells")
        if not any(cell.get("cell_type") == "markdown" for cell in cells):
            raise SystemExit(f"{path.name}: notebook needs explanatory markdown")

        notebook_source: list[str] = []
        for index, cell in enumerate(cells):
            if not isinstance(cell, dict) or cell.get("cell_type") != "code":
                continue
            code_cells += 1
            if cell.get("execution_count") is not None or cell.get("outputs") != []:
                raise SystemExit(f"{path.name} cell {index}: stored execution output")
            source = source_text(cell)
            if "/Users/" in source or "C:\\Users\\" in source:
                raise SystemExit(f"{path.name} cell {index}: developer-specific path")
            compile(source, f"{path.name}:cell-{index}", "exec")
            notebook_source.append(source)

        combined_source = "\n".join(notebook_source)
        reject_stale_api(combined_source, path.name)
        missing_markers = [
            marker
            for marker in REQUIRED_API_MARKERS[path.name]
            if marker not in combined_source
        ]
        if missing_markers:
            raise SystemExit(
                f"{path.name}: missing current API examples: {missing_markers}"
            )

    if code_cells == 0:
        raise SystemExit("example notebooks contain no Python cells")

    documentation_blocks = 0
    for path, required_markers in DOCUMENTATION_CONTRACTS.items():
        markdown = path.read_text(encoding="utf-8")
        relative = path.relative_to(REPOSITORY_ROOT)
        missing_markers = [
            marker for marker in required_markers if marker not in markdown
        ]
        if missing_markers:
            raise SystemExit(
                f"{relative}: missing current API documentation: {missing_markers}"
            )
        blocks = python_fences(markdown, str(relative))
        if not blocks:
            raise SystemExit(f"{relative}: no Python examples")
        for index, source in enumerate(blocks, 1):
            reject_stale_api(source, f"{relative} Python block {index}")
            compile(source, f"{relative}:python-{index}", "exec")
        documentation_blocks += len(blocks)

    print(
        f"Validated {len(EXPECTED_NOTEBOOKS)} clean, API-current notebooks "
        f"({code_cells} code cells) and {documentation_blocks} documentation examples."
    )


if __name__ == "__main__":
    main()
