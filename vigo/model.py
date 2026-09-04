from __future__ import annotations

import copy
import csv
import datetime as dt
import json
import os
import queue
import re
import subprocess
import tempfile
import threading
from collections import deque
from collections.abc import Mapping, Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any, TypeVar

from .runtime import (
    API_VERSION,
    CITY_FORMAT_VERSION,
    RESULT_SCHEMA_VERSION,
    VERSION,
    RuntimeInfo,
    VigoError,
    VigoTimeoutError,
    resolve_runtime,
    run_json,
)

Point = str | Sequence[float] | Mapping[str, Any]
PointSet = Point | Mapping[str, Point] | Sequence[Point]
_CityT = TypeVar("_CityT", bound="City")

_SERVICE_DAYS = {0: "weekday", 1: "weekday", 2: "weekday", 3: "weekday", 4: "weekday", 5: "saturday", 6: "sunday"}

def _job_worker_count() -> int:
    cpu_limit = max(1, min(4, (os.cpu_count() or 2) // 2))
    try:
        physical_bytes = int(os.sysconf("SC_PHYS_PAGES")) * int(os.sysconf("SC_PAGE_SIZE"))
    except (AttributeError, OSError, TypeError, ValueError):
        return 1
    memory_limit = max(1, min(4, physical_bytes // (6 * 1024**3)))
    return min(cpu_limit, memory_limit)


_EXECUTOR = ThreadPoolExecutor(max_workers=_job_worker_count(), thread_name_prefix="vigo")


class InvalidQuery(VigoError, ValueError):
    """A Query is malformed or internally contradictory."""


@dataclass(frozen=True, slots=True)
class Support:
    supported: bool
    reason: str | None = None
    available: tuple[str, ...] = ()


class UnsupportedQuery(VigoError):
    """A valid Query cannot run in the selected VIGO context."""

    def __init__(self, support: Support) -> None:
        self.support = support
        super().__init__(support.reason or "unsupported query")


@dataclass(frozen=True, slots=True)
class Route:
    origin: Point
    destination: Point
    waypoints: Sequence[Point] = ()
    mode: str = "transit"
    depart_at: str | dt.time | dt.datetime | None = None
    arrive_by: str | dt.time | dt.datetime | None = None
    service_date: str | dt.date | None = None
    max_walk_km: float = 1.2
    departure_window_minutes: int = 0
    objective: str = "earliest_arrival"


@dataclass(frozen=True, slots=True)
class Matrix:
    origins: PointSet
    destinations: PointSet
    mode: str = "transit"
    depart_at: str | dt.time | dt.datetime | None = None
    arrive_by: str | dt.time | dt.datetime | None = None
    service_date: str | dt.date | None = None
    max_walk_km: float = 1.2
    horizon_minutes: float = 480
    walk_speed_kph: float = 4.8
    max_distance_km: float | None = None
    objective: str = "earliest_arrival"


@dataclass(frozen=True, slots=True)
class Reach:
    origin: Point
    depart_at: str | dt.time | dt.datetime = "08:00"
    service_date: str | dt.date | None = None
    cutoffs_minutes: Sequence[float] = (15, 30, 45, 60)
    max_walk_km: float = 1.2
    walk_speed_kph: float = 4.8
    extent_radius_km: float = 8
    raster_size: int = 96


Query = Route | Matrix | Reach


def _immutable(value: Mapping[str, Any]) -> Mapping[str, Any]:
    return MappingProxyType(copy.deepcopy(dict(value)))


def _date_text(value: str | dt.date | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, dt.datetime):
        value = value.date()
    if isinstance(value, dt.date):
        return value.isoformat()
    try:
        return dt.date.fromisoformat(str(value)).isoformat()
    except ValueError as error:
        raise InvalidQuery("service_date must be YYYY-MM-DD") from error


def _clock(value: str | dt.time | dt.datetime | None, service_date: str | dt.date | None) -> tuple[str, str]:
    selected_date = _date_text(service_date)
    if isinstance(value, dt.datetime):
        selected_date = selected_date or value.date().isoformat()
        clock = value.strftime("%H:%M")
    elif isinstance(value, dt.time):
        clock = value.strftime("%H:%M")
    elif isinstance(value, str):
        match = re.fullmatch(r"([01]?\d|2[0-3]):([0-5]\d)", value)
        if match is None:
            raise InvalidQuery("time must use HH:MM")
        clock = f"{int(match.group(1)):02d}:{match.group(2)}"
    else:
        raise InvalidQuery("a departure or arrival time is required")
    if selected_date is None:
        raise InvalidQuery("service_date is required unless time is a datetime")
    return clock, selected_date


def _service_day(service_date: str) -> str:
    return _SERVICE_DAYS[dt.date.fromisoformat(service_date).weekday()]


def _point_payload(value: Point) -> Any:
    if isinstance(value, str):
        if not value.strip():
            raise InvalidQuery("stop ids cannot be empty")
        return value.strip()
    if isinstance(value, Mapping):
        return copy.deepcopy(dict(value))
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)) and len(value) == 2:
        longitude, latitude = float(value[0]), float(value[1])
        if not -180 <= longitude <= 180 or not -90 <= latitude <= 90:
            raise InvalidQuery("coordinates must be [longitude, latitude]")
        return {"coordinate": [longitude, latitude]}
    raise InvalidQuery("points must be stop ids, coordinate pairs, or point mappings")


def _point_rows(values: PointSet, label: str) -> list[dict[str, Any]]:
    point_keys = {"stopId", "stop_id", "coordinate"}
    coordinate_keys = "lat" in values and ("lon" in values or "lng" in values) if isinstance(values, Mapping) else False
    if isinstance(values, Mapping) and not (point_keys & values.keys()) and not coordinate_keys:
        rows = [(str(key), point) for key, point in values.items()]
    elif isinstance(values, (str, Mapping)):
        rows = [(label, values)]
    elif isinstance(values, Sequence):
        if len(values) == 2 and all(isinstance(item, (int, float)) for item in values):
            rows = [(label, values)]
        else:
            rows = [(f"{label}_{index + 1}", point) for index, point in enumerate(values)]
    else:
        rows = [(label, values)]
    if not rows:
        raise InvalidQuery(f"{label}s cannot be empty")
    return [{"id": row_id, "point": _point_payload(point)} for row_id, point in rows]


def _json_command(
    runtime: RuntimeInfo,
    city: Path,
    name: str,
    request: Mapping[str, Any],
    arguments: Sequence[str],
    timeout: float,
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix=f"vigo-{name}-") as temporary:
        request_path = Path(temporary) / "request.json"
        request_path.write_text(json.dumps(request, allow_nan=False), encoding="utf-8")
        output = run_json(
            runtime,
            [name, f"--city={city}", f"--request={request_path}", *arguments],
            timeout=timeout,
        )
    try:
        payload = json.loads(output)
    except json.JSONDecodeError as error:
        raise VigoError(f"VIGO {name} returned invalid JSON") from error
    if not isinstance(payload, dict):
        raise VigoError(f"VIGO {name} returned an invalid Result")
    return payload


class _RouteStream:
    def __init__(self, city: City, service_date: str, timeout: float) -> None:
        self._timeout = timeout
        self._lock = threading.Lock()
        self._sequence = 0
        self._responses: queue.Queue[dict[str, Any] | BaseException] = queue.Queue()
        self._errors: deque[str] = deque(maxlen=40)
        self._process = subprocess.Popen(
            [
                *city.runtime.command,
                "_route-stream",
                f"--city={city.path}",
                f"--service-date={service_date}",
                f"--service-day={_service_day(service_date)}",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        self._reader = threading.Thread(target=self._read, name="vigo-route-reader", daemon=True)
        self._error_reader = threading.Thread(target=self._read_errors, name="vigo-route-errors", daemon=True)
        self._reader.start()
        self._error_reader.start()

    def _read_errors(self) -> None:
        assert self._process.stderr is not None
        for line in self._process.stderr:
            if text := line.strip():
                self._errors.append(text)

    def _read(self) -> None:
        assert self._process.stdout is not None
        try:
            for line in self._process.stdout:
                try:
                    value = json.loads(line)
                    if not isinstance(value, dict):
                        raise TypeError("route stream returned a non-object")
                    self._responses.put(value)
                except (json.JSONDecodeError, TypeError) as error:
                    self._responses.put(error)
                    return
        finally:
            if self._process.poll() is not None:
                detail = "\n".join(self._errors)
                self._responses.put(VigoError(detail or "VIGO route process stopped"))

    def route(self, request: Mapping[str, Any]) -> dict[str, Any]:
        with self._lock:
            if self._process.poll() is not None:
                raise VigoError("VIGO route process is not running")
            self._sequence += 1
            request_id = f"python_{self._sequence}"
            message = {"id": request_id, **copy.deepcopy(dict(request))}
            assert self._process.stdin is not None
            self._process.stdin.write(json.dumps(message, allow_nan=False) + "\n")
            self._process.stdin.flush()
            try:
                response = self._responses.get(timeout=self._timeout)
            except queue.Empty as error:
                self.close()
                raise VigoTimeoutError(f"VIGO Route exceeded {self._timeout:g} seconds") from error
            if isinstance(response, BaseException):
                raise response
            if response.get("id") != request_id:
                self.close()
                raise VigoError("VIGO returned a Route for the wrong request")
            if response.get("status") == "error":
                raise VigoError(str(response.get("error", {}).get("message", "Route failed")))
            return response

    def close(self) -> None:
        if self._process.poll() is not None:
            return
        if self._process.stdin:
            self._process.stdin.close()
        try:
            self._process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            self._process.terminate()
            try:
                self._process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                self._process.kill()


@dataclass(frozen=True, slots=True)
class Result:
    kind: str
    _payload: Mapping[str, Any] = field(repr=False)
    city_revision_id: str | None
    scenario_name: str | None = None

    def __post_init__(self) -> None:
        if self._payload.get("resultSchemaVersion") != RESULT_SCHEMA_VERSION:
            raise VigoError("VIGO returned an unsupported Result schema")
        if self.status not in {"ready", "blocked"}:
            raise VigoError(f"VIGO returned an invalid Result status: {self.status}")

    @property
    def status(self) -> str:
        value = self._payload.get("status")
        if value == "ok":
            return str(self._payload.get("routingStatus", "ready"))
        return str(value or "ready")

    @property
    def warnings(self) -> tuple[Any, ...]:
        value = self._payload.get("warnings", ())
        return tuple(copy.deepcopy(value)) if isinstance(value, Sequence) else ()

    @property
    def timing(self) -> dict[str, Any]:
        return copy.deepcopy(dict(self._payload.get("timing") or {}))

    @property
    def query(self) -> dict[str, Any]:
        return copy.deepcopy(dict(self._payload.get("query") or {}))

    @property
    def value(self) -> Any:
        if self.kind == "route":
            return copy.deepcopy(self._payload.get("result", self._payload.get("plan")))
        if self.kind == "matrix":
            return copy.deepcopy(self._payload.get("rows", []))
        if self.kind == "reach":
            return copy.deepcopy(self._payload.get("surface"))
        return copy.deepcopy(self._payload.get("change"))

    @property
    def rows(self) -> tuple[dict[str, Any], ...]:
        return tuple(copy.deepcopy(row) for row in self._payload.get("rows", ()))

    @property
    def duration_minutes(self) -> float | None:
        route = self.value if self.kind == "route" else None
        value = route.get("durationMinutes") if isinstance(route, Mapping) else None
        return None if value is None else float(value)

    @property
    def legs(self) -> tuple[dict[str, Any], ...]:
        route = self.value if self.kind == "route" else None
        return tuple(copy.deepcopy(leg) for leg in route.get("legs", ())) if isinstance(route, Mapping) else ()

    @property
    def record(self) -> dict[str, Any]:
        return {
            "cityRevisionId": self.city_revision_id,
            "scenario": self.scenario_name,
            "query": self.query,
            "status": self.status,
            "warnings": list(self.warnings),
            "timing": self.timing,
        }

    def to_dict(self) -> dict[str, Any]:
        return copy.deepcopy(dict(self._payload))

    def to_json(self, *, indent: int | None = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, allow_nan=False)

    def to_geojson(self) -> dict[str, Any]:
        if self.kind == "reach":
            return copy.deepcopy(
                self._payload.get("contours")
                or {"type": "FeatureCollection", "features": []}
            )
        if self.kind == "route":
            features = []
            for index, leg in enumerate(self.legs):
                coordinates = leg.get("coordinates")
                if isinstance(coordinates, list) and len(coordinates) >= 2:
                    features.append({
                        "type": "Feature",
                        "properties": {"index": index, "type": leg.get("type")},
                        "geometry": {"type": "LineString", "coordinates": coordinates},
                    })
            return {"type": "FeatureCollection", "features": features}
        raise TypeError("only Route and Reach Results have GeoJSON")

    def export(self, path: str | os.PathLike[str]) -> Path:
        destination = Path(path).expanduser().resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.suffix.lower() in {".geojson", ".json"}:
            value = self.to_geojson() if destination.suffix.lower() == ".geojson" else self.to_dict()
            destination.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        elif destination.suffix.lower() == ".csv" and self.kind == "matrix":
            rows = list(self.rows)
            fields = list(dict.fromkeys(key for row in rows for key in row))
            with destination.open("w", newline="", encoding="utf-8") as stream:
                writer = csv.DictWriter(stream, fieldnames=fields)
                writer.writeheader()
                writer.writerows(rows)
        else:
            raise ValueError("use .json, .geojson, or Matrix .csv")
        return destination

    def compare(self, other: Result) -> Result:
        return compare(self, other)


class Job:
    def __init__(self, future: Future[Any]) -> None:
        self._future = future

    @property
    def status(self) -> str:
        if self._future.cancelled():
            return "cancelled"
        if self._future.running():
            return "running"
        if not self._future.done():
            return "queued"
        return "error" if self._future.exception() else "ready"

    def wait(self, timeout: float | None = None) -> Any:
        return self._future.result(timeout=timeout)

    def cancel(self) -> bool:
        return self._future.cancel()


class City:
    """One immutable VIGO City revision opened for queries."""

    def __init__(
        self,
        path: Path,
        runtime: RuntimeInfo,
        manifest: Mapping[str, Any],
        *,
        timeout: float = 120.0,
    ) -> None:
        self.path = path
        self.runtime = runtime
        self._manifest = _immutable(manifest)
        self.timeout = float(timeout)
        if self.timeout <= 0:
            raise ValueError("timeout must be positive")
        self._streams: dict[str, _RouteStream] = {}
        self._lock = threading.Lock()

    @property
    def name(self) -> str:
        return str(self._manifest.get("name") or self.path.name)

    @property
    def revision_id(self) -> str | None:
        value = self._manifest.get("revisionId") or self._manifest.get("createdAt")
        return None if value is None else str(value)

    @property
    def built_at(self) -> str | None:
        value = self._manifest.get("builtAt") or self._manifest.get("createdAt")
        return None if value is None else str(value)

    @property
    def sources(self) -> dict[str, Any]:
        value = self._manifest.get("sources") or self._manifest.get("inputs") or {}
        return copy.deepcopy(dict(value))

    @property
    def capabilities(self) -> dict[str, Any]:
        return copy.deepcopy(dict(self.runtime.capabilities))

    @property
    def details(self) -> dict[str, Any]:
        return copy.deepcopy(dict(self._manifest))

    def route(self, origin: Point, destination: Point, **options: Any) -> Result:
        return self.run(Route(origin, destination, **options))

    def matrix(self, origins: PointSet, destinations: PointSet, **options: Any) -> Result:
        return self.run(Matrix(origins, destinations, **options))

    def reach(self, origin: Point, **options: Any) -> Result:
        return self.run(Reach(origin, **options))

    def run(self, query: Query | Sequence[Query]) -> Result | list[Result]:
        if isinstance(query, Sequence) and not isinstance(query, (str, bytes)):
            return [self._execute(item, None) for item in query]
        if not isinstance(query, (Route, Matrix, Reach)):
            raise InvalidQuery("query must be Route, Matrix, or Reach")
        return self._execute(query, None)

    def supports(self, query: Query) -> Support:
        return self._support(query, None)

    def _support(self, query: Query, scenario: Scenario | None) -> Support:
        if not isinstance(query, (Route, Matrix, Reach)):
            raise InvalidQuery("query must be Route, Matrix, or Reach")
        if isinstance(query, (Route, Matrix)):
            if query.mode not in {"transit", "walk", "drive"}:
                raise InvalidQuery("mode must be transit, walk, or drive")
            if query.objective != "earliest_arrival":
                return Support(False, "objective", ("earliest_arrival",))
        if isinstance(query, Route) and query.depart_at is not None and query.arrive_by is not None:
            raise InvalidQuery("choose depart_at or arrive_by, not both")
        if isinstance(query, Matrix) and query.depart_at is not None and query.arrive_by is not None:
            raise InvalidQuery("choose depart_at or arrive_by, not both")
        if isinstance(query, Matrix) and query.arrive_by is not None:
            return Support(False, "arrive_by_matrix", ("depart_at",))
        if scenario is None:
            return Support(True)

        planned = bool(scenario.services or scenario.without_routes)
        live = scenario.live is not None
        traffic = scenario.traffic is not None
        if sum((planned, live, traffic)) > 1:
            return Support(False, "scenario_state_combination")
        if planned:
            return Support(True) if isinstance(query, Reach) else Support(
                False,
                "planned_transit_route" if isinstance(query, Route) else "planned_transit_matrix",
                ("reach",),
            )
        if live:
            return Support(False, "live_transit_python", ("studio",))
        if traffic:
            if isinstance(query, Route) and query.mode == "drive":
                return Support(True)
            if isinstance(query, Matrix) and query.mode == "drive":
                return Support(True)
            return Support(False, "traffic_context", ("drive_route", "drive_matrix"))
        return Support(True)

    def submit(self, query: Query | Sequence[Query]) -> Job:
        return Job(_EXECUTOR.submit(self.run, query))

    def scenario(
        self,
        name: str,
        *,
        services: Sequence[Mapping[str, Any]] = (),
        without_routes: Sequence[str] = (),
        live: Mapping[str, Any] | None = None,
        traffic: Mapping[str, Any] | None = None,
        expires_at: dt.datetime | None = None,
    ) -> Scenario:
        return Scenario(
            self,
            name,
            services=services,
            without_routes=without_routes,
            live=live,
            traffic=traffic,
            expires_at=expires_at,
        )

    def _stream(self, service_date: str) -> _RouteStream:
        with self._lock:
            stream = self._streams.get(service_date)
            if stream is None:
                stream = _RouteStream(self, service_date, self.timeout)
                self._streams[service_date] = stream
            return stream

    def _execute(self, query: Query, scenario: Scenario | None) -> Result:
        if scenario is not None:
            scenario._check()
        support = self._support(query, scenario)
        if not support.supported:
            raise UnsupportedQuery(support)
        if isinstance(query, Route):
            return self._route(query, scenario)
        if isinstance(query, Matrix):
            return self._matrix(query, scenario)
        if isinstance(query, Reach):
            return self._reach(query, scenario)
        raise TypeError("query must be Route, Matrix, or Reach")

    def _route(self, query: Route, scenario: Scenario | None) -> Result:
        selected_time = query.arrive_by if query.arrive_by is not None else query.depart_at
        clock, service_date = _clock(selected_time, query.service_date)
        request = {
            "origin": _point_payload(query.origin),
            "destination": _point_payload(query.destination),
            "waypoints": [_point_payload(waypoint) for waypoint in query.waypoints],
            "mode": query.mode,
            "time": clock,
            "timePreference": "arrive" if query.arrive_by is not None else "depart",
            "objective": query.objective,
            "maxWalkKm": query.max_walk_km,
            "departureWindowMinutes": query.departure_window_minutes,
            **({"traffic": dict(scenario.traffic)} if scenario and scenario.traffic else {}),
        }
        if query.mode == "transit" and scenario is None and not query.waypoints:
            response = self._stream(service_date).route(request)
            payload = {
                "schemaVersion": "vigo.result.route.v1",
                "productVersion": self.runtime.product_version,
                "apiVersion": self.runtime.api_version,
                "resultSchemaVersion": RESULT_SCHEMA_VERSION,
                "kind": "route",
                "status": response.get("routingStatus", "ready"),
                "query": request,
                "result": response.get("plan"),
                "warnings": [],
                "timing": response.get("timing", {}),
            }
        else:
            payload = _json_command(
                self.runtime,
                self.path,
                "route",
                request,
                [
                    f"--mode={query.mode}",
                    f"--time={clock}",
                    f"--time-preference={'arrive' if query.arrive_by is not None else 'depart'}",
                    f"--service-date={service_date}",
                    f"--max-walk={query.max_walk_km:g}",
                    f"--departure-window={query.departure_window_minutes}",
                    f"--objective={query.objective}",
                ],
                self.timeout,
            )
        return Result("route", _immutable(payload), self.revision_id, scenario.name if scenario else None)

    def _matrix(self, query: Matrix, scenario: Scenario | None) -> Result:
        clock, service_date = _clock(query.depart_at or "08:00", query.service_date)
        request = {
            "origins": _point_rows(query.origins, "origin"),
            "destinations": _point_rows(query.destinations, "destination"),
            "mode": query.mode,
            "objective": query.objective,
            "horizonMinutes": query.horizon_minutes,
            "walkSpeedKph": query.walk_speed_kph,
            **({"maxDistanceKm": query.max_distance_km} if query.max_distance_km is not None else {}),
            **({"traffic": dict(scenario.traffic)} if scenario and scenario.traffic else {}),
        }
        payload = _json_command(
            self.runtime,
            self.path,
            "matrix",
            request,
            [
                f"--mode={query.mode}",
                f"--time={clock}",
                f"--service-date={service_date}",
                f"--max-walk={query.max_walk_km:g}",
                f"--horizon={query.horizon_minutes:g}",
                f"--objective={query.objective}",
            ],
            self.timeout,
        )
        return Result("matrix", _immutable(payload), self.revision_id, scenario.name if scenario else None)

    def _reach(self, query: Reach, scenario: Scenario | None) -> Result:
        clock, service_date = _clock(query.depart_at, query.service_date)
        request = {
            "origin": _point_payload(query.origin),
            "cutoffsMinutes": list(query.cutoffs_minutes),
            "extentRadiusKm": query.extent_radius_km,
            "rasterSize": query.raster_size,
            "walkSpeedKph": query.walk_speed_kph,
            **(
                {
                    "scenario": {
                        "id": "scenario",
                        "name": scenario.name,
                        "services": [dict(service) for service in scenario.services],
                        "excludedRouteIds": list(scenario.without_routes),
                    }
                }
                if scenario and (scenario.services or scenario.without_routes)
                else {}
            ),
        }
        payload = _json_command(
            self.runtime,
            self.path,
            "reach",
            request,
            [
                f"--time={clock}",
                f"--service-date={service_date}",
                f"--max-walk={query.max_walk_km:g}",
                f"--walk-speed={query.walk_speed_kph:g}",
                f"--extent-radius={query.extent_radius_km:g}",
                f"--raster-size={query.raster_size}",
                f"--cutoffs={','.join(f'{value:g}' for value in query.cutoffs_minutes)}",
            ],
            self.timeout,
        )
        return Result("reach", _immutable(payload), self.revision_id, scenario.name if scenario else None)

    def close(self) -> None:
        """Release resident runtime resources held by this City."""
        with self._lock:
            streams = list(self._streams.values())
            self._streams.clear()
        for stream in streams:
            stream.close()

    def __enter__(self: _CityT) -> _CityT:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


@dataclass(frozen=True, slots=True, init=False)
class Scenario:
    """An immutable set of changes tied to exactly one City revision."""

    city: City
    city_revision_id: str | None
    name: str
    services: tuple[Mapping[str, Any], ...]
    without_routes: tuple[str, ...]
    live: Mapping[str, Any] | None
    traffic: Mapping[str, Any] | None
    expires_at: dt.datetime | None

    def __init__(
        self,
        city: City,
        name: str,
        *,
        services: Sequence[Mapping[str, Any]] = (),
        without_routes: Sequence[str] = (),
        live: Mapping[str, Any] | None = None,
        traffic: Mapping[str, Any] | None = None,
        expires_at: dt.datetime | None = None,
    ) -> None:
        if not name.strip():
            raise ValueError("Scenario name cannot be empty")
        if isinstance(services, (str, bytes)) or not isinstance(services, Sequence):
            raise TypeError("services must be a sequence of mappings")
        if any(not isinstance(service, Mapping) for service in services):
            raise TypeError("every service must be a mapping")
        if isinstance(without_routes, (str, bytes)) or not isinstance(without_routes, Sequence):
            raise TypeError("without_routes must be a sequence of route ids")
        route_ids = tuple(str(route_id).strip() for route_id in without_routes)
        if any(not route_id for route_id in route_ids):
            raise ValueError("without_routes cannot contain an empty route id")
        for label, value in (("live", live), ("traffic", traffic)):
            if value is not None and not isinstance(value, Mapping):
                raise TypeError(f"{label} must be a mapping")
        if expires_at is not None and not isinstance(expires_at, dt.datetime):
            raise TypeError("expires_at must be a datetime")
        object.__setattr__(self, "city", city)
        object.__setattr__(self, "city_revision_id", city.revision_id)
        object.__setattr__(self, "name", name.strip())
        object.__setattr__(self, "services", tuple(_immutable(service) for service in services))
        object.__setattr__(self, "without_routes", tuple(dict.fromkeys(route_ids)))
        object.__setattr__(self, "live", _immutable(live) if live is not None else None)
        object.__setattr__(self, "traffic", _immutable(traffic) if traffic is not None else None)
        object.__setattr__(self, "expires_at", expires_at)

    def _check(self) -> None:
        if self.city.revision_id != self.city_revision_id:
            raise VigoError("Scenario belongs to a different City revision")
        if self.expires_at is not None:
            now = dt.datetime.now(tz=self.expires_at.tzinfo)
            if now >= self.expires_at:
                raise VigoError(f"Scenario {self.name!r} has expired")

    def route(self, origin: Point, destination: Point, **options: Any) -> Result:
        return self.run(Route(origin, destination, **options))

    def matrix(self, origins: PointSet, destinations: PointSet, **options: Any) -> Result:
        return self.run(Matrix(origins, destinations, **options))

    def reach(self, origin: Point, **options: Any) -> Result:
        return self.run(Reach(origin, **options))

    def run(self, query: Query | Sequence[Query]) -> Result | list[Result]:
        if isinstance(query, Sequence) and not isinstance(query, (str, bytes)):
            return [self.city._execute(item, self) for item in query]
        if not isinstance(query, (Route, Matrix, Reach)):
            raise InvalidQuery("query must be Route, Matrix, or Reach")
        return self.city._execute(query, self)

    def supports(self, query: Query) -> Support:
        return self.city._support(query, self)

    def submit(self, query: Query | Sequence[Query]) -> Job:
        return Job(_EXECUTOR.submit(self.run, query))


def open(
    city: str | os.PathLike[str],
    *,
    runtime: RuntimeInfo | str | os.PathLike[str] | Sequence[str] | None = None,
    timeout: float = 120.0,
) -> City:
    """Open one complete VIGO City directory."""

    path = Path(city).expanduser().resolve()
    manifest_path = path / "network.json"
    routing = path / "routing" / "project.sqlite"
    streets = path / "osm" / "street-index.sqlite"
    if not manifest_path.is_file() or not routing.is_file() or not streets.is_file():
        raise VigoError("City must contain network.json, routing/project.sqlite, and osm/street-index.sqlite")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise VigoError("City metadata is unreadable") from error
    if (
        manifest.get("schemaVersion") != "vigo.city.v1"
        or manifest.get("cityFormatVersion") != CITY_FORMAT_VERSION
    ):
        raise VigoError(f"VIGO Python requires City format {CITY_FORMAT_VERSION}")
    return City(path, resolve_runtime(runtime), manifest, timeout=timeout)


def build(
    output: str | os.PathLike[str],
    *,
    gtfs: str | os.PathLike[str] | Sequence[str | os.PathLike[str]],
    osm: str | os.PathLike[str],
    replace: bool = False,
    runtime: RuntimeInfo | str | os.PathLike[str] | Sequence[str] | None = None,
    timeout: float = 1_800.0,
) -> City:
    """Build one City directly from GTFS and OSM, then open it."""

    output_path = Path(output).expanduser().resolve()
    sources = [gtfs] if isinstance(gtfs, (str, os.PathLike)) else list(gtfs)
    if not sources:
        raise ValueError("at least one GTFS ZIP is required")
    runtime_info = resolve_runtime(runtime)
    arguments = [
        "build",
        *(f"--gtfs={Path(source).expanduser().resolve()}" for source in sources),
        f"--osm={Path(osm).expanduser().resolve()}",
        f"--output={output_path}",
        *( ["--replace"] if replace else [] ),
    ]
    run_json(runtime_info, arguments, timeout=timeout)
    return open(output_path, runtime=runtime_info)


def compare(before: Result, after: Result) -> Result:
    """Compare two compatible Results without rerunning either Query."""

    if not isinstance(before, Result) or not isinstance(after, Result):
        raise TypeError("compare requires two Results")
    if before.kind != after.kind or before.kind not in {"route", "matrix", "reach"}:
        raise ValueError("Results must come from the same Query family")
    if before.kind == "route":
        left = before.value or {}
        right = after.value or {}
        left_duration = left.get("durationMinutes")
        right_duration = right.get("durationMinutes")
        change = {
            "beforeStatus": before.status,
            "afterStatus": after.status,
            "durationChangeMinutes": (
                float(right_duration) - float(left_duration)
                if left_duration is not None and right_duration is not None
                else None
            ),
            "transferChange": int(right.get("transfers", 0)) - int(left.get("transfers", 0)),
        }
    elif before.kind == "matrix":
        key = lambda row: (row.get("originId", row.get("originIndex")), row.get("destinationId", row.get("destinationIndex")))
        left_rows = {key(row): row for row in before.rows}
        changes = []
        for row in after.rows:
            previous = left_rows.get(key(row))
            if not previous or previous.get("durationMinutes") is None or row.get("durationMinutes") is None:
                continue
            changes.append(float(row["durationMinutes"]) - float(previous["durationMinutes"]))
        change = {
            "comparablePairs": len(changes),
            "fasterPairs": sum(value < 0 for value in changes),
            "slowerPairs": sum(value > 0 for value in changes),
            "unchangedPairs": sum(value == 0 for value in changes),
            "meanChangeMinutes": sum(changes) / len(changes) if changes else None,
        }
    else:
        left_values = before.to_dict().get("surface", {}).get("values", [])
        right_values = after.to_dict().get("surface", {}).get("values", [])
        if len(left_values) != len(right_values):
            raise ValueError("Reach Results must use the same grid")
        changes = [
            float(right) - float(left)
            for left, right in zip(left_values, right_values)
            if isinstance(left, (int, float)) and isinstance(right, (int, float))
        ]
        change = {
            "comparableCells": len(changes),
            "fasterCells": sum(value < 0 for value in changes),
            "slowerCells": sum(value > 0 for value in changes),
            "unchangedCells": sum(value == 0 for value in changes),
            "meanChangeMinutes": sum(changes) / len(changes) if changes else None,
        }

    payload = {
        "schemaVersion": "vigo.result.comparison.v1",
        "productVersion": VERSION,
        "apiVersion": API_VERSION,
        "resultSchemaVersion": RESULT_SCHEMA_VERSION,
        "kind": "comparison",
        "queryKind": before.kind,
        "status": "ready",
        "cities": {
            "before": before.city_revision_id,
            "after": after.city_revision_id,
        },
        "change": change,
    }
    return Result("comparison", _immutable(payload), before.city_revision_id)
