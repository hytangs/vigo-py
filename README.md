# VIGO Python

VIGO turns GTFS and OSM into a city model for routing, network-wide travel-time analysis, and service-change testing.

The Python API uses the same four-part model as VIGO Studio and the VIGO command line:

```text
City -> Scenario -> Route | Matrix | Reach -> Result
```

There is no separate Python product and no second routing implementation. The package is named `vigo`, opens a complete VIGO City, and delegates computation to the VIGO runtime.

> VIGO 0.3 is pre-release software. Do not use it for safety-critical, operational, or passenger-information systems without independent validation.

## Install

Install this package in Python 3.10 or newer:

```bash
python -m pip install .
```

Install VIGO Studio separately, or place the `vigo` command on `PATH`. `VIGO_RUNTIME` may point to a specific runtime and `VIGO_APP` may point to VIGO Studio.

Python and the runtime negotiate the public API rather than requiring identical product patch versions:

```python
runtime = vigo.resolve_runtime()
print(runtime.product_version, runtime.api_version, runtime.source)
```

## Open a City

A City is one complete directory built from GTFS and OSM. It moves as one unit.

```python
import vigo

city = vigo.open("./washington-dc")
```

Use a context manager when you want VIGO to release resident runtime resources immediately:

```python
with vigo.open("./washington-dc") as city:
    result = city.route(
        "place-pktrm",
        "place-dwnxg",
        depart_at="08:00",
        service_date="2026-09-04",
    )

print(result.status, result.duration_minutes)
```

Coordinates always use `[longitude, latitude]`:

```python
result = city.route(
    [-71.062, 42.356],
    [-71.058, 42.349],
    mode="walk",
    depart_at="08:00",
    service_date="2026-09-04",
)
```

## Build a City

`vigo.build()` writes one complete City directly to the requested location.

```python
with vigo.build(
    "./boston",
    gtfs=["./mbta.zip"],
    osm="./massachusetts.osm.pbf",
) as city:
    print(city.name, city.revision_id, city.built_at)
    print(city.sources)
```

Pass `replace=True` only when you intend to replace an existing City.

## Route

Route includes point-to-point, depart-at, arrive-by, departure-window, walking, driving, and batch use.

```python
route = city.route(
    "A",
    "B",
    arrive_by="09:00",
    service_date="2026-09-04",
    objective="earliest_arrival",
)

requests = [
    vigo.Route("A", "B", depart_at="08:00", service_date="2026-09-04"),
    vigo.Route("C", "D", depart_at="08:15", service_date="2026-09-04"),
]
results = city.run(requests)
```

`Context.run(query)` is the execution primitive. `city.route(...)`, `city.matrix(...)`, and `city.reach(...)` are convenience constructors with exactly the same behavior.

The 0.3.0 Route objective is explicit and singular: earliest arrival, then fewer boardings, then less walking, then a stable final order.

Repeated transit Route calls reuse one open process for the selected service date. Route answers themselves are recomputed.

## Matrix

One origin and many origins use the same method:

```python
matrix = city.matrix(
    {"home": [-71.062, 42.356]},
    {
        "school": [-71.058, 42.349],
        "hospital": [-71.071, 42.361],
    },
    depart_at="08:00",
    service_date="2026-09-04",
)

for row in matrix.rows:
    print(row["originId"], row["destinationId"], row["durationMinutes"])
```

Use `mode="walk"` or `mode="drive"` for street matrices.

## Reach

Reach answers where the modeled network can travel within stated time limits. It does not count people, jobs, schools, or other opportunities.

```python
reach = city.reach(
    [-71.062, 42.356],
    depart_at="08:00",
    service_date="2026-09-04",
    cutoffs_minutes=[15, 30, 45, 60],
    extent_radius_km=8,
)

reach.export("reach.geojson")
```

Use the word Accessibility only when a separate analysis combines travel impedance with opportunities.

## Scenario

A Scenario is an immutable set of changes tied to one City revision. Query walking limits remain Query parameters; they are not Scenario changes.

```python
service_changes = [{
    "operation": "add",
    "name": "Crosstown",
    "stops": [
        {"label": "West", "coordinate": [-71.08, 42.35]},
        {"label": "East", "coordinate": [-71.04, 42.36]},
    ],
    "headwayMinutes": 10,
}]

proposal = city.scenario(
    "Ten-minute service",
    services=service_changes,
)

before = city.reach(
    origin,
    depart_at="08:00",
    service_date="2026-09-04",
)
after = proposal.reach(
    origin,
    depart_at="08:00",
    service_date="2026-09-04",
)
difference = vigo.compare(before, after)
```

Inspect support before running a context-dependent Query:

```python
support = proposal.supports(query)
if not support.supported:
    print(support.reason, support.available)
```

In VIGO 0.3, planned service changes are available for Reach. Unsupported combinations raise `UnsupportedQuery`; malformed or contradictory Queries raise `InvalidQuery`. Neither becomes a Result.

## Result

Every Query returns `Result` with the same basic shape:

```python
result.kind
result.status
result.query
result.warnings
result.timing
result.record
result.export("result.json")
```

Route Results add `duration_minutes` and `legs`. Matrix Results add `rows`. Reach Results add `to_geojson()`.

`Result.status` is only `ready` or `blocked`. A blocked Result is a valid computation with no usable journey or surface. Cancellation and execution failures belong to `Job.status` or exceptions, not Result status.

Long work can run in the background:

```python
job = city.submit(vigo.Matrix(origins, destinations, depart_at="08:00", service_date="2026-09-04"))
result = job.wait()
```

`Job.status` is `queued`, `running`, `ready`, `cancelled`, or `error`.

## Documentation

- [Concepts](docs/concepts.md)
- [Python reference](docs/README.md)
- [VIGO product and Studio documentation](https://github.com/hytangs/vigo/tree/main/docs)

## Development

```bash
python -m pytest -q
python -m ruff check vigo tests
```

VIGO Python is licensed under the [Apache License 2.0](LICENSE).
