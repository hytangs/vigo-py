#!/usr/bin/env python3
"""Build and route a tiny public fixture through an installed VIGO runtime."""

from __future__ import annotations

import json
import struct
import tempfile
import zipfile
from pathlib import Path

import vigo_router


def _varint(value: int) -> bytes:
    if value < 0:
        raise ValueError("varint values must be non-negative")
    output = bytearray()
    while value > 0x7F:
        output.append((value & 0x7F) | 0x80)
        value >>= 7
    output.append(value)
    return bytes(output)


def _signed_varint(value: int) -> bytes:
    return _varint((value << 1) ^ (value >> 63))


def _field(number: int, wire_type: int) -> bytes:
    return _varint((number << 3) | wire_type)


def _varint_field(number: int, value: int) -> bytes:
    return _field(number, 0) + _varint(value)


def _signed_varint_field(number: int, value: int) -> bytes:
    return _field(number, 0) + _signed_varint(value)


def _bytes_field(number: int, value: bytes) -> bytes:
    return _field(number, 2) + _varint(len(value)) + value


def _string_field(number: int, value: str) -> bytes:
    return _bytes_field(number, value.encode("utf-8"))


def _packed_varints(number: int, values: list[int]) -> bytes:
    return _bytes_field(number, b"".join(_varint(value) for value in values))


def _packed_signed_varints(number: int, values: list[int]) -> bytes:
    return _bytes_field(number, b"".join(_signed_varint(value) for value in values))


def _osm_block(block_type: str, payload: bytes) -> bytes:
    blob = _bytes_field(1, payload) + _varint_field(2, len(payload))
    header = _string_field(1, block_type) + _varint_field(3, len(blob))
    return struct.pack(">I", len(header)) + header + blob


def _write_osm(path: Path) -> None:
    header = _string_field(4, "OsmSchema-V0.6")
    strings = b"".join(
        _string_field(1, value) for value in ("", "highway", "residential")
    )
    nodes = b"".join(
        _bytes_field(
            1,
            _varint_field(1, node_id)
            + _signed_varint_field(8, latitude)
            + _signed_varint_field(9, longitude),
        )
        for node_id, latitude, longitude in (
            (1, 389_000_000, -770_500_000),
            (2, 389_050_000, -770_400_000),
            (3, 389_100_000, -770_300_000),
        )
    )
    way = (
        _varint_field(1, 10)
        + _packed_varints(2, [1])
        + _packed_varints(3, [2])
        + _packed_signed_varints(8, [1, 1, 1])
    )
    primitive_group = nodes + _bytes_field(3, way)
    primitive_block = _bytes_field(1, strings) + _bytes_field(2, primitive_group)
    path.write_bytes(
        _osm_block("OSMHeader", header) + _osm_block("OSMData", primitive_block)
    )


def _write_gtfs(path: Path) -> None:
    tables = {
        "agency.txt": "agency_id,agency_name,agency_url,agency_timezone\nfixture,Fixture Transit,https://example.test,America/New_York\n",
        "stops.txt": "stop_id,stop_name,stop_lat,stop_lon\nA,Alpha,38.900,-77.050\nX,Transfer,38.905,-77.040\nB,Bravo,38.910,-77.030\n",
        "routes.txt": "route_id,agency_id,route_short_name,route_long_name,route_type\nR1,fixture,R1,First Route,3\nR2,fixture,R2,Second Route,3\n",
        "trips.txt": "route_id,service_id,trip_id,direction_id\nR1,WKD,T1,0\nR2,WKD,T2,0\n",
        "stop_times.txt": "trip_id,arrival_time,departure_time,stop_id,stop_sequence\nT1,08:00:00,08:00:00,A,1\nT1,08:10:00,08:10:00,X,2\nT2,08:15:00,08:15:00,X,1\nT2,08:30:00,08:30:00,B,2\n",
        "calendar.txt": "service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,start_date,end_date\nWKD,1,1,1,1,1,0,0,20260101,20261231\n",
    }
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, contents in tables.items():
            info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, contents)


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="vigo-python-live-runtime-") as temporary:
        root = Path(temporary)
        gtfs = root / "fixture.zip"
        osm = root / "fixture.osm.pbf"
        _write_gtfs(gtfs)
        _write_osm(osm)
        with vigo_router.TransportNetwork(
            osm,
            [gtfs],
            cache_dir=root / "cache",
            route_timeout=30,
            build_timeout=300,
        ) as compiled:
            store = compiled.store
            command = compiled.command
            build_summary = compiled.build_summary
        with vigo_router.open_network(
            store,
            cli=command[0],
            route_timeout=30,
        ) as network:
            plan = network.route(
                "A",
                "B",
                time="08:00",
                service_date="2026-08-31",
                service_day="weekday",
                max_walk_km=0.1,
            )
        if plan.status != "ready" or plan.route_sequence != ("R1", "R2"):
            raise AssertionError(
                "live VIGO route did not return the expected R1/R2 itinerary: "
                f"{plan.to_dict()}"
            )
        if not plan.algorithm.startswith("rust_exact_connection_scan"):
            raise AssertionError(f"unexpected live routing owner: {plan.algorithm}")
        print(
            json.dumps(
                {
                    "status": "passed",
                    "bindingVersion": vigo_router.__version__,
                    "runtimeCommand": list(command),
                    "buildSchema": build_summary.get("schemaVersion"),
                    "routeStatus": plan.status,
                    "routeSequence": list(plan.route_sequence),
                    "algorithm": plan.algorithm,
                },
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
