# VIGO concepts

VIGO has four first-class concepts and three Query families. Build or open a City, optionally apply a Scenario, then retain the Result of a Query.

```text
City -> Route | Matrix | Reach -> Result
  ^
optional Scenario
```

## City

A City is a compiled mobility model built from GTFS and OSM. Its directory is one atomic unit containing the timetable, street model, and the data needed to open them quickly.

A named City may be rebuilt over time. Each build is an immutable revision. A Result records the revision it used.

`city.revision_id` identifies the build. `city.built_at` records when it was created. `city.sources` records its GTFS and OSM inputs. Moving the complete City directory does not change that identity.

## Scenario

A Scenario is an immutable set of changes applied to exactly one City revision.

The shared VIGO model can describe:

- proposed transit service changes;
- one live transit state;
- one supplied traffic state.

Query choices such as a walking limit, departure time, or time cutoff are not Scenario changes. A complete alternative GTFS source is another City revision, not a Scenario. Map geometry used to draw an edit is presentation data, not a transport change by itself.

VIGO checks a selected combination before running it. It never drops an unsupported change and quietly returns the unchanged City.

Python supports planned service changes in Reach and supplied traffic in Drive Route/Matrix. Python live-transit Scenarios, realtime transit Matrix, and realtime Reach are unsupported. Supplied traffic uses the shared native driving kernel and requires an engine that advertises that capability.

`city.supports(query)` and `scenario.supports(query)` return a `Support` value with a reason and available alternative when the selected context cannot execute a combination. This is not a complete data-validation pass; execution still checks points, dates, and runtime inputs. See the [Python support table](reference.md#scenario).

## Query

VIGO has exactly three Query families.

### Route

Find and explain travel between ordered points. Transport mode, depart-at, arrive-by, departure windows, waypoints, and batch requests are Route options.

Depart-at minimizes arrival time, then boardings, then walking. Arrive-by maximizes departure time, then minimizes boardings and walking among journeys arriving by the deadline, then actual arrival.

### Matrix

Compute scalar travel time between one or more origins and destinations.

Both depart-at and arrive-by accept up to 100,000 pairs. Fixed departures share
one forward scan per unique origin; fixed arrival deadlines share one reverse
scan per unique destination. Arrive-by duration runs from the latest departure
to the deadline, including destination waiting.

### Reach

Compute where the represented network can travel within stated time limits. A Reach Result may be viewed as contours or reached streets.

Reach is not Accessibility. Accessibility requires opportunities such as jobs, population, schools, or healthcare in addition to travel impedance.

## Result

A Result is the immutable answer to one Query. Every Result exposes:

- status;
- Query inputs;
- warnings;
- timing;
- City revision and Scenario name;
- export methods.

Compare is an action on two compatible Results. It is not a fourth Query family.

Result status is only `ready` or `blocked`. A completed Matrix is ready even when individual rows are blocked. Python-side query validation raises `InvalidQuery`; unsupported combinations raise `UnsupportedQuery`; runtime validation and execution failures raise `VigoError`. Background work separately reports `queued`, `running`, `ready`, `cancelled`, or `error` through `Job.status`.

Compare uses retained values and checks family-specific structure; the caller must align dates, points, modes, and other study assumptions. A valid numerical difference does not establish that two Results answer the same question. See [Results](results.md) for comparison and evidence boundaries.

## Time

VIGO keeps these durations separate:

- Build: GTFS and OSM become a City.
- Open: a City revision becomes ready for queries.
- Compute: the selected Query runs.
- End to end: caller submission through complete Result.

Repeated Route calls may reuse one open process. The answer is still computed for every call.

The wrapper's `timing["endToEndMs"]` ends before Result construction and export. Use an outer timer for the complete Python operation; see [runtime measurement](runtime.md#measure-the-complete-operation).

## Python

```python
import vigo

with vigo.open("./city") as city:
    route = city.route("A", "B", depart_at="08:00", service_date="2026-09-04")
    matrix = city.matrix({"a": "A"}, {"b": "B"}, depart_at="08:00", service_date="2026-09-04")
    reach = city.reach("A", depart_at="08:00", service_date="2026-09-04")
```

The VIGO command line uses the same nouns: `vigo build`, `vigo inspect`, `vigo route`, `vigo matrix`, `vigo reach`, and `vigo compare`.

`vigo capabilities` reports the public API version and supported combinations. Python requires Engine 0.5.x with API 1.0, capability schema v3, and trace output. Runtime selection rejects other API versions and capability schemas.

`Route` and `Matrix` accept `max_transfers=0` for at most one boarding,
`max_transfers=1` for at most two, and so on through 31. Omit the option
(or use `None`) for no additional cap. It applies to both `depart_at` and
`arrive_by` and all Matrix shapes. A finite cap with ordered transit
waypoints is currently unsupported.

[Documentation](README.md) · [Workflows](workflows.md) · [Reference](reference.md)
