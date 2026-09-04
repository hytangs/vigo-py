# VIGO Python documentation

Start with the [project README](../README.md), then use this map.

## Concepts

- [City, Scenario, Query, and Result](concepts.md)
- Route, Matrix, and Reach are the only Query families.
- Compare acts on compatible Results.

## Python surface

```text
vigo.build(...)
vigo.open(...)

City
  capabilities
  supports(query)
  run(query)
  route(...)
  matrix(...)
  reach(...)
  submit(...)
  scenario(...)

Scenario
  supports(query)
  run(query)
  route(...)
  matrix(...)
  reach(...)
  submit(...)

Result
  status
  query
  warnings
  timing
  record
  to_dict()
  to_json()
  to_geojson()
  export(...)
  compare(...)
```

## Data

A City is built from one or more static GTFS sources and one OSM PBF. Keep the complete City directory together when moving it.

## Current limits

- Planned transit service changes are available for Reach in VIGO 0.3.
- Live transit routing remains a VIGO Studio feature in VIGO 0.3.
- Reach measures modeled network reach, not opportunity-weighted Accessibility.
- Exact stop IDs are not place searches. Coordinates are `[longitude, latitude]`.

## Runtime

VIGO Python searches `VIGO_RUNTIME`, `VIGO_APP`, a sibling source build, installed VIGO apps, and then `vigo` on `PATH`. `resolve_runtime()` accepts a compatible API 1.x runtime; product patch versions do not need to match. Normal Query calls manage opening and reuse automatically.
