# Changelog

## 0.3.0

VIGO 0.3 unifies the product around one model:

```text
City -> Scenario -> Route | Matrix | Reach -> Result
```

- Renamed the Python package to `vigo`.
- Replaced store-pair objects with one complete City.
- Added immutable Scenario objects tied to one City revision.
- Replaced separate batch and one-origin analysis functions with Route and Matrix.
- Renamed network range analysis to Reach.
- Added one Result shape, local comparison, export, and background jobs.
- Removed the Python pass-through command and runtime installer. VIGO Studio and the VIGO command line are installed independently.
