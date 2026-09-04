# Contributing to VIGO SDK

VIGO SDK is the Python interface to VIGO. It must preserve the same City, Scenario, Route, Matrix, Reach, and Result model used by VIGO Studio and the command line.

## Before changing code

- Put computation changes in the VIGO engine repository.
- Keep this repository focused on Python objects, process communication, validation, and examples.
- Do not add a second GTFS parser or routing implementation.
- Do not expose runtime preparation as a user task.
- Do not introduce a new public noun when an option on Route, Matrix, or Reach is sufficient.

## Checks

```bash
python -m pytest -q
python -m ruff check vigo tests
python -m pip wheel . --no-deps --wheel-dir dist
```

Add focused tests for public behavior. Use a real VIGO City for end-to-end checks when the change crosses the Python and engine boundary.

## Pull requests

Explain the user-visible behavior, affected Query family, tests run, and known limits. Keep unrelated changes out of the patch.
