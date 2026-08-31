# Contributing to VIGO for Python

VIGO for Python is a thin binding around the canonical VIGO runtime. Changes
must preserve that boundary: routing, timetable compilation, and street-network
algorithms belong in the [VIGO native runtime](https://github.com/hytangs/vigo), not in
this package.

## Development setup

Use Python 3.10 or newer:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e .
```

Run the complete source-level checks before opening a change:

```bash
PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s tests -t . -p 'test_vigo_router*.py'
python scripts/check-notebooks.py
python scripts/check-public-release.py
python -m pip wheel --no-deps --wheel-dir dist .
```

Tests must not require private GTFS feeds, private OSM extracts, developer
paths, or generated artifacts. Use the focused fixtures already in `tests/`.

## Runtime changes

The default runtime URL and SHA-256 digest are versioned together in
`vigo_router/runtime.py`. Change them only for a verified VIGO release artifact,
and test installation from a clean virtual environment with no source-tree
imports.

## Reporting results

Keep Python wrapper time, VIGO preparation time, native routing time, and output
serialization time separate. A passing binding test is not a general claim
about GTFS correctness or operational suitability.
