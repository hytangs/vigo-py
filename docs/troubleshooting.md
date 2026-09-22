# Troubleshooting

Start with the failing layer: importing Python, selecting a runtime, opening a City, executing a Query, or interpreting a completed Result. Keep the original error and request before changing inputs.

## Check the Python environment

```python
import sys
import vigo

print("interpreter", sys.executable)
print("package", vigo.__file__)
print("wrapper version", getattr(vigo, "__version__", "unavailable"))
```

If the import has no documented functions, a local `vigo.py` or directory named `vigo` may shadow the installed package. Use `python -m pip` with the intended interpreter, then restart the Python or notebook session. A notebook kernel can use a different interpreter from the terminal.

## Check the selected runtime

```python
import vigo

try:
    runtime = vigo.resolve_runtime()
except vigo.VigoError as error:
    print(error)
else:
    print(runtime.source, runtime.path)
    print(runtime.product_version, runtime.api_version)
    print(runtime.capabilities)
```

Explicit `runtime=`, `VIGO_RUNTIME`, and `VIGO_APP` take precedence over a bundled engine. An invalid explicit choice fails instead of trying another engine. Check old environment settings when a platform wheel appears to select the wrong runtime. See [runtime selection](runtime.md#select-a-runtime) for the full order.

| Symptom | Check and next action |
| --- | --- |
| No compatible VIGO runtime | Confirm that the install includes a platform runtime, or select a complete external runtime. Source installs require an external Engine. |
| Native library cannot load | Check OS, CPU, and platform requirements; keep the Node executable, bundle, and native kernel from the same runtime together. |
| External `.mjs` cannot run | A source bundle needs compatible system Node and its native kernel. Prefer a complete headless runtime directory or follow Engine's source-build guide. |
| Studio installation cannot run as a runtime | Select a complete app or extracted distribution, including its `resources` directory. |
| API compatibility succeeds but a feature is unsupported | API compatibility and optional Query capabilities are separate. Inspect `city.supports(query)` and the runtime's advertised capabilities. |

## Check the City

`vigo.open()` needs a complete City directory, not a raw GTFS ZIP or one SQLite file. Move or restore the whole build. Its initial checks cover the manifest and required entry files; later native loading can still detect missing or corrupt data.

If the build destination already exists, use a new directory or intentionally pass `replace=True`. If inputs changed, build a new revision. An incomplete required street model needs restoration or rebuilding; increasing a query timeout cannot repair it.

## Distinguish unsupported from blocked

`UnsupportedQuery` means a requested combination cannot run. Inspect `error.support.reason` and `error.support.available`. Planned service changes support Reach; supplied traffic supports Drive Route/Matrix with the advertised capability; Python live transit is unsupported. `supports()` checks combinations, not every endpoint, date, or data-dependent constraint.

`InvalidQuery` covers Python-side request checks. Additional invalid requests can fail in Engine and surface as `VigoError`; catch that base class at a batch boundary when every query failure must be retained. See the [batch workflow](workflows.md#retain-every-batch-outcome).

A blocked journey is a completed model answer. Check, in this order:

1. The exact service date and feed coverage, including the correct service day for an after-midnight time.
2. Exact stop IDs or `[longitude, latitude]` coordinate order and City coverage.
3. Departure versus arrival semantics, horizon, walking budget, and transfer cap.
4. Whether a vehicle boarding is required. The default is `require_transit_ride=True`.
5. Warnings and the Engine's [routing limits](https://github.com/hytangs/vigo/blob/main/docs/reference/known-routing-limitations.md).

For Matrix, inspect each row even when the Result itself is ready. Do not retry blocked queries as if the runtime crashed. Only relax constraints when that matches the intended question.

## Diagnose time and comparison surprises

| Symptom | Explanation |
| --- | --- |
| First query is slower than repeats | `vigo.open()` does not prepare the complete network; the first query pays runtime and mode preparation costs. |
| `disable_cache=True` still gives fast repeats | It disables specific transit street caches, while keeping the network and resident process prepared. |
| A query times out | Use `vigo.open(..., timeout=...)` for query limits. Build timeout and `Job.wait(timeout=...)` control different operations. |
| `job.wait()` times out but work continues | The wait timed out; it did not cancel the running Query. `cancel()` only cancels queued work. |
| Arrive-by Matrix duration is longer than the journey | The row measures to the deadline; the nested journey can arrive earlier. |
| Reach comparisons reject two maps | Their computation grids must have identical bounds and dimensions. Match origin, extent, and raster size. |
| A comparison reports fewer pairs than expected | Matrix comparison includes matched IDs only. Check unique IDs, stable endpoint meanings, and pair coverage. |

Keep runtime preparation, computation, Python call duration, and export time separate. The [runtime guide](runtime.md#measure-the-complete-operation) describes these boundaries.

## Report a reproducible issue

Include the wrapper and Engine versions, OS/CPU, runtime selection method, smallest failing Python request, exact exception or Result status, and whether the failure occurs on the first or a repeated query. Include a shareable synthetic fixture when possible. Describe City inputs and build options without uploading licensed feeds, credentials, private paths, or private study results.

Report Python packaging, argument conversion, process lifetime, and Python exports in [VIGO Python issues](https://github.com/hytangs/vigo-py/issues). Shared transport computation belongs in [VIGO Engine issues](https://github.com/hytangs/vigo/issues).

[Documentation](README.md) · [Reference](reference.md) · [Runtime](runtime.md)
