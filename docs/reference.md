# Python reference

Install the package and runtime using the [README](../README.md). The examples below use a City built from the corresponding GTFS and OSM sources.

## Open a City

A City is one complete directory built from GTFS and OSM. It moves as one unit.

```python
import vigo

city = vigo.open("./boston")
```

Use a context manager when you want VIGO to release resident runtime resources immediately:

```python
with vigo.open("./boston") as city:
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
Pass `progress=True` to show Build stages and elapsed time with the optional
`tqdm` dependency. It defaults to `False`; see [Runtime and loading](runtime.md)
for installation and separate raw-build, first-query, and reopening timings.

For travelers authorized to use internal roads at their own origin and destination,
pass `private_access="endpoints"` to `vigo.build()`. The default is `"public"`.
The endpoint model includes mapped private access walks within the walking budget;
the public middle of a journey and transit transfers cannot use private shortcuts.
It assumes endpoint authorization and does not infer ownership or gate hours.
An older runtime that lacks this feature is rejected before Build.

## Route

Transit Route and Matrix require a vehicle boarding by default. Use `mode="walk"` for a walking journey, or `require_transit_ride=False` to compare transit with walking. `horizon_minutes` sets the transit search horizon (1–2880 minutes; default 480).

Route includes point-to-point, depart-at, arrive-by, departure-window, walking, driving, and batch use.

| Argument | Default | Meaning |
| --- | --- | --- |
| `origin`, `destination` | Required | Exact stop IDs, coordinate pairs, or point mappings |
| `waypoints` | Empty | Intermediate points in the given order |
| `mode` | `"transit"` | `"transit"`, `"walk"`, or `"drive"` |
| `depart_at` / `arrive_by` | One required | `HH:MM`, `datetime.time`, or `datetime.datetime` |
| `service_date` | Required unless time is a datetime | ISO date string or `datetime.date`; interpreted on the feed's local service day |
| `max_walk_km` | `1.2` | Each transit access and egress walking budget |
| `departure_window_minutes` | `0` | Depart-at window of plus or minus 0–30 whole minutes |
| `objective` | `"earliest_arrival"` | The supported objective |
| `max_transfers` | `None` | Optional cap, 0–31 changes |
| `require_transit_ride` | `True` | Require at least one vehicle boarding |
| `horizon_minutes` | `480` | Timetable search horizon, 1–2880 minutes |
| `disable_cache` | `False` | Disable transit street-access and walking-path caches |

Choose departure or arrival time, never both. Datetime values supply their date and clock; the wrapper does not convert them between timezones.

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

`City.run(query)` and `Scenario.run(query)` are the execution primitives. `city.route(...)`, `city.matrix(...)`, and `city.reach(...)` are convenience constructors with exactly the same behavior.

Depart-at minimizes arrival time, then boardings, then walking. Arrive-by maximizes departure time; among journeys leaving at that boundary and arriving by the deadline, it minimizes boardings, then walking, then actual arrival.

`Route` and `Matrix` accept `max_transfers=0` for at most one boarding,
`max_transfers=1` for at most two, and so on through 31. Omit the option
(or use `None`) for no additional cap. It applies to both `depart_at` and
`arrive_by` and all Matrix shapes. A finite cap with ordered transit
waypoints is currently unsupported. The runtime must advertise transfer-cap
support; Python rejects this option on older runtimes instead of ignoring it.

Repeated transit Route calls reuse an open process for the selected service date. Each open City keeps a bounded pool of one to four processes, according to available CPU and memory. Switching dates reclaims the least recently used idle process; active queries finish before their process is closed. Route answers themselves are recomputed.

For transit Route and Matrix measurements, `disable_cache=True` disables
street-access frontier and reconstructed walking-path caches. It keeps the
prepared City resident. Python requires the runtime to advertise this control;
older runtimes are rejected instead of silently ignoring it. `Result.to_json(indent=None)`
returns compact JSON, including the complete itinerary and geometry.

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

`origins` and `destinations` accept a point, a mapping of caller IDs to points, or a sequence of points. Matrix shares Route's mode, date, time constraint, walking budget, objective, transfer cap, transit requirement, horizon, and cache control. It defaults to an `08:00` departure when neither time constraint is supplied. Matrix has no waypoints or departure window.

| Additional argument | Default | Meaning |
| --- | --- | --- |
| `walk_speed_kph` | `4.8` | Walking Matrix speed |
| `max_distance_km` | `None` | Optional street distance limit |
| `include_journeys` | `False` | Add timed transit journeys to ready rows |
| `include_geometry` | `False` | Add geometry; requires `include_journeys=True` |

Transit supports both `depart_at` and `arrive_by` with one-to-many, many-to-one,
or many-to-many endpoint sets, up to 100,000 pairs per request. For a morning
school trip, use `city.matrix(assigned_homes, {"school": school},
arrive_by="08:30", service_date="2026-07-15")`; for dismissal, reverse the
endpoint sets and use `depart_at="15:00"`. A common origin and departure share one forward scan; a common destination and arrival deadline share one reverse scan. Other shapes use one scan per unique origin or destination, respectively.
Arrive-by rows report the latest `departMinutes` and the deadline as
`arriveMinutes`; `durationMinutes` includes waiting after early arrival.
Use Route to obtain actual itinerary arrival and legs. This requires a VIGO
runtime whose Matrix capabilities include `arrive_by`.
Successive Matrix and transit Route queries on the same City and service date
reuse one resident process. Keep the City open while iterating over schools so
the network is loaded once. Closing the City releases those processes. Matrix
requires a runtime that advertises resident Matrix support; older runtimes
raise `UnsupportedQuery` before execution.

For transit matrices, `include_journeys=True` adds actual arrival, walking,
waiting, ride time, transfers, and timed trip/stop legs to each ready row's
`journey`. Add `include_geometry=True` to materialize those selected paths with
the same renderer as Route, without repeating timetable searches. The runtime
must advertise journey support. Equal-objective paths can differ from Route's
stable traversal order. The outer arrive-by row still reports the deadline;
the nested journey reports actual arrival.

## Reach

Reach answers where the modeled network can travel within stated time limits. It does not count people, jobs, schools, or other opportunities.

Reach accepts `origin`, `service_date`, and `depart_at` (default `"08:00"`). It uses scheduled transit with walking; walking-only and driving Reach are unavailable.

| Argument | Default | Meaning |
| --- | --- | --- |
| `cutoffs_minutes` | `(15, 30, 45, 60)` | Increasing cutoffs, each 5–240 minutes |
| `max_walk_km` | `1.2` | Transit access and terminal walking budget |
| `walk_speed_kph` | `4.8` | Walking speed, 1–8 kph |
| `extent_radius_km` | `8` | Computation radius, 1–40 km |
| `raster_size` | `96` | Grid side length: 48, 64, 96, 128, 192, 256, 384, 512, or 1024 |

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

origin = [-71.062, 42.356]
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
query = vigo.Reach(origin, service_date="2026-09-04")
support = proposal.supports(query)
if not support.supported:
    print(support.reason, support.available)
```

In VIGO 0.3, planned service changes are available for Reach. Unsupported combinations raise `UnsupportedQuery`; malformed or contradictory Queries raise `InvalidQuery`. Neither becomes a Result.

## Result

Every Query returns `Result` with the same basic shape. Result properties return independent copies; `to_json(indent=None)` avoids an intermediate full-result copy:

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


`Job.cancel()` cancels queued work only. A running Query finishes normally.
`Job.wait(timeout=...)` bounds the wait for the background result; it does not cancel the Query.
See [Runtime and loading](runtime.md) for process reuse and timing boundaries.
