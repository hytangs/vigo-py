# Runtime and loading

`vigo.open(path)` reads the City manifest, checks its required files, and resolves a runtime. It does not load the complete routing network. The first Query starts its runtime process and opens the required native data.

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
time when Engine has not reported a total. Older runtimes can report fewer
stages. Build errors retain Engine's explanation; timeout or interruption stops
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
on the first resident query and is zero for later queries in that process.

## Select a runtime

```python
import vigo

runtime = vigo.resolve_runtime()
print(runtime.product_version, runtime.api_version, runtime.source)

with vigo.open("./boston", runtime=runtime) as city:
    print(city.sources)
```

An explicit `runtime=` argument takes precedence. Otherwise discovery tries `VIGO_RUNTIME`, `VIGO_APP`, a sibling source build, installed `VIGO Studio.app` locations, and `vigo` on `PATH`, in that order. Runtime paths and explicit command sequences are supported. Python requires API 1.x, City format 1, Result schema 1, and the public commands; product patch versions may differ.

The Python wheel is platform independent; its selected runtime must match the OS and CPU. VIGO 0.3.1 targets macOS Apple Silicon/Intel, Linux ARM64/x64 with glibc, and Windows x64. Set `VIGO_APP` or `runtime=` to a macOS `.app`, an extracted Studio distribution directory, or the packaged executable (`VIGO Studio.exe` on Windows, `VIGO Studio` on Linux). Keep the accompanying `resources` directory in place. The packaged runtime does not require a separately installed Node.js. Windows/Linux installations are selected explicitly unless `vigo` is on `PATH`.

Successful capability checks are retained for up to eight runtime identities. Each lookup checks the command files' metadata, so replacing or rebuilding the runtime triggers a new check. Failed checks are not cached. Returned capability dictionaries are independent copies. Pass a previously resolved `RuntimeInfo` to reuse that selection explicitly.

Matrix requires advertised resident execution. Transfer caps, arrive-by Matrix, journey output, and transit cache controls also require their advertised capabilities. Unsupported combinations raise `UnsupportedQuery`; Python does not silently ignore these options or fall back to the retired one-shot Matrix path.

## Keep a City open

Transit Route without waypoints or a Scenario, and all supported Matrix queries, share resident processes by service date. A City keeps one to four processes according to CPU and physical memory. Changing dates reclaims the least recently used idle process. Active queries finish before eviction or `City.close()` releases their process.

Walking, driving, waypoint and Scenario Route calls, and Reach calls use separate command invocations. These currently reopen their required data on each call. Opening several City objects does not share their routing processes.

For repeated work, reuse one City in a context manager. Route answers are recomputed for every call. `disable_cache=True` on transit Route or Matrix disables street-access and reconstructed walking-path caches while leaving the prepared network resident.

## Timeouts and failures

`vigo.open(..., timeout=120)` sets the Query timeout in seconds. Waiting for an available resident process and waiting for its response each use that limit. A timed-out resident process is stopped; a later Query can start a fresh one. `City.close()` waits for active resident queries before closing their processes.

`vigo.build(..., timeout=1800)` bounds the build command. Runtime discovery uses a separate 30-second capability-check timeout per candidate. Invalid or unavailable runtimes raise `VigoError`; Query timeouts raise `VigoTimeoutError`.

If importing the package exposes none of the documented functions, inspect `vigo.__file__` and the active interpreter. A local directory named `vigo` can shadow the installed package. Install into the intended interpreter and restart the Python session after correcting its import path.

## Measure the complete operation

Record `vigo.open()` separately from the first Query: most native loading occurs during that Query. `Result.timing` distinguishes runtime opening and computation where available. For Route and Matrix, `endToEndMs` includes Python request preparation and the runtime response, through payload decoding; Result construction and export occur afterwards. Use an outer wall-clock measurement when including those stages.

Compare first-query and repeated-query durations on the same City, service date, query options, and output detail. Native `engineQueryMs` excludes parts of access, geometry, communication, and Python work; it is not full-route throughput. Count ready Results, blocked Results, setup failures, and query exceptions separately.

Keep `require_transit_ride` explicit when comparing older results: the current
default is `True`, while `False` also allows walk-only answers. Match
`disable_cache`, date, walking budget, coordinates, and output detail before
attributing a latency difference to a version change. A sub-millisecond native
timetable measurement is distinct from a complete Python Route call.
