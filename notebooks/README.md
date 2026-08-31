# Example notebooks

These notebooks demonstrate the public `vigo_router` API without stored output
or developer-specific paths:

1. `01_build_network.ipynb` builds a VIGO network from GTFS and OSM inputs.
2. `02_route_and_batch.ipynb` runs one route and a batch.
3. `03_one_to_many.ipynb` produces a travel-time field.
4. `04_isochrone.ipynb` generates an accessibility surface.

Set the input paths and service date for your own data before executing them.
Run `python scripts/check-notebooks.py` after editing an example.
