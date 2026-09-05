from __future__ import annotations

import gc
import json
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

import vigo

FAKE_VIGO = r'''from __future__ import annotations
import json
import pathlib
import sys

args = sys.argv[1:]
if args == ["--version"]:
    print("0.3.7")
    raise SystemExit
if args == ["--help"]:
    print("vigo build\nvigo capabilities\nvigo inspect\nvigo route\nvigo matrix\nvigo reach\nvigo compare")
    raise SystemExit
if args == ["capabilities"]:
    print(json.dumps({
        "schemaVersion": "vigo.capabilities.v3",
        "productVersion": "0.3.7",
        "apiVersion": "1.1",
        "cityFormatVersion": 1,
        "resultSchemaVersion": 1,
        "publicCliCommands": ["build", "capabilities", "inspect", "route", "matrix", "reach", "compare"],
    }))
    raise SystemExit

command = args[0]
options = dict(part[2:].split("=", 1) for part in args[1:] if part.startswith("--") and "=" in part)
if command == "build":
    city = pathlib.Path(options["output"])
    (city / "routing").mkdir(parents=True)
    (city / "osm").mkdir()
    (city / "routing" / "project.sqlite").touch()
    (city / "osm" / "street-index.sqlite").touch()
    manifest = {
        "schemaVersion": "vigo.city.v1",
        "cityFormatVersion": 1,
        "name": "built-city",
        "revisionId": "20260904T120000-001Z",
        "builtAt": "2026-09-04T12:00:00.000Z",
        "sources": {"gtfs": [{"name": "feed.zip"}], "osm": {"name": "region.osm.pbf"}},
    }
    (city / "network.json").write_text(json.dumps(manifest))
    print(json.dumps(manifest))
elif command == "_route-stream":
    for line in sys.stdin:
        request = json.loads(line)
        unexpected = set(request) - {
            "id", "origin", "destination", "mode", "time", "timePreference",
            "objective", "maxWalkKm", "departureWindowMinutes", "waypoints",
        }
        if unexpected:
            raise SystemExit(f"unexpected route fields: {sorted(unexpected)}")
        plan = {
            "status": "ready",
            "durationMinutes": 12.5,
            "transfers": 0,
            "legs": [{"type": "ride", "coordinates": [[0, 0], [1, 1]]}],
        }
        print(json.dumps({
            "schemaVersion": "vigo.result.route.v1",
            "productVersion": "0.3.7",
            "apiVersion": "1.1",
            "resultSchemaVersion": 1,
            "id": request["id"],
            "status": "ok",
            "routingStatus": "ready",
            "plan": plan,
            "timing": {"requestMs": 1.0},
        }), flush=True)
elif command == "route":
    print(json.dumps({
        "schemaVersion": "vigo.result.route.v1",
        "productVersion": "0.3.7",
        "apiVersion": "1.1",
        "resultSchemaVersion": 1,
        "kind": "route",
        "status": "ready",
        "query": {},
        "result": {"status": "ready", "durationMinutes": 8, "transfers": 0, "legs": []},
        "warnings": [],
        "timing": {"computeMs": 1.0},
    }))
elif command == "matrix":
    request = json.loads(pathlib.Path(options["request"]).read_text())
    rows = []
    for origin_index, origin in enumerate(request["origins"]):
        for destination_index, destination in enumerate(request["destinations"]):
            rows.append({
                "originIndex": origin_index,
                "destinationIndex": destination_index,
                "originId": origin["id"],
                "destinationId": destination["id"],
                "status": "ready",
                "durationMinutes": 10 + origin_index + destination_index,
            })
    print(json.dumps({
        "schemaVersion": "vigo.result.matrix.v1",
        "productVersion": "0.3.7",
        "apiVersion": "1.1",
        "resultSchemaVersion": 1,
        "kind": "matrix",
        "status": "ready",
        "query": request,
        "rows": rows,
        "warnings": [],
        "timing": {"queryMs": 1.0},
    }))
elif command == "reach":
    request = json.loads(pathlib.Path(options["request"]).read_text())
    size = request["rasterSize"]
    print(json.dumps({
        "schemaVersion": "vigo.result.reach.v1",
        "productVersion": "0.3.7",
        "apiVersion": "1.1",
        "resultSchemaVersion": 1,
        "kind": "reach",
        "status": "ready",
        "query": request,
        "surface": {"width": size, "height": size, "values": [1] * (size * size)},
        "contours": {"type": "FeatureCollection", "features": []},
        "warnings": [],
        "timing": {"queryMs": 1.0},
    }))
else:
    raise SystemExit(2)
'''


class VigoPythonTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.cli = self.root / "fake_vigo.py"
        self.cli.write_text(textwrap.dedent(FAKE_VIGO), encoding="utf-8")
        self.command = (sys.executable, str(self.cli))
        self.city_path = self.root / "city"
        (self.city_path / "routing").mkdir(parents=True)
        (self.city_path / "osm").mkdir()
        (self.city_path / "routing" / "project.sqlite").touch()
        (self.city_path / "osm" / "street-index.sqlite").touch()
        (self.city_path / "network.json").write_text(
            json.dumps({
                "schemaVersion": "vigo.city.v1",
                "cityFormatVersion": 1,
                "name": "city",
                "revisionId": "20260904T120000-001Z",
                "builtAt": "2026-09-04T12:00:00.000Z",
                "sources": {"gtfs": [{"name": "feed.zip"}], "osm": {"name": "region.osm.pbf"}},
            }),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_city_has_one_query_model(self) -> None:
        with vigo.open(self.city_path, runtime=self.command) as city:
            route = city.route("A", "B", depart_at="08:00", service_date="2026-09-04")
            route_via = city.route(
                "A",
                "B",
                waypoints=["C"],
                depart_at="08:00",
                service_date="2026-09-04",
            )
            matrix = city.matrix(
                {"a": "A", "b": "B"},
                {"c": "C", "d": "D"},
                depart_at="08:00",
                service_date="2026-09-04",
            )
            reach = city.reach(
                [0, 0],
                depart_at="08:00",
                service_date="2026-09-04",
                raster_size=48,
            )

        self.assertEqual(route.kind, "route")
        self.assertEqual(route.duration_minutes, 12.5)
        self.assertEqual(route_via.duration_minutes, 8)
        self.assertEqual(len(matrix.rows), 4)
        self.assertEqual(reach.to_geojson()["type"], "FeatureCollection")
        self.assertEqual(route.city_revision_id, "20260904T120000-001Z")

    def test_scenario_is_tied_to_one_city_revision(self) -> None:
        with vigo.open(self.city_path, runtime=self.command) as city:
            proposal = city.scenario(
                "More service",
                services=[{
                    "name": "Crosstown",
                    "stops": [
                        {"coordinate": [0, 0]},
                        {"coordinate": [1, 1]},
                    ],
                }],
            )
            result = proposal.reach(
                [0, 0],
                depart_at="08:00",
                service_date="2026-09-04",
                raster_size=48,
            )
            support = proposal.supports(
                vigo.Route("A", "B", depart_at="08:00", service_date="2026-09-04")
            )
            self.assertFalse(support.supported)
            self.assertEqual(support.reason, "planned_transit_route")
            with self.assertRaises(vigo.UnsupportedQuery):
                proposal.route("A", "B", depart_at="08:00", service_date="2026-09-04")

        self.assertEqual(result.scenario_name, "More service")
        self.assertIn("scenario", result.query)
        self.assertEqual(result.query["scenario"]["name"], "More service")

    def test_queries_can_be_reused_and_compared(self) -> None:
        with vigo.open(self.city_path, runtime=self.command) as city:
            query = vigo.Route("A", "B", depart_at="08:00", service_date="2026-09-04")
            before, after = city.run([query, query])
            difference = vigo.compare(before, after)

        self.assertEqual(difference.kind, "comparison")
        self.assertEqual(difference.value["durationChangeMinutes"], 0)

    def test_build_publishes_one_city_directory(self) -> None:
        gtfs = self.root / "feed.zip"
        osm = self.root / "region.osm.pbf"
        gtfs.touch()
        osm.touch()
        output = self.root / "built-city"
        with vigo.build(output, gtfs=gtfs, osm=osm, runtime=self.command) as city:
            self.assertEqual(city.revision_id, "20260904T120000-001Z")
            self.assertEqual(city.runtime.product_version, "0.3.7")
            self.assertEqual(city.runtime.api_version, "1.1")
            self.assertTrue((output / "routing" / "project.sqlite").is_file())
            self.assertTrue((output / "osm" / "street-index.sqlite").is_file())

    def test_support_is_introspection_not_exception_discovery(self) -> None:
        with vigo.open(self.city_path, runtime=self.command) as city:
            query = vigo.Matrix(
                {"a": "A"},
                {"b": "B"},
                arrive_by="08:30",
                service_date="2026-09-04",
            )
            support = city.supports(query)
            self.assertFalse(support.supported)
            self.assertEqual(support.reason, "arrive_by_matrix")
            self.assertEqual(support.available, ("depart_at",))
            with self.assertRaises(vigo.UnsupportedQuery):
                city.run(query)

    def test_scenario_nested_values_are_immutable_and_serializable(self) -> None:
        stops = [{"coordinate": [0, 0]}, {"coordinate": [1, 1]}]
        with vigo.open(self.city_path, runtime=self.command) as city:
            scenario = city.scenario("Frozen", services=[{"stops": stops}])
            stops[0]["coordinate"][0] = 40
            with self.assertRaises(TypeError):
                scenario.services[0]["stops"][0]["coordinate"][0] = 50
            traffic = city.scenario("Traffic", traffic={"observations": [{"delayFactor": 2}]})
            with self.assertRaises(TypeError):
                traffic.traffic["observations"][0]["delayFactor"] = 3
            result = scenario.reach([0, 0], service_date="2026-09-04", raster_size=48)
            self.assertEqual(result.query["scenario"]["services"][0]["stops"][0]["coordinate"], [0, 0])

    def test_route_stream_recovers_and_closes_all_pipes(self) -> None:
        city = vigo.open(self.city_path, runtime=self.command)
        options = {"depart_at": "08:00", "service_date": "2026-09-04"}
        city.route("A", "B", **options)
        old = city._streams["2026-09-04"]
        old._process.kill()
        old._process.wait()
        result = city.route("A", "B", **options)
        current = city._streams["2026-09-04"]
        self.assertEqual(result.status, "ready")
        self.assertIsNot(current, old)
        city.close()
        for stream in (old, current):
            self.assertIsNotNone(stream._process.poll())
            self.assertTrue(stream._process.stdin.closed)
            self.assertTrue(stream._process.stdout.closed)
            self.assertTrue(stream._process.stderr.closed)
            self.assertFalse(stream._reader.is_alive())
            self.assertFalse(stream._error_reader.is_alive())

    def test_discarded_city_releases_resident_process(self) -> None:
        city = vigo.open(self.city_path, runtime=self.command)
        city.route("A", "B", depart_at="08:00", service_date="2026-09-04")
        process = city._streams["2026-09-04"]._process
        del city
        gc.collect()
        self.assertIsNotNone(process.poll())

    def test_after_midnight_query_keeps_its_service_date(self) -> None:
        with vigo.open(self.city_path, runtime=self.command) as city:
            result = city.route("A", "B", depart_at="25:15", service_date="2026-09-04")
        self.assertEqual(result.query["time"], "25:15")
        self.assertEqual(result.query["serviceDate"], "2026-09-04")

    def test_comparison_preserves_unreachable_values_and_grid_identity(self) -> None:
        def result(kind, **payload):
            return vigo.Result(kind, {"resultSchemaVersion": 1, "status": "ready", **payload}, "city")

        def reach(values, bounds=(0, 0, 1, 1), width=2, height=2):
            return result("reach", surface={"values": values, "bounds": list(bounds), "width": width, "height": height})

        difference = vigo.compare(reach([None, 10, 0, None]), reach([20, None, 0, None])).value
        self.assertEqual(difference["comparableCells"], 1)
        self.assertEqual(difference["meanChangeMinutes"], 0)
        self.assertEqual(difference["newlyReachableCells"], 1)
        self.assertEqual(difference["noLongerReachableCells"], 1)
        for other in (reach([1, 2, 3, 4], bounds=(1, 1, 2, 2)), reach([1, 2, 3, 4], width=1, height=4)):
            with self.assertRaisesRegex(ValueError, "same grid"):
                vigo.compare(reach([1, 2, 3, 4]), other)
        before = result("route", status="blocked", result={"durationMinutes": None, "transfers": None})
        after = result("route", result={"durationMinutes": 10, "transfers": 1})
        self.assertIsNone(vigo.compare(before, after).value["transferChange"])
        self.assertIsNone(vigo.compare(before, after).value["durationChangeMinutes"])

    def test_studio_runtime_uses_the_packaged_electron_layout(self) -> None:
        from vigo.runtime import _command_environment

        app = self.root.resolve() / "VIGO Studio.app"
        executable = app / "Contents" / "MacOS" / "VIGO Studio"
        program = app / "Contents" / "Resources" / "app" / "public" / "vigo.mjs"
        for artifact in (executable, program):
            artifact.parent.mkdir(parents=True, exist_ok=True)
            artifact.touch()
        runtime = vigo.resolve_runtime(app, verify=False)
        self.assertEqual(runtime.command, (str(executable), str(program)))
        environment = _command_environment(runtime.command)
        self.assertEqual(environment["ELECTRON_RUN_AS_NODE"], "1")
        self.assertEqual(Path(environment["VIGO_NATIVE_ROUTING_KERNEL"]), program.parent.parent / "server" / "vigo-routing-kernel.node")


if __name__ == "__main__":
    unittest.main()
