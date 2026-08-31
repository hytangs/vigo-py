"""Standalone Python binding for VIGO's canonical native/JavaScript routing CLI.

This module intentionally contains no GTFS parser or routing algorithm. Raw
GTFS/OSM inputs are compiled by the bundled CLI; desktop, CLI, and Python route
through the same persisted SQLite stores and resident Rust kernels. Each plan
carries the exact algorithm, method, and timing diagnostics for that request.
"""

from __future__ import annotations

import copy
import datetime as dt
import hashlib
import json
import math
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time as time_module
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any
from urllib.parse import quote

PointInput = str | Sequence[float] | Mapping[str, Any]
RouteRequest = Mapping[str, Any]

_ROUTING_TABLES = frozenset({"metadata", "stops", "routes", "trips", "connections"})
_STREET_RUNTIME_LAYOUT = "runtime-snapshots-v1"
_STREET_WALK_SNAPSHOT_SUFFIX = ".street-accelerator-v7.bin"
_STREET_DRIVE_SNAPSHOT_SUFFIX = ".drive-accelerator-v2.bin"
_STREAM_RESULT_SCHEMA = "vigo.cli.route-result.v1"
_BUILD_SCHEMA = "vigo.cli.build-network.v1"
_ONE_TO_MANY_SCHEMA = "vigo.cli.one-to-many.v1"
_ISOCHRONE_SCHEMA = "vigo.cli.isochrone.v1"
_PACKAGE_VERSION = "0.3.0"


class VigoCliError(RuntimeError):
    """Raised when the canonical VIGO CLI cannot execute a routing request."""


class _ResidentProtocolError(VigoCliError):
    """Internal signal that a resident response violated the CLI contract."""


class _ResidentSessionBroken(RuntimeError):
    """Internal signal that a resident CLI process must be restarted."""


class _RoutingSession:
    """One prepared ``route-ndjson`` child process."""

    __slots__ = ("_process", "_stderr")

    def __init__(
        self,
        network: RoutingNetwork,
        *,
        service_date: str,
        service_day: str,
    ) -> None:
        arguments = [
            *network.command,
            "route-ndjson",
            f"--store={network.store}",
            f"--service-date={service_date}",
            f"--service-day={service_day}",
        ]
        if network.street_store is not None:
            arguments.append(f"--street-store={network.street_store}")
        stderr = tempfile.TemporaryFile(mode="w+b")  # noqa: SIM115 - owned by close()
        try:
            process = subprocess.Popen(
                arguments,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=stderr,
            )
        except (OSError, ValueError):
            stderr.close()
            raise
        self._stderr = stderr
        self._process = process

    def exchange(self, requests: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        process = self._process
        if process.stdin is None or process.stdout is None:
            raise _ResidentSessionBroken(self._failure_message())
        responses: list[dict[str, Any]] = []
        try:
            for request in requests:
                process.stdin.write(
                    json.dumps(
                        request,
                        separators=(",", ":"),
                        ensure_ascii=False,
                    ).encode("utf-8")
                    + b"\n"
                )
                process.stdin.flush()
                line = process.stdout.readline()
                if not line:
                    raise _ResidentSessionBroken(self._failure_message())
                try:
                    response = json.loads(line)
                except json.JSONDecodeError as error:
                    raise _ResidentSessionBroken(
                        f"VIGO resident session returned invalid NDJSON: {error}"
                    ) from error
                if not isinstance(response, dict):
                    raise _ResidentSessionBroken(
                        "VIGO resident session returned a non-object row"
                    )
                responses.append(response)
        except (BrokenPipeError, OSError, ValueError) as error:
            raise _ResidentSessionBroken(self._failure_message()) from error
        return responses

    def _failure_message(self) -> str:
        try:
            self._stderr.flush()
            self._stderr.seek(0)
            detail = self._stderr.read().decode("utf-8", errors="replace").strip()
        except (OSError, ValueError):
            detail = ""
        return detail or f"VIGO resident session exited {self._process.poll()}"

    def close(self) -> None:
        process = self._process
        if process.stdin is not None and not process.stdin.closed:
            try:
                process.stdin.close()
            except (BrokenPipeError, OSError):
                pass
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            try:
                process.terminate()
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
                process.wait()
        if process.stdout is not None and not process.stdout.closed:
            process.stdout.close()
        if not self._stderr.closed:
            self._stderr.close()


class _RoutingSessionPool:
    """Keep at most one date-bound resident router per Python network."""

    __slots__ = ("_exchange_lock", "_key", "_lock", "_session")

    def __init__(self) -> None:
        self._key: tuple[str, str] | None = None
        self._lock = threading.Lock()
        self._exchange_lock = threading.Lock()
        self._session: _RoutingSession | None = None

    def exchange(
        self,
        network: RoutingNetwork,
        requests: Sequence[Mapping[str, Any]],
        *,
        service_date: str,
        service_day: str,
    ) -> tuple[list[dict[str, Any]], bool]:
        key = (service_date, service_day)
        with self._exchange_lock:
            with self._lock:
                reused = self._session is not None and self._key == key
                if not reused:
                    self._replace_unlocked(network, key)
                assert self._session is not None
                session = self._session
            try:
                return session.exchange(requests), reused
            except _ResidentSessionBroken:
                with self._lock:
                    if self._session is session or self._key != key:
                        self._replace_unlocked(network, key)
                    assert self._session is not None
                    session = self._session
                try:
                    return session.exchange(requests), False
                except _ResidentSessionBroken as error:
                    with self._lock:
                        if self._session is session:
                            self._close_unlocked()
                    raise VigoCliError(
                        f"VIGO resident routing session failed: {error}"
                    ) from error

    def close(self) -> None:
        with self._lock:
            self._close_unlocked()

    def _close_unlocked(self) -> None:
        if self._session is not None:
            self._session.close()
        self._session = None
        self._key = None

    def _replace_unlocked(
        self,
        network: RoutingNetwork,
        key: tuple[str, str],
    ) -> None:
        self._close_unlocked()
        self._session = _RoutingSession(
            network,
            service_date=key[0],
            service_day=key[1],
        )
        self._key = key

    def __del__(self) -> None:
        try:
            self.close()
        except (AttributeError, OSError, RuntimeError, ValueError):
            pass


@dataclass(frozen=True, slots=True)
class RoutingNetwork:
    """A validated reference to VIGO's persisted routing stores and CLI."""

    store: Path
    street_store: Path | None
    command: tuple[str, ...]
    stats: Mapping[str, int | str] = field(repr=False)
    engine: str = "vigo-native-js-cli"
    _sessions: _RoutingSessionPool = field(
        default_factory=_RoutingSessionPool,
        init=False,
        repr=False,
        compare=False,
    )

    def route(
        self, origin: PointInput, destination: PointInput, **options: Any
    ) -> RoutePlan:
        return route(self, origin, destination, **options)

    def route_batch(
        self,
        requests: Sequence[RouteRequest],
        **options: Any,
    ) -> BatchRoutingResult:
        return route_batch(self, requests, **options)

    def route_many(
        self,
        origin: PointInput,
        destinations: Mapping[str, PointInput] | Sequence[PointInput],
        **options: Any,
    ) -> OneToManyResult:
        return route_many(self, origin, destinations, **options)

    def isochrone(
        self,
        origin: PointInput,
        **options: Any,
    ) -> IsochroneResult:
        return isochrone(self, origin, **options)

    def close(self) -> None:
        """Stop this network's resident routing process, if one was started."""

        self._sessions.close()

    def __enter__(self) -> RoutingNetwork:  # noqa: PYI034 - Python 3.10 support
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


@dataclass(frozen=True, slots=True)
class Leg:
    """Read-only Python view of one canonical VIGO routing leg."""

    _data: Mapping[str, Any] = field(repr=False)

    @property
    def type(self) -> str:
        return str(self._data.get("type", ""))

    @property
    def route_id(self) -> str | None:
        value = self._data.get("routeId")
        return None if value is None else str(value)

    @property
    def route_short_name(self) -> str | None:
        value = self._data.get("routeShortName")
        return None if value is None else str(value)

    @property
    def trip_id(self) -> str | None:
        value = self._data.get("tripId")
        return None if value is None else str(value)

    @property
    def duration_minutes(self) -> float:
        return float(self._data.get("durationMinutes", 0))

    @property
    def coordinates(self) -> tuple[tuple[float, float], ...]:
        return tuple(
            (float(point[0]), float(point[1]))
            for point in self._data.get("coordinates", ())
            if isinstance(point, Sequence) and len(point) >= 2
        )

    def to_dict(self) -> dict[str, Any]:
        return copy.deepcopy(dict(self._data))


@dataclass(frozen=True, slots=True)
class RoutePlan:
    """Read-only Python view of a full-fidelity canonical VIGO plan."""

    request_id: str
    _data: Mapping[str, Any] = field(repr=False)
    _summary: Mapping[str, Any] = field(repr=False)
    _timing: Mapping[str, Any] = field(default_factory=dict, repr=False)
    legs: tuple[Leg, ...] = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "legs",
            tuple(
                Leg(MappingProxyType(leg if isinstance(leg, dict) else dict(leg)))
                for leg in self._data.get("legs", ())
                if isinstance(leg, Mapping)
            ),
        )

    @property
    def status(self) -> str:
        return str(self._data.get("status", "blocked"))

    @property
    def routing_status(self) -> str:
        diagnostics = self._data.get("diagnostics")
        if isinstance(diagnostics, Mapping):
            value = diagnostics.get("routingStatus")
            if value:
                return str(value)
        return "ready" if self.status == "ready" else "blocked"

    def _diagnostic_text(self, key: str) -> str | None:
        diagnostics = self._data.get("diagnostics")
        if not isinstance(diagnostics, Mapping):
            return None
        value = diagnostics.get(key)
        return None if value is None or value == "" else str(value)

    @property
    def algorithm(self) -> str | None:
        """Canonical route algorithm reported by VIGO."""

        return self._diagnostic_text("algorithm")

    @property
    def walking_network(self) -> str | None:
        """Walking-network source used for access and egress, if reported."""

        return self._diagnostic_text("walkingNetwork")

    @property
    def optimality(self) -> str | None:
        """Canonical optimality statement reported by VIGO."""

        return self._diagnostic_text("optimality")

    @property
    def failure_code(self) -> str | None:
        """Stable failure code for a blocked or failed route, if available."""

        diagnostics = self._data.get("diagnostics")
        if not isinstance(diagnostics, Mapping):
            return None
        failure = diagnostics.get("failure")
        if isinstance(failure, Mapping) and failure.get("code"):
            return str(failure["code"])
        return self._diagnostic_text("failureCode")

    @property
    def failure_category(self) -> str | None:
        """Failure category for a blocked or failed route, if available."""

        diagnostics = self._data.get("diagnostics")
        if not isinstance(diagnostics, Mapping):
            return None
        failure = diagnostics.get("failure")
        if isinstance(failure, Mapping) and failure.get("category"):
            return str(failure["category"])
        return self._diagnostic_text("failureCategory")

    @property
    def method_used(self) -> tuple[str, ...]:
        """Exact route methods used, normalized to an immutable tuple."""

        diagnostics = self._data.get("diagnostics")
        if not isinstance(diagnostics, Mapping):
            return ()
        value = diagnostics.get("methodUsed", diagnostics.get("method"))
        if isinstance(value, str):
            return (value,) if value else ()
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            return tuple(str(method) for method in value if method)
        return ()

    @property
    def title(self) -> str:
        return str(self._data.get("title", ""))

    @property
    def detail(self) -> str:
        return str(self._data.get("detail", ""))

    @property
    def duration_minutes(self) -> float:
        return float(self._data.get("durationMinutes", 0))

    @property
    def depart_minutes(self) -> float:
        return float(self._data.get("departMinutes", 0))

    @property
    def arrive_minutes(self) -> float | None:
        value = self._data.get("arriveMinutes")
        return None if value is None else float(value)

    @property
    def transfers(self) -> int:
        return int(self._data.get("transfers", 0))

    @property
    def origin(self) -> dict[str, Any]:
        return copy.deepcopy(self._data.get("origin", {}))

    @property
    def destination(self) -> dict[str, Any]:
        return copy.deepcopy(self._data.get("destination", {}))

    @property
    def diagnostics(self) -> dict[str, Any]:
        return copy.deepcopy(self._data.get("diagnostics", {}))

    @property
    def cli_summary(self) -> dict[str, Any]:
        return copy.deepcopy(dict(self._summary))

    @property
    def timing(self) -> dict[str, Any]:
        """Measured query timing reported by the canonical CLI."""

        return copy.deepcopy(dict(self._timing))

    @property
    def route_sequence(self) -> tuple[str, ...]:
        sequence: list[str] = []
        for leg in self.legs:
            if leg.type != "ride":
                continue
            route = leg.route_short_name or leg.route_id
            if route and (not sequence or sequence[-1] != route):
                sequence.append(route)
        return tuple(sequence)

    def to_dict(self) -> dict[str, Any]:
        return copy.deepcopy(dict(self._data))

    def to_json(self, *, indent: int | None = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)

    def to_geojson(self) -> dict[str, Any]:
        features = []
        for index, leg in enumerate(self.legs):
            if len(leg.coordinates) < 2:
                continue
            features.append(
                {
                    "type": "Feature",
                    "geometry": {
                        "type": "LineString",
                        "coordinates": [list(point) for point in leg.coordinates],
                    },
                    "properties": {
                        "index": index,
                        "type": leg.type,
                        "route_id": leg.route_id,
                        "route_short_name": leg.route_short_name,
                        "trip_id": leg.trip_id,
                        "duration_minutes": leg.duration_minutes,
                    },
                }
            )
        return {
            "type": "FeatureCollection",
            "properties": {"request_id": self.request_id, "status": self.status},
            "features": features,
        }


class BatchRoutingResult(list[RoutePlan]):
    """List-compatible route batch with one-process timing and CLI metadata."""

    def __init__(
        self,
        plans: Sequence[RoutePlan] = (),
        *,
        summary: Mapping[str, Any] | None = None,
        timing: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(plans)
        self._summary = MappingProxyType(dict(summary or {}))
        self._timing = MappingProxyType(dict(timing or {}))

    @property
    def summary(self) -> dict[str, Any]:
        return copy.deepcopy(dict(self._summary))

    @property
    def timing(self) -> dict[str, Any]:
        return copy.deepcopy(dict(self._timing))

    def to_dict(self) -> dict[str, Any]:
        return {
            "timing": self.timing,
            "summary": self.summary,
            "results": [plan.to_dict() for plan in self],
        }


@dataclass(frozen=True, slots=True)
class TravelTime:
    """One destination row from the resident Rust one-to-many operator."""

    _data: Mapping[str, Any] = field(repr=False)

    @property
    def destination_id(self) -> str:
        return str(self._data.get("destinationId", ""))

    @property
    def destination_index(self) -> int:
        return int(self._data.get("destinationIndex", 0))

    @property
    def status(self) -> str:
        return str(self._data.get("status", "blocked"))

    @property
    def depart_minutes(self) -> float:
        return float(self._data.get("departMinutes", 0))

    @property
    def arrive_minutes(self) -> float | None:
        value = self._data.get("arriveMinutes")
        return None if value is None else float(value)

    @property
    def duration_minutes(self) -> float | None:
        value = self._data.get("durationMinutes")
        return None if value is None else float(value)

    @property
    def failure_code(self) -> str | None:
        value = self._data.get("failureCode")
        return None if value is None else str(value)

    def to_dict(self) -> dict[str, Any]:
        return copy.deepcopy(dict(self._data))


class OneToManyResult(list[TravelTime]):
    """List-compatible travel-time field with native diagnostics and timing."""

    def __init__(self, payload: Mapping[str, Any], *, python_wall_ms: float) -> None:
        data = copy.deepcopy(dict(payload))
        timing = dict(data.get("timing") or {})
        timing["python_wall_ms"] = round(python_wall_ms, 3)
        data["timing"] = timing
        rows = data.get("rows") or []
        super().__init__(
            TravelTime(MappingProxyType(copy.deepcopy(row))) for row in rows
        )
        self._data = MappingProxyType(data)

    @property
    def engine(self) -> dict[str, Any]:
        return copy.deepcopy(dict(self._data.get("engine") or {}))

    @property
    def query(self) -> dict[str, Any]:
        return copy.deepcopy(dict(self._data.get("query") or {}))

    @property
    def diagnostics(self) -> dict[str, Any]:
        return copy.deepcopy(dict(self._data.get("diagnostics") or {}))

    @property
    def timing(self) -> dict[str, Any]:
        return copy.deepcopy(dict(self._data.get("timing") or {}))

    def to_dict(self) -> dict[str, Any]:
        return copy.deepcopy(dict(self._data))

    def to_json(self, *, indent: int | None = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)


@dataclass(frozen=True, slots=True)
class IsochroneResult:
    """Rust-routed accessibility surface and generated GeoJSON contours."""

    _data: Mapping[str, Any] = field(repr=False)

    @property
    def engine(self) -> dict[str, Any]:
        return copy.deepcopy(dict(self._data.get("engine") or {}))

    @property
    def query(self) -> dict[str, Any]:
        return copy.deepcopy(dict(self._data.get("query") or {}))

    @property
    def stops(self) -> tuple[dict[str, Any], ...]:
        return tuple(copy.deepcopy(stop) for stop in self._data.get("stops", ()))

    @property
    def surface(self) -> dict[str, Any]:
        return copy.deepcopy(dict(self._data.get("surface") or {}))

    @property
    def diagnostics(self) -> dict[str, Any]:
        return copy.deepcopy(dict(self._data.get("diagnostics") or {}))

    @property
    def timing(self) -> dict[str, Any]:
        return copy.deepcopy(dict(self._data.get("timing") or {}))

    @property
    def cutoffs_minutes(self) -> tuple[float, ...]:
        return tuple(float(value) for value in self.query.get("cutoffsMinutes", ()))

    def to_geojson(self) -> dict[str, Any]:
        return copy.deepcopy(
            dict(
                self._data.get("isochrones")
                or {"type": "FeatureCollection", "features": []}
            )
        )

    def to_dict(self) -> dict[str, Any]:
        return copy.deepcopy(dict(self._data))

    def to_json(self, *, indent: int | None = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)


class TransportNetwork:
    """Compile OSM PBF + GTFS ZIP inputs through VIGO's canonical CLI.

    The positional order keeps the OSM PBF first and one or more GTFS ZIPs
    second. Compilation is content-addressed and persisted; Python never parses
    or routes the raw inputs itself. Raw compilation uses the CLI's memory- and
    load-gated parallel mode by default; set ``sequential_raw_build=True`` when
    a constrained host should force one compiler at a time.
    """

    def __init__(
        self,
        osm_pbf: str | os.PathLike[str],
        gtfs: str | os.PathLike[str] | Sequence[str | os.PathLike[str]],
        *,
        cache_dir: str | os.PathLike[str] | None = None,
        cli: str | os.PathLike[str] | None = None,
        rebuild: bool = False,
        progress: bool = False,
        sequential_raw_build: bool = False,
    ) -> None:
        gtfs_paths = _raw_gtfs_paths(gtfs)
        osm_path = _raw_osm_path(osm_pbf)
        command = _resolve_cli(cli)
        cache_root = (
            Path(cache_dir).expanduser().resolve()
            if cache_dir is not None
            else _default_network_cache()
        )
        network_directory = cache_root / _network_fingerprint(osm_path, gtfs_paths)
        summary = _compile_transport_network(
            command,
            osm_path,
            gtfs_paths,
            network_directory,
            rebuild=rebuild,
            progress=progress,
            sequential_raw_build=sequential_raw_build,
        )
        store_path = _sqlite_path(summary["routingStore"]["path"], "routing store")
        street_path = _sqlite_path(summary["streetStore"]["path"], "street store")
        _validate_runtime_routing_store(store_path)
        _validate_street_store(street_path)
        network = RoutingNetwork(
            store=store_path,
            street_store=street_path,
            command=command,
            stats=MappingProxyType(_routing_store_stats(store_path)),
        )
        self._network = network
        self._osm_pbf = osm_path
        self._gtfs = gtfs_paths
        self._network_directory = network_directory
        self._build_summary = MappingProxyType(copy.deepcopy(summary))

    @classmethod
    def from_directory(
        cls,
        directory: str | os.PathLike[str],
        **options: Any,
    ) -> TransportNetwork:
        root = Path(directory).expanduser().resolve()
        if not root.is_dir():
            raise FileNotFoundError(f"network input directory not found: {root}")
        osm = sorted((*root.glob("*.osm.pbf"), *root.glob("*.pbf")))
        osm = list(dict.fromkeys(path.resolve() for path in osm))
        gtfs = sorted(root.glob("*.zip"))
        if len(osm) != 1:
            raise ValueError(
                f"network input directory requires exactly one OSM PBF; found {len(osm)}"
            )
        if not gtfs:
            raise ValueError("network input directory requires at least one GTFS ZIP")
        return cls(osm[0], gtfs, **options)

    @property
    def store(self) -> Path:
        return self._network.store

    @property
    def street_store(self) -> Path:
        assert self._network.street_store is not None
        return self._network.street_store

    @property
    def command(self) -> tuple[str, ...]:
        return self._network.command

    @property
    def stats(self) -> Mapping[str, int | str]:
        return self._network.stats

    @property
    def engine(self) -> str:
        return self._network.engine

    @property
    def osm_pbf(self) -> Path:
        return self._osm_pbf

    @property
    def gtfs(self) -> tuple[Path, ...]:
        return self._gtfs

    @property
    def network_directory(self) -> Path:
        return self._network_directory

    @property
    def build_summary(self) -> dict[str, Any]:
        return copy.deepcopy(dict(self._build_summary))

    def route(
        self, origin: PointInput, destination: PointInput, **options: Any
    ) -> RoutePlan:
        return route(self, origin, destination, **options)

    def route_batch(
        self,
        requests: Sequence[RouteRequest],
        **options: Any,
    ) -> BatchRoutingResult:
        return route_batch(self, requests, **options)

    def route_many(
        self,
        origin: PointInput,
        destinations: Mapping[str, PointInput] | Sequence[PointInput],
        **options: Any,
    ) -> OneToManyResult:
        return route_many(self, origin, destinations, **options)

    def isochrone(
        self,
        origin: PointInput,
        **options: Any,
    ) -> IsochroneResult:
        return isochrone(self, origin, **options)

    def close(self) -> None:
        """Stop the compiled network's resident routing process."""

        self._network.close()

    def __enter__(self) -> TransportNetwork:  # noqa: PYI034 - Python 3.10 support
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def _routing_network(network: RoutingNetwork | TransportNetwork) -> RoutingNetwork:
    if isinstance(network, TransportNetwork):
        return network._network
    if isinstance(network, RoutingNetwork):
        return network
    raise TypeError("network must be returned by open_network() or TransportNetwork()")


def _raw_gtfs_paths(
    values: str | os.PathLike[str] | Sequence[str | os.PathLike[str]],
) -> tuple[Path, ...]:
    if isinstance(values, (str, os.PathLike)):
        candidates = [values]
    else:
        candidates = list(values)
    if not candidates:
        raise ValueError("at least one GTFS ZIP is required")
    paths = tuple(_raw_file(value, "GTFS ZIP", (".zip",)) for value in candidates)
    if len(set(paths)) != len(paths):
        raise ValueError("GTFS ZIP paths must be unique")
    return paths


def _raw_osm_path(value: str | os.PathLike[str]) -> Path:
    return _raw_file(value, "OSM PBF", (".pbf",))


def _raw_file(
    value: str | os.PathLike[str],
    label: str,
    suffixes: tuple[str, ...],
) -> Path:
    path = Path(value).expanduser().resolve()
    if not any(path.name.lower().endswith(suffix) for suffix in suffixes):
        raise ValueError(f"{label} must use {' or '.join(suffixes)}")
    if not path.is_file():
        raise FileNotFoundError(f"{label} not found: {path}")
    return path


def _default_network_cache() -> Path:
    configured = os.environ.get("VIGO_ROUTER_CACHE")
    if configured:
        return Path(configured).expanduser().resolve() / "networks"
    xdg = os.environ.get("XDG_CACHE_HOME")
    if xdg:
        return Path(xdg).expanduser().resolve() / "vigo-router" / "networks"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / "vigo-router" / "networks"
    return Path.home() / ".cache" / "vigo-router" / "networks"


def _network_fingerprint(osm_pbf: Path, gtfs: Sequence[Path]) -> str:
    digest = hashlib.sha256()
    digest.update(f"vigo-router-network:{_PACKAGE_VERSION}\0".encode())
    for role, path in [
        ("osm", osm_pbf),
        *((f"gtfs-{index}", item) for index, item in enumerate(gtfs)),
    ]:
        digest.update(f"{role}:{path.name}\0".encode())
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        digest.update(b"\0")
    return digest.hexdigest()


def _compile_transport_network(
    command: tuple[str, ...],
    osm_pbf: Path,
    gtfs: tuple[Path, ...],
    network_directory: Path,
    *,
    rebuild: bool,
    progress: bool,
    sequential_raw_build: bool,
) -> dict[str, Any]:
    manifest_path = network_directory / "network.json"
    if manifest_path.is_file() and not rebuild:
        try:
            cached = json.loads(manifest_path.read_text(encoding="utf-8"))
            _validate_build_summary(cached)
            _validate_runtime_routing_store(
                _sqlite_path(cached["routingStore"]["path"], "routing store")
            )
            _validate_street_store(
                _sqlite_path(cached["streetStore"]["path"], "street store")
            )
        except (OSError, ValueError, json.JSONDecodeError, KeyError, TypeError, VigoCliError):
            pass
        else:
            return {**cached, "reused": True}

    arguments = [
        *command,
        "build-network",
        f"--osm-pbf={osm_pbf}",
        f"--output-dir={network_directory}",
        *(f"--gtfs={path}" for path in gtfs),
    ]
    if (
        rebuild
        or (network_directory / "routing").exists()
        or (network_directory / "osm").exists()
    ):
        arguments.append("--force")
    if sequential_raw_build:
        arguments.append("--sequential-raw-build")
    completed = subprocess.run(
        arguments,
        stdout=subprocess.PIPE,
        stderr=None if progress else subprocess.PIPE,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        message = (
            completed.stderr.strip()
            if completed.stderr is not None and completed.stderr.strip()
            else "see build progress above"
            if progress
            else "unknown CLI error"
        )
        raise VigoCliError(
            f"VIGO build-network exited {completed.returncode}: {message}"
        )
    try:
        summary = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise VigoCliError(
            f"VIGO build-network returned invalid JSON: {error}"
        ) from error
    _validate_build_summary(summary)
    return {**summary, "reused": False}


def _validate_build_summary(summary: Mapping[str, Any]) -> None:
    if summary.get("schemaVersion") != _BUILD_SCHEMA:
        raise VigoCliError(
            f"Unsupported VIGO build schema: {summary.get('schemaVersion')!r}"
        )
    for key in ("routingStore", "streetStore"):
        descriptor = summary.get(key)
        if not isinstance(descriptor, Mapping) or not descriptor.get("path"):
            raise VigoCliError(f"VIGO build result is missing {key}.path")
        if not Path(str(descriptor["path"])).is_file():
            raise VigoCliError(
                f"VIGO build result file is missing: {descriptor['path']}"
            )


@dataclass(frozen=True, slots=True)
class _StreamTimingTotals:
    preparation_ms: float
    routing_ms: float
    request_ms: float
    serialization_ms: float | None

    @property
    def reported_ms(self) -> float:
        return self.preparation_ms + self.request_ms + (self.serialization_ms or 0)


_StreamPlanRow = tuple[str, dict[str, Any], dict[str, Any]]


def _stream_timing(rows: Sequence[Mapping[str, Any]]) -> _StreamTimingTotals:
    preparation_ms = 0.0
    routing_ms = 0.0
    request_ms = 0.0
    serialization_ms = 0.0
    complete_serialization_timing = True
    for row in rows:
        timing = row.get("timing")
        if not isinstance(timing, Mapping):
            complete_serialization_timing = False
            continue
        preparation_ms += float(timing.get("preparationMs") or 0)
        routing_ms += float(timing.get("routeMs") or 0)
        request_ms += float(timing.get("requestMs") or 0)
        serialization = timing.get("serializationMs")
        if (
            not isinstance(serialization, (int, float))
            or isinstance(serialization, bool)
            or not math.isfinite(float(serialization))
        ):
            complete_serialization_timing = False
        else:
            serialization_ms += float(serialization)
    return _StreamTimingTotals(
        preparation_ms=preparation_ms,
        routing_ms=routing_ms,
        request_ms=request_ms,
        serialization_ms=(serialization_ms if complete_serialization_timing else None),
    )


def _stream_request(
    request: RouteRequest,
    index: int,
    *,
    clock: str,
    time_preference: str,
    routing_preference: str,
    max_walk_km: float,
    departure_window_minutes: int,
) -> dict[str, Any]:
    if not isinstance(request, Mapping):
        raise TypeError("each route request must be a mapping")
    request_id = str(request.get("id") or f"row_{index + 1}")
    if "origin" not in request or "destination" not in request:
        raise ValueError(
            f"route request {request_id!r} requires origin and destination"
        )
    return {
        "id": request_id,
        "origin": _point_payload(request["origin"], "origin"),
        "destination": _point_payload(request["destination"], "destination"),
        "time": clock,
        "timePreference": time_preference,
        "routingPreference": routing_preference,
        "maxWalkKm": float(max_walk_km),
        "departureWindowMinutes": int(departure_window_minutes),
    }


def _validate_stream_results(
    rows: Sequence[Mapping[str, Any]],
    request_ids: Sequence[str],
) -> None:
    if len(rows) != len(request_ids):
        raise _ResidentProtocolError(
            f"VIGO resident session returned {len(rows)} rows for "
            f"{len(request_ids)} requests"
        )
    for row, request_id in zip(rows, request_ids, strict=True):
        if row.get("schemaVersion") != _STREAM_RESULT_SCHEMA:
            raise _ResidentProtocolError(
                f"Unsupported VIGO stream schema: {row.get('schemaVersion')!r}"
            )
        if str(row.get("id")) != request_id:
            raise _ResidentProtocolError(
                f"VIGO stream result id {row.get('id')!r} does not match "
                f"request {request_id!r}"
            )
        if row.get("status") != "ok":
            error = row.get("error")
            message = (
                str(error.get("message"))
                if isinstance(error, Mapping) and error.get("message")
                else "unknown resident routing error"
            )
            raise VigoCliError(f"VIGO route {request_id!r} failed: {message}")
        _validate_engine_descriptor(row.get("engine"))


def _engine_values(
    engine: Mapping[str, Any], plural: str, singular: str
) -> tuple[str, ...]:
    values = engine.get(plural)
    if isinstance(values, Sequence) and not isinstance(values, (str, bytes)):
        return tuple(str(value) for value in values if value)
    value = engine.get(singular) if plural not in engine else None
    return (str(value),) if value else ()


def _aggregate_stream_engines(rows: Sequence[Mapping[str, Any]]) -> Mapping[str, Any]:
    first = rows[0].get("engine")
    if len(rows) == 1 or not isinstance(first, Mapping):
        return first if isinstance(first, Mapping) else {}
    algorithms: set[str] = set()
    methods: set[str] = set()
    for row in rows:
        engine = row.get("engine")
        if not isinstance(engine, Mapping):
            continue
        algorithms.update(_engine_values(engine, "algorithms", "algorithm"))
        methods.update(_engine_values(engine, "methods", "method"))
    descriptor = dict(first)
    algorithm_list = sorted(algorithms)
    descriptor.update(
        {
            "algorithm": (
                algorithm_list[0]
                if len(algorithm_list) == 1
                else "mixed_exact_routing"
                if algorithm_list
                else "no_route_executed"
            ),
            "algorithms": algorithm_list,
            "methods": sorted(methods),
        }
    )
    return descriptor


def _stream_plan_rows(
    envelopes: Sequence[Mapping[str, Any]], request_ids: Sequence[str]
) -> list[_StreamPlanRow]:
    rows: list[_StreamPlanRow] = []
    for request_id, envelope in zip(request_ids, envelopes, strict=True):
        raw_plan = envelope.get("plan")
        plan = (
            dict(raw_plan)
            if isinstance(raw_plan, Mapping)
            else _blocked_payload(request_id, "No canonical route was returned.")
        )
        response_timing = envelope.get("timing")
        timing = response_timing if isinstance(response_timing, Mapping) else {}
        diagnostics = plan.get("diagnostics")
        raw_search_stats = (
            diagnostics.get("searchStats") if isinstance(diagnostics, Mapping) else None
        )
        search_stats = raw_search_stats if isinstance(raw_search_stats, Mapping) else {}
        rows.append(
            (
                request_id,
                plan,
                {
                    "query_wall_ms": float(timing.get("routeMs") or 0),
                    "engine_query_ms": (
                        float(timing["engineQueryMs"])
                        if timing.get("engineQueryMs") is not None
                        else None
                    ),
                    "cache_hit": search_stats.get("cacheHit") is True,
                    "request_ms": float(timing.get("requestMs") or 0),
                    "serialization_ms": (
                        float(timing["serializationMs"])
                        if isinstance(timing.get("serializationMs"), (int, float))
                        and not isinstance(timing.get("serializationMs"), bool)
                        else None
                    ),
                },
            )
        )
    return rows


def _unique_stream_requests(requests: Sequence[Mapping[str, Any]]) -> int:
    if len(requests) <= 1:
        return len(requests)
    return len(
        {
            json.dumps(
                {key: value for key, value in request.items() if key != "id"},
                sort_keys=True,
                separators=(",", ":"),
            )
            for request in requests
        }
    )


def _stream_summary(
    network: RoutingNetwork,
    requests: Sequence[Mapping[str, Any]],
    envelopes: Sequence[Mapping[str, Any]],
    plan_rows: Sequence[_StreamPlanRow],
    timing: _StreamTimingTotals,
    *,
    clock: str,
    time_preference: str,
    routing_preference: str,
    departure_window_minutes: int,
    service_day: str,
    service_date: str,
    max_walk_km: float,
) -> dict[str, Any]:
    ready = sum(plan.get("status") == "ready" for _, plan, _ in plan_rows)
    unique_routes = _unique_stream_requests(requests)
    first = envelopes[0]
    raw_routing_store = first.get("routingStore")
    routing_store = (
        dict(raw_routing_store) if isinstance(raw_routing_store, Mapping) else {}
    )
    routing_store.update(
        {
            "path": str(network.store),
            "routeCount": network.stats.get("routes", 0),
            "stopCount": network.stats.get("stops", 0),
            "tripCount": network.stats.get("trips", 0),
            "connectionCount": network.stats.get("connections", 0),
        }
    )
    semantics = (
        "centered_departure_profile"
        if departure_window_minutes > 0
        else "fixed_arrival"
        if time_preference == "arrive"
        else "fixed_departure"
    )
    hours, minutes = clock.split(":", 1)
    return {
        "schemaVersion": "vigo.cli.route.v2",
        "version": first.get("version") or _PACKAGE_VERSION,
        "engine": _aggregate_stream_engines(envelopes),
        "query": {
            "semantics": semantics,
            "timeMinutes": int(hours) * 60 + int(minutes),
            "timePreference": time_preference,
            "routingPreference": routing_preference,
            "departureWindowMinutes": departure_window_minutes,
            "serviceDay": service_day,
            "serviceDate": service_date,
            "maxWalkKm": max_walk_km,
        },
        "routingStore": routing_store,
        "streetStore": str(network.street_store) if network.street_store else None,
        "preparation": {"elapsedMs": timing.preparation_ms},
        "rows": {
            "total": len(plan_rows),
            "ready": ready,
            "blocked": len(plan_rows) - ready,
        },
        "uniqueRoutingKeys": unique_routes,
        "elapsedMs": timing.routing_ms,
        "meanMsPerUniqueRoute": (
            round(timing.routing_ms / unique_routes, 4) if unique_routes else 0
        ),
        "timing": {
            "processToSummaryMs": timing.reported_ms,
            "preparationMs": timing.preparation_ms,
            "routingMs": timing.routing_ms,
            "outputMs": timing.serialization_ms,
        },
        "transport": "resident-ndjson",
    }


def _batch_timing(
    timing: _StreamTimingTotals,
    *,
    python_wall_ms: float,
    row_count: int,
    session_reused: bool,
) -> dict[str, Any]:
    return {
        "python_wall_ms": round(python_wall_ms, 3),
        "python_wall_ms_per_row": round(python_wall_ms / max(1, row_count), 4),
        "cli_process_to_summary_ms": timing.reported_ms,
        "cli_preparation_ms": timing.preparation_ms,
        "cli_routing_ms": timing.routing_ms,
        "cli_request_ms": timing.request_ms,
        "cli_output_ms": timing.serialization_ms,
        "python_process_io_overhead_ms": round(
            max(0, python_wall_ms - timing.reported_ms), 3
        ),
        "resident_session_reused": session_reused,
    }


def open_network(
    store: str | os.PathLike[str],
    *,
    street_store: str | os.PathLike[str] | None = None,
    cli: str | os.PathLike[str] | None = None,
) -> RoutingNetwork:
    """Validate VIGO SQLite stores and resolve the canonical standalone CLI."""

    store_path = _sqlite_path(store, "routing store")
    stats = _routing_store_stats(store_path)
    _validate_runtime_routing_store(store_path)
    street_path = (
        _sqlite_path(street_store, "street store") if street_store is not None else None
    )
    if street_path is not None:
        _validate_street_store(street_path)
    return RoutingNetwork(
        store=store_path,
        street_store=street_path,
        command=_resolve_cli(cli),
        stats=MappingProxyType(stats),
    )


def resolve_cli(
    cli: str | os.PathLike[str] | None = None,
) -> tuple[str, ...]:
    """Resolve the canonical VIGO command used by Python and the pass-through CLI."""

    return _resolve_cli(cli)


def route(
    network: RoutingNetwork | TransportNetwork,
    origin: PointInput,
    destination: PointInput,
    **options: Any,
) -> RoutePlan:
    """Run one exact request through the canonical SQLite CLI."""

    return route_batch(
        network,
        [{"id": "route", "origin": origin, "destination": destination}],
        **options,
    )[0]


def route_batch(
    network: RoutingNetwork | TransportNetwork,
    requests: Sequence[RouteRequest],
    *,
    time: str | float | dt.time | dt.datetime = "08:00",
    time_preference: str = "depart",
    routing_preference: str = "balanced",
    service_day: str = "weekday",
    service_date: str | dt.date | None = None,
    max_walk_km: float = 1.2,
    departure_window_minutes: int = 0,
) -> BatchRoutingResult:
    """Route an OD batch through a reusable, prepared VIGO process."""

    routing_network = _routing_network(network)
    if not requests:
        return BatchRoutingResult()
    if time_preference not in {"depart", "arrive"}:
        raise ValueError("time_preference must be 'depart' or 'arrive'")
    if routing_preference not in {"balanced", "fastest"}:
        raise ValueError("routing_preference must be 'balanced' or 'fastest'")
    if service_day not in {"weekday", "saturday", "sunday"}:
        raise ValueError("service_day must be weekday, saturday, or sunday")
    max_walk = _positive_number(max_walk_km, "max_walk_km")
    departure_window = _nonnegative_integer(
        departure_window_minutes,
        "departure_window_minutes",
    )
    if departure_window < 0:
        raise ValueError("departure_window_minutes must be non-negative")
    if time_preference == "arrive" and departure_window:
        raise ValueError("departure_window_minutes is only valid for depart searches")

    clock, inferred_date = _clock(time)
    selected_date = _required_service_date(service_date or inferred_date)
    stream_requests = [
        _stream_request(
            request,
            index,
            clock=clock,
            time_preference=time_preference,
            routing_preference=routing_preference,
            max_walk_km=max_walk,
            departure_window_minutes=departure_window,
        )
        for index, request in enumerate(requests)
    ]
    request_ids = [str(request["id"]) for request in stream_requests]
    if len(set(request_ids)) != len(request_ids):
        raise ValueError("route request ids must be unique")

    python_started = time_module.perf_counter()
    envelopes, session_reused = routing_network._sessions.exchange(
        routing_network,
        stream_requests,
        service_date=selected_date,
        service_day=service_day,
    )
    try:
        _validate_stream_results(envelopes, request_ids)
    except _ResidentProtocolError:
        routing_network._sessions.close()
        raise

    timing = _stream_timing(envelopes)
    plan_rows = _stream_plan_rows(envelopes, request_ids)
    summary = _stream_summary(
        routing_network,
        stream_requests,
        envelopes,
        plan_rows,
        timing,
        clock=clock,
        time_preference=time_preference,
        routing_preference=routing_preference,
        departure_window_minutes=departure_window,
        service_day=service_day,
        service_date=selected_date,
        max_walk_km=max_walk,
    )
    summary_view = MappingProxyType(summary)
    plans = [
        RoutePlan(
            request_id=request_id,
            _data=MappingProxyType(plan),
            _summary=summary_view,
            _timing=MappingProxyType(plan_timing),
        )
        for request_id, plan, plan_timing in plan_rows
    ]
    python_wall_ms = (time_module.perf_counter() - python_started) * 1000
    return BatchRoutingResult(
        plans,
        summary=summary,
        timing=_batch_timing(
            timing,
            python_wall_ms=python_wall_ms,
            row_count=len(plan_rows),
            session_reused=session_reused,
        ),
    )


def route_many(
    network: RoutingNetwork | TransportNetwork,
    origin: PointInput,
    destinations: Mapping[str, PointInput] | Sequence[PointInput],
    *,
    time: str | float | dt.time | dt.datetime = "08:00",
    service_day: str = "weekday",
    service_date: str | dt.date | None = None,
    max_walk_km: float = 1.2,
    horizon_minutes: float = 480,
    matrix_strategy: str = "shared",
) -> OneToManyResult:
    """Route one origin to many destinations with one resident Rust scan."""

    routing_network = _routing_network(network)
    if service_day not in {"weekday", "saturday", "sunday"}:
        raise ValueError("service_day must be weekday, saturday, or sunday")
    if matrix_strategy not in {"shared", "pairwise", "auto"}:
        raise ValueError("matrix_strategy must be shared, pairwise, or auto")
    _bounded_number(max_walk_km, "max_walk_km", 0.2, 5)
    _bounded_number(horizon_minutes, "horizon_minutes", 1, 2_880)
    clock, inferred_date = _clock(time)
    selected_date = _required_service_date(service_date or inferred_date)
    destination_rows = _destination_payloads(destinations)
    request = {
        "origin": _point_payload(origin, "origin"),
        "destinations": destination_rows,
        "horizonMinutes": float(horizon_minutes),
        "matrixStrategy": matrix_strategy,
    }
    arguments = [
        f"--time={clock}",
        "--time-preference=depart",
        "--routing-preference=fastest",
        f"--service-day={service_day}",
        f"--service-date={selected_date}",
        f"--max-walk={float(max_walk_km):g}",
        f"--horizon={float(horizon_minutes):g}",
        f"--matrix-strategy={matrix_strategy}",
    ]
    payload, python_wall_ms = _run_structured_command(
        routing_network,
        "one-to-many",
        request,
        arguments,
    )
    _validate_analytical_payload(
        payload,
        schema=_ONE_TO_MANY_SCHEMA,
        operator="one-to-many",
    )
    rows = payload.get("rows")
    if not isinstance(rows, list) or len(rows) != len(destination_rows):
        raise VigoCliError(
            "VIGO one-to-many result does not match the destination count"
        )
    expected_ids = [destination["id"] for destination in destination_rows]
    received_ids = [
        str(row.get("destinationId")) if isinstance(row, Mapping) else ""
        for row in rows
    ]
    if received_ids != expected_ids:
        raise VigoCliError(
            f"VIGO one-to-many destination ids do not match the request: {received_ids}"
        )
    return OneToManyResult(payload, python_wall_ms=python_wall_ms)


def isochrone(
    network: RoutingNetwork | TransportNetwork,
    origin: PointInput,
    *,
    cutoffs_minutes: Sequence[float] = (15, 30, 45, 60),
    time: str | float | dt.time | dt.datetime = "08:00",
    service_day: str = "weekday",
    service_date: str | dt.date | None = None,
    max_walk_km: float = 1.2,
    walk_speed_kph: float = 4.8,
    radius_km: float = 8,
    raster_size: int = 96,
    excluded_route_ids: Sequence[str] = (),
    scenario_overlay: Mapping[str, Any] | None = None,
) -> IsochroneResult:
    """Generate walk-transit-walk contours with VIGO's Rust range kernels."""

    routing_network = _routing_network(network)
    if routing_network.street_store is None:
        raise ValueError("isochrone generation requires a VIGO street store")
    if service_day not in {"weekday", "saturday", "sunday"}:
        raise ValueError("service_day must be weekday, saturday, or sunday")
    _bounded_number(max_walk_km, "max_walk_km", 0.2, 5)
    _bounded_number(walk_speed_kph, "walk_speed_kph", 1, 8)
    _bounded_number(radius_km, "radius_km", 1, 40)
    if int(raster_size) != raster_size or int(raster_size) not in {48, 64, 96, 128}:
        raise ValueError("raster_size must be 48, 64, 96, or 128")
    cutoffs = sorted({float(cutoff) for cutoff in cutoffs_minutes})
    if not cutoffs or any(
        not math.isfinite(cutoff) or cutoff < 5 or cutoff > 240 for cutoff in cutoffs
    ):
        raise ValueError("cutoffs_minutes must contain values between 5 and 240")
    excluded = tuple(
        dict.fromkeys(str(route_id).strip() for route_id in excluded_route_ids)
    )
    if any(not route_id for route_id in excluded):
        raise ValueError("excluded_route_ids cannot contain empty ids")
    if len(excluded) > 512:
        raise ValueError("excluded_route_ids is limited to 512 routes")
    if scenario_overlay is not None and not isinstance(scenario_overlay, Mapping):
        raise TypeError("scenario_overlay must be a mapping")

    clock, inferred_date = _clock(time)
    selected_date = _required_service_date(service_date or inferred_date)
    request: dict[str, Any] = {
        "origin": _point_payload(origin, "origin"),
        "cutoffsMinutes": cutoffs,
        "radiusKm": float(radius_km),
        "rasterSize": int(raster_size),
        "walkSpeedKph": float(walk_speed_kph),
        "excludedRouteIds": list(excluded),
    }
    if scenario_overlay is not None:
        request["scenarioOverlay"] = copy.deepcopy(dict(scenario_overlay))
    arguments = [
        f"--time={clock}",
        "--time-preference=depart",
        f"--service-day={service_day}",
        f"--service-date={selected_date}",
        f"--max-walk={float(max_walk_km):g}",
        f"--walk-speed={float(walk_speed_kph):g}",
        f"--radius={float(radius_km):g}",
        f"--raster-size={int(raster_size)}",
        f"--cutoffs={','.join(f'{cutoff:g}' for cutoff in cutoffs)}",
    ]
    payload, python_wall_ms = _run_structured_command(
        routing_network,
        "isochrone",
        request,
        arguments,
    )
    _validate_analytical_payload(
        payload,
        schema=_ISOCHRONE_SCHEMA,
        operator="isochrone",
    )
    surface = payload.get("surface")
    if (
        not isinstance(surface, Mapping)
        or surface.get("schemaVersion") != "vigo.street.network-raster.v1"
        or not isinstance(surface.get("values"), list)
        or len(surface["values"]) != int(raster_size) ** 2
    ):
        raise VigoCliError("VIGO isochrone result contains an invalid Rust surface")
    contours = payload.get("isochrones")
    if not isinstance(contours, Mapping) or contours.get("type") != "FeatureCollection":
        raise VigoCliError("VIGO isochrone result is missing GeoJSON contours")
    data = copy.deepcopy(payload)
    timing = dict(data.get("timing") or {})
    timing["python_wall_ms"] = round(python_wall_ms, 3)
    data["timing"] = timing
    return IsochroneResult(MappingProxyType(data))


def _run_structured_command(
    network: RoutingNetwork,
    command: str,
    request: Mapping[str, Any],
    arguments: Sequence[str],
) -> tuple[dict[str, Any], float]:
    started = time_module.perf_counter()
    with tempfile.TemporaryDirectory(prefix=f"vigo-python-{command}-") as temporary:
        request_path = Path(temporary) / "request.json"
        request_path.write_text(json.dumps(request), encoding="utf-8")
        invocation = [
            *network.command,
            command,
            f"--store={network.store}",
            f"--request={request_path}",
            *arguments,
        ]
        if network.street_store is not None:
            invocation.append(f"--street-store={network.street_store}")
        completed = subprocess.run(
            invocation,
            capture_output=True,
            text=True,
            check=False,
        )
    python_wall_ms = (time_module.perf_counter() - started) * 1000
    if completed.returncode != 0:
        message = (
            completed.stderr.strip() or completed.stdout.strip() or "unknown CLI error"
        )
        raise VigoCliError(f"VIGO {command} exited {completed.returncode}: {message}")
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise VigoCliError(f"VIGO {command} returned invalid JSON: {error}") from error
    if not isinstance(payload, dict):
        raise VigoCliError(f"VIGO {command} returned a non-object result")
    return payload, python_wall_ms


def _validate_analytical_payload(
    payload: Mapping[str, Any],
    *,
    schema: str,
    operator: str,
) -> None:
    if payload.get("schemaVersion") != schema:
        raise VigoCliError(
            f"Unsupported VIGO {operator} schema: {payload.get('schemaVersion')!r}"
        )
    engine = payload.get("engine")
    if not isinstance(engine, Mapping):
        raise VigoCliError(f"VIGO {operator} result is missing its engine descriptor")
    expected = {
        "operator": operator,
        "storage": "sqlite-persisted-resident-compiled",
        "persistentStore": "sqlite",
        "queryExecutor": "resident-active-service-kernel",
        "sqlRouteExecutor": False,
    }
    mismatches = {
        key: {"expected": expected_value, "actual": engine.get(key)}
        for key, expected_value in expected.items()
        if engine.get(key) != expected_value
    }
    if mismatches:
        raise VigoCliError(
            f"VIGO {operator} did not declare the resident Rust boundary: {mismatches}"
        )


def _bounded_number(value: Any, label: str, minimum: float, maximum: float) -> float:
    if isinstance(value, bool):
        raise TypeError(f"{label} must be numeric, not bool")
    try:
        number = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(
            f"{label} must be between {minimum:g} and {maximum:g}"
        ) from error
    if not math.isfinite(number) or number < minimum or number > maximum:
        raise ValueError(f"{label} must be between {minimum:g} and {maximum:g}")
    return number


def _positive_number(value: Any, label: str) -> float:
    message = f"{label} must be a positive finite number"
    if isinstance(value, bool):
        raise TypeError(f"{label} must be numeric, not bool")
    try:
        number = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(message) from error
    if not math.isfinite(number) or number <= 0:
        raise ValueError(message)
    return number


def _nonnegative_integer(value: Any, label: str) -> int:
    message = f"{label} must be a non-negative integer"
    if isinstance(value, bool):
        raise TypeError(f"{label} must be an integer, not bool")
    try:
        number = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(message) from error
    if not math.isfinite(number) or number < 0 or not number.is_integer():
        raise ValueError(message)
    return int(number)


def _required_service_date(value: str | dt.date | None) -> str:
    selected = _service_date(value)
    if selected is None:
        raise ValueError(
            "service_date is required for exact timetable routing; "
            "pass YYYY-MM-DD or use a datetime as time"
        )
    return selected


def _looks_like_point_mapping(value: Mapping[str, Any]) -> bool:
    return bool(
        {"stop_id", "stopId", "coordinate"} & value.keys()
        or ({"lat"} <= value.keys() and bool({"lon", "lng"} & value.keys()))
    )


def _destination_payloads(
    destinations: Mapping[str, PointInput] | Sequence[PointInput],
) -> list[dict[str, Any]]:
    candidates: list[tuple[str | None, Any]]
    if isinstance(destinations, Mapping) and not _looks_like_point_mapping(
        destinations
    ):
        candidates = [(str(key), point) for key, point in destinations.items()]
    elif isinstance(destinations, Sequence) and not isinstance(
        destinations, (str, bytes, bytearray)
    ):
        candidates = [(None, point) for point in destinations]
    else:
        candidates = [(None, destinations)]
    if not candidates:
        raise ValueError("destinations must not be empty")
    if len(candidates) > 256:
        raise ValueError("destinations is limited to 256 points")

    payloads = []
    for index, (mapped_id, candidate) in enumerate(candidates):
        point = candidate
        explicit_id = mapped_id
        if isinstance(candidate, Mapping) and "point" in candidate:
            point = candidate["point"]
            explicit_id = str(candidate.get("id") or explicit_id or "").strip() or None
        elif isinstance(candidate, Mapping) and candidate.get("id") is not None:
            explicit_id = str(candidate["id"]).strip() or None
        destination_id = explicit_id or (
            str(point).strip()
            if isinstance(point, str) and str(point).strip()
            else f"destination_{index + 1}"
        )
        payloads.append(
            {
                "id": destination_id,
                "point": _point_payload(point, f"destination {destination_id}"),
            }
        )
    ids = [payload["id"] for payload in payloads]
    if len(set(ids)) != len(ids):
        raise ValueError("destination ids must be unique")
    return payloads


def _point_payload(value: PointInput, label: str) -> dict[str, Any] | str:
    columns = _point_columns(value, label)
    stop_id = columns.get(f"{label}_stop_id")
    if stop_id:
        point: dict[str, Any] = {"stopId": stop_id}
        point_label = columns.get(f"{label}_name")
        if point_label:
            point["label"] = point_label
        return point
    return {
        "coordinate": [
            columns[f"{label}_lon"],
            columns[f"{label}_lat"],
        ],
        **({"label": columns[f"{label}_name"]} if columns.get(f"{label}_name") else {}),
    }


def _point_columns(value: PointInput, prefix: str) -> dict[str, Any]:
    if isinstance(value, str):
        if not value.strip():
            raise ValueError(f"{prefix} stop id is empty")
        return {f"{prefix}_stop_id": value.strip()}
    label = ""
    stop_id = ""
    coordinate: Sequence[Any] | None = None
    if isinstance(value, Mapping):
        stop_id = str(value.get("stop_id") or value.get("stopId") or "").strip()
        label = str(value.get("label") or value.get("name") or "").strip()
        if stop_id:
            return {f"{prefix}_stop_id": stop_id, f"{prefix}_name": label}
        if "coordinate" in value:
            coordinate = value["coordinate"]
        elif (
            value.get("lon", value.get("lng")) is not None
            and value.get("lat") is not None
        ):
            coordinate = (value.get("lon", value.get("lng")), value["lat"])
    elif isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        coordinate = value
    if not isinstance(coordinate, Sequence) or len(coordinate) < 2:
        raise ValueError(f"{prefix} must be a stop id or lon/lat coordinate")
    lon, lat = float(coordinate[0]), float(coordinate[1])
    if (
        not math.isfinite(lon)
        or not math.isfinite(lat)
        or not -180 <= lon <= 180
        or not -90 <= lat <= 90
    ):
        raise ValueError(f"{prefix} coordinate is invalid")
    return {f"{prefix}_lon": lon, f"{prefix}_lat": lat, f"{prefix}_name": label}


def _sqlite_path(value: str | os.PathLike[str], label: str) -> Path:
    path = Path(value).expanduser().resolve()
    if path.suffix.lower() != ".sqlite":
        raise ValueError(f"{label} must be a VIGO SQLite file")
    if not path.is_file():
        raise FileNotFoundError(f"{label} not found: {path}")
    return path


def _connect_read_only(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{quote(str(path), safe='/')}?mode=ro", uri=True)


def _validate_store_tables(
    path: Path, required: frozenset[str], label: str
) -> sqlite3.Connection:
    try:
        database = _connect_read_only(path)
        tables = {
            row[0]
            for row in database.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    except sqlite3.Error as error:
        raise ValueError(f"{path} is not a readable {label}: {error}") from error
    missing = sorted(required - tables)
    if missing:
        database.close()
        raise ValueError(f"{label} is missing required tables: {', '.join(missing)}")
    return database


def _routing_store_stats(path: Path) -> dict[str, int | str]:
    database = _validate_store_tables(path, _ROUTING_TABLES, "VIGO routing store")
    try:
        metadata = {
            key: _metadata_value(value)
            for key, value in database.execute("SELECT key, value FROM metadata")
        }
        schema = str(metadata.get("schemaVersion", ""))
        if not schema.startswith("vigo.routing.store."):
            raise ValueError(
                f"Unsupported VIGO routing-store schema: {schema or 'missing'}"
            )
        stats: dict[str, int | str] = {"schema_version": schema}
        for table, metadata_key in (
            ("stops", "stopCount"),
            ("routes", "routeCount"),
            ("trips", "tripCount"),
            ("connections", "connectionCount"),
        ):
            value = metadata.get(metadata_key)
            stats[table] = (
                int(value)
                if isinstance(value, (int, float))
                else int(
                    database.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                )
            )
        return stats
    finally:
        database.close()


def _validate_runtime_routing_store(path: Path) -> None:
    database = _validate_store_tables(path, _ROUTING_TABLES, "VIGO routing store")
    try:
        metadata = {
            key: _metadata_value(value)
            for key, value in database.execute("SELECT key, value FROM metadata")
        }
        if metadata.get("departureIndexState") != "deferred":
            raise ValueError(
                "VIGO routing store must use the sealed runtime state "
                "departureIndexState=deferred"
            )
        cover_index = database.execute(
            "SELECT 1 FROM sqlite_master "
            "WHERE type='index' AND name='connections_from_departure_cover'"
        ).fetchone()
        if cover_index is not None:
            raise ValueError(
                "VIGO routing store still contains the import-only "
                "connections_from_departure_cover index"
            )
    finally:
        database.close()


def _validate_street_store(path: Path) -> None:
    try:
        database = _connect_read_only(path)
    except sqlite3.Error as error:
        raise ValueError(f"{path} is not a readable VIGO street store: {error}") from error
    try:
        try:
            metadata = {
                key: _metadata_value(value)
                for key, value in database.execute("SELECT key, value FROM metadata")
            }
        except sqlite3.Error as error:
            raise ValueError(f"{path} is not a readable VIGO street store: {error}") from error
        schema = str(metadata.get("schemaVersion", ""))
        if not schema.startswith("vigo.street.store."):
            raise ValueError(
                f"Unsupported VIGO street-store schema: {schema or 'missing'}"
            )
        storage_layout = metadata.get("storageLayout")
        if storage_layout != _STREET_RUNTIME_LAYOUT:
            raise ValueError(
                "Unsupported VIGO street-store storage layout: "
                f"{storage_layout or 'missing'}; expected {_STREET_RUNTIME_LAYOUT}"
            )
        if not (path.parent / f"{path.name}{_STREET_WALK_SNAPSHOT_SUFFIX}").is_file():
            raise ValueError(
                f"{path} declares {_STREET_RUNTIME_LAYOUT} but its walk snapshot is missing"
            )
        try:
            drive_edges = int(metadata.get("driveEdgeCount", 0) or 0)
        except (TypeError, ValueError) as error:
            raise ValueError(f"{path} has invalid driveEdgeCount metadata") from error
        if drive_edges > 0 and not (
            path.parent / f"{path.name}{_STREET_DRIVE_SNAPSHOT_SUFFIX}"
        ).is_file():
            raise ValueError(
                f"{path} declares driving edges but its drive snapshot is missing"
            )
    finally:
        database.close()


def _metadata_value(value: str) -> Any:
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def _resolve_cli(explicit: str | os.PathLike[str] | None) -> tuple[str, ...]:
    if explicit is not None:
        command = _candidate_command(Path(explicit).expanduser())
        if command:
            return command
        raise FileNotFoundError(
            f"VIGO CLI or app runtime not found: {Path(explicit).expanduser()}"
        )

    candidates: list[Path] = []
    if os.environ.get("VIGO_CLI"):
        candidates.append(Path(os.environ["VIGO_CLI"]).expanduser())
    if os.environ.get("VIGO_APP"):
        candidates.append(Path(os.environ["VIGO_APP"]).expanduser())
    package_root = Path(__file__).resolve().parents[1]
    try:
        from .runtime import installed_runtime_candidates

        installed_candidates = list(installed_runtime_candidates())
    except (ImportError, OSError):
        installed_candidates = []
    candidates.extend(
        [
            package_root / "dist-cli" / "vigo.mjs",
            package_root / "release" / "VIGO-mac-arm64" / "vigo",
            package_root / "release" / "VIGO-mac-arm64" / "VIGO.app",
            *installed_candidates,
            Path("/Applications/VIGO.app"),
            Path.home() / "Applications" / "VIGO.app",
        ]
    )
    installed = shutil.which("vigo")
    if installed:
        candidates.append(Path(installed))
    for candidate in candidates:
        command = _candidate_command(candidate)
        if command:
            return command
    raise FileNotFoundError(
        "VIGO CLI not found. Install/extract VIGO.app, put the 'vigo' launcher on PATH, "
        "or pass cli=/path/to/vigo (VIGO_APP and VIGO_CLI are also supported)."
    )


def _candidate_command(candidate: Path) -> tuple[str, ...] | None:
    candidate = candidate.resolve()
    if candidate.suffix == ".app" and candidate.is_dir():
        resources = candidate / "Contents" / "Resources" / "bin"
        node = resources / "node"
        cli_module = resources / "vigo.mjs"
        if node.is_file() and os.access(node, os.X_OK) and cli_module.is_file():
            return (str(node), str(cli_module))
        return None
    if candidate.is_file() and os.access(candidate, os.X_OK):
        return (str(candidate),)
    return None


def _clock(
    value: str | float | dt.time | dt.datetime,
) -> tuple[str, dt.date | None]:
    inferred_date = value.date() if isinstance(value, dt.datetime) else None
    if isinstance(value, (dt.datetime, dt.time)):
        return f"{value.hour:02d}:{value.minute:02d}", inferred_date
    if isinstance(value, (int, float)):
        number = float(value)
        if isinstance(value, bool) or not math.isfinite(number):
            raise ValueError("numeric time must be minutes in [0, 1800)")
        minutes = int(number)
        if number < 0 or number >= 30 * 60:
            raise ValueError("numeric time must be minutes in [0, 1800)")
        return f"{minutes // 60:02d}:{minutes % 60:02d}", inferred_date
    text = str(value).strip()
    parts = text.split(":")
    if len(parts) != 2 or not all(part.isdigit() for part in parts):
        raise ValueError("time must use HH:MM")
    hours, minutes = map(int, parts)
    if hours >= 30 or minutes >= 60:
        raise ValueError("time must use HH:MM within the GTFS service day")
    return f"{hours:02d}:{minutes:02d}", inferred_date


def _service_date(value: str | dt.date | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, dt.datetime):
        return value.date().isoformat()
    if isinstance(value, dt.date):
        return value.isoformat()
    text = str(value).strip()
    try:
        return dt.date.fromisoformat(text).isoformat()
    except ValueError as error:
        raise ValueError("service_date must use YYYY-MM-DD") from error


def _validate_engine_descriptor(engine: Any) -> None:
    if not isinstance(engine, Mapping):
        raise _ResidentProtocolError("VIGO CLI result is missing its engine descriptor")
    expected_engine_boundary = {
        "storage": "sqlite-persisted-resident-compiled",
        "persistentStore": "sqlite",
        "queryExecutor": "resident-active-service-kernel",
        "sqlRouteExecutor": False,
    }
    mismatches = {
        key: {"expected": expected, "actual": engine.get(key)}
        for key, expected in expected_engine_boundary.items()
        if engine.get(key) != expected
    }
    if mismatches:
        raise _ResidentProtocolError(
            "VIGO CLI result did not declare the persisted-store/resident-query "
            f"boundary: {mismatches}"
        )


def _blocked_payload(request_id: str, reason: Any) -> dict[str, Any]:
    detail = str(reason or "No canonical route was returned.")
    return {
        "id": f"blocked-{request_id}",
        "status": "blocked",
        "travelMode": "transit",
        "title": "No route",
        "detail": detail,
        "departMinutes": 0,
        "durationMinutes": 0,
        "waitMinutes": 0,
        "walkMinutes": 0,
        "rideMinutes": 0,
        "transfers": 0,
        "origin": {},
        "destination": {},
        "legs": [],
        "diagnostics": {
            "algorithm": "connection_scan_sqlite",
            "storage": "sqlite-only",
            "methodState": "failed",
            "failureCode": "canonical_result_missing",
            "failureCategory": "internal_error",
            "failure": {
                "code": "canonical_result_missing",
                "category": "internal_error",
                "message": detail,
                "retryable": False,
            },
        },
    }
