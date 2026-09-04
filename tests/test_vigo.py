from __future__ import annotations

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


if __name__ == "__main__":
    unittest.main()
