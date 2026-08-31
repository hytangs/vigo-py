"""Contract tests for VIGO's standalone Python binding."""

import datetime as dt
import json
import os
import sqlite3
import tempfile
import textwrap
import threading
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import vigo_router

FAKE_CLI = r"""#!/usr/bin/env python3
import json
import os
import sqlite3
import sys
from pathlib import Path

if os.environ.get("VIGO_FAKE_FAIL") == "1":
    print("fixture CLI failure", file=sys.stderr)
    raise SystemExit(2)

args = {}
values = {}
for token in sys.argv[1:]:
    if token.startswith("--") and "=" in token:
        key, value = token[2:].split("=", 1)
        args[key] = value
        values.setdefault(key, []).append(value)

command = next((token for token in sys.argv[1:] if not token.startswith("--")), "route")

process_counter = os.environ.get("VIGO_FAKE_PROCESS_COUNTER")
if process_counter:
    counter_path = Path(process_counter)
    count = int(counter_path.read_text(encoding="utf-8") or "0") if counter_path.exists() else 0
    counter_path.write_text(str(count + 1), encoding="utf-8")

capture = os.environ.get("VIGO_FAKE_CAPTURE")
if capture:
    Path(capture).write_text(json.dumps({"argv": sys.argv[1:], "args": args, "values": values}), encoding="utf-8")

if command == "build-network":
    output_root = Path(args["output-dir"])
    store_path = output_root / "routing" / "project.sqlite"
    street_path = output_root / "osm" / "street-index.sqlite"
    store_path.parent.mkdir(parents=True, exist_ok=True)
    street_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(store_path) as database:
        database.execute("CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        database.execute("INSERT INTO metadata VALUES ('schemaVersion', '\"vigo.routing.store.v1\"')")
        database.execute("INSERT INTO metadata VALUES ('departureIndexState', '\"deferred\"')")
        database.execute("CREATE TABLE stops (stop_id TEXT PRIMARY KEY, name TEXT, lon REAL, lat REAL)")
        database.execute("CREATE TABLE routes (route_id TEXT PRIMARY KEY, short_name TEXT, long_name TEXT)")
        database.execute("CREATE TABLE trips (trip_id TEXT PRIMARY KEY, route_id TEXT, service_id TEXT)")
        database.execute("CREATE TABLE connections (departure INTEGER, arrival INTEGER, trip_id TEXT, route_id TEXT, service_id TEXT, from_stop_id TEXT, to_stop_id TEXT, stop_sequence INTEGER, PRIMARY KEY (trip_id, stop_sequence))")
        database.executemany("INSERT INTO stops VALUES (?, ?, ?, ?)", [
            ("A", "Alpha", -71.06, 42.35),
            ("B", "Beta", -71.05, 42.36),
        ])
        database.execute("INSERT INTO routes VALUES ('blue', 'Blue', 'Blue Line')")
        database.execute("INSERT INTO trips VALUES ('blue-1', 'blue', 'weekday')")
        database.execute("INSERT INTO connections VALUES (480, 495, 'blue-1', 'blue', 'weekday', 'A', 'B', 1)")
    with sqlite3.connect(street_path) as database:
        database.execute("CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        database.execute("INSERT INTO metadata VALUES ('schemaVersion', '\"vigo.street.store.v1\"')")
        database.execute("INSERT INTO metadata VALUES ('storageLayout', '\"runtime-snapshots-v1\"')")
    (street_path.parent / f"{street_path.name}.street-accelerator-v7.bin").write_bytes(b"fixture-walk-snapshot")
    manifest = {
        "schemaVersion": "vigo.cli.build-network.v1",
        "version": "fixture",
        "inputs": {"gtfs": values.get("gtfs", []), "osmPbf": args["osm-pbf"]},
        "outputDirectory": str(output_root),
        "routingStore": {"path": str(store_path), "stopCount": 2, "connectionCount": 1},
        "streetStore": {"path": str(street_path), "edgeCount": 0},
        "timing": {"totalMs": 12.5, "gtfsBuildMs": 4.0, "osmBuildMs": 8.0},
    }
    (output_root / "network.json").write_text(json.dumps(manifest), encoding="utf-8")
    print(json.dumps(manifest))
    raise SystemExit(0)

if command == "route-ndjson":
    crash_once = os.environ.get("VIGO_FAKE_CRASH_ONCE")
    if crash_once:
        crash_marker = Path(crash_once)
        if not crash_marker.exists():
            next(line for line in sys.stdin if line.strip())
            crash_marker.write_text("crashed", encoding="utf-8")
            print("fixture resident crash", file=sys.stderr)
            raise SystemExit(3)

    def stream_point(value, fallback):
        if isinstance(value, str):
            stop_id = value
            coordinate = [-71.06, 42.35] if stop_id == "A" else [-71.05, 42.36]
            return {"stopId": stop_id, "coordinate": coordinate, "label": stop_id, "source": "stop"}
        stop_id = str(value.get("stopId") or "")
        if stop_id:
            coordinate = [-71.06, 42.35] if stop_id == "A" else [-71.05, 42.36]
            return {"stopId": stop_id, "coordinate": coordinate, "label": value.get("label") or stop_id, "source": "stop"}
        return {
            "coordinate": [float(value["coordinate"][0]), float(value["coordinate"][1])],
            "label": value.get("label") or fallback,
            "source": "map",
        }

    for sequence, line in enumerate(sys.stdin, 1):
        if not line.strip():
            continue
        request = json.loads(line)
        if capture:
            Path(capture).write_text(json.dumps({"argv": sys.argv[1:], "args": args, "values": values, "request": request}), encoding="utf-8")
        result_id = str(request.get("id") or f"request_{sequence}")
        if result_id == "stream-error":
            print(json.dumps({
                "schemaVersion": "vigo.cli.route-result.v1",
                "version": "fixture",
                "sequence": sequence,
                "id": result_id,
                "status": "error",
                "timing": {"requestMs": 0.2, "serializationMs": 0.1},
                "error": {"message": "fixture request error"},
            }), flush=True)
            continue
        origin = stream_point(request["origin"], "Origin")
        destination = stream_point(request["destination"], "Destination")
        plan = {
            "id": f"plan-{result_id}",
            "status": "ready",
            "travelMode": "transit",
            "timePreference": request.get("timePreference", "depart"),
            "maxWalkKm": float(request.get("maxWalkKm", 1.2)),
            "title": "Blue",
            "detail": "Canonical resident-kernel route",
            "departMinutes": 480,
            "arriveMinutes": 495,
            "durationMinutes": 15,
            "waitMinutes": 5,
            "walkMinutes": 0,
            "rideMinutes": 10,
            "transfers": 0,
            "origin": origin,
            "destination": destination,
            "legs": [{
                "type": "ride",
                "travelMode": "transit",
                "fromStopId": origin.get("stopId"),
                "toStopId": destination.get("stopId"),
                "fromName": origin["label"],
                "toName": destination["label"],
                "routeId": "blue",
                "routeShortName": "Blue",
                "tripId": "blue-1",
                "startMinutes": 485,
                "endMinutes": 495,
                "durationMinutes": 10,
                "distanceKm": 2.1,
                "stopCount": 2,
                "coordinates": [origin["coordinate"], destination["coordinate"]],
            }],
            "diagnostics": {
                "algorithm": "rust_exact_connection_scan_scalar_no_heuristic",
                "optimality": "exact_earliest_arrival",
                "scheduleMode": "exact",
                "walkingNetwork": "osm-pedestrian-network" if "street-store" in args else "direct",
                "serviceDay": args.get("service-day", "weekday"),
                "scannedDepartures": 12,
                "relaxedStops": 3,
                "walkingSpeedKph": 4.2,
                "searchStats": {"engineQueryMs": 0.5, "cacheHit": False},
            },
        }
        print(json.dumps({
            "schemaVersion": "vigo.cli.route-result.v1",
            "version": "fixture",
            "sequence": sequence,
            "id": result_id,
            "status": "ok",
            "engine": {
                "name": "VIGO",
                "algorithm": "rust_exact_connection_scan_scalar_no_heuristic",
                "algorithms": ["rust_exact_connection_scan_scalar_no_heuristic"],
                "methods": [],
                "storage": "sqlite-persisted-resident-compiled",
                "persistentStore": "sqlite",
                "queryExecutor": "resident-active-service-kernel",
                "sqlRouteExecutor": False,
            },
            "timing": {
                "requestMs": 2.0,
                "routeMs": 1.25 + sequence - 1,
                "engineQueryMs": 0.5 + sequence - 1,
                "preparationMs": 2.5 if sequence == 1 else 0,
                "serializationMs": 0.25,
            },
            "routingStore": {"storeId": "fixture", "connectionCount": 1},
            "plan": plan,
            "profileSampleCount": 0,
        }), flush=True)
    raise SystemExit(0)

if command == "one-to-many":
    request = json.loads(Path(args["request"]).read_text(encoding="utf-8"))
    rows = [
        {
            "originIndex": 0,
            "destinationIndex": index,
            "destinationId": destination["id"],
            "status": "ready" if index == 0 else "blocked",
            "departMinutes": 480,
            "arriveMinutes": 495 if index == 0 else None,
            "durationMinutes": 15 if index == 0 else None,
        }
        for index, destination in enumerate(request["destinations"])
    ]
    print(json.dumps({
        "schemaVersion": "vigo.cli.one-to-many.v1",
        "version": "fixture",
        "engine": {
            "name": "VIGO",
            "operator": "one-to-many",
            "owner": "rust-resident-timetable-kernel",
            "algorithm": "rust_exact_connection_scan_one_to_many",
            "storage": "sqlite-persisted-resident-compiled",
            "persistentStore": "sqlite",
            "queryExecutor": "resident-active-service-kernel",
            "sqlRouteExecutor": False,
        },
        "query": request,
        "rows": rows,
        "diagnostics": {
            "matrixStrategy": args.get("matrix-strategy", "shared"),
            "matrixEngine": "rust_exact_connection_scan_one_to_many",
            "forwardSearches": 1,
        },
        "timing": {"preparationMs": 2.5, "queryMs": 0.75, "engineQueryMs": 0.4},
    }))
    raise SystemExit(0)

if command == "isochrone":
    request = json.loads(Path(args["request"]).read_text(encoding="utf-8"))
    size = int(args.get("raster-size", request.get("rasterSize", 96)))
    cutoffs = [float(value) for value in args.get("cutoffs", "15,30,45,60").split(",")]
    print(json.dumps({
        "schemaVersion": "vigo.cli.isochrone.v1",
        "version": "fixture",
        "engine": {
            "name": "VIGO",
            "operator": "isochrone",
            "owner": "rust-resident-accessibility-pipeline",
            "algorithm": "rust_resident_generation_tagged_connection_scan_one_to_many",
            "surfaceKernel": "rust_mmap_street_surface_v1",
            "storage": "sqlite-persisted-resident-compiled",
            "persistentStore": "sqlite",
            "queryExecutor": "resident-active-service-kernel",
            "sqlRouteExecutor": False,
        },
        "query": {**request, "cutoffsMinutes": cutoffs},
        "stops": [{"stopId": "B", "durationMinutes": 15}],
        "scenarioStops": [],
        "surface": {
            "schemaVersion": "vigo.street.network-raster.v1",
            "width": size,
            "height": size,
            "bounds": [-71.1, 42.3, -71.0, 42.4],
            "values": [15.0] * (size * size),
            "diagnostics": {"kernel": "rust_mmap_street_surface_v1"},
        },
        "isochrones": {
            "type": "FeatureCollection",
            "features": [{
                "type": "Feature",
                "properties": {"cutoffMinutes": cutoffs[0]},
                "geometry": {"type": "MultiLineString", "coordinates": [[[-71.06, 42.35], [-71.05, 42.36]]]},
            }],
        },
        "diagnostics": {
            "algorithm": "rust_resident_generation_tagged_connection_scan_one_to_many",
            "surface": {"kernel": "rust_mmap_street_surface_v1"},
        },
        "timing": {"preparationMs": 2.5, "queryMs": 1.25, "contourMs": 0.1},
    }))
    raise SystemExit(0)
"""


def write_store(path: Path, *, complete: bool = True) -> None:
    with closing(sqlite3.connect(path)) as database, database:
        database.execute(
            "CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        database.execute(
            "INSERT INTO metadata VALUES ('schemaVersion', '\"vigo.routing.store.v1\"')"
        )
        database.execute(
            "INSERT INTO metadata VALUES ('departureIndexState', '\"deferred\"')"
        )
        if complete:
            database.execute(
                "CREATE TABLE stops (stop_id TEXT PRIMARY KEY, name TEXT, lon REAL, lat REAL)"
            )
            database.execute(
                "CREATE TABLE routes (route_id TEXT PRIMARY KEY, short_name TEXT, long_name TEXT)"
            )
            database.execute(
                "CREATE TABLE trips (trip_id TEXT PRIMARY KEY, route_id TEXT, service_id TEXT)"
            )
            database.execute(
                "CREATE TABLE connections (departure INTEGER, arrival INTEGER, trip_id TEXT, route_id TEXT, service_id TEXT, from_stop_id TEXT, to_stop_id TEXT, stop_sequence INTEGER, PRIMARY KEY (trip_id, stop_sequence))"
            )
            database.executemany(
                "INSERT INTO stops VALUES (?, ?, ?, ?)",
                [
                    ("A", "Alpha", -71.06, 42.35),
                    ("B", "Beta", -71.05, 42.36),
                ],
            )
            database.execute("INSERT INTO routes VALUES ('blue', 'Blue', 'Blue Line')")
            database.execute("INSERT INTO trips VALUES ('blue-1', 'blue', 'weekday')")
            database.execute(
                "INSERT INTO connections VALUES (480, 495, 'blue-1', 'blue', 'weekday', 'A', 'B', 1)"
            )


def write_street_store(path: Path, schema: str = "vigo.street.store.v1") -> None:
    with closing(sqlite3.connect(path)) as database, database:
        database.execute(
            "CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        database.execute(
            "INSERT INTO metadata VALUES ('schemaVersion', ?)", (json.dumps(schema),)
        )
        database.execute(
            "INSERT INTO metadata VALUES ('storageLayout', '\"runtime-snapshots-v1\"')"
        )
    (path.parent / f"{path.name}.street-accelerator-v7.bin").write_bytes(b"fixture-walk-snapshot")


class VigoRouterWrapperTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.store = self.root / "feed.sqlite"
        self.street_store = self.root / "streets.sqlite"
        self.gtfs = self.root / "feed.zip"
        self.osm_pbf = self.root / "region.osm.pbf"
        self.cli = self.root / "vigo"
        write_store(self.store)
        write_street_store(self.street_store)
        self.gtfs.write_bytes(b"fixture-gtfs")
        self.osm_pbf.write_bytes(b"fixture-osm")
        self.cli.write_text(textwrap.dedent(FAKE_CLI), encoding="utf-8")
        self.cli.chmod(0o755)

    def tearDown(self):
        self.temporary.cleanup()

    def test_open_network_accepts_only_vigo_sqlite(self):
        network = vigo_router.open_network(
            self.store, street_store=self.street_store, cli=self.cli
        )
        self.assertEqual(network.store, self.store.resolve())
        self.assertEqual(network.street_store, self.street_store.resolve())
        self.assertEqual(network.stats["stops"], 2)
        self.assertEqual(network.stats["connections"], 1)
        self.assertEqual(network.engine, "vigo-native-js-cli")

        with self.assertRaisesRegex(ValueError, "SQLite"):
            vigo_router.open_network(self.root / "feed.zip", cli=self.cli)

        incomplete = self.root / "incomplete.sqlite"
        write_store(incomplete, complete=False)
        with self.assertRaisesRegex(ValueError, "missing required tables"):
            vigo_router.open_network(incomplete, cli=self.cli)

        invalid_street = self.root / "invalid-street.sqlite"
        write_street_store(invalid_street, schema="other.street.v1")
        with self.assertRaisesRegex(ValueError, "street-store schema"):
            vigo_router.open_network(
                self.store, street_store=invalid_street, cli=self.cli
            )

        legacy_street = self.root / "legacy-street.sqlite"
        with closing(sqlite3.connect(legacy_street)) as database, database:
            database.execute(
                "CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
            )
            database.execute(
                "INSERT INTO metadata VALUES ('schemaVersion', 'vigo.street.store.v3')"
            )
            database.execute(
                "INSERT INTO metadata VALUES ('storageLayout', 'walk-drive-role-tables-v2')"
            )
            database.execute(
                "CREATE TABLE walk_nodes (node_id INTEGER PRIMARY KEY, lat REAL NOT NULL, lon REAL NOT NULL)"
            )
            database.execute(
                "CREATE TABLE edges (from_node INTEGER NOT NULL, to_node INTEGER NOT NULL, distance_m REAL NOT NULL, way_id INTEGER NOT NULL)"
            )
        with self.assertRaisesRegex(ValueError, "expected runtime-snapshots-v1"):
            vigo_router.open_network(
                self.store, street_store=legacy_street, cli=self.cli
            )

    def test_route_invokes_canonical_cli_and_returns_full_plan(self):
        capture = self.root / "capture.json"
        network = vigo_router.open_network(
            self.store, street_store=self.street_store, cli=self.cli
        )
        with patch.dict(os.environ, {"VIGO_FAKE_CAPTURE": str(capture)}):
            plan = vigo_router.route(
                network,
                "A",
                "B",
                time="08:15",
                time_preference="arrive",
                routing_preference="fastest",
                service_day="weekday",
                service_date=dt.datetime(
                    2026,
                    7,
                    15,
                    12,
                    30,
                    tzinfo=dt.timezone.utc,
                ),
                max_walk_km=1.5,
            )

        invocation = json.loads(capture.read_text(encoding="utf-8"))["args"]
        self.assertEqual(invocation["store"], str(self.store.resolve()))
        self.assertEqual(invocation["street-store"], str(self.street_store.resolve()))
        self.assertEqual(invocation["service-date"], "2026-07-15")
        request = json.loads(capture.read_text(encoding="utf-8"))["request"]
        self.assertEqual(request["time"], "08:15")
        self.assertEqual(request["timePreference"], "arrive")
        self.assertEqual(request["routingPreference"], "fastest")
        self.assertEqual(request["maxWalkKm"], 1.5)
        self.assertEqual(plan.status, "ready")
        self.assertEqual(plan.routing_status, "ready")
        self.assertEqual(
            plan.algorithm, "rust_exact_connection_scan_scalar_no_heuristic"
        )
        self.assertEqual(plan.walking_network, "osm-pedestrian-network")
        self.assertEqual(plan.optimality, "exact_earliest_arrival")
        self.assertEqual(plan.failure_code, None)
        self.assertEqual(plan.failure_category, None)
        self.assertEqual(plan.method_used, ())
        self.assertEqual(plan.duration_minutes, 15)
        self.assertEqual(plan.route_sequence, ("Blue",))
        self.assertEqual(plan.legs[0].route_short_name, "Blue")
        self.assertEqual(plan.diagnostics["scheduleMode"], "exact")
        self.assertEqual(plan.to_dict()["id"], "plan-route")
        self.assertEqual(
            plan.to_geojson()["features"][0]["geometry"]["type"], "LineString"
        )
        json.loads(plan.to_json())

    def test_route_batch_preserves_stop_and_coordinate_requests(self):
        network = vigo_router.open_network(self.store, cli=self.cli)
        plans = vigo_router.route_batch(
            network,
            [
                {"id": "stops", "origin": "A", "destination": "B"},
                {
                    "id": "coordinates",
                    "origin": {"lon": -71.061, "lat": 42.351, "label": "Map A"},
                    "destination": (-71.049, 42.361),
                },
            ],
            service_date="2026-07-15",
        )
        self.assertEqual([plan.request_id for plan in plans], ["stops", "coordinates"])
        self.assertEqual(plans[0].origin["stopId"], "A")
        self.assertEqual(plans[1].origin["source"], "map")
        self.assertIsInstance(plans, list)
        self.assertEqual(plans.timing["cli_preparation_ms"], 2.5)
        self.assertEqual(plans.timing["cli_routing_ms"], 3.5)
        self.assertEqual(plans.timing["cli_output_ms"], 0.5)
        self.assertEqual(plans.summary["timing"]["outputMs"], 0.5)
        self.assertEqual(
            plans.summary["engine"]["algorithm"],
            "rust_exact_connection_scan_scalar_no_heuristic",
        )
        self.assertEqual(plans[0].timing["query_wall_ms"], 1.25)
        self.assertEqual(plans[1].timing["engine_query_ms"], 1.5)
        self.assertEqual(plans[1].timing["serialization_ms"], 0.25)
        self.assertFalse(plans[0].timing["cache_hit"])

    def test_route_reuses_one_resident_process_until_closed(self):
        counter = self.root / "process-count.txt"
        network = vigo_router.open_network(
            self.store, street_store=self.street_store, cli=self.cli
        )
        self.addCleanup(network.close)
        with patch.dict(os.environ, {"VIGO_FAKE_PROCESS_COUNTER": str(counter)}):
            first = network.route_batch(
                [{"id": "first", "origin": "A", "destination": "B"}],
                service_date="2026-07-15",
            )
            second = network.route_batch(
                [{"id": "second", "origin": "A", "destination": "B"}],
                service_date="2026-07-15",
            )
            self.assertEqual(counter.read_text(encoding="utf-8"), "1")
            self.assertFalse(first.timing["resident_session_reused"])
            self.assertTrue(second.timing["resident_session_reused"])

            changed_date = network.route_batch(
                [{"id": "changed-date", "origin": "A", "destination": "B"}],
                service_date="2026-07-16",
            )
            self.assertEqual(counter.read_text(encoding="utf-8"), "2")
            self.assertFalse(changed_date.timing["resident_session_reused"])

            network.close()
            third = network.route_batch(
                [{"id": "third", "origin": "A", "destination": "B"}],
                service_date="2026-07-15",
            )
            self.assertEqual(counter.read_text(encoding="utf-8"), "3")
            self.assertFalse(third.timing["resident_session_reused"])

    def test_close_can_interrupt_a_blocked_resident_exchange(self):
        from vigo_router import core

        entered = threading.Event()
        release = threading.Event()
        close_calls = []

        class BlockingSession:
            def exchange(self, _requests):
                entered.set()
                if not release.wait(2):
                    raise AssertionError("test exchange was not released")
                return []

            def close(self):
                close_calls.append(True)

        session = BlockingSession()
        errors = []
        pool = core._RoutingSessionPool()

        def exchange():
            try:
                pool.exchange(
                    object(),
                    [{"id": "blocked"}],
                    service_date="2026-07-15",
                    service_day="weekday",
                )
            except Exception as error:  # noqa: BLE001 - aggregate worker failures for assertion
                errors.append(error)

        with patch.object(core, "_RoutingSession", return_value=session):
            worker = threading.Thread(target=exchange)
            worker.start()
            self.assertTrue(entered.wait(1))

            closer = threading.Thread(target=pool.close)
            closer.start()
            closer.join(0.5)
            self.assertFalse(closer.is_alive())

            release.set()
            worker.join(1)

        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(len(close_calls), 1)

    def test_route_recovers_once_after_resident_process_crash(self):
        counter = self.root / "process-count.txt"
        crash_marker = self.root / "crashed-once.txt"
        network = vigo_router.open_network(self.store, cli=self.cli)
        self.addCleanup(network.close)
        with patch.dict(
            os.environ,
            {
                "VIGO_FAKE_PROCESS_COUNTER": str(counter),
                "VIGO_FAKE_CRASH_ONCE": str(crash_marker),
            },
        ):
            plan = network.route("A", "B", service_date="2026-07-15")

        self.assertEqual(plan.status, "ready")
        self.assertTrue(crash_marker.is_file())
        self.assertEqual(counter.read_text(encoding="utf-8"), "2")

    def test_request_error_does_not_poison_resident_session(self):
        counter = self.root / "process-count.txt"
        network = vigo_router.open_network(self.store, cli=self.cli)
        self.addCleanup(network.close)
        with patch.dict(os.environ, {"VIGO_FAKE_PROCESS_COUNTER": str(counter)}):
            with self.assertRaisesRegex(
                vigo_router.VigoCliError, "fixture request error"
            ):
                network.route_batch(
                    [{"id": "stream-error", "origin": "A", "destination": "B"}],
                    service_date="2026-07-15",
                )
            recovered = network.route_batch(
                [{"id": "after-error", "origin": "A", "destination": "B"}],
                service_date="2026-07-15",
            )

        self.assertEqual(recovered[0].status, "ready")
        self.assertTrue(recovered.timing["resident_session_reused"])
        self.assertEqual(counter.read_text(encoding="utf-8"), "1")

    def test_transport_network_compiles_gtfs_and_osm_through_canonical_cli(self):
        capture = self.root / "build-capture.json"
        with patch.dict(os.environ, {"VIGO_FAKE_CAPTURE": str(capture)}):
            network = vigo_router.TransportNetwork(
                self.osm_pbf,
                [self.gtfs],
                cache_dir=self.root / "cache",
                cli=self.cli,
            )

        invocation = json.loads(capture.read_text(encoding="utf-8"))
        self.assertEqual(invocation["argv"][0], "build-network")
        self.assertNotIn("--sequential-raw-build", invocation["argv"])
        self.assertEqual(invocation["values"]["gtfs"], [str(self.gtfs.resolve())])
        self.assertEqual(invocation["args"]["osm-pbf"], str(self.osm_pbf.resolve()))
        self.assertTrue(network.store.is_file())
        self.assertTrue(network.street_store.is_file())
        self.assertEqual(
            network.build_summary["schemaVersion"], "vigo.cli.build-network.v1"
        )

        plan = network.route("A", "B", service_date="2026-07-15")
        self.assertEqual(plan.status, "ready")
        batch = network.route_batch(
            [
                {"id": "one", "origin": "A", "destination": "B"},
                {"id": "two", "origin": "A", "destination": "B"},
            ],
            service_date="2026-07-15",
        )
        self.assertEqual(len(batch), 2)
        self.assertGreaterEqual(batch.timing["python_wall_ms"], 0)

    def test_transport_network_can_force_sequential_raw_build(self):
        capture = self.root / "sequential-build-capture.json"
        with patch.dict(os.environ, {"VIGO_FAKE_CAPTURE": str(capture)}):
            vigo_router.TransportNetwork(
                self.osm_pbf,
                [self.gtfs],
                cache_dir=self.root / "sequential-cache",
                cli=self.cli,
                sequential_raw_build=True,
            )

        invocation = json.loads(capture.read_text(encoding="utf-8"))
        self.assertIn("--sequential-raw-build", invocation["argv"])

    def test_transport_network_reuses_content_addressed_build(self):
        first = vigo_router.TransportNetwork(
            self.osm_pbf,
            [self.gtfs],
            cache_dir=self.root / "cache",
            cli=self.cli,
        )
        with patch.dict(os.environ, {"VIGO_FAKE_FAIL": "1"}):
            second = vigo_router.TransportNetwork(
                self.osm_pbf,
                [self.gtfs],
                cache_dir=self.root / "cache",
                cli=self.cli,
            )
        self.assertEqual(second.store, first.store)
        self.assertTrue(second.build_summary["reused"])

    def test_route_many_uses_shared_resident_rust_operator(self):
        capture = self.root / "one-to-many-capture.json"
        network = vigo_router.open_network(
            self.store, street_store=self.street_store, cli=self.cli
        )
        with patch.dict(os.environ, {"VIGO_FAKE_CAPTURE": str(capture)}):
            result = network.route_many(
                "A",
                {
                    "beta": "B",
                    "map": [-71.049, 42.361],
                },
                time="08:00",
                service_date="2026-07-15",
                max_walk_km=1.5,
                horizon_minutes=90,
            )

        invocation = json.loads(capture.read_text(encoding="utf-8"))
        self.assertEqual(invocation["argv"][0], "one-to-many")
        self.assertEqual(invocation["args"]["matrix-strategy"], "shared")
        self.assertEqual(invocation["args"]["horizon"], "90")
        self.assertEqual([row.destination_id for row in result], ["beta", "map"])
        self.assertEqual(result[0].status, "ready")
        self.assertEqual(result[0].duration_minutes, 15)
        self.assertIsNone(result[1].arrive_minutes)
        self.assertEqual(
            result.engine["algorithm"], "rust_exact_connection_scan_one_to_many"
        )
        self.assertEqual(result.diagnostics["forwardSearches"], 1)
        self.assertGreaterEqual(result.timing["python_wall_ms"], 0)
        json.loads(result.to_json())

    def test_isochrone_returns_rust_surface_and_geojson(self):
        capture = self.root / "isochrone-capture.json"
        network = vigo_router.open_network(
            self.store, street_store=self.street_store, cli=self.cli
        )
        with patch.dict(os.environ, {"VIGO_FAKE_CAPTURE": str(capture)}):
            result = vigo_router.isochrone(
                network,
                {"stopId": "A", "label": "Alpha"},
                cutoffs_minutes=(10, 20, 30),
                time="08:00",
                service_date="2026-07-15",
                max_walk_km=1.0,
                radius_km=5,
                raster_size=48,
            )

        invocation = json.loads(capture.read_text(encoding="utf-8"))
        self.assertEqual(invocation["argv"][0], "isochrone")
        self.assertEqual(invocation["args"]["cutoffs"], "10,20,30")
        self.assertEqual(invocation["args"]["raster-size"], "48")
        self.assertEqual(result.cutoffs_minutes, (10.0, 20.0, 30.0))
        self.assertEqual(result.surface["width"], 48)
        self.assertEqual(len(result.surface["values"]), 48 * 48)
        self.assertEqual(result.stops[0]["stopId"], "B")
        self.assertEqual(result.to_geojson()["type"], "FeatureCollection")
        self.assertEqual(result.engine["owner"], "rust-resident-accessibility-pipeline")
        self.assertGreaterEqual(result.timing["python_wall_ms"], 0)
        json.loads(result.to_json())

        without_streets = vigo_router.open_network(self.store, cli=self.cli)
        with self.assertRaisesRegex(ValueError, "requires a VIGO street store"):
            without_streets.isochrone("A", service_date="2026-07-15", raster_size=48)

    def test_transport_network_validates_raw_inputs(self):
        with self.assertRaisesRegex(ValueError, "GTFS ZIP"):
            vigo_router.TransportNetwork(
                self.osm_pbf,
                [self.root / "feed.txt"],
                cache_dir=self.root / "cache",
                cli=self.cli,
            )
        with self.assertRaisesRegex(ValueError, "OSM PBF"):
            vigo_router.TransportNetwork(
                self.root / "region.osm",
                [self.gtfs],
                cache_dir=self.root / "cache",
                cli=self.cli,
            )

    def test_cli_failure_and_missing_runtime_fail_loudly(self):
        network = vigo_router.open_network(self.store, cli=self.cli)
        with (
            patch.dict(os.environ, {"VIGO_FAKE_FAIL": "1"}),
            self.assertRaisesRegex(vigo_router.VigoCliError, "fixture CLI failure"),
        ):
            vigo_router.route(network, "A", "B", service_date="2026-07-15")

        with self.assertRaises(FileNotFoundError):
            vigo_router.open_network(self.store, cli=self.root / "missing-vigo")

    def test_route_requires_an_exact_service_date(self):
        network = vigo_router.open_network(self.store, cli=self.cli)
        with self.assertRaisesRegex(ValueError, "service_date is required"):
            vigo_router.route(network, "A", "B")

    def test_route_rejects_ambiguous_numeric_control_values(self):
        network = vigo_router.open_network(self.store, cli=self.cli)
        with self.assertRaisesRegex(ValueError, "numeric time"):
            network.route(
                "A",
                "B",
                time=True,
                service_date="2026-07-15",
            )

        for invalid_time in (-0.5, float("inf"), float("nan")):
            with (
                self.subTest(time=invalid_time),
                self.assertRaisesRegex(
                    ValueError,
                    "numeric time",
                ),
            ):
                network.route(
                    "A",
                    "B",
                    time=invalid_time,
                    service_date="2026-07-15",
                )

        with self.assertRaisesRegex(ValueError, "non-negative integer"):
            network.route(
                "A",
                "B",
                service_date="2026-07-15",
                departure_window_minutes=1.5,
            )
        with self.assertRaisesRegex(TypeError, "numeric, not bool"):
            network.route(
                "A",
                "B",
                service_date="2026-07-15",
                max_walk_km=True,
            )

    def test_open_network_can_use_packaged_runtime_inside_vigo_app(self):
        app_bin = self.root / "VIGO.app" / "Contents" / "Resources" / "bin"
        app_bin.mkdir(parents=True)
        node = app_bin / "node"
        module = app_bin / "vigo.mjs"
        node.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        node.chmod(0o755)
        module.write_text("// bundled CLI fixture\n", encoding="utf-8")
        network = vigo_router.open_network(self.store, cli=self.root / "VIGO.app")
        self.assertEqual(network.command, (str(node.resolve()), str(module.resolve())))


if __name__ == "__main__":
    unittest.main()
