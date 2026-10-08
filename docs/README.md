# VIGO Python documentation

Use Python to build a City from GTFS and OSM, ask transport questions, and retain the answers. Start with the [installation and first query](../README.md), then follow the guide for your task.

| I want to… | Read |
| --- | --- |
| Understand City, Scenario, Query, and Result | [Concepts](concepts.md) |
| Find one journey or visit points in order | [Route workflow](workflows.md#find-a-journey) · [Route reference](reference.md#route) |
| Calculate travel times for many places | [Matrix workflow](workflows.md#calculate-travel-times-to-a-common-destination) · [Matrix reference](reference.md#matrix) |
| Compare reachable areas under a service proposal | [Scenario workflow](workflows.md#compare-a-service-proposal) · [Reach reference](reference.md#reach) |
| Run many requests and retain failures | [Batch workflow](workflows.md#retain-every-batch-outcome) |
| Interpret or export an answer | [Results](results.md) |
| Install an engine, reuse a City, or measure execution | [Runtime and loading](runtime.md) |
| Diagnose a failure or an unexpected answer | [Troubleshooting](troubleshooting.md) |
| Work interactively | [Notebooks](../notebooks/README.md) |
| Contribute or package the runtime | [Contributing](../CONTRIBUTING.md) |

## Choose a Query

| Query | Question | Main output |
| --- | --- | --- |
| Route | How can I travel between these ordered points? | One journey with timed legs |
| Matrix | How long does each origin–destination pair take? | A row per pair, optionally with transit journeys |
| Reach | Where can I get within these time limits? | Travel-time surface and contours |

Walk, Drive, and Transit are Route and Matrix modes. Reach supports scheduled transit with walking and walking-only queries. Compare acts on completed Results; it does not run another routing query.

Use a Scenario for a supported change to one City revision. Planned transit changes support Reach. Supplied traffic supports Drive Route and Matrix when the runtime advertises it. Live transit is not exposed by the Python API. The [support table](reference.md#scenario) makes these boundaries explicit.

## Keep the question with the answer

Coordinates use `[longitude, latitude]`; stop IDs identify exact stops in the City. Every study needs a service date covered by its feed and explicit travel assumptions. A saved Result retains the query, warnings, and timing. Keep its City revision and any authored Scenario inputs alongside it; [Results](results.md#retain-a-reproducible-record) explains what the export does and does not preserve.

For shared transport semantics, use the Engine's [routing reference](https://github.com/hytangs/vigo/blob/main/docs/reference/routing.md) and [known limits](https://github.com/hytangs/vigo/blob/main/docs/reference/known-routing-limitations.md). This guide owns Python objects, argument names, process lifetime, and exports.
