# Changelog

## 0.3.1 — 2026-09-11

Stabilization release; public API and data schema versions remain unchanged.

- Discover complete packaged runtimes on macOS, Linux, and Windows.
- Test supported Python versions on all three operating systems and run public integration fixtures against packaged VIGO.

- Explain reusable City packages, portable prepared state, and the distinction between reopening and rebuilding.
- Reuse successful runtime capability checks until the executable or command file changes.
- Require resident Matrix support and remove the old one-shot Matrix path.
- Read current City metadata fields directly and remove automatic discovery of the retired VIGO.app name.
- Keep installation and examples in this repository; expand the Python and runtime references.

- Preserve native plan JSON for compact Result export, avoiding repeated geometry encoding while keeping independently mutable Python exports and compatibility with older runtimes.

## 0.3.0

VIGO 0.3 unifies the product around one model:

```text
City -> Scenario -> Route | Matrix | Reach -> Result
```

- Renamed the Python package to `vigo`.
- Replaced store-pair objects with one complete City.
- Added immutable Scenario objects tied to one City revision.
- Replaced separate batch and one-origin analysis functions with Route and Matrix.
- Renamed network range analysis to Reach.
- Added one Result shape, local comparison, export, and background jobs.
- Removed the Python pass-through command and runtime installer. VIGO Studio and the VIGO command line are installed independently.
- Bound the per-City resident Route process pool and reclaim idle dates automatically. Queries retain their process until completion; closing a City waits for active Route calls.
- Add transit requirements, search horizons, transfer caps, and optional Matrix journeys, including arrive-by and shared many-to-one queries.
- Add `disable_cache` to Route and Matrix for transit street-access and path measurements. Keep result accessors independent while avoiding redundant copies during result construction and JSON/GeoJSON export.
