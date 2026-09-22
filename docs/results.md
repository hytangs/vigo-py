# Results

A Result is the retained answer to one Query. Read its outcome before interpreting its numbers, and keep the query and City revision with any exported table or map.

## Outcomes

| Outcome | Meaning | How to retain it |
| --- | --- | --- |
| Route or Reach `ready` | A journey or surface was produced under the request | Keep values, assumptions, warnings, and timing |
| Route or Reach `blocked` | The computation completed without a usable journey or surface | Keep the outcome; do not turn missing values into zero |
| Matrix row `ready` | This pair has a modeled travel time | Analyze the row's duration |
| Matrix row `blocked` | This pair has no usable answer under the request | Keep the pair in outcome counts |
| Exception | Setup, validation, support, timeout, or execution failed | Keep the exception separately from blocked outcomes |

A completed Matrix's top-level status is `ready` even when some or all pairs are blocked. Inspect `matrix.rows`. A ready Result may still carry warnings; it does not establish that the source data represent every real route, restriction, or operational condition.

Background `Job.status` is separate. `ready` means the job returned without an exception; the returned Result can be blocked. See [errors and jobs](reference.md#job).

## Shared fields

| Python property | Meaning |
| --- | --- |
| `kind` | `route`, `matrix`, `reach`, or `comparison` |
| `status` | `ready` or `blocked` |
| `query` | Normalized runtime request, using Engine field names such as `serviceDate` |
| `warnings` | Warnings returned by the runtime |
| `timing` | Runtime and wrapper timing fields, where reported |
| `city_revision_id` | City build used by the Query |
| `scenario_name` | Selected Scenario name, or `None` |
| `record` | Compact record with City revision, Scenario name, query, outcome, warnings, and timing |
| `value` | Journey, Matrix rows, Reach surface, or comparison change |

Properties such as `query`, `rows`, `legs`, and `timing` return copies. Editing them does not change the retained Result. `to_dict()` returns the complete Engine payload; `record` is a compact Python record, not an alias for that payload. Comparison Results have a different envelope and do not include the two full source Results.

## Route

Use `duration_minutes` for the selected journey and `legs` for its detail. A blocked Route normally has no duration and no legs. Export JSON for the complete journey; GeoJSON contains only legs with line geometry and a small set of properties.

Depart-at minimizes arrival time, then boardings and walking. Arrive-by maximizes departure time and then resolves secondary objectives within the arrival deadline. A departure-window query is a different question from an exact departure; retain the window with the answer. See the [Route reference](reference.md#route) for constraints.

## Matrix

Rows contain caller IDs (`originId`, `destinationId`), endpoint indexes, `status`, and `durationMinutes`, with other fields depending on the mode and requested detail. Output uses origin-major order. Prefer IDs to row position when joining analysis tables. Use unique IDs and keep their associated coordinates or stop IDs stable across runs.

For arrive-by transit Matrix, `departMinutes` is the latest feasible departure and `arriveMinutes` is the requested deadline. `durationMinutes` includes destination waiting after an early arrival. With `include_journeys=True`, each ready row's `journey` supplies actual arrival, transfers, walking, waiting, ride time, and timed legs. With `include_geometry=True`, that journey also includes selected path geometry. Blocked rows have no journey.

Equal-objective Matrix journeys can differ from Route in which tied path they choose, and therefore in ride/wait splits. The parent Matrix timing covers the shared computation. A nested journey's timing does not represent an independent timetable search or an allocated share of batch time.

## Reach

`value` contains the travel-time surface; `to_geojson()` returns contours. The grid size and bounds belong to the computation. `extent_radius_km` limits the area computed around the origin, and `raster_size` controls its resolution. A contour is a representation of this model, not a guarantee that every location inside a polygon has identical access.

Reach measures travel impedance. An accessibility measure additionally needs opportunity data and an aggregation rule, such as counting jobs below a travel-time threshold. Keep those external inputs and assumptions separate from the Reach Result.

## Compare completed Results

`vigo.compare(before, after)` and `before.compare(after)` compute differences without running either Query again. The sign is **after minus before**: a negative duration change is faster.

| Family | Comparison behavior |
| --- | --- |
| Route | Duration and transfer change when both Results are ready; otherwise the corresponding change is `None` |
| Matrix | Match rows by origin/destination IDs, falling back to indexes; count faster, slower, unchanged, newly reachable, and no-longer-reachable pairs |
| Reach | Require equal grid dimensions, bounds, and value counts; count the same changes across cells |

For Matrix and Reach, `meanChangeMinutes` uses only matched pairs or cells with finite values on both sides. It is `None` when none are comparable. Newly reachable and no-longer-reachable outcomes are reported separately; they do not enter this mean.

The caller must align the study. Compare does not require equal City revisions, dates, times, modes, walking limits, or other Query settings. Route can compare unrelated endpoints unless the caller prevents it. Matrix silently skips unmatched pairs; repeated IDs can collapse the before-side lookup. Keep unique, stable IDs, check the complete pair sets, and report coverage as well as speed changes. The Reach grid check alone does not establish matching inputs.

## Export

| Extension | Supported Results | Contents |
| --- | --- | --- |
| `.json` | All | Complete Engine payload |
| `.geojson` | Route, Reach | Route leg lines or Reach contours |
| `.csv` | Matrix | Row fields |

`result.export(path)` creates parent directories, writes the selected representation, and returns its absolute `Path`. It overwrites an existing destination. Python exports write directly; use distinct run directories when preserving earlier evidence. `to_json(indent=None)` returns compact JSON.

Keep JSON as the complete record. GeoJSON omits most query and runtime detail. CSV does not preserve the Result envelope; nested journey objects become text in cells, so JSON is preferable when journeys are requested. Matrix does not expose `to_geojson()`, even when its nested journeys include geometry.

## Retain a reproducible record

Alongside the full Result JSON, retain the authored request or script, complete City revision, Scenario inputs, selected runtime version, and wrapper version. The normalized query is useful but is not a promise to echo every input: for example, a supplied traffic snapshot is not fully reproduced in every query envelope. A Scenario name alone does not reproduce the change.

```python
import json
from pathlib import Path
import vigo

with vigo.open("./boston") as city:
    result = city.route(
        [-71.062, 42.356], [-71.058, 42.349],
        depart_at="08:00", service_date="2026-09-04",
    )
    result.export("outputs/route.json")
    record = {
        **result.record,
        "wrapperVersion": vigo.__version__,
        "engineVersion": city.runtime.product_version,
        "apiVersion": city.runtime.api_version,
        "sources": city.sources,
    }
    Path("outputs/record.json").write_text(
        json.dumps(record, indent=2) + "\n", encoding="utf-8"
    )
```

Timing describes execution, not accuracy. Synthetic integration tests check contracts and controlled routing behavior; they do not establish field prediction accuracy. Interpret results within the Engine's [data and routing limits](https://github.com/hytangs/vigo/blob/main/docs/reference/known-routing-limitations.md).

[Documentation](README.md) · [Workflows](workflows.md) · [Runtime measurement](runtime.md#measure-the-complete-operation)
