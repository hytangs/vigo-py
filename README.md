# VIGO Python

Build a transport model once. Query journeys, travel-time matrices, and reachable areas from Python.

The package imports as `vigo` and uses the shared [VIGO Engine](https://github.com/hytangs/vigo). Studio provides the desktop interface; Python provides the same City and Query model for scripts and notebooks. Version 0.5.0 requires Engine 0.5 and uses its documented resident `stream` protocol for Route, Matrix, and Reach. Rebuild prepared Cities with Engine 0.5.

```text
GTFS + OSM -> City -> Route | Matrix | Reach -> Result
                       ^
                 optional Scenario
```

## Install

Use Python 3.10 or newer. For a self-contained install, select the platform wheel built for your OS and CPU:

```bash
python -m pip install /path/to/vigo-0.5.0-py3-none-PLATFORM.whl
```

The platform wheel includes Node, the headless Engine bundle, and the Rust routing kernel. No Studio, system Node, runtime download, or runtime configuration is needed. The installation works offline when the wheel is available locally. Select the actual wheel filename in place of `PLATFORM`.

Source installs remain lightweight and require an explicit compatible Engine installation. Maintainers can [build a self-contained wheel](CONTRIBUTING.md#build-a-headless-wheel) from the two public repositories. External runtimes remain available through `runtime=`, `VIGO_RUNTIME`, or `VIGO_APP`.

See [Runtime and loading](docs/runtime.md) for platform requirements, runtime selection, and process reuse.

For faster decoding of large Matrix and Reach responses, install the optional
`speed` extra: `python -m pip install ".[speed]"` from a source checkout, or
append `[speed]` to the quoted platform wheel path when installing it. This adds
`orjson`; the base package still works without Python dependencies.

## Open and query

Use a complete City built from GTFS and OSM. Stop IDs must exist in that City; coordinates use `[longitude, latitude]`.

```python
import vigo

with vigo.open("./boston") as city:
    result = city.route(
        [-71.062, 42.356],
        [-71.058, 42.349],
        depart_at="08:00",
        service_date="2026-09-04",
    )
    if result.status == "ready":
        print(result.duration_minutes)
    else:
        print("No journey under these inputs and constraints.")
    result.export("route.json")
```

Replace the paths, points, and example date with values covered by your data. Transit queries compare a feasible direct walk by default. Use `require_transit_ride=True` to require a vehicle boarding, or `mode="walk"` for walking. Keep the City open for repeated queries; the context manager releases its resident processes when finished.

## Build a City

```python
import vigo

with vigo.build("./boston", gtfs="./mbta.zip", osm="./massachusetts.osm.pbf") as city:
    print(city.name, city.revision_id, city.built_at)
```

Build once, then reopen the complete directory with `vigo.open("./boston")`. Raw GTFS and OSM files are no longer needed for querying. An existing City is replaced only with `replace=True`.

For live build stages and elapsed time, install `tqdm` and pass `progress=True`
to `vigo.build()`. See
[Runtime and loading](docs/runtime.md) for progress and complete Build timing.

## Documentation

| Task | Guide |
| --- | --- |
| Choose the right Query | [Documentation](docs/README.md) · [Concepts](docs/concepts.md) |
| Run a study or automate repeated work | [Workflows](docs/workflows.md) · [Notebooks](notebooks/README.md) |
| Look up arguments and defaults | [Python reference](docs/reference.md) |
| Interpret, compare, and export answers | [Results](docs/results.md) |
| Configure or diagnose execution | [Runtime](docs/runtime.md) · [Troubleshooting](docs/troubleshooting.md) |
| Develop the wrapper or build wheels | [Contributing](CONTRIBUTING.md) |

A `blocked` Result is a valid computation with no usable journey or surface. Matrix outcomes belong to individual rows; a completed Matrix can include blocked pairs. Invalid requests and execution failures raise exceptions. VIGO 0.4 is pre-release software; results describe the supplied timetable and street model.

Planned-service Scenarios support Reach; supplied traffic supports Drive Route/Matrix. Live transit is unavailable through this Python API. Read the engine's [routing limits](https://github.com/hytangs/vigo/blob/main/docs/reference/known-routing-limitations.md), particularly station access, unsupported GTFS semantics, and street coverage.

Licensed under the [Apache License 2.0](LICENSE).
