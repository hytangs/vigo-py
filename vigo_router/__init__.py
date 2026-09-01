"""Standalone Python binding for VIGO's canonical resident-kernel router.

The package is intentionally a thin wrapper: it asks the bundled CLI to compile
GTFS/OSM inputs, validates the resulting VIGO stores, and routes through the
same resident kernels used by the desktop release. SQLite is the durable
compiler output. This package does not parse GTFS or implement a second routing
algorithm.
"""

from .core import (
    BatchRoutingResult,
    IsochroneResult,
    Leg,
    OneToManyResult,
    RoutePlan,
    RoutingNetwork,
    TransportNetwork,
    TravelTime,
    VigoCliError,
    VigoCliTimeoutError,
    isochrone,
    open_network,
    resolve_cli,
    route,
    route_batch,
    route_many,
)
from .runtime import CliProbe, RuntimeInstall, install_runtime, probe_cli

__version__ = "0.3.0"

__all__ = [
    "BatchRoutingResult",
    "CliProbe",
    "IsochroneResult",
    "Leg",
    "OneToManyResult",
    "RoutePlan",
    "RoutingNetwork",
    "RuntimeInstall",
    "TransportNetwork",
    "TravelTime",
    "VigoCliError",
    "VigoCliTimeoutError",
    "__version__",
    "install_runtime",
    "isochrone",
    "open_network",
    "probe_cli",
    "resolve_cli",
    "route",
    "route_batch",
    "route_many",
]
