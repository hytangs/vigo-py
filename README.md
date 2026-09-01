# VIGO for Python

`vigo-router` is the standalone Python interface to
[VIGO](https://github.com/hytangs/vigo)—Visual Intelligence for GTFS
Operations. It uses VIGO to compile scheduled public-transit networks from
GTFS and OpenStreetMap inputs and exposes routing, one-to-many, and isochrone
results as Python objects.

This repository contains only the Python binding. It deliberately does not
copy VIGO's inspection and analysis workbench, GTFS compiler, timetable graph,
street router, or routing algorithms. Every build and query goes through the
version-matched VIGO runtime, so the Python API and VIGO CLI share one routing
implementation.

> [!IMPORTANT]
> VIGO and vigo-router are experimental pre-release software. Interfaces, file
> formats, supported platforms, and routing behavior may change. Do not use
> them for safety-critical, operational, or passenger-information systems
> without independent validation.

## Requirements

- Python 3.10 or newer.
- A VIGO 0.3.0 CLI/runtime.
- Either matching GTFS ZIP and OSM PBF inputs, or existing VIGO routing and
  street SQLite stores.

The packaged runtime currently targets macOS on Apple silicon. The Python
package can use another platform when a compatible VIGO CLI is available.

## Install

Clone this repository and run the standalone installer:

```bash
git clone https://github.com/hytangs/vigo-py.git
cd vigo-py
./install.sh
. .venv/bin/activate
```

The installer creates a virtual environment, builds and installs the Python
package from this checkout, downloads the official VIGO 0.3.0 Apple-silicon
runtime into that environment, verifies its pinned SHA-256 digest, and checks
both installed versions. It does not require a VIGO source checkout or modify a
system Python installation.

To use a different virtual-environment location, pass it as the first argument:

```bash
./install.sh /absolute/path/to/venv
```

You can also install the package directly and obtain the runtime separately:

```bash
python3 -m pip install "vigo-router @ git+https://github.com/hytangs/vigo-py.git@v0.3.0"
vigo-router-download
```

No VIGO environment variable is required to import `vigo_router`. Runtime
discovery happens when a network is opened or a pass-through command is run.

## Install a packaged runtime

After installing a wheel, point the binding at an existing app:

```bash
export VIGO_APP="/Applications/VIGO.app"
```

Running `vigo-router-download` with no arguments installs the official,
checksum-pinned VIGO 0.3.0 runtime. To use a separately obtained archive:

```bash
vigo-router-download \
  --archive /path/to/VIGO-0.3.0-mac-arm64.zip \
  --sha256 <64-character-sha256>
```

An HTTPS release endpoint is also supported:

```bash
vigo-router-download \
  --url "$VIGO_RUNTIME_URL" \
  --sha256 "$VIGO_RUNTIME_SHA256"
```

The installer rejects non-HTTPS URLs and redirects. Custom URLs require an
explicit SHA-256 digest; only the official versioned runtime has a digest built
into this package.

For a local archive, `--sha256` may be omitted when an adjacent
`.zip.sha256` sidecar exists. The installer reuses a verified runtime by
default. `--force` may replace a verified VIGO runtime or an empty destination,
but it will not remove a populated non-VIGO directory.

The default location is
`~/.local/share/vigo-router/runtime/<version>`. Set
`VIGO_ROUTER_RUNTIME_DIR` to change its parent, or pass `--destination` for one
explicit installation. `vigo_router.install_runtime()` exposes the same
installer from Python.

Runtime discovery uses this order:

1. the explicit `cli=` argument;
2. `VIGO_CLI`, then `VIGO_APP`;
3. a runtime beside a source checkout or package;
4. the checksum-installed per-user runtime;
5. `/Applications/VIGO.app`, `~/Applications/VIGO.app`, then `vigo` on
   `PATH`.

Every candidate, including `cli=`, `VIGO_CLI`, `VIGO_APP`, and `PATH`, must
report exactly VIGO 0.3.0 and expose the required build, resident-routing,
one-to-many, and isochrone commands. An incompatible explicitly selected
runtime fails immediately. Use `vigo_router.resolve_cli()` to inspect the
resolved command or `vigo_router.probe_cli()` for its verified contract.

## Open existing stores

Use a context manager so the resident point-routing process is always closed:

```python
import os
import vigo_router

with vigo_router.open_network(
    os.environ["VIGO_ROUTING_STORE"],
    street_store=os.environ.get("VIGO_STREET_STORE"),
) as network:
    plan = network.route(
        [-73.9857, 40.7484],
        [-73.9772, 40.7527],
        time="08:00",
        service_date=os.environ["VIGO_SERVICE_DATE"],
        service_day=os.environ.get("VIGO_SERVICE_DAY", "weekday"),
        max_walk_km=1.2,
    )

print(plan.status, plan.duration_minutes, plan.route_sequence)
for leg in plan.legs:
    print(leg.type, leg.duration_minutes, leg.coordinates)
```

`service_date` is mandatory for exact timetable routing unless `time` is a
`datetime`, in which case its date is used. VIGO does not silently substitute
a representative service date.

Coordinate points require a matching street store. Exact GTFS stop-to-stop
queries may omit it.

## Build from GTFS and OSM

`TransportNetwork` hashes the VIGO version, source names, and source bytes,
then reuses the corresponding compiled directory when its manifest and stores
remain valid:

```python
import os
import vigo_router

with vigo_router.TransportNetwork(
    os.environ["VIGO_OSM"],
    [os.environ["VIGO_GTFS"]],
    cache_dir=os.environ.get("VIGO_DATA_HOME"),
    progress=True,
) as network:
    print(network.store)
    print(network.street_store)
    print(network.build_summary)
```

Pass `rebuild=True` to rebuild the content-addressed directory. With no
`cache_dir`, `VIGO_ROUTER_CACHE` is used when set, followed by the platform
cache directory. `TransportNetwork.from_directory(path)` is a convenience for
a directory containing exactly one OSM PBF and at least one GTFS ZIP.

## Point inputs

Every routing method accepts any of these point forms:

```python
stop = "place-pktrm"
named_stop = {"stop_id": "place-pktrm", "label": "Park Street"}
coordinate = [-71.062, 42.356]  # [longitude, latitude]
named_coordinate = {"lon": -71.062, "lat": 42.356, "label": "Origin"}
alternate_coordinate = {"coordinate": [-71.062, 42.356], "label": "Origin"}
```

Strings are exact stop IDs, not place-name searches. Longitude always comes
before latitude.

## Route one itinerary or a batch

`route` returns one `RoutePlan`. `route_batch` accepts mappings with `origin`
and `destination` and returns a list-compatible `BatchRoutingResult`:

```python
batch = network.route_batch(
    [
        {"id": "stops", "origin": "place-pktrm", "destination": "place-dwnxg"},
        {"id": "map", "origin": [-71.062, 42.356], "destination": [-71.071, 42.350]},
    ],
    time="08:00",
    service_date="2026-08-19",
    service_day="weekday",
    routing_preference="fastest",
)

first = batch[0]
print(first.status, first.to_geojson())
print(batch.summary["rows"], batch.timing)
```

Batch IDs must be unique. `time_preference` is `"depart"` or `"arrive"`;
`routing_preference` is `"balanced"` or `"fastest"`.
`departure_window_minutes` is available only for depart searches.

Both methods share one prepared `route-ndjson` child per network and service
date/day. Reusing a network avoids paying startup and preparation for every
request. A date/day change replaces the child, and a broken child is restarted
once before the request fails. Call `close()` or use `with` when finished.

The module-level `vigo_router.route(network, ...)` and
`vigo_router.route_batch(network, ...)` forms are equivalent to the network
methods. The same applies to `route_many` and `isochrone`.

## Route one origin to many destinations

`route_many` performs one native forward scan rather than a Python loop. A
mapping preserves your destination IDs:

```python
field = network.route_many(
    "place-pktrm",
    {
        "downtown-crossing": "place-dwnxg",
        "city-hall": [-71.058, 42.360],
    },
    time="08:00",
    service_date="2026-08-19",
    service_day="weekday",
    horizon_minutes=120,
    matrix_strategy="shared",
)

for row in field:
    print(row.destination_id, row.status, row.duration_minutes)
```

The result is a list-compatible `OneToManyResult`. It contains travel-time
rows, not detailed itineraries. Use `field.engine`, `field.diagnostics`,
`field.timing`, or `field.to_dict()` for the full receipt.

## Generate an isochrone

Isochrones require a street store and use a sequence named
`cutoffs_minutes`:

```python
surface = network.isochrone(
    [-71.062, 42.356],
    cutoffs_minutes=(15, 30, 45),
    time="08:00",
    service_date="2026-08-19",
    service_day="weekday",
    radius_km=8,
    raster_size=96,
)

geojson = surface.to_geojson()
print(surface.cutoffs_minutes, len(geojson["features"]))
```

`IsochroneResult.surface` retains the native raster, while `to_geojson()`
returns the contours. Valid raster sizes are 48, 64, 96, and 128.

## Results and failures

| Result | Main interface |
| --- | --- |
| `RoutePlan` | `status`, `routing_status`, `algorithm`, `walking_network`, `optimality`, `failure_code`, `failure_category`, `method_used`, `legs`, `route_sequence`, `diagnostics`, `timing`, `to_dict()`, `to_geojson()` |
| `Leg` | `type`, route/trip IDs, `duration_minutes`, `coordinates`, `to_dict()` |
| `BatchRoutingResult` | list indexing/iteration, `summary`, `timing`, `to_dict()` |
| `TravelTime` / `OneToManyResult` | `destination_id`, status/times, list iteration, engine/query/diagnostics/timing |
| `IsochroneResult` | `query`, `stops`, `surface`, `cutoffs_minutes`, `to_geojson()` |

A returned `status == "blocked"` means VIGO ran the request but found no
admissible path. It is not a transport or protocol failure. Invalid arguments
raise `TypeError` or `ValueError`, missing files/runtimes raise
`FileNotFoundError`, bounded CLI operations raise `VigoCliTimeoutError`, and
canonical CLI, native, or response-contract failures raise `VigoCliError`.

Result properties return copies or immutable views. Use `to_dict()` or
`to_json()` when persisting a receipt.

## Timing

Each `RoutePlan.timing` includes:

- `query_wall_ms`: CLI route and plan materialization time;
- `engine_query_ms`: native kernel query time when reported;
- `request_ms`: complete resident request work before response serialization;
- `serialization_ms`: response JSON serialization;
- `cache_hit`: whether the canonical result cache was used.

`BatchRoutingResult.timing` adds Python wall time, per-row wall time, CLI
preparation/routing/request/output totals, process-I/O overhead, and
`resident_session_reused`. Keep these regions separate when reporting
performance.

Performance comparisons must alternate direct CLI and Python requests against
the same installed runtime, prepared stores, request corpus, and service date.
Report process startup, preparation, native query, serialization, and Python
materialization separately.

## Execution deadlines

Resident route responses, one-to-many queries, and isochrones have a 120-second
deadline by default. A timeout terminates the child process, waits a bounded
grace period, kills it if necessary, invalidates the resident session, and
raises `VigoCliTimeoutError`. Set a network-specific deadline with
`open_network(..., route_timeout=seconds)` or
`TransportNetwork(..., route_timeout=seconds)`.

Raw GTFS/OSM compilation has a separate 1,800-second deadline so a large build
does not inherit an interactive query limit. Configure it with
`TransportNetwork(..., build_timeout=seconds)`. Both values must be positive,
finite, and no greater than 86,400 seconds.

## Console commands

`vigo-router` and `python -m vigo_router` pass their arguments directly to the
resolved canonical VIGO CLI. `vigo-router-download` installs and verifies the
packaged runtime. These are entry points from `pyproject.toml`; no separate
script wrapper is required.

## Verify a change

From the Python source directory:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -t . -p 'test_vigo_router*.py'
python3 scripts/check-notebooks.py
python3 scripts/check-public-release.py
python3 -m pip wheel --no-deps --wheel-dir dist .
```

For a release, install the resulting wheel into a new virtual environment,
install the checksum-pinned VIGO archive with `vigo-router-download`, and run
the CLI version/help checks from outside the source tree without `PYTHONPATH`.
The VIGO platform itself is built from the separate
[VIGO source repository](https://github.com/hytangs/vigo#build-from-source).
Main-branch and tagged builds also run this clean-wheel path on Apple-silicon
macOS against the published runtime and a generated GTFS/OSM fixture.

Continue with the [example notebooks](notebooks/) or the
[canonical VIGO CLI contract](https://github.com/hytangs/vigo/blob/main/docs/vigo-cli.md).

## Repository scope and license

This repository contains the Python package, focused contract tests, clean
example notebooks, and public documentation. It excludes the VIGO platform
source, packaged runtimes, generated wheels, private data, publication
material, performance experiments, debug scripts, and generated results.

VIGO for Python is licensed under the Apache License 2.0. See [LICENSE](LICENSE)
and [NOTICE](NOTICE).
