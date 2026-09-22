# Workflows

These examples use the public `vigo` package and a complete City at `./boston`. Replace the paths, points, and service date with values covered by your data. They describe scheduled model outputs, not observed journeys. See the [reference](reference.md) for all arguments and the [notebooks](../notebooks/README.md) for interactive versions.

## Find a journey

Use Route when you need departure and arrival times, transfers, and legs. An arrive-by request searches for the latest feasible departure. Waypoints are visited in their supplied order.

```python
import vigo

with vigo.open("./boston") as city:
    journey = city.route(
        [-71.062, 42.356],
        [-71.071, 42.361],
        waypoints=[[-71.058, 42.349]],
        arrive_by="09:00",
        service_date="2026-09-04",
        max_walk_km=1.2,
    )
    if journey.status == "ready":
        print(journey.duration_minutes)
        for leg in journey.legs:
            print(leg)
    else:
        print("No journey satisfies this request.")
    journey.export("outputs/journey.json")
    journey.export("outputs/journey.geojson")
```

Transit requires at least one boarding by default. Use `mode="walk"` or `mode="drive"` for street routing. With `require_transit_ride=False`, transit can also choose a direct walk within its walking limit. A finite `max_transfers` is currently unsupported with ordered transit waypoints. Changing a constraint changes the question; retain it in the study record.

## Calculate travel times to a common destination

Use Matrix for an assignment table, travel-time screen, or repeated origin–destination analysis. Give points stable caller IDs so rows remain joinable when their input order changes.

```python
import vigo

homes = {
    "home-west": [-71.080, 42.356],
    "home-south": [-71.062, 42.340],
}
schools = {"school": [-71.058, 42.349]}

with vigo.open("./boston") as city:
    morning = city.matrix(
        homes,
        schools,
        arrive_by="08:30",
        service_date="2026-09-04",
        max_walk_km=1.2,
        max_transfers=1,
        require_transit_ride=True,
    )
    rows = morning.rows
    ready = [row for row in rows if row["status"] == "ready"]
    blocked = [row for row in rows if row["status"] == "blocked"]
    print("ready", len(ready), "blocked", len(blocked))
    morning.export("outputs/morning.json")
    morning.export("outputs/morning.csv")

    afternoon = city.matrix(
        schools,
        homes,
        depart_at="15:00",
        service_date="2026-09-04",
        max_walk_km=1.2,
        max_transfers=1,
        require_transit_ride=True,
    )
    afternoon.export("outputs/afternoon.json")
```

A shared destination and deadline share one reverse timetable scan. A shared origin and departure share one forward scan. Keep the City open while changing endpoint groups. A request accepts up to 100,000 pairs; the product of origin and destination counts is the relevant size.

For arrive-by Matrix, `durationMinutes` runs from the latest departure to the requested deadline, including waiting after early arrival. Use `include_journeys=True` when actual arrival and timed legs matter; `include_geometry=True` additionally materializes their paths. These options increase output and assembly work. The [results guide](results.md#matrix) explains row status, missing durations, and nested journeys.

## Compare a service proposal

Use the same Reach Query for the baseline and proposed service. Here the proposal adds a modeled service between two coordinates: both directions, a 05:00–25:00 service span, ten-minute headways, estimated travel at 22 km/h, and 0.35 minutes of dwell. These are authored assumptions, not observed operations. Use the Engine's [Scenario reference](https://github.com/hytangs/vigo/blob/main/docs/reference/scenarios.md) for adding, augmenting, replacing, or excluding service.

```python
import vigo

service_changes = [{
    "operation": "add",
    "name": "Crosstown",
    "stops": [
        {"label": "West", "coordinate": [-71.08, 42.35]},
        {"label": "East", "coordinate": [-71.04, 42.36]},
    ],
    "headwayMinutes": 10,
    "startMinutes": 300,
    "endMinutes": 1500,
    "bidirectional": True,
    "timeModel": "estimate-distance",
    "averageSpeedKph": 22,
    "dwellMinutes": 0.35,
}]
query = vigo.Reach(
    [-71.062, 42.356],
    service_date="2026-09-04",
    depart_at="08:00",
    cutoffs_minutes=(15, 30, 45, 60),
    extent_radius_km=8,
    raster_size=96,
)

with vigo.open("./boston") as city:
    proposal = city.scenario("Ten-minute service", services=service_changes)
    before = city.run(query)
    after = proposal.run(query)
    before.export("outputs/before.json")
    after.export("outputs/after.json")
    after.export("outputs/after.geojson")
    difference = before.compare(after)
    difference.export("outputs/change.json")
    print(difference.value)
```

Reach's radius bounds the computed area; increasing it changes the analysis, not just the map zoom. Keep the grid, date, time, walking assumptions, and origin aligned. Compare checks grid compatibility but does not establish that all study assumptions match. Report newly reachable and no-longer-reachable cells beside duration changes among comparable cells. Reach alone does not measure jobs, people, or other opportunities.

## Retain every batch outcome

`city.run([query, ...])` executes sequentially and stops at the first exception. Use an explicit loop when a study must preserve each success, blocked outcome, and query failure. Opening the City happens outside this loop: a setup failure is a failure of the batch setup, not one blocked journey per request.

```python
import json
from collections import Counter
from pathlib import Path
import vigo

requests = {
    "morning": vigo.Route(
        [-71.062, 42.356], [-71.058, 42.349],
        depart_at="08:00", service_date="2026-09-04",
    ),
    "evening": vigo.Route(
        [-71.058, 42.349], [-71.062, 42.356],
        depart_at="17:00", service_date="2026-09-04",
    ),
}
output = Path("outputs/batch")
output.mkdir(parents=True, exist_ok=True)
outcomes = []

with vigo.open("./boston") as city:
    for request_id, query in requests.items():
        try:
            result = city.run(query)
        except vigo.VigoError as error:
            outcomes.append({
                "id": request_id,
                "outcome": "query_error",
                "errorType": type(error).__name__,
                "message": str(error),
            })
            continue
        result.export(output / f"{request_id}.json")
        outcomes.append({"id": request_id, "outcome": result.status})

(output / "outcomes.json").write_text(
    json.dumps(outcomes, indent=2) + "\n", encoding="utf-8"
)
print(Counter(row["outcome"] for row in outcomes))
```

Keep the authored requests with this output, including failed requests that have no Result. An export failure remains an exception rather than becoming a routing outcome. Do not replace blocked durations with zero or drop failed requests from the denominator.

For background work, use [`city.submit(query)`](reference.md#job). A background job can finish successfully with a blocked Result; `Job.status` describes execution, while `Result.status` describes the modeled answer.

[Documentation](README.md) · [Results](results.md) · [Python reference](reference.md)
