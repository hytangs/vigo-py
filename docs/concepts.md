# VIGO concepts

VIGO has four first-class concepts and three Queries.

```text
City -> Scenario -> Route | Matrix | Reach -> Result
```

## City

A City is a compiled mobility model built from GTFS and OSM. Its directory is one atomic unit containing the timetable, street model, and the data needed to open them quickly.

A named City may be rebuilt over time. Each build is an immutable revision. A Result records the revision it used.

`city.revision_id` identifies the build. `city.built_at` records when it was created. `city.sources` records its GTFS and OSM inputs. Moving the complete City directory does not change that identity.

## Scenario

A Scenario is an immutable set of changes applied to exactly one City revision.

It may contain:

- proposed transit service changes;
- one live transit state;
- one supplied traffic state.

Query choices such as a walking limit, departure time, or time cutoff are not Scenario changes. A complete alternative GTFS source is another City revision, not a Scenario. Map geometry used to draw an edit is presentation data, not a transport change by itself.

VIGO checks a selected combination before running it. It never drops an unsupported change and quietly returns the unchanged City.

`city.supports(query)` and `scenario.supports(query)` return a `Support` value with a reason and available alternative when the selected context cannot execute a valid Query.

## Query

VIGO has exactly three Query families.

### Route

Find and explain travel between ordered points. Transport mode, depart-at, arrive-by, departure windows, waypoints, and batch requests are Route options.

VIGO 0.3.0 exposes one Route objective: earliest arrival. Ties prefer fewer boardings, then less walking, then a stable final order.

### Matrix

Compute scalar travel time between one or more origins and destinations.

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

Result status is only `ready` or `blocked`. Invalid inputs raise `InvalidQuery`. Valid combinations that the selected City or Scenario cannot execute raise `UnsupportedQuery`. Runtime failures raise `VigoError`. Background work separately reports `queued`, `running`, `ready`, `cancelled`, or `error` through `Job.status`.

## Time

VIGO keeps these durations separate:

- Build: GTFS and OSM become a City.
- Open: a City revision becomes ready for queries.
- Compute: the selected Query runs.
- End to end: caller submission through complete Result.

Repeated Route calls may reuse one open process. The answer is still computed for every call.

## Python

```python
import vigo

with vigo.open("./city") as city:
    route = city.route("A", "B", depart_at="08:00", service_date="2026-09-04")
    matrix = city.matrix({"a": "A"}, {"b": "B"}, depart_at="08:00", service_date="2026-09-04")
    reach = city.reach("A", depart_at="08:00", service_date="2026-09-04")
```

The VIGO command line uses the same nouns: `vigo build`, `vigo inspect`, `vigo route`, `vigo matrix`, `vigo reach`, and `vigo compare`.

`vigo capabilities` reports the public API version and supported combinations. Python accepts a compatible API major version; it does not require the same product patch version.
