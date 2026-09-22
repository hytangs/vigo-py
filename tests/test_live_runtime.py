"""Public synthetic integration fixture; enable with VIGO_TEST_INPUTS and VIGO_RUNTIME."""

from __future__ import annotations

import contextlib
import datetime as dt
import io
import json
import os
import tempfile
import unittest
from pathlib import Path

import vigo


@unittest.skipUnless(
    os.environ.get("VIGO_TEST_INPUTS"),
    "set VIGO_TEST_INPUTS to the public CLI fixture directory",
)
class LiveRuntimeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary = tempfile.TemporaryDirectory(prefix="vigo-python-integration-")
        inputs = Path(os.environ["VIGO_TEST_INPUTS"])
        cls.build_progress = io.StringIO()
        with contextlib.redirect_stderr(cls.build_progress):
            cls.city = vigo.build(
                Path(cls.temporary.name) / "city",
                gtfs=inputs / "fixture.zip",
                osm=inputs / "fixture.osm.pbf",
                progress=True,
            )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.city.close()
        cls.temporary.cleanup()

    def test_route_matrix_parity_for_each_mode(self) -> None:
        for mode in ("transit", "walk", "drive"):
            with self.subTest(mode=mode):
                route = self.city.route(
                    "A",
                    "B",
                    mode=mode,
                    depart_at="07:55",
                    service_date="2026-07-15",
                    max_walk_km=0.2,
                )
                matrix = self.city.matrix(
                    {"a": "A"},
                    {"b": "B"},
                    mode=mode,
                    depart_at="07:55",
                    service_date="2026-07-15",
                    max_walk_km=0.2,
                )
                self.assertEqual(route.status, "ready")
                self.assertEqual(matrix.rows[0]["status"], "ready")
                self.assertAlmostEqual(
                    route.duration_minutes,
                    matrix.rows[0]["durationMinutes"],
                    delta=0.001,
                )
                for field in ("openMs", "computeMs", "endToEndMs"):
                    self.assertGreaterEqual(route.timing[field], 0)
                self.assertEqual(route.query["serviceDate"], "2026-07-15")

    def test_resident_queries_match_one_shot_and_reuse_process(self) -> None:
        from vigo.model import _json_command
        options = {"depart_at": "07:55", "service_date": "2026-07-15", "max_walk_km": 0.2}
        stream = None
        for mode, waypoints in (("transit", []), ("walk", []), ("drive", []), ("walk", ["X"])):
            with self.subTest(mode=mode, waypoints=waypoints):
                resident = self.city.route("A", "B", mode=mode, waypoints=waypoints, **options)
                current = self.city._streams["2026-07-15"]
                if stream is not None:
                    self.assertIs(current, stream)
                stream = current
                one_shot = _json_command(self.city.runtime, self.city.path, "route", {
                    "origin": "A", "destination": "B", "mode": mode, "waypoints": waypoints,
                    "requireTransitRide": True,
                }, ["--service-date=2026-07-15", "--time=07:55", "--max-walk=0.2"], 30)
                self.assertEqual(resident.status, one_shot["status"])
                for field in ("durationMinutes", "departMinutes", "arriveMinutes"):
                    self.assertEqual(resident.value[field], one_shot["result"][field])
                repeated = self.city.route("A", "B", mode=mode, waypoints=waypoints, **options)
                self.assertEqual(repeated.timing["openMs"], 0)
        resident = self.city.reach("A", raster_size=48, extent_radius_km=2, **options)
        one_shot = _json_command(self.city.runtime, self.city.path, "reach", {
            "origin": "A", "rasterSize": 48, "extentRadiusKm": 2,
        }, ["--service-date=2026-07-15", "--time=07:55", "--max-walk=0.2"], 30)
        for field in ("values", "width", "height", "bounds", "schemaVersion"):
            self.assertEqual(resident.value[field], one_shot["surface"][field])
        self.assertEqual(resident.to_geojson(), one_shot["contours"])
        self.assertIs(self.city._streams["2026-07-15"], stream)
        self.assertEqual(resident.timing["openMs"], 0)

    def test_traffic_changes_drive_route_and_matrix_without_leaking(self) -> None:
        options = {"mode": "drive", "service_date": "2026-07-15", "depart_at": "07:55"}
        baseline = self.city.route("A", "B", **options)
        traffic = self.city.scenario("delay", traffic={
            "source": "public-fixture", "observedAt": dt.datetime.now(dt.timezone.utc).isoformat(),
            "ttlSeconds": 300, "observations": [{
                "fromCoordinate": [-77.05, 38.9], "toCoordinate": [-77.04, 38.905], "delayFactor": 10,
            }],
        })
        delayed = traffic.route("A", "B", **options)
        matrix = traffic.matrix({"a": "A"}, {"b": "B"}, **options)
        self.assertGreater(delayed.duration_minutes, baseline.duration_minutes)
        self.assertAlmostEqual(delayed.duration_minutes, matrix.rows[0]["durationMinutes"], delta=0.001)
        self.assertEqual(matrix.query["routingDataMode"], "realtime")
        self.assertEqual(self.city.route("A", "B", **options).duration_minutes, baseline.duration_minutes)
        for snapshot in ({}, {"observations": []}):
            invalid = self.city.scenario("invalid", traffic=snapshot)
            with self.assertRaises(vigo.VigoError):
                invalid.matrix({"a": "A"}, {"b": "B"}, **options)
            with self.assertRaises(vigo.VigoError):
                invalid.route("A", "B", **options)
        # A rejected request does not poison the resident process.
        self.assertEqual(self.city.route("A", "B", **options).duration_minutes, baseline.duration_minutes)

    def test_build_reports_actual_phases(self) -> None:
        output = self.build_progress.getvalue()
        for phase in ("[gtfs]", "[osm]", "Preparing street routing", "Preparing station access", "Saving City", "City built"):
            self.assertIn(phase, output)
        self.assertNotIn("100%", output)

    def test_waypoints_arrive_by_and_exports(self) -> None:
        route = self.city.route(
            "A",
            "B",
            waypoints=["X"],
            mode="walk",
            arrive_by="08:30",
            service_date="2026-07-15",
        )
        self.assertEqual(route.status, "ready")
        self.assertEqual(len(route.value["waypoints"]), 1)
        self.assertAlmostEqual(route.value["arriveMinutes"], 510, delta=0.001)
        exported = route.export(Path(self.temporary.name) / "route.geojson")
        self.assertGreater(len(json.loads(exported.read_text())["features"]), 0)
        matrix = self.city.matrix({"a": "A"}, {"b": "B"}, service_date="2026-07-15")
        self.assertIn(
            "durationMinutes",
            matrix.export(Path(self.temporary.name) / "matrix.csv").read_text(),
        )

    def test_transit_cache_control_reaches_native_paths(self) -> None:
        options = {"depart_at": "07:55", "service_date": "2026-07-15", "max_walk_km": 0.2}
        cached = self.city.route("A", "B", **options)
        uncached = self.city.route("A", "B", disable_cache=True, **options)
        self.assertEqual(cached.duration_minutes, uncached.duration_minutes)
        stats = uncached.value["diagnostics"]["searchStats"]
        self.assertTrue(stats["nativeStreetPathCacheDisabled"])
        self.assertEqual(stats["nativeStreetPathCacheHits"], 0)
        matrix = self.city.matrix({"a": "A"}, {"b": "B"}, disable_cache=True,
                                  include_journeys=True, include_geometry=True, **options)
        self.assertEqual(matrix.rows[0]["durationMinutes"], uncached.duration_minutes)

    def test_arrive_by_large_many_to_one_matrix(self) -> None:
        result = self.city.matrix(
            {f"point_{i}": "A" for i in range(1024)}, {"hub": "B"},
            arrive_by="08:30", service_date="2026-07-15", max_walk_km=0.2,
        )
        stream = self.city._streams["2026-07-15"]
        point = self.city.route("A", "B", arrive_by="08:30", service_date="2026-07-15", max_walk_km=0.2)
        repeated = self.city.matrix({"a": "A"}, {"b": "B"}, depart_at="07:55", service_date="2026-07-15", max_walk_km=0.2)
        self.assertIs(self.city._streams["2026-07-15"], stream)
        self.assertEqual(repeated.timing["openMs"], 0)
        self.assertEqual(result.query["timePreference"], "arrive")
        self.assertEqual(len(result.rows), 1024)
        for row in result.rows:
            self.assertEqual(row["status"], "ready")
            self.assertEqual(row["departMinutes"], point.value["departMinutes"])
            self.assertEqual(row["arriveMinutes"], 510)

    def test_transfer_caps_use_the_resident_runtime_in_both_directions(self) -> None:
        for time in ({"depart_at": "07:55"}, {"arrive_by": "08:30"}):
            for cap in (0, 1, None):
                options = {**time, "service_date": "2026-07-15", "max_walk_km": 0.2, "max_transfers": cap}
                route = self.city.route("A", "B", **options)
                matrix = self.city.matrix({"a": "A"}, {"b": "B"}, **options)
                self.assertEqual(route.status, matrix.rows[0]["status"])
                if route.status == "ready":
                    self.assertLessEqual(route.value["transfers"], cap if cap is not None else 31)
                    field = "departMinutes" if "arrive_by" in time else "arriveMinutes"
                    self.assertAlmostEqual(route.value[field], matrix.rows[0][field], delta=0.001)
                detailed = self.city.matrix({"a": "A"}, {"b": "B"}, include_journeys=True, **options)
                self.assertEqual(matrix.rows[0]["status"], detailed.rows[0]["status"])
                if route.status == "ready":
                    journey = detailed.rows[0]["journey"]
                    for field in ("departMinutes", "arriveMinutes", "walkMinutes", "transfers"):
                        self.assertAlmostEqual(route.value[field], journey[field], delta=0.003)
                    self.assertTrue(journey["legs"])

    def test_reach_replacement_and_compare(self) -> None:
        options = {
            "depart_at": "07:55",
            "service_date": "2026-07-15",
            "max_walk_km": 0.2,
            "raster_size": 48,
            "extent_radius_km": 2,
        }
        baseline = self.city.reach("A", **options)
        repeated = self.city.reach("A", **options)
        difference = vigo.compare(baseline, repeated)
        self.assertEqual(difference.value["meanChangeMinutes"], 0)
        self.assertGreater(difference.value["comparableCells"], 0)
        replacement = self.city.scenario(
            "Replace R1",
            services=[
                {
                    "operation": "replace",
                    "sourceRouteId": "R1",
                    "stops": [
                        {"coordinate": [-77.05, 38.9]},
                        {"coordinate": [-77.03, 38.91]},
                    ],
                }
            ],
        ).reach("A", **options)
        self.assertTrue(
            any(
                route.endswith("R1")
                for route in replacement.query["scenario"]["excludedRouteIds"]
            )
        )
        self.assertEqual(replacement.to_geojson()["type"], "FeatureCollection")


if __name__ == "__main__":
    unittest.main()
