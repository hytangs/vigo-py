# VIGO Python

Build and query VIGO City models from Python. The package imports as `vigo` and delegates routing to the VIGO runtime.

VIGO has three components: Engine computes the queries, Studio provides the desktop application, and Python wraps Engine for scripts. 0.3.1 is a stabilization release with API 1.0, City format 1, and Result schema 1 unchanged.

```text
City -> Scenario -> Route | Matrix | Reach -> Result
```

## Install

Use Python 3.10 or newer:

```bash
git clone https://github.com/hytangs/vigo-py.git
python -m pip install ./vigo-py
```

Install [VIGO Studio or the VIGO command](https://github.com/hytangs/vigo) separately. Set `VIGO_RUNTIME` to select a command, or `VIGO_APP` to select a Studio installation.

The same wheel works with a matching runtime on macOS Apple Silicon/Intel, Linux ARM64/x64, and Windows x64. For packaged layouts and City reuse, see [Runtime and loading](docs/runtime.md).

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
    print(result.status, result.duration_minutes)
    result.export("route.json")
```

Choose a service date covered by the supplied feed. Keep the City open for repeated queries: transit Route and Matrix reuse resident processes by service date. The context manager releases them when finished.

## Build a City

```python
with vigo.build("./boston", gtfs="./mbta.zip", osm="./massachusetts.osm.pbf") as city:
    print(city.name, city.revision_id, city.built_at)
```

An existing City is replaced only with `replace=True`.

## Documentation

- [Python reference](docs/reference.md): Route, Matrix, Reach, Scenario, Result, and Job.
- [Runtime and loading](docs/runtime.md): discovery, process reuse, timeouts, and measurement.
- [Concepts](docs/concepts.md): model, outcomes, and evidence limits.
- [Contributing](CONTRIBUTING.md): tests and a public synthetic integration fixture.

A `blocked` Result is a valid computation with no usable journey or surface. Invalid requests and execution failures raise exceptions. VIGO 0.3 is pre-release software; results describe its supplied timetable and street model.

Planned-service Scenarios support Reach; supplied traffic supports Drive Route/Matrix. Live transit is limited to Studio Route and is unavailable through this Python API. Read the engine's [routing limits](https://github.com/hytangs/vigo/blob/main/docs/known-routing-limitations.md), particularly station access, unsupported GTFS semantics, and street coverage.

Licensed under the [Apache License 2.0](LICENSE).
