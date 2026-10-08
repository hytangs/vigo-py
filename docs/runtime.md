# Runtime and loading

`vigo.open(path)` reads the City manifest, checks its required files, and resolves a runtime. It does not load the complete routing network. The first Query starts its runtime process and opens the required native data.

## Installation choices

| Install | Engine requirement | Use it for |
| --- | --- | --- |
| Platform wheel with bundled runtime | Included Node, headless bundle, and native kernel | A self-contained Python environment |
| Source checkout or universal development wheel | Select a compatible external Engine | Wrapper development or an independently managed Engine |
| Python with a complete Studio installation | Select the app or extracted distribution | Reusing the Engine shipped with Studio |

Python requires version 3.10 or newer. Install a local platform wheel with `python -m pip install /path/to/vigo-0.5.0-py3-none-PLATFORM.whl`, substituting its real filename. The package has no mandatory Python dependencies. `tqdm` is optional for Build progress.

For a source install, run `python -m pip install -e .` from the wrapper checkout, then select a compatible runtime explicitly. An adjacent Engine checkout is not automatically selected. Maintainers can [stage and build a headless platform wheel](../CONTRIBUTING.md#build-a-headless-wheel).

The self-contained wheel still communicates with a Node process. Rust performs routing kernels; JavaScript handles GTFS row loading, orchestration, and itinerary assembly. It is not a direct Python/Rust extension.

## From GTFS and OSM to the first answer

Measure `vigo.build()` from just before the call until it returns a City. Use
local raw files and a new output directory: this includes import, required
street indexes, transfers, station-access preparation, and publication of the
saved City. It does not include a Query for a particular service date.

Measure the first `city.route()` separately, and report their combined time
when answering "how long until a new city can answer?" The first Query can
prepare its active-service timetable and selected ride geometry.

```python
from time import perf_counter
import vigo

runtime = vigo.resolve_runtime()  # Runtime discovery is outside these timers.
started = perf_counter()
with vigo.build(
    "./boston", gtfs="./mbta.zip", osm="./massachusetts.osm.pbf", runtime=runtime
) as city:
    built = perf_counter()
    result = city.route(
        [-71.062, 42.356], [-71.058, 42.349],
        depart_at="08:00", service_date="2026-09-04",
    )
    answered = perf_counter()
    print("raw_build_seconds", built - started)
    print("first_query_seconds", answered - built)
    print("raw_to_first_answer_seconds", answered - started)
    print("status", result.status)
```

Choose a date covered by the feed. These timers exclude downloads, installation,
runtime discovery, printing, export, and closing the City. Record those costs
separately when they belong to the intended workflow. The compiler's
`city.details["timing"]["totalMs"]` ends before final publication and is not the
complete elapsed time of `vigo.build()`. Its component stages can overlap.

## Build progress

Install `tqdm` or install this checkout with
`python -m pip install ".[progress]"`, then call:

```python
with vigo.build("./boston", gtfs="./mbta.zip", osm="./massachusetts.osm.pbf", progress=True) as city:
    print(city.name)
```

The optional display uses `tqdm.auto` and shows the latest stage reported by
Engine and the elapsed Build time. The timer continues during long stages,
including street-index preparation. It does not invent a percentage or remaining
time when Engine has not reported a total. Build errors retain Engine's explanation; timeout or interruption stops
the build processes and closes the display. Progress defaults to `False` and
does not add work to Route or Matrix calls.

## Reopen a built City

Call `vigo.build()` once for the GTFS and OSM inputs, then use `vigo.open()` on
the saved directory in later sessions. Raw inputs are not needed for querying.
Keep the whole City directory when moving or packaging it: it contains the
street indexes, prepared station-access state, and retained timetable snapshots.

The current VIGO runtime restores these prepared files instead of repeating
their compilation. Node and the packaged runtime share the same portable
timetable format. A new set of active services may need one timetable
preparation; dates with identical active services share it. The timetable disk
cache is bounded, so an evicted pattern needs preparation again. Missing or
outdated timetable/access caches are rebuilt from the compiled City. Missing or
corrupt required street files need a complete City restored or rebuilt; changing
the raw network data requires a new build.

Reopening still reads files and allocates working memory. Reusing one open City
also avoids those costs. `Result.timing["openMs"]` measures runtime preparation
the first time each mode is prepared in a resident process, and is zero when that mode is reused.

## Select a runtime

```python
import vigo

runtime = vigo.resolve_runtime()
print(runtime.product_version, runtime.api_version, runtime.source)

with vigo.open("./boston", runtime=runtime) as city:
    print(city.sources)
```

Selection follows this order:

1. The explicit `runtime=` argument.
2. `VIGO_RUNTIME`, then `VIGO_APP`.
3. The bundled headless runtime.
4. Installed `VIGO Studio.app` locations on macOS.
5. `vigo` on `PATH`.

An invalid explicit setting fails rather than selecting another engine. Once a complete bundled layout is selected, a failed capability check also ends discovery. Sibling source checkouts are not discovered implicitly. Python requires Engine 0.5.x, API 1.0, capability schema v3, City format 1, Result schema 1, the public commands, and advertised trace output. Other API versions and capability schemas are rejected at runtime selection.

Use a path for a complete runtime directory or executable. Use an argument sequence for a custom command; a shell command string is not split into executable and arguments:

```python
runtime = vigo.resolve_runtime("./headless")
with vigo.open("./boston", runtime=runtime) as city:
    print(city.runtime.source, city.runtime.product_version)
```

Selecting a source `vigo.mjs` file uses system Node. Build its compatible native kernel and CLI together; the [maintainer build steps](../CONTRIBUTING.md#build-a-headless-wheel) show the required Engine commands.

Platform wheels include a private Node 24.18.0 executable, `vigo.mjs`, the native kernel, license notices, and a checksum manifest. Supported build targets are macOS 14+ ARM64/x64, Linux ARM64/x64 with glibc 2.39+ (Ubuntu 24.04 builders), and Windows x64. A wheel must match the target OS and CPU. Source installs and universal development wheels need an external runtime. No import-time or query-time downloads occur.

A headless runtime directory can also be passed to `runtime=`. Keep its files together. To use Studio as the runtime, select a complete macOS `.app`, extracted Studio distribution, or packaged executable with its `resources` directory. Bundled/headless child processes inherit only OS paths and locale settings; provider secrets, Node injection options, and foreign kernel overrides are excluded. Explicit external commands retain the caller's environment.

Successful capability checks are retained for up to eight runtime identities. Each lookup checks the command files' and explicitly selected native kernel's metadata, so replacing or rebuilding the runtime triggers a new check. Failed checks are not cached. Returned capability dictionaries are independent copies. Pass a previously resolved `RuntimeInfo` to reuse that selection explicitly.

Matrix requires advertised resident execution. Supplied traffic for Drive Route/Matrix requires the corresponding advertised capability; traffic is sent in explicit realtime mode. Transfer caps, arrive-by Matrix, journey output, and transit cache controls also require their advertised capabilities. Unsupported combinations raise `UnsupportedQuery`; Python validates these options before sending each query to the resident stream.

## Keep a City open

With the bundled engine, all Route modes (including waypoints and supplied traffic), Matrix, and Reach share resident processes by service date. A City keeps one to four processes according to CPU and physical memory. Changing dates reclaims the least recently used idle process. Active queries finish before eviction or `City.close()` releases their process.

VIGO Python 0.5 requires Engine 0.5. All queries use the documented `stream` protocol. Detailed Python Results request its explicit trace output. Opening several City objects does not share their routing processes.

For repeated work, reuse one City in a context manager. Route answers are recomputed for every call. `disable_cache=True` on transit Route or Matrix disables street-access and reconstructed walking-path caches while leaving the prepared network resident.

Requests for the same service date share and serialize access to its process. Different dates can use separate processes within the pool limit. `submit()` moves work to a background thread; it does not create an independent process per request. Await submitted jobs before leaving the City's context.

## Timeouts and failures

`vigo.open(..., timeout=120)` sets the Query timeout in seconds. Waiting for an available resident process and waiting for its response each use that limit. A timed-out resident process is stopped; a later Query can start a fresh one. `City.close()` waits for active resident queries before closing their processes.

`vigo.build(..., timeout=1800)` bounds the build command. The City it returns has the default 120-second Query timeout; reopen with a different timeout if needed. Runtime discovery uses a separate 30-second capability-check timeout per candidate. Invalid or unavailable runtimes raise `VigoError`; Query timeouts raise `VigoTimeoutError`. These are per-operation limits, not a single end-to-end deadline.

If importing the package exposes none of the documented functions, inspect `vigo.__file__` and the active interpreter. A local directory named `vigo` can shadow the installed package. Install into the intended interpreter and restart the Python session after correcting its import path.

## Reduce Python overhead

Install `python -m pip install ".[speed]"` from this checkout to enable optional
`orjson` decoding of resident responses. Without it, Python uses the standard
JSON decoder. Request encoding and exports retain strict finite-number checks
and existing formatting. Decoding acceleration does not change routing or
cache policy. The detailed trace is still requested to preserve Result fields.

Result access copies JSON containers while reusing immutable scalar values.
For large matrices, consume `result.iter_rows()` to avoid copying rows you do
not need. CSV export writes stored rows without first copying the full matrix.
Use `include_journeys=False` and `include_geometry=False` when only travel
times are needed; these remain the Matrix defaults. Reuse an open City.

Run `python scripts/benchmark-wrapper.py` from the checkout to compare the old
generic row-copy operation with current Result access and compare standard
JSON decoding with the installed decoder. This synthetic benchmark excludes
Engine computation, process communication, and City loading. It is not a
routing speedup claim.

## Measure the complete operation

Record `vigo.open()` separately from the first Query: most native loading occurs during that Query. `Result.timing` distinguishes runtime opening and computation where available. For Route, Matrix, and Reach, `endToEndMs` includes Python request preparation and the runtime response, through payload decoding; Result construction and export occur afterwards. Use an outer wall-clock measurement when including those stages.

Compare first-query and repeated-query durations on the same City, service date, query options, and output detail. Native `engineQueryMs` excludes parts of access, geometry, communication, and Python work; it is not full-route throughput. Count ready Results, blocked Results, setup failures, and query exceptions separately.

Keep `require_transit_ride` explicit when comparing results: the current
default is `False`, which allows a direct walk to compete with transit. Set `True` to require a boarding. Match
`disable_cache`, date, walking budget, coordinates, and output detail before
attributing a latency difference to a version change. A sub-millisecond native
timetable measurement is distinct from a complete Python Route call.

[Documentation](README.md) · [Troubleshooting](troubleshooting.md) · [Results](results.md)
