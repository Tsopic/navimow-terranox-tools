#!/usr/bin/env python3
"""Build a sanitized local browser map for Navimow Terranox captures."""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import hashlib
import io
import json
import math
import os
import re
import shutil
import sqlite3
import ssl
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import Any

import certifi
import navimow_state_store as store
from PIL import Image


DEFAULT_DB = Path("data/navimow.sqlite")
DEFAULT_OUTPUT = Path("viewer/navimow-map")
TEMPLATE_DIR = Path(__file__).resolve().parents[1] / "viewer_src" / "navimow-map"
VIEWER_DATA_PREFIX = "window.NAVIMOW_MAP_DATA = "
ESRI_WORLD_IMAGERY = "https://services.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"
ESRI_ATTRIBUTION = "Source: Esri, Vantor, Earthstar Geographics, and the GIS User Community"
SETTING_FIELD_RE = re.compile(r"(?P<key>[A-Za-z][A-Za-z0-9]*)=(?P<value>[^,}]*)")

DAY_NAMES = {
    1: "Sunday",
    2: "Monday",
    3: "Tuesday",
    4: "Wednesday",
    5: "Thursday",
    6: "Friday",
    7: "Saturday",
}

SCHEDULE_OPTIMIZER_DEFAULTS = {
    "defaultDay": 3,
    "defaultStart": "04:00",
    "defaultEnd": "22:00",
    "defaultM2PerHour": 250,
    "defaultMaxPeriodsPerDay": 4,
    "defaultMinDaysBetween": 2,
}
LIVE_AREA_ACTIVE_MAX_AGE_SECONDS = 24 * 60 * 60

PALETTE = [
    "#ff6b6b",
    "#f4b942",
    "#4cc9f0",
    "#76c893",
    "#b5179e",
    "#4895ef",
    "#f72585",
    "#90be6d",
    "#f8961e",
    "#43aa8b",
    "#577590",
    "#e9c46a",
]

ROUTE_CATALOG = [
    {
        "path": "/vehicle/vehicle/index2",
        "method": "POST",
        "purpose": "Device state, map version sync, partition metadata, network and battery snapshot",
        "access": "read",
        "evidence": "http_cache_entries, device_state_snapshots, app logs",
        "shape": "data.vehicle_state, soc, soh, network status/signal, mapVersion, partitionLength, partitionIdList, update times",
        "unknowns": "Exact request body and signing headers are not normalized yet",
    },
    {
        "path": "/vehicle/vehicle/get-device-info",
        "method": "POST",
        "purpose": "Static mower capabilities, limits, firmware, speed/height lists",
        "access": "read",
        "evidence": "http_cache_entries, device_info_snapshots",
        "shape": "model, display name, map limits, plan max time, mowing height list, speed lists, firmware fields",
        "unknowns": "Some commercial/account fields need continued redaction",
    },
    {
        "path": "/vehicle/vehicle/auth-list",
        "method": "POST",
        "purpose": "Authorized mower list and basic mower-card state",
        "access": "read",
        "evidence": "http_cache_entries, app logs",
        "shape": "vehicle card list with state, battery, network, type, images",
        "unknowns": "Request body and pagination behavior",
    },
    {
        "path": "/map/index/map-list",
        "method": "POST",
        "purpose": "Map list for the mower",
        "access": "read",
        "evidence": "app logger endpoint counts",
        "shape": "map records that lead to map detail fetches",
        "unknowns": "Stored as compact route snapshots; version-driven delta behavior still needs live validation",
    },
    {
        "path": "/map/index/map-detail-compress",
        "method": "POST",
        "purpose": "Full map and area detail",
        "access": "read",
        "evidence": "map_detail_snapshots, map_detail_areas, decoded logger data",
        "shape": "base64/Zstandard response with map metadata, sub_maps, obstacles, local-coordinate points",
        "unknowns": "Request envelope fields beyond observed response decode",
    },
    {
        "path": "/mowerbot/vehicle/common/get-iot-file",
        "method": "POST",
        "purpose": "Map artifact URL for the current map version",
        "access": "read",
        "evidence": "map_resource_events, map_artifacts, map_render_metadata",
        "shape": "version plus short-lived signed blob URL; local DB stores sanitized metadata and downloaded artifact",
        "unknowns": "Artifact refresh cadence and authorization edge cases",
    },
    {
        "path": "signed blob URL from get-iot-file",
        "method": "GET",
        "purpose": "Download terrain/map resource bundle",
        "access": "read",
        "evidence": "map_artifacts, map_artifact_files",
        "shape": "zip-like bundle with terrain WebP and JSON calibration",
        "unknowns": "URL expires and must not be surfaced",
    },
    {
        "path": "/vehicle/vehicle/get-location",
        "method": "POST",
        "purpose": "Live mower pose/progress",
        "access": "read",
        "evidence": "app logs, live_location_snapshots",
        "shape": "local position/progress fields; exact GPS exists in raw evidence and is suppressed",
        "unknowns": "Status enums and current-area semantics need mapping",
    },
    {
        "path": "/mowerbot/vehicle/vehicle/state",
        "method": "POST",
        "purpose": "Richer mower live state/status semantics",
        "access": "read",
        "evidence": "app logs, route_snapshot_records",
        "shape": "state/task/status payload captured as compact sanitized route snapshots",
        "unknowns": "State enum names, active task semantics, and current-area mapping need live classification",
    },
    {
        "path": "/vehicle/vehicle/get-vehicle-weather",
        "method": "POST",
        "purpose": "Weather condition flags",
        "access": "read",
        "evidence": "app logs",
        "shape": "rainState, rainLevel, frostState, snowState, stormState, highTemperatureState",
        "unknowns": "Enum semantics need app-resource mapping",
    },
    {
        "path": "/vehicle/vehicle/get-today-plan",
        "method": "POST",
        "purpose": "Current-day task/plan refresh around schedule saves",
        "access": "read",
        "evidence": "app logs, notes/navimow-schedule-cli-plan.md",
        "shape": "current plan start/end ticks, task status, partition details",
        "unknowns": "Appears to validate/refresh rather than define weekly schedule",
    },
    {
        "path": "/vehicle/vehicle/set-list",
        "method": "POST",
        "purpose": "Full settings snapshot",
        "access": "read",
        "evidence": "app logs, area_setting_snapshots",
        "shape": "startPlan, mowingCycle, cutting height, weather switches, child lock, DND, battery thresholds",
        "unknowns": "Not fully normalized; many settings need enum decoding",
    },
    {
        "path": "/mowerbot/vehicle/set/send",
        "method": "POST",
        "purpose": "Generic IoT command send for schedule/settings writes",
        "access": "write",
        "evidence": "command_envelopes, app logs, notes",
        "shape": "response includes command number/status; outgoing command envelope is encrypted/opaque",
        "unknowns": "Exact signed/encrypted body and headers remain the blocker for direct writes",
    },
    {
        "path": "/vehicle/set/response",
        "method": "POST",
        "purpose": "Poll command result",
        "access": "read-after-write",
        "evidence": "app logs, notes",
        "shape": "status plus small response body marker",
        "unknowns": "Full status enum",
    },
    {
        "path": "/vehicle/set/save-set-data",
        "method": "POST",
        "purpose": "Persist a setting after command success",
        "access": "write",
        "evidence": "app logs, notes",
        "shape": "boolean/success response",
        "unknowns": "Required fields and ordering for all setting types",
    },
    {
        "path": "/vehicle/trail/get-path-info-time",
        "method": "POST",
        "purpose": "Historical/path time index",
        "access": "read",
        "evidence": "app logs, trail_time_snapshots, trail_time_entries",
        "shape": "path/time index entries",
        "unknowns": "Compressed trail replay still needs decoding",
    },
    {
        "path": "/vehicle/trail/get-path-info-data-compress",
        "method": "POST",
        "purpose": "Compressed path/trail data",
        "access": "read",
        "evidence": "app logs, route_snapshot_records when synced",
        "shape": "compressed trail payload stored as a sanitized compact snapshot for now",
        "unknowns": "Compression/envelope and local path point schema still need decoding",
    },
    {
        "path": "/vehicle/firmware/get-new-firmware",
        "method": "POST",
        "purpose": "Firmware update check",
        "access": "read",
        "evidence": "http_cache_entries, app logs",
        "shape": "firmware update list",
        "unknowns": "Not central to local operations console",
    },
    {
        "path": "/vehicle/vehicle/get-component-maintenance",
        "method": "POST",
        "purpose": "Maintenance counters",
        "access": "read",
        "evidence": "app logs",
        "shape": "battery/chassis/knife maintenance counters",
        "unknowns": "Units and reset behavior need mapping",
    },
    {
        "path": "/openapi/oauth/getAccessToken",
        "method": "POST form",
        "purpose": "OAuth authorization-code exchange and refresh",
        "access": "auth",
        "evidence": "navimow-sdk, NavimowHA, ioBroker.navimow",
        "shape": "access token, optional refresh token, expiry metadata",
        "unknowns": "Token values stay local-only and are never exported",
    },
    {
        "path": "/openapi/smarthome/authList",
        "method": "GET",
        "purpose": "OpenAPI mower/device discovery",
        "access": "read",
        "evidence": "navimow-sdk, NavimowHA, ioBroker.navimow, route_snapshot_records",
        "shape": "device list/card payload",
        "unknowns": "OpenAPI device identifiers are local-only",
    },
    {
        "path": "/openapi/smarthome/getVehicleStatus",
        "method": "POST",
        "purpose": "OpenAPI mower status cards",
        "access": "read",
        "evidence": "navimow-sdk, NavimowHA, ioBroker.navimow, route_snapshot_records",
        "shape": "status payload for configured device ids",
        "unknowns": "Needs fresh local status samples for typed field promotion",
    },
    {
        "path": "/openapi/mqtt/userInfo/get/v2",
        "method": "GET",
        "purpose": "OpenAPI MQTT connection metadata",
        "access": "read",
        "evidence": "navimow-sdk, NavimowHA, ioBroker.navimow, route_snapshot_records",
        "shape": "MQTT host/path/user/password metadata",
        "unknowns": "Credentials and URL-like values are sanitized from snapshots",
    },
    {
        "path": "/openapi/smarthome/responseCommands",
        "method": "POST",
        "purpose": "OpenAPI command result polling",
        "access": "read-after-write",
        "evidence": "navimow-sdk",
        "shape": "command result status payload",
        "unknowns": "Only useful if command sends are explicitly implemented later",
    },
    {
        "path": "/openapi/smarthome/sendCommands",
        "method": "POST",
        "purpose": "OpenAPI basic mower commands",
        "access": "write",
        "evidence": "navimow-sdk, ioBroker.navimow",
        "shape": "Google Smart Home command execution payload",
        "unknowns": "Deliberately refused by this read-only client; does not expose schedule CRUD",
    },
]


def now_iso() -> str:
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat()


def parse_iso_datetime(value: Any) -> dt.datetime | None:
    if not isinstance(value, str) or not value:
        return None
    text = value.replace("Z", "+00:00")
    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.UTC)
    return parsed.astimezone(dt.UTC)


def seconds_since(value: Any, now: dt.datetime) -> int | None:
    parsed = parse_iso_datetime(value)
    if parsed is None:
        return None
    return int((now - parsed).total_seconds())


def tick_to_time(tick: int) -> str:
    if tick == 96:
        return "24:00"
    minutes = tick * 15
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def parse_json(value: str | None, fallback: Any = None) -> Any:
    if value is None:
        return fallback
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return fallback


def safe_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def safe_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def short_hash(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    if not text:
        return None
    return hashlib.sha256(text.encode()).hexdigest()[:12]


def normalize_setting_value(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    if value in {"", "null", "None"}:
        return None
    return value


def parse_setting_fields(raw_text: str | None) -> dict[str, str]:
    if not raw_text:
        return {}
    parsed = parse_json(raw_text, None)
    if isinstance(parsed, dict):
        return {
            str(key): str(value)
            for key, value in parsed.items()
            if normalize_setting_value(str(value) if value is not None else None) is not None
        }
    return {
        match.group("key"): value
        for match in SETTING_FIELD_RE.finditer(raw_text)
        if (value := normalize_setting_value(match.group("value"))) is not None
    }


def load_latest_setting_fields(con: sqlite3.Connection) -> dict[str, Any]:
    if not table_exists(con, "area_setting_snapshots"):
        return {"snapshotId": None, "observedAt": None, "fields": {}}
    row = con.execute(
        """
        SELECT id, observed_at, raw_text
        FROM area_setting_snapshots
        WHERE raw_text LIKE '%height=%'
           OR raw_text LIKE '%cutterHeight=%'
           OR raw_text LIKE '%"height"%'
           OR raw_text LIKE '%"cutterHeight"%'
        ORDER BY id DESC
        LIMIT 1
        """
    ).fetchone()
    if row is None:
        return {"snapshotId": None, "observedAt": None, "fields": {}}
    return {
        "snapshotId": int(row["id"]),
        "observedAt": row["observed_at"],
        "fields": parse_setting_fields(row["raw_text"]),
    }


def normalize_cutting_height(value: Any) -> int | None:
    height = safe_int(value)
    if height is None:
        return None
    if 10 <= height <= 120:
        return height
    return None


def first_cutting_height(values: list[Any]) -> int | None:
    for value in values:
        height = normalize_cutting_height(value)
        if height is not None:
            return height
    return None


def epoch_to_iso(value: Any) -> str | None:
    timestamp = safe_int(value)
    if timestamp is None or timestamp <= 0:
        return None
    try:
        return dt.datetime.fromtimestamp(timestamp, dt.UTC).replace(microsecond=0).isoformat()
    except (OSError, OverflowError, ValueError):
        return None


def table_exists(con: sqlite3.Connection, table: str) -> bool:
    row = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    return row is not None


def load_map_detail(con: sqlite3.Connection) -> tuple[sqlite3.Row, dict[str, Any]]:
    row = con.execute(
        """
        SELECT id, observed_at, map_name, total_area, detail_area, raw_json
        FROM map_detail_snapshots
        ORDER BY id DESC
        LIMIT 1
        """
    ).fetchone()
    if row is None:
        raise SystemExit("No map_detail_snapshots rows found. Run navimow_state_store.py ingest first.")

    raw = json.loads(row["raw_json"])
    if isinstance(raw, list):
        raw = raw[0] if raw else {}

    detail = raw.get("map_detail") or raw.get("detail") or raw
    if isinstance(detail, str):
        detail = json.loads(detail)
    if not isinstance(detail, dict):
        raise SystemExit("Map detail payload did not decode to an object.")

    return row, detail


def load_latest_json_snapshot(con: sqlite3.Connection, table: str) -> dict[str, Any]:
    row = con.execute(f"SELECT raw_json FROM {table} ORDER BY id DESC LIMIT 1").fetchone()
    if row is None:
        return {}
    try:
        return json.loads(row["raw_json"])
    except json.JSONDecodeError:
        return {}


def load_render_metadata(con: sqlite3.Connection) -> tuple[sqlite3.Row, Path, str]:
    row = con.execute(
        """
        SELECT
            mrm.width,
            mrm.height,
            mrm.min_x,
            mrm.max_x,
            mrm.min_y,
            mrm.max_y,
            mrm.pixel_per_meter,
            mrm.terrain_view_image_name,
            ma.file_path
        FROM map_render_metadata mrm
        JOIN map_artifacts ma ON ma.id = mrm.artifact_id
        ORDER BY ma.id DESC, mrm.id DESC
        LIMIT 1
        """
    ).fetchone()
    if row is None:
        raise SystemExit("No map_render_metadata rows found. Run ingest with --download-maps first.")

    image_name = row["terrain_view_image_name"]
    if not image_name:
        raise SystemExit("Map render metadata did not include a terrain image name.")

    return row, Path(row["file_path"]), image_name


def local_to_pixel(point: list[Any], render: sqlite3.Row) -> list[float] | None:
    if not isinstance(point, list) or len(point) < 2:
        return None
    try:
        x = float(point[0])
        y = float(point[1])
    except (TypeError, ValueError):
        return None

    col = (x - float(render["min_x"])) * float(render["pixel_per_meter"])
    row = (float(render["max_y"]) - y) * float(render["pixel_per_meter"])
    return [round(col, 2), round(row, 2)]


def pixel_to_local(pixel_x: float, pixel_y: float, render: sqlite3.Row) -> tuple[float, float]:
    x = float(render["min_x"]) + pixel_x / float(render["pixel_per_meter"])
    y = float(render["max_y"]) - pixel_y / float(render["pixel_per_meter"])
    return x, y


def local_to_lon_lat(x: float, y: float, detail: dict[str, Any]) -> tuple[float, float] | None:
    center_gps = detail.get("center_gps")
    center_local = detail.get("map_circle_center")
    if not (
        isinstance(center_gps, list)
        and len(center_gps) >= 2
        and isinstance(center_local, list)
        and len(center_local) >= 2
    ):
        return None

    center_lon = safe_float(center_gps[0])
    center_lat = safe_float(center_gps[1])
    center_x = safe_float(center_local[0])
    center_y = safe_float(center_local[1])
    if None in {center_lon, center_lat, center_x, center_y}:
        return None

    north_offset = safe_float(detail.get("map_north_offset")) or 0.0
    dx = x - center_x
    dy = y - center_y
    if abs(north_offset) > 0.000001:
        angle = math.radians(north_offset)
        dx, dy = (
            dx * math.cos(angle) - dy * math.sin(angle),
            dx * math.sin(angle) + dy * math.cos(angle),
        )

    meters_per_degree_lat = 111_320.0
    meters_per_degree_lon = meters_per_degree_lat * math.cos(math.radians(center_lat))
    if abs(meters_per_degree_lon) < 0.000001:
        return None
    lon = center_lon + dx / meters_per_degree_lon
    lat = center_lat + dy / meters_per_degree_lat
    return lon, lat


def lon_lat_to_tile_pixel(lon: float, lat: float, zoom: int) -> tuple[float, float]:
    lat = max(min(lat, 85.05112878), -85.05112878)
    scale = 256 * (2**zoom)
    x = (lon + 180.0) / 360.0 * scale
    sin_lat = math.sin(math.radians(lat))
    y = (0.5 - math.log((1 + sin_lat) / (1 - sin_lat)) / (4 * math.pi)) * scale
    return x, y


def fetch_tile(url: str) -> Image.Image:
    request = urllib.request.Request(url, headers={"User-Agent": "NavimowLocalViewer/1.0"})
    context = ssl.create_default_context(cafile=certifi.where())
    with urllib.request.urlopen(request, timeout=20, context=context) as response:
        data = response.read()
    return Image.open(io.BytesIO(data)).convert("RGB")


def build_satellite_image(
    detail: dict[str, Any],
    render: sqlite3.Row,
    output_assets: Path,
    zoom: int,
) -> str | None:
    top_left_local = pixel_to_local(0, 0, render)
    bottom_right_local = pixel_to_local(float(render["width"]), float(render["height"]), render)
    top_left = local_to_lon_lat(*top_left_local, detail)
    bottom_right = local_to_lon_lat(*bottom_right_local, detail)
    if top_left is None or bottom_right is None:
        return None

    left_px, top_px = lon_lat_to_tile_pixel(top_left[0], top_left[1], zoom)
    right_px, bottom_px = lon_lat_to_tile_pixel(bottom_right[0], bottom_right[1], zoom)
    min_px_x, max_px_x = sorted([left_px, right_px])
    min_px_y, max_px_y = sorted([top_px, bottom_px])

    min_tile_x = math.floor(min_px_x / 256)
    max_tile_x = math.floor(max_px_x / 256)
    min_tile_y = math.floor(min_px_y / 256)
    max_tile_y = math.floor(max_px_y / 256)
    tile_count = (max_tile_x - min_tile_x + 1) * (max_tile_y - min_tile_y + 1)
    if tile_count > 180:
        raise SystemExit(f"Satellite tile request would fetch {tile_count} tiles; lower --satellite-zoom.")

    mosaic = Image.new("RGB", ((max_tile_x - min_tile_x + 1) * 256, (max_tile_y - min_tile_y + 1) * 256))
    for tile_x in range(min_tile_x, max_tile_x + 1):
        for tile_y in range(min_tile_y, max_tile_y + 1):
            url = ESRI_WORLD_IMAGERY.format(z=zoom, y=tile_y, x=tile_x)
            tile = fetch_tile(url)
            mosaic.paste(tile, ((tile_x - min_tile_x) * 256, (tile_y - min_tile_y) * 256))

    crop = (
        int(round(min_px_x - min_tile_x * 256)),
        int(round(min_px_y - min_tile_y * 256)),
        int(round(max_px_x - min_tile_x * 256)),
        int(round(max_px_y - min_tile_y * 256)),
    )
    crop = (
        max(0, crop[0]),
        max(0, crop[1]),
        min(mosaic.width, crop[2]),
        min(mosaic.height, crop[3]),
    )
    satellite = mosaic.crop(crop).resize((int(render["width"]), int(render["height"])), Image.Resampling.LANCZOS)
    output_assets.mkdir(parents=True, exist_ok=True)
    output_path = output_assets / "satellite.webp"
    satellite.save(output_path, "WEBP", quality=88, method=6)
    return "assets/satellite.webp"


def polygon_area(points: list[list[float]]) -> float:
    if len(points) < 3:
        return 0.0
    total = 0.0
    for index, point in enumerate(points):
        nxt = points[(index + 1) % len(points)]
        total += point[0] * nxt[1] - nxt[0] * point[1]
    return total / 2.0


def polygon_centroid(points: list[list[float]]) -> list[float] | None:
    if not points:
        return None

    area = polygon_area(points)
    if abs(area) < 0.000001:
        return [
            round(sum(point[0] for point in points) / len(points), 2),
            round(sum(point[1] for point in points) / len(points), 2),
        ]

    cx = 0.0
    cy = 0.0
    for index, point in enumerate(points):
        nxt = points[(index + 1) % len(points)]
        cross = point[0] * nxt[1] - nxt[0] * point[1]
        cx += (point[0] + nxt[0]) * cross
        cy += (point[1] + nxt[1]) * cross
    factor = 1 / (6 * area)
    return [round(cx * factor, 2), round(cy * factor, 2)]


def first_number(values: list[Any]) -> int | float | None:
    for value in values:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return value
    return None


def unique_numbers(values: list[Any]) -> list[int | float]:
    found: list[int | float] = []
    for value in values:
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value not in found:
            found.append(value)
    return found


def extract_areas(detail: dict[str, Any], render: sqlite3.Row) -> list[dict[str, Any]]:
    areas: list[dict[str, Any]] = []
    sub_maps = sorted(detail.get("sub_maps", []), key=lambda item: item.get("id", 0))

    for index, sub_map in enumerate(sub_maps):
        elements = sub_map.get("elements", [])
        boundary_elements = []
        tunnel_lines = []
        all_points: list[list[float]] = []
        height_values = []
        mow_edge_values = []
        obstacle_mow_edge_values = []
        boundary_types = []
        rec_base_angles = []
        clock_directions = []
        avai_segs = []

        for element in elements:
            points = [
                pixel
                for point in element.get("points", [])
                if (pixel := local_to_pixel(point, render)) is not None
            ]
            if points:
                boundary_elements.append(
                    {
                        "id": element.get("id"),
                        "name": element.get("name") or sub_map.get("name"),
                        "type": element.get("type"),
                        "points": points,
                    }
                )
                all_points.extend(points)

            tunnel = element.get("tunnel")
            if isinstance(tunnel, dict):
                tunnel_points = [
                    pixel
                    for point in tunnel.get("points", [])
                    if (pixel := local_to_pixel(point, render)) is not None
                ]
                if len(tunnel_points) >= 2:
                    tunnel_lines.append(
                        {
                            "id": tunnel.get("id"),
                            "name": tunnel.get("name") or "Tunnel",
                            "type": tunnel.get("type"),
                            "points": tunnel_points,
                        }
                    )

            height_values.append(element.get("height_set"))
            mow_edge_values.append(element.get("mow_edge"))
            obstacle_mow_edge_values.append(element.get("obstacle_mow_edge"))
            boundary_types.append(element.get("boundary_type"))
            rec_base_angles.append(element.get("rec_base_angle"))
            clock_directions.append(element.get("clock_direction"))
            avai_segs.append(element.get("avai_segs"))

        label = polygon_centroid(all_points)
        if label is None and boundary_elements:
            label = polygon_centroid(boundary_elements[0]["points"])

        area_id = int(sub_map.get("id"))
        raw_height_set = unique_numbers(height_values)
        area_height_mm = first_cutting_height(raw_height_set)
        areas.append(
            {
                "id": area_id,
                "name": sub_map.get("name") or f"Area {area_id}",
                "sizeM2": round(float(sub_map.get("area") or 0), 1),
                "type": sub_map.get("type") or "SUB_MAP",
                "color": PALETTE[index % len(PALETTE)],
                "label": label,
                "elements": boundary_elements,
                "tunnels": tunnel_lines,
                "cutting": {
                    "areaHeightMm": area_height_mm,
                    "effectiveHeightMm": area_height_mm,
                    "source": "area height_set" if area_height_mm is not None else "pending global setting",
                    "rawHeightSet": raw_height_set,
                },
                "lastMow": {
                    "status": "not_synced",
                    "lastAt": None,
                    "source": "trail history not normalized",
                    "note": "Needs /vehicle/trail/get-path-info-time plus /vehicle/trail/get-path-info-data-compress decoding and area intersection.",
                },
                "settings": {
                    "heightSet": raw_height_set,
                    "mowEdge": unique_numbers(mow_edge_values),
                    "obstacleMowEdge": unique_numbers(obstacle_mow_edge_values),
                    "boundaryType": unique_numbers(boundary_types),
                    "recBaseAngle": unique_numbers(rec_base_angles),
                    "clockDirection": unique_numbers(clock_directions),
                    "availableSegments": first_number(avai_segs),
                    "containedObstacles": len(sub_map.get("contain_obstacles_id") or []),
                    "elementCount": len(elements),
                    "pointCount": sum(len(element["points"]) for element in boundary_elements),
                },
            }
        )

    return areas


def extract_obstacles(detail: dict[str, Any], render: sqlite3.Row) -> list[dict[str, Any]]:
    obstacles = []
    for obstacle in detail.get("obstacles", []):
        points = [
            pixel
            for point in obstacle.get("points", [])
            if (pixel := local_to_pixel(point, render)) is not None
        ]
        if len(points) < 3:
            continue
        obstacles.append(
            {
                "id": obstacle.get("id"),
                "type": obstacle.get("type") or "OBSTACLE",
                "areaM2": round(float(obstacle.get("area") or 0), 2),
                "status": obstacle.get("status"),
                "points": points,
            }
        )
    return obstacles


def load_schedule(con: sqlite3.Connection) -> dict[str, Any]:
    required_tables = ("schedule_snapshots", "schedule_days", "schedule_periods")
    if any(not table_exists(con, table) for table in required_tables):
        return {"snapshotId": None, "periods": [], "customPeriods": [], "allZonePeriods": []}

    snapshot = con.execute(
        """
        SELECT
            ss.id,
            ss.observed_at,
            SUM(CASE
                WHEN sp.partition_ids_json IS NOT NULL
                 AND sp.partition_ids_json NOT IN ('[]', 'null', '""')
                THEN 1 ELSE 0 END) AS custom_period_count
        FROM schedule_snapshots ss
        JOIN schedule_days sd ON sd.snapshot_id = ss.id
        LEFT JOIN schedule_periods sp ON sp.day_id = sd.id
        GROUP BY ss.id
        ORDER BY custom_period_count DESC, ss.id DESC
        LIMIT 1
        """
    ).fetchone()
    if snapshot is None:
        return {"snapshotId": None, "periods": [], "customPeriods": [], "allZonePeriods": []}

    rows = con.execute(
        """
        SELECT sd.day, sd.open, sp.period_index, sp.start_tick, sp.end_tick, sp.partition_ids_json
        FROM schedule_days sd
        LEFT JOIN schedule_periods sp ON sp.day_id = sd.id
        WHERE sd.snapshot_id = ?
        ORDER BY sd.day, sp.period_index
        """,
        (snapshot["id"],),
    ).fetchall()

    periods = []
    for row in rows:
        if row["start_tick"] is None:
            continue
        partition_ids = parse_json(row["partition_ids_json"], []) or []
        partition_ids = [int(value) for value in partition_ids if str(value).isdigit()]
        period = {
            "day": int(row["day"]),
            "dayName": DAY_NAMES.get(int(row["day"]), f"Day {row['day']}"),
            "open": int(row["open"]) if row["open"] is not None else None,
            "startTick": int(row["start_tick"]),
            "endTick": int(row["end_tick"]),
            "start": tick_to_time(int(row["start_tick"])),
            "end": tick_to_time(int(row["end_tick"])),
            "partitionIds": partition_ids,
            "mode": "custom" if partition_ids else "all_zones",
        }
        periods.append(period)

    custom_periods = [period for period in periods if period["partitionIds"]]
    all_zone_periods = [period for period in periods if not period["partitionIds"]]
    by_area: dict[str, list[dict[str, Any]]] = {}
    for period in custom_periods:
        for area_id in period["partitionIds"]:
            by_area.setdefault(str(area_id), []).append(period)

    return {
        "snapshotId": int(snapshot["id"]),
        "observedAt": snapshot["observed_at"],
        "periods": periods,
        "customPeriods": custom_periods,
        "allZonePeriods": all_zone_periods,
        "byArea": by_area,
    }


def schedule_to_draft(schedule: dict[str, Any]) -> dict[str, Any]:
    days = []
    for day in range(1, 8):
        day_periods = [period for period in schedule.get("periods", []) if period["day"] == day]
        days.append(
            {
                "day": day,
                "dayName": DAY_NAMES[day],
                "open": 1 if day_periods else 0,
                "periods": [
                    {
                        "start": period["start"],
                        "end": period["end"],
                        "startTick": period["startTick"],
                        "endTick": period["endTick"],
                        "partitionIds": period["partitionIds"],
                        "mode": period["mode"],
                    }
                    for period in day_periods
                ],
            }
        )
    return {
        "version": 1,
        "baseSnapshotId": schedule.get("snapshotId"),
        "baseObservedAt": schedule.get("observedAt"),
        "days": days,
    }


def schedule_optimizer_context(schedule: dict[str, Any]) -> dict[str, Any]:
    return {
        "dryRunOnly": True,
        "status": "browser_and_cli_preview_only",
        **SCHEDULE_OPTIMIZER_DEFAULTS,
        "baseSnapshotId": schedule.get("snapshotId"),
        "baseObservedAt": schedule.get("observedAt"),
        "inputs": [
            "area size",
            "last mow status",
            "completion percentage",
            "cutting height",
            "weather flags",
            "mower status",
        ],
        "blockedWrites": [
            "/mowerbot/vehicle/set/send",
            "/vehicle/set/response",
            "/vehicle/set/save-set-data",
        ],
        "privacy": "Local draft only; no mower command is sent.",
    }


def stable_json_hash(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]


def viewer_layout_version(data: dict[str, Any]) -> str:
    map_data = data.get("map") or {}
    structural = {
        "map": {
            "name": map_data.get("name"),
            "mode": map_data.get("mode"),
            "statusOnly": bool(map_data.get("statusOnly")),
            "backgrounds": map_data.get("backgrounds"),
            "satellite": map_data.get("satellite"),
            "width": map_data.get("width"),
            "height": map_data.get("height"),
            "bounds": map_data.get("bounds"),
            "totalAreaM2": map_data.get("totalAreaM2"),
            "areaCount": map_data.get("areaCount"),
            "obstacleCount": map_data.get("obstacleCount"),
            "customSelectedAreaM2": map_data.get("customSelectedAreaM2"),
        },
        "areas": [
            {
                "id": area.get("id"),
                "name": area.get("name"),
                "sizeM2": area.get("sizeM2"),
                "centroid": area.get("centroid"),
                "points": area.get("points"),
                "schedule": area.get("schedule"),
                "cutting": pick_existing(
                    area.get("cutting"),
                    ("areaHeightMm", "rawHeightSet"),
                ),
            }
            for area in data.get("areas", [])
        ],
        "obstacles": data.get("obstacles", []),
        "scheduleDraft": data.get("scheduleDraft"),
    }
    return stable_json_hash(structural)


def pick_existing(source: Any, keys: tuple[str, ...]) -> dict[str, Any]:
    if not isinstance(source, dict):
        return {}
    return {key: source.get(key) for key in keys if key in source}


def build_live_mower_status(mower: dict[str, Any]) -> dict[str, Any]:
    insights = mower.get("routeInsights") or {}
    route_snapshots = mower.get("routeSnapshots") or {}
    safe_snapshot_aliases = (
        "mower-state",
        "today-plan",
        "weather",
        "maintenance",
        "firmware",
        "trail-data",
        "auth-list",
        "openapi-auth-list",
        "openapi-vehicle-status",
        "openapi-mqtt-info",
        "mqtt-message",
        "map-list",
        "get-iot-file",
    )
    safe_insights: dict[str, Any] = {
        "openapiAuth": pick_existing(insights.get("openapiAuth"), ("observedAt", "deviceCount")),
        "openapiStatus": pick_existing(
            insights.get("openapiStatus"),
            ("observedAt", "vehicleState", "capacityPercent", "capacityLabel"),
        ),
        "mqtt": pick_existing(insights.get("mqtt"), ("observedAt", "configured", "topicCount")),
        "mqttStatus": pick_existing(
            insights.get("mqttStatus"),
            (
                "observedAt",
                "reportAt",
                "state",
                "taskStatus",
                "workStatus",
                "batterySoc",
                "capacityLabel",
                "currentPartitionId",
                "mowingPercentage",
                "pathId",
                "eventType",
                "source",
            ),
        ),
        "mqttMessages": pick_existing(
            insights.get("mqttMessages"),
            (
                "observedAt",
                "totalMessages",
                "observedTopicCount",
                "payloadShapes",
                "messageClasses",
                "statusSnapshotCount",
                "latest",
                "privacy",
            ),
        ),
        "weather": pick_existing(insights.get("weather"), ("observedAt", "flags")),
        "todayPlan": pick_existing(insights.get("todayPlan"), ("observedAt", "status", "start", "end", "partitionCount")),
        "consumerLiveState": pick_existing(
            insights.get("consumerLiveState"),
            ("observedAt", "state", "batterySoc", "currentPartitionId"),
        ),
        "maintenance": pick_existing(insights.get("maintenance"), ("observedAt", "itemCount")),
        "firmwareUpdate": pick_existing(insights.get("firmwareUpdate"), ("observedAt", "itemCount")),
    }
    return {
        "stateCode": mower.get("stateCode"),
        "observed": pick_existing(mower.get("observed"), ("stateObservedAt", "infoObservedAt", "settingsObservedAt")),
        "battery": pick_existing(
            mower.get("battery"),
            ("soc", "soh", "chargeRemainingMinutes", "chargingLimitRecommend", "returnBatteryLevelRecommend"),
        ),
        "network": pick_existing(mower.get("network"), ("type", "status", "signal", "signal4g", "signalWifi")),
        "map": pick_existing(mower.get("map"), ("version", "settingUpdateTime", "infoUpdateTime", "partitionLength")),
        "cutting": pick_existing(
            mower.get("cutting"),
            ("heightMm", "cutterHeightCode", "supportedMm", "sourceRoute", "observedAt"),
        ),
        "liveLocation": pick_existing(
            mower.get("liveLocation"),
            (
                "observedAt",
                "reportAt",
                "positionPixel",
                "postureTheta",
                "mowingPercentage",
                "pathId",
                "subtotalAreaM2",
                "mowingWeekAreaM2",
                "source",
            ),
        ),
        "routeInsights": {key: value for key, value in safe_insights.items() if value},
        "routeSnapshots": {
            alias: pick_existing(route_snapshots.get(alias), ("observedAt", "itemCount", "shape", "routeAlias"))
            for alias in safe_snapshot_aliases
            if alias in route_snapshots
        },
        "sync": {
            "batteryAndState": pick_existing(
                (mower.get("sync") or {}).get("batteryAndState"),
                ("route", "cadenceSeconds", "activeCadenceSeconds", "idleCadenceSeconds", "status"),
            ),
            "liveLocation": pick_existing(
                (mower.get("sync") or {}).get("liveLocation"),
                ("route", "cadenceSeconds", "activeCadenceSeconds", "idleCadenceSeconds", "status"),
            ),
            "lastMowPerArea": pick_existing((mower.get("sync") or {}).get("lastMowPerArea"), ("routes", "cadenceSeconds", "status")),
        },
    }


def safe_area_last_mow_status(last_mow: Any) -> dict[str, Any]:
    return pick_existing(
        last_mow,
        (
            "status",
            "lastAt",
            "startedAt",
            "source",
            "observedAt",
            "partitionPercentage",
            "areaM2",
            "finishedAreaM2",
            "confidence",
            "note",
        ),
    )


def safe_area_cutting_status(area: dict[str, Any], global_height_mm: Any) -> dict[str, Any]:
    cutting = area.get("cutting") or {}
    if not isinstance(cutting, dict):
        return {}
    area_height_mm = cutting.get("areaHeightMm")
    effective_height = area_height_mm if area_height_mm is not None else global_height_mm
    source = "area height_set" if area_height_mm is not None else "global mower height"
    return {
        "areaHeightMm": area_height_mm,
        "effectiveHeightMm": effective_height,
        "source": source,
    }


def classify_live_activity(value: Any) -> str | None:
    if value in (None, ""):
        return None
    raw_text = str(value).strip()
    text = raw_text.lower()
    if not text:
        return None
    normalized = re.sub(r"[^a-z0-9]+", "_", text).strip("_")
    camel_tokens = re.sub(r"(?<!^)(?=[A-Z])", "_", raw_text).lower()
    tokens = {item for item in re.split(r"[^a-z0-9]+", camel_tokens) if item}
    tokens.update(item for item in normalized.split("_") if item)
    if normalized in {"notrunning", "idle", "docked", "charging", "standby", "stopped", "complete", "completed"}:
        return "idle"
    if "not" in tokens and ("running" in tokens or "mowing" in tokens or "working" in tokens):
        return "idle"
    if tokens & {"idle", "docked", "charging", "standby", "stopped", "stop", "complete", "completed"}:
        return "idle"
    if normalized in {"active", "isrunning", "running", "mowing", "working", "returning", "paused", "moving"}:
        return "active"
    if tokens & {"running", "mowing", "mow", "working", "returning", "paused", "moving", "active"}:
        return "active"
    return None


def is_live_candidate_fresh(candidate: dict[str, Any], now: dt.datetime, max_age_seconds: int) -> bool:
    timestamp = candidate.get("reportAt") or candidate.get("observedAt")
    age = seconds_since(timestamp, now)
    return age is None or -300 <= age <= max_age_seconds


def is_live_candidate_active(candidate: dict[str, Any], now: dt.datetime, max_age_seconds: int) -> bool:
    if not is_live_candidate_fresh(candidate, now, max_age_seconds):
        return False
    classifications = [
        classify_live_activity(candidate.get("state")),
        classify_live_activity(candidate.get("workStatus")),
        classify_live_activity(candidate.get("taskStatus")),
        classify_live_activity(candidate.get("eventType")),
    ]
    if "idle" in classifications:
        return False
    if "active" in classifications:
        return True
    progress = safe_int(candidate.get("mowingPercentage"))
    return progress is not None and 0 < progress < 100


def current_area_live_status(
    mower: dict[str, Any],
    *,
    now: dt.datetime,
    max_age_seconds: int = LIVE_AREA_ACTIVE_MAX_AGE_SECONDS,
) -> tuple[int | None, dict[str, Any]]:
    insights = mower.get("routeInsights") or {}
    candidates = [
        insights.get("mqttStatus"),
        insights.get("consumerLiveState"),
        mower.get("liveLocation"),
    ]
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        area_id = safe_int(candidate.get("currentPartitionId"))
        if area_id is None:
            area_id = safe_int(candidate.get("partitionId"))
        if area_id is None and candidate is mower.get("liveLocation"):
            area_id = safe_int(candidate.get("pathId"))
        if area_id is None:
            continue
        if not is_live_candidate_active(candidate, now, max_age_seconds):
            continue
        live = pick_existing(
            candidate,
            (
                "observedAt",
                "reportAt",
                "state",
                "taskStatus",
                "workStatus",
                "currentPartitionId",
                "mowingPercentage",
                "pathId",
                "eventType",
                "source",
            ),
        )
        live["active"] = True
        live.setdefault("currentPartitionId", area_id)
        return area_id, live
    return None, {}


def build_area_status(data: dict[str, Any]) -> dict[str, Any]:
    mower = data.get("mower") or {}
    global_height_mm = (mower.get("cutting") or {}).get("heightMm")
    now = parse_iso_datetime(data.get("generatedAt")) or dt.datetime.now(dt.UTC)
    current_area_id, current_live = current_area_live_status(mower, now=now)
    area_status: dict[str, Any] = {}
    for area in data.get("areas", []):
        if not isinstance(area, dict):
            continue
        area_id = safe_int(area.get("id"))
        if area_id is None:
            continue
        live = {"active": area_id == current_area_id}
        if live["active"]:
            live.update(current_live)
        entry = {
            "id": area_id,
            "lastMow": safe_area_last_mow_status(area.get("lastMow")),
            "cutting": safe_area_cutting_status(area, global_height_mm),
            "live": live,
        }
        area_status[str(area_id)] = {key: value for key, value in entry.items() if value != {}}
    return area_status


def build_live_status(data: dict[str, Any]) -> dict[str, Any]:
    map_data = data.get("map") or {}
    schedule_draft = data.get("scheduleDraft") or {}
    live_status = data.get("liveStatus") or {}
    return {
        "version": 1,
        "generatedAt": data.get("generatedAt"),
        "layoutVersion": live_status.get("layoutVersion") or viewer_layout_version(data),
        "map": {
            "name": map_data.get("name"),
            "mode": map_data.get("mode"),
            "statusOnly": bool(map_data.get("statusOnly")),
            "areaCount": map_data.get("areaCount"),
            "obstacleCount": map_data.get("obstacleCount"),
            "customSelectedAreaM2": map_data.get("customSelectedAreaM2"),
        },
        "schedule": {
            "baseSnapshotId": schedule_draft.get("baseSnapshotId"),
            "baseObservedAt": schedule_draft.get("baseObservedAt"),
        },
        "areaStatus": build_area_status(data),
        "mower": build_live_mower_status(data.get("mower") or {}),
        "privacy": (
            "Sanitized local status only; no polygons, raw route payloads, tokens, "
            "serials, signed URLs, MQTT credentials, or GPS arrays are included."
        ),
    }


def trail_time_status(row: sqlite3.Row) -> str:
    percentage = safe_int(row["partition_percentage"]) or 0
    finished = safe_float(row["finished_area_m2"]) or 0.0
    area = safe_float(row["area_m2"]) or 0.0
    if percentage >= 98 or (area > 0 and finished >= area * 0.98):
        return "completed"
    if percentage > 0 or finished > 0:
        return "partial"
    return "no_mow_in_history"


def load_area_last_mow(con: sqlite3.Connection) -> dict[int, dict[str, Any]]:
    if not table_exists(con, "trail_time_entries") or not table_exists(con, "trail_time_snapshots"):
        return {}

    rows = con.execute(
        """
        SELECT
            e.partition_id,
            e.start_time,
            e.end_time,
            e.end_time_alias,
            e.area_m2,
            e.finished_area_m2,
            e.partition_percentage,
            s.id AS snapshot_id,
            s.observed_at AS observed_at
        FROM trail_time_entries e
        JOIN trail_time_snapshots s ON s.id = e.snapshot_id
        WHERE e.partition_id IS NOT NULL
        ORDER BY e.partition_id, e.end_time DESC, s.id DESC, e.id DESC
        """
    ).fetchall()

    latest: dict[int, dict[str, Any]] = {}
    for row in rows:
        area_id = safe_int(row["partition_id"])
        if area_id is None or area_id in latest:
            continue
        latest[area_id] = {
            "status": trail_time_status(row),
            "lastAt": epoch_to_iso(row["end_time"]),
            "startedAt": epoch_to_iso(row["start_time"]),
            "source": "/vehicle/trail/get-path-info-time",
            "snapshotId": safe_int(row["snapshot_id"]),
            "observedAt": row["observed_at"],
            "partitionPercentage": safe_int(row["partition_percentage"]),
            "areaM2": safe_float(row["area_m2"]),
            "finishedAreaM2": safe_float(row["finished_area_m2"]),
            "confidence": "medium",
            "note": "Derived from trail time index; compressed path data is still needed for exact path replay.",
        }
    return latest


def load_latest_live_location(con: sqlite3.Connection, render: sqlite3.Row | None) -> dict[str, Any] | None:
    if not table_exists(con, "live_location_snapshots"):
        return None
    row = con.execute(
        """
        SELECT
            id,
            observed_at,
            posture_x,
            posture_y,
            posture_theta,
            report_time,
            mowing_percentage,
            path_id,
            subtotal_area,
            mowing_week_area,
            map_id,
            map_base_id,
            map_edit_time
        FROM live_location_snapshots
        ORDER BY COALESCE(report_time, 0) DESC, id DESC
        LIMIT 1
        """
    ).fetchone()
    if row is None:
        return None

    pixel = None
    if render is not None and row["posture_x"] is not None and row["posture_y"] is not None:
        pixel = local_to_pixel([row["posture_x"], row["posture_y"]], render)

    return {
        "snapshotId": int(row["id"]),
        "observedAt": row["observed_at"],
        "reportAt": epoch_to_iso(row["report_time"]),
        "positionPixel": pixel,
        "postureTheta": safe_float(row["posture_theta"]),
        "mowingPercentage": safe_int(row["mowing_percentage"]),
        "pathId": safe_int(row["path_id"]),
        "subtotalAreaM2": safe_float(row["subtotal_area"]),
        "mowingWeekAreaM2": safe_float(row["mowing_week_area"]),
        "mapId": row["map_id"],
        "mapBaseId": safe_int(row["map_base_id"]),
        "mapEditTime": safe_int(row["map_edit_time"]),
        "source": "/vehicle/vehicle/get-location",
        "privacy": "Local pose only; exact GPS fields are not exported.",
    }


def load_latest_mqtt_status(con: sqlite3.Connection) -> dict[str, Any] | None:
    if not table_exists(con, "mqtt_status_snapshots"):
        return None
    row = con.execute(
        """
        SELECT
            id,
            route_snapshot_id,
            observed_at,
            report_time,
            state,
            task_status,
            work_status,
            battery_soc,
            capacity_label,
            current_partition_id,
            mowing_percentage,
            path_id,
            event_type
        FROM mqtt_status_snapshots
        ORDER BY COALESCE(report_time, CAST(strftime('%s', observed_at) AS INTEGER), 0) DESC, id DESC
        LIMIT 1
        """
    ).fetchone()
    if row is None:
        return None
    return {
        "snapshotId": int(row["id"]),
        "routeSnapshotId": safe_int(row["route_snapshot_id"]),
        "observedAt": row["observed_at"],
        "reportAt": epoch_to_iso(row["report_time"]),
        "state": row["state"],
        "taskStatus": row["task_status"],
        "workStatus": row["work_status"],
        "batterySoc": safe_int(row["battery_soc"]),
        "capacityLabel": row["capacity_label"],
        "currentPartitionId": safe_int(row["current_partition_id"]),
        "mowingPercentage": safe_int(row["mowing_percentage"]),
        "pathId": safe_int(row["path_id"]),
        "eventType": row["event_type"],
        "source": "mqtt-message",
    }


def increment_count(counts: dict[str, int], key: Any) -> None:
    if key in (None, ""):
        return
    key_text = str(key)
    counts[key_text] = counts.get(key_text, 0) + 1


def sorted_counts(counts: dict[str, int]) -> dict[str, int]:
    return {key: counts[key] for key in sorted(counts)}


def load_mqtt_message_summary(con: sqlite3.Connection) -> dict[str, Any] | None:
    if not table_exists(con, "route_snapshot_records"):
        return None
    rows = con.execute(
        """
        SELECT observed_at, sanitized_json
        FROM route_snapshot_records
        WHERE route_alias='mqtt-message'
        ORDER BY id DESC
        """
    ).fetchall()
    if not rows:
        return None

    payload_shapes: dict[str, int] = {}
    message_classes: dict[str, int] = {}
    topic_hashes: set[str] = set()
    latest = parse_json(rows[0]["sanitized_json"], {})
    if not isinstance(latest, dict):
        latest = {}

    for row in rows:
        snapshot = parse_json(row["sanitized_json"], {})
        if not isinstance(snapshot, dict):
            continue
        increment_count(payload_shapes, snapshot.get("payloadShape"))
        topic_hash = snapshot.get("topicHash")
        if isinstance(topic_hash, str) and topic_hash:
            topic_hashes.add(topic_hash)
        classes = snapshot.get("messageClasses")
        if not isinstance(classes, list):
            classes = store.mqtt_snapshot_classes(snapshot)
        for class_name in classes:
            increment_count(message_classes, class_name)

    status_snapshot_count = None
    if table_exists(con, "mqtt_status_snapshots"):
        status_snapshot_count = safe_int(con.execute("SELECT COUNT(*) FROM mqtt_status_snapshots").fetchone()[0])

    latest_classes = latest.get("messageClasses")
    if not isinstance(latest_classes, list):
        latest_classes = store.mqtt_snapshot_classes(latest)

    return {
        "observedAt": rows[0]["observed_at"],
        "totalMessages": len(rows),
        "observedTopicCount": len(topic_hashes),
        "payloadShapes": sorted_counts(payload_shapes),
        "messageClasses": sorted_counts(message_classes),
        "statusSnapshotCount": status_snapshot_count,
        "latest": {
            "observedAt": rows[0]["observed_at"],
            "payloadShape": latest.get("payloadShape"),
            "messageClasses": latest_classes,
            "payloadKeys": latest.get("payloadKeys") if isinstance(latest.get("payloadKeys"), list) else [],
            "payloadBytes": safe_int(latest.get("payloadBytes")),
        },
        "privacy": "MQTT routing strings and raw payloads are never exported; only distinct routing counts are shown.",
    }


def load_route_snapshots(con: sqlite3.Connection) -> dict[str, Any]:
    if not table_exists(con, "route_snapshot_records"):
        return {}
    rows = con.execute(
        """
        SELECT r.route_alias, r.observed_at, r.item_count, r.summary_json
        FROM route_snapshot_records r
        JOIN (
            SELECT route_alias, MAX(id) AS id
            FROM route_snapshot_records
            GROUP BY route_alias
        ) latest ON latest.id = r.id
        ORDER BY r.route_alias
        """
    ).fetchall()
    snapshots: dict[str, Any] = {}
    for row in rows:
        summary = parse_json(row["summary_json"], {})
        if not isinstance(summary, dict):
            summary = {}
        summary["observedAt"] = row["observed_at"] or summary.get("observedAt")
        summary["itemCount"] = safe_int(row["item_count"]) if row["item_count"] is not None else summary.get("itemCount")
        summary["routeAlias"] = row["route_alias"]
        snapshots[row["route_alias"]] = summary
    return snapshots


def load_route_snapshot_payloads(con: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    if not table_exists(con, "route_snapshot_records"):
        return {}
    rows = con.execute(
        """
        SELECT r.route_alias, r.observed_at, r.sanitized_json
        FROM route_snapshot_records r
        JOIN (
            SELECT route_alias, MAX(id) AS id
            FROM route_snapshot_records
            GROUP BY route_alias
        ) latest ON latest.id = r.id
        ORDER BY r.route_alias
        """
    ).fetchall()
    payloads: dict[str, dict[str, Any]] = {}
    for row in rows:
        payloads[row["route_alias"]] = {
            "observedAt": row["observed_at"],
            "data": parse_json(row["sanitized_json"], {}),
        }
    return payloads


def load_typed_openapi_insights(con: sqlite3.Connection) -> dict[str, Any]:
    insights: dict[str, Any] = {}
    if table_exists(con, "openapi_auth_snapshots"):
        row = con.execute(
            """
            SELECT observed_at, device_count, devices_json
            FROM openapi_auth_snapshots
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()
        if row is not None:
            devices = parse_json(row["devices_json"], [])
            insights["openapiAuth"] = {
                "observedAt": row["observed_at"],
                "deviceCount": safe_int(row["device_count"]) or 0,
                "devices": devices if isinstance(devices, list) else [],
            }
    if table_exists(con, "openapi_status_snapshots"):
        row = con.execute(
            """
            SELECT observed_at, primary_device_hash, vehicle_state, capacity_percent, capacity_label
            FROM openapi_status_snapshots
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()
        if row is not None:
            insights["openapiStatus"] = {
                "observedAt": row["observed_at"],
                "deviceHash": row["primary_device_hash"],
                "vehicleState": row["vehicle_state"],
                "capacityPercent": safe_int(row["capacity_percent"]),
                "capacityLabel": row["capacity_label"],
            }
    return insights


def unwrap_payload(value: Any) -> Any:
    if isinstance(value, dict):
        for key in ("payload", "data", "result"):
            item = value.get(key)
            if item is not None:
                return item
    return value


def payload_devices(value: Any) -> list[dict[str, Any]]:
    value = unwrap_payload(value)
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        for key in ("devices", "list", "items", "records"):
            item = value.get(key)
            if isinstance(item, list):
                return [entry for entry in item if isinstance(entry, dict)]
        return [value]
    return []


def find_first_value(value: Any, keys: tuple[str, ...]) -> Any:
    if isinstance(value, dict):
        for key in keys:
            item = value.get(key)
            if item not in (None, ""):
                return item
        for item in value.values():
            found = find_first_value(item, keys)
            if found not in (None, ""):
                return found
    elif isinstance(value, list):
        for item in value:
            found = find_first_value(item, keys)
            if found not in (None, ""):
                return found
    return None


def first_capacity_percent(device: dict[str, Any]) -> int | None:
    capacity = device.get("capacityRemaining")
    if isinstance(capacity, list):
        for item in capacity:
            if not isinstance(item, dict):
                continue
            unit = str(item.get("unit") or "").upper()
            if unit == "PERCENTAGE":
                return safe_int(item.get("rawValue"))
    return safe_int(device.get("batterySoc") or device.get("soc") or device.get("battery"))


def item_count(value: Any) -> int:
    if isinstance(value, list):
        return len(value)
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return 0
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            return len([item for item in stripped.split(",") if item.strip()])
        return item_count(parsed)
    return 0


def derive_route_insights(
    payloads: dict[str, dict[str, Any]],
    summaries: dict[str, Any],
) -> dict[str, Any]:
    insights: dict[str, Any] = {}

    auth_payload = payloads.get("openapi-auth-list")
    if auth_payload:
        devices = payload_devices(auth_payload.get("data"))
        insights["openapiAuth"] = {
            "observedAt": auth_payload.get("observedAt"),
            "deviceCount": len(devices),
            "devices": [
                {
                    "deviceHash": short_hash(device.get("id") or device.get("deviceId") or device.get("device_id")),
                    "name": device.get("name") or device.get("selfDefinedName"),
                    "model": device.get("model"),
                    "firmware": device.get("firmware"),
                }
                for device in devices[:5]
            ],
        }

    status_payload = payloads.get("openapi-vehicle-status")
    if status_payload:
        devices = payload_devices(status_payload.get("data"))
        device = devices[0] if devices else {}
        insights["openapiStatus"] = {
            "observedAt": status_payload.get("observedAt"),
            "deviceHash": short_hash(device.get("id") or device.get("deviceId") or device.get("device_id")),
            "vehicleState": device.get("vehicleState") or device.get("state"),
            "capacityPercent": first_capacity_percent(device),
            "capacityLabel": device.get("descriptiveCapacityRemaining"),
        }

    mqtt_payload = payloads.get("openapi-mqtt-info")
    if mqtt_payload:
        data = mqtt_payload.get("data")
        topic_count = None
        configured = False
        if isinstance(data, dict):
            topic_count = safe_int(data.get("topicCount"))
            if topic_count is None:
                topics = data.get("subTopics") if isinstance(data.get("subTopics"), list) else []
                topic_count = len(topics)
            configured = bool(data.get("configured") or data.get("mqttHost") or data.get("mqttUrl"))
        insights["mqtt"] = {
            "observedAt": mqtt_payload.get("observedAt"),
            "configured": configured,
            "topicCount": topic_count or 0,
        }

    weather_payload = payloads.get("weather")
    if weather_payload:
        data = weather_payload.get("data")
        weather_keys = (
            "rainState",
            "rainLevel",
            "frostState",
            "snowState",
            "stormState",
            "highTemperatureState",
            "weatherState",
        )
        insights["weather"] = {
            "observedAt": weather_payload.get("observedAt"),
            "flags": {
                key: find_first_value(data, (key,))
                for key in weather_keys
                if find_first_value(data, (key,)) not in (None, "")
            },
        }

    today_payload = payloads.get("today-plan")
    if today_payload:
        data = today_payload.get("data")
        insights["todayPlan"] = {
            "observedAt": today_payload.get("observedAt"),
            "status": find_first_value(data, ("status", "taskStatus", "planStatus", "workStatus", "state")),
            "start": find_first_value(data, ("startTime", "start", "beginTime")),
            "end": find_first_value(data, ("endTime", "end", "finishTime")),
            "partitionCount": item_count(find_first_value(data, ("partitionIds", "partitionIdList"))),
        }

    live_state_payload = payloads.get("mower-state")
    if live_state_payload:
        data = live_state_payload.get("data")
        insights["consumerLiveState"] = {
            "observedAt": live_state_payload.get("observedAt"),
            "state": find_first_value(data, ("state", "vehicleState", "vehicle_state", "workStatus")),
            "batterySoc": safe_int(find_first_value(data, ("soc", "battery", "batterySoc"))),
            "currentPartitionId": safe_int(find_first_value(data, ("currentPartitionId", "partitionId", "path_id"))),
        }

    for alias, key in (("maintenance", "maintenance"), ("firmware", "firmwareUpdate")):
        summary = summaries.get(alias)
        if summary:
            insights[key] = {
                "observedAt": summary.get("observedAt"),
                "itemCount": summary.get("itemCount"),
                "keys": summary.get("keys") or [],
            }

    return insights


def load_mower_display(con: sqlite3.Connection) -> dict[str, Any]:
    state_row = (
        con.execute("SELECT id, observed_at, raw_json FROM device_state_snapshots ORDER BY id DESC LIMIT 1").fetchone()
        if table_exists(con, "device_state_snapshots")
        else None
    )
    info_row = (
        con.execute("SELECT id, observed_at, raw_json FROM device_info_snapshots ORDER BY id DESC LIMIT 1").fetchone()
        if table_exists(con, "device_info_snapshots")
        else None
    )
    state = parse_json(state_row["raw_json"], {}) if state_row is not None else {}
    info = parse_json(info_row["raw_json"], {}) if info_row is not None else {}
    state = state.get("data", state) if isinstance(state, dict) else {}
    info = info.get("data", info) if isinstance(info, dict) else {}
    firmware = ((info.get("nonstandardVehicleConfig") or {}).get("firmwareVersion") or {})
    battery_config = ((info.get("nonstandardVehicleConfig") or {}).get("batteryConfig") or {})
    network_extend = info.get("networkExtend") or {}
    mowing_extend = info.get("mowingExtend") or {}
    setting_snapshot = load_latest_setting_fields(con)
    setting_fields = setting_snapshot.get("fields", {})
    current_height_mm = normalize_cutting_height(setting_fields.get("height"))

    route_snapshots = load_route_snapshots(con)
    route_payloads = load_route_snapshot_payloads(con)

    route_insights = derive_route_insights(route_payloads, route_snapshots)
    route_insights.update(load_typed_openapi_insights(con))
    mqtt_status = load_latest_mqtt_status(con)
    if mqtt_status:
        route_insights["mqttStatus"] = mqtt_status
    mqtt_messages = load_mqtt_message_summary(con)
    if mqtt_messages:
        route_insights["mqttMessages"] = mqtt_messages

    return {
        "name": info.get("selfDefinedName") or info.get("name") or "Mower",
        "model": info.get("model"),
        "vehicleType": state.get("vehicle_type"),
        "stateCode": state.get("vehicle_state"),
        "observed": {
            "stateSnapshotId": int(state_row["id"]) if state_row is not None else None,
            "stateObservedAt": state_row["observed_at"] if state_row is not None else None,
            "infoSnapshotId": int(info_row["id"]) if info_row is not None else None,
            "infoObservedAt": info_row["observed_at"] if info_row is not None else None,
            "settingsSnapshotId": setting_snapshot.get("snapshotId"),
            "settingsObservedAt": setting_snapshot.get("observedAt"),
        },
        "battery": {
            "soc": safe_int(state.get("soc")),
            "soh": safe_int(state.get("soh")),
            "chargeRemainingMinutes": safe_int(state.get("chgRemainTimeUser")),
            "chargingLimitRecommend": safe_int(battery_config.get("chargingLimitRecommend")),
            "returnBatteryLevelRecommend": safe_int(battery_config.get("returnBatteryLevelRecommend")),
        },
        "network": {
            "type": safe_int(state.get("networkType")),
            "status": safe_int(state.get("network_status")),
            "signal": safe_int(state.get("network_signal")),
            "signal4g": safe_int(state.get("network_signal_4G")),
            "signalWifi": safe_int(state.get("network_signal_wifi")),
            "moduleType": safe_int(network_extend.get("moduleType")),
            "continent": network_extend.get("continent"),
        },
        "cameraBox": state.get("camerabox") or {},
        "map": {
            "version": state.get("mapVersion"),
            "settingUpdateTime": state.get("vehicleSettingUpdateTime"),
            "infoUpdateTime": state.get("vehicle_info_update_time"),
            "partitionLength": state.get("partitionLength"),
        },
        "cutting": {
            "heightMm": current_height_mm,
            "cutterHeightCode": safe_int(setting_fields.get("cutterHeight")),
            "heightRaw": setting_fields.get("height"),
            "cutterHeightRaw": setting_fields.get("cutterHeight"),
            "supportedMm": info.get("mowingHeightList") or [],
            "supportedInches": mowing_extend.get("mowingHeightInchList") or [],
            "sourceRoute": "/vehicle/vehicle/set-list",
            "capabilityRoute": "/vehicle/vehicle/get-device-info",
            "observedAt": setting_snapshot.get("observedAt"),
        },
        "capabilities": {
            "hasScreen": ((info.get("nonstandardVehicleConfig") or {}).get("hasScreen") == "1"),
            "isCutterHeight": bool(info.get("isCutterHeight")),
            "mowingHeightList": info.get("mowingHeightList") or [],
            "mowingHeightInchList": mowing_extend.get("mowingHeightInchList") or [],
            "lineSpeedList": info.get("line_speed_list") or [],
            "rotationSpeedList": info.get("rotation_speed_list") or [],
            "planMaxTimeHours": safe_float(info.get("plan_max_time")),
            "mapAreaLimitM2": safe_float(info.get("map_area_limit")),
            "mapMaxAreaLimitM2": safe_float(info.get("map_max_area_limit")),
            "subMapLimit": safe_float(info.get("sub_map_limit")),
        },
        "firmware": {
            "ECU": firmware.get("ECU"),
            "SW": firmware.get("SW"),
            "VCU": firmware.get("VCU"),
        },
        "routeSnapshots": route_snapshots,
        "routeInsights": route_insights,
        "sync": {
            "batteryAndState": {
                "route": "/vehicle/vehicle/index2",
                "method": "POST",
                "cadenceSeconds": 45,
                "activeCadenceSeconds": 30,
                "idleCadenceSeconds": 90,
                "status": "normalized_snapshot",
                "fields": ["soc", "soh", "vehicle_state", "network_status", "network_signal", "mapVersion"],
            },
            "liveLocation": {
                "route": "/vehicle/vehicle/get-location",
                "method": "POST",
                "cadenceSeconds": 10,
                "activeCadenceSeconds": 5,
                "idleCadenceSeconds": 60,
                "status": "normalized_snapshot",
                "fields": ["posture_x", "posture_y", "posture_theta", "mowing_percentage", "report_time"],
            },
            "settings": {
                "route": "/vehicle/vehicle/set-list",
                "method": "POST",
                "cadenceSeconds": 300,
                "status": "partially_normalized",
                "fields": ["height", "cutterHeight", "startPlan", "mowingCycle", "chargingLimit"],
            },
            "capabilities": {
                "route": "/vehicle/vehicle/get-device-info",
                "method": "POST",
                "cadenceSeconds": 3600,
                "status": "normalized_snapshot",
                "fields": ["mowingHeightList", "line_speed_list", "plan_max_time", "map_area_limit"],
            },
            "lastMowPerArea": {
                "routes": [
                    "/vehicle/trail/get-path-info-time",
                    "/vehicle/trail/get-path-info-data-compress",
                ],
                "cadenceSeconds": 900,
                "status": "trail_time_index_normalized",
                "nextStep": "decode compressed trail points, suppress GPS, then intersect local path points with area polygons for exact path replay",
            },
        },
    }


def extract_terrain_image(artifact_path: Path, image_name: str, output_assets: Path) -> str:
    output_assets.mkdir(parents=True, exist_ok=True)
    output_image = output_assets / "terrain.webp"
    with zipfile.ZipFile(artifact_path) as archive:
        members = archive.namelist()
        match = next((member for member in members if member.endswith(f"/{image_name}") or member == image_name), None)
        if match is None:
            match = next((member for member in members if member.lower().endswith(".webp")), None)
        if match is None:
            raise SystemExit(f"No WebP terrain image found in {artifact_path}")
        with archive.open(match) as src, output_image.open("wb") as dst:
            shutil.copyfileobj(src, dst)
    return "assets/terrain.webp"


def build_data(
    con: sqlite3.Connection,
    output_dir: Path,
    *,
    include_satellite: bool = True,
    satellite_zoom: int = 19,
    status_only: bool = False,
) -> dict[str, Any]:
    if status_only:
        schedule = load_schedule(con)
        schedule_draft = schedule_to_draft(schedule)
        mower = load_mower_display(con)
        mower["liveLocation"] = load_latest_live_location(con, None)
        data = {
            "generatedAt": now_iso(),
            "source": {
                "database": str(DEFAULT_DB),
                "mode": "status_only",
                "mapSnapshotId": None,
                "scheduleSnapshotId": schedule.get("snapshotId"),
                "privacy": "Sanitized: no serials, tokens, signed URLs, raw payloads, or GPS arrays are exported.",
            },
            "map": {
                "name": "Navimow live status",
                "mode": "status_only",
                "statusOnly": True,
                "backgrounds": {
                    "terrain": None,
                    "satellite": None,
                },
                "background": None,
                "satellite": {
                    "available": False,
                    "zoom": None,
                    "attribution": None,
                    "error": "Map captures have not populated local map detail or render metadata.",
                },
                "width": 960,
                "height": 540,
                "bounds": {
                    "minX": None,
                    "maxX": None,
                    "minY": None,
                    "maxY": None,
                    "pixelPerMeter": None,
                },
                "totalAreaM2": 0,
                "areaCount": 0,
                "obstacleCount": 0,
                "customSelectedAreaM2": 0,
            },
            "areas": [],
            "obstacles": [],
            "schedule": schedule,
            "scheduleDraft": schedule_draft,
            "scheduleOptimizer": schedule_optimizer_context(schedule),
            "mower": mower,
            "routeCatalog": ROUTE_CATALOG,
        }
        data["liveStatus"] = {"layoutVersion": viewer_layout_version(data)}
        return data

    detail_row, detail = load_map_detail(con)
    render, artifact_path, image_name = load_render_metadata(con)
    background = extract_terrain_image(artifact_path, image_name, output_dir / "assets")
    satellite_background = None
    satellite_error = None
    if include_satellite:
        try:
            satellite_background = build_satellite_image(detail, render, output_dir / "assets", satellite_zoom)
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            satellite_error = str(exc)
    areas = extract_areas(detail, render)
    obstacles = extract_obstacles(detail, render)
    schedule = load_schedule(con)
    schedule_draft = schedule_to_draft(schedule)
    mower = load_mower_display(con)
    mower["liveLocation"] = load_latest_live_location(con, render)
    global_height_mm = (mower.get("cutting") or {}).get("heightMm")
    last_mow_by_area = load_area_last_mow(con)

    custom_area_ids = {
        area_id
        for period in schedule.get("customPeriods", [])
        for area_id in period.get("partitionIds", [])
    }
    for area in areas:
        area_height_mm = (area.get("cutting") or {}).get("areaHeightMm")
        area["cutting"]["effectiveHeightMm"] = area_height_mm if area_height_mm is not None else global_height_mm
        area["cutting"]["source"] = "area height_set" if area_height_mm is not None else "global mower height"
        if area["id"] in last_mow_by_area:
            area["lastMow"] = last_mow_by_area[area["id"]]
        area["schedule"] = {
            "customSelected": area["id"] in custom_area_ids,
            "customPeriods": schedule.get("byArea", {}).get(str(area["id"]), []),
            "allZonePeriodCount": len(schedule.get("allZonePeriods", [])),
        }

    selected_area = sum(area["sizeM2"] for area in areas if area["schedule"]["customSelected"])

    data = {
        "generatedAt": now_iso(),
        "source": {
            "database": str(DEFAULT_DB),
            "mapSnapshotId": int(detail_row["id"]),
            "scheduleSnapshotId": schedule.get("snapshotId"),
            "privacy": "Sanitized: no serials, tokens, signed URLs, raw payloads, or GPS arrays are exported. Satellite imagery is local-only and may reveal the property location if shared.",
        },
        "map": {
            "name": detail_row["map_name"] or detail.get("name") or "Navimow map",
            "backgrounds": {
                "terrain": background,
                "satellite": satellite_background,
            },
            "background": background,
            "satellite": {
                "available": satellite_background is not None,
                "zoom": satellite_zoom if satellite_background else None,
                "attribution": ESRI_ATTRIBUTION if satellite_background else None,
                "error": satellite_error,
            },
            "width": int(render["width"]),
            "height": int(render["height"]),
            "bounds": {
                "minX": render["min_x"],
                "maxX": render["max_x"],
                "minY": render["min_y"],
                "maxY": render["max_y"],
                "pixelPerMeter": render["pixel_per_meter"],
            },
            "totalAreaM2": round(float(detail_row["detail_area"] or detail.get("area") or 0), 1),
            "areaCount": len(areas),
            "obstacleCount": len(obstacles),
            "customSelectedAreaM2": round(selected_area, 1),
        },
        "areas": areas,
        "obstacles": obstacles,
        "schedule": schedule,
        "scheduleDraft": schedule_draft,
        "scheduleOptimizer": schedule_optimizer_context(schedule),
        "mower": mower,
        "routeCatalog": ROUTE_CATALOG,
    }
    data["areaStatus"] = build_area_status(data)
    data["liveStatus"] = {"layoutVersion": viewer_layout_version(data)}
    return data


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        tmp_path.write_text(text, encoding="utf-8")
        tmp_path.replace(path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            tmp_path.unlink()


def write_live_status_file(output_dir: Path, data: dict[str, Any]) -> Path:
    path = output_dir / "navimow-live-status.json"
    write_text_atomic(path, json.dumps(build_live_status(data), ensure_ascii=False, indent=2) + "\n")
    return path


def parse_viewer_data_js(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if not stripped.startswith(VIEWER_DATA_PREFIX):
        raise SystemExit("Generated viewer data does not start with the expected NAVIMOW prefix")
    body = stripped[len(VIEWER_DATA_PREFIX) :].strip()
    if body.endswith(";"):
        body = body[:-1].strip()
    parsed = json.loads(body)
    if not isinstance(parsed, dict):
        raise SystemExit("Generated viewer data has an unsupported shape")
    return parsed


def read_viewer_data(output_dir: Path) -> dict[str, Any]:
    path = output_dir / "navimow-map-data.js"
    if not path.exists():
        raise SystemExit(f"Missing generated viewer data: {path}; run the full viewer build first")
    return parse_viewer_data_js(path.read_text(encoding="utf-8"))


def refresh_live_status_file(output_dir: Path, con: sqlite3.Connection) -> Path:
    try:
        data = read_viewer_data(output_dir)
    except SystemExit:
        if not (output_dir / "navimow-map-data.js").exists():
            data = build_data(con, output_dir, include_satellite=False, status_only=True)
            write_viewer(output_dir, data)
            return output_dir / "navimow-live-status.json"
        raise
    render = None
    if not (data.get("map") or {}).get("statusOnly"):
        render, _, _ = load_render_metadata(con)
    mower = load_mower_display(con)
    mower["liveLocation"] = load_latest_live_location(con, render)
    global_height_mm = (mower.get("cutting") or {}).get("heightMm")
    last_mow_by_area = load_area_last_mow(con)
    for area in data.get("areas", []):
        if not isinstance(area, dict):
            continue
        area_id = safe_int(area.get("id"))
        cutting = area.get("cutting")
        if isinstance(cutting, dict):
            area_height_mm = cutting.get("areaHeightMm")
            cutting["effectiveHeightMm"] = area_height_mm if area_height_mm is not None else global_height_mm
            cutting["source"] = "area height_set" if area_height_mm is not None else "global mower height"
        if area_id in last_mow_by_area:
            area["lastMow"] = last_mow_by_area[area_id]
    data["generatedAt"] = now_iso()
    data["mower"] = mower
    data["areaStatus"] = build_area_status(data)
    data.setdefault("liveStatus", {})
    return write_live_status_file(output_dir, data)


def write_viewer(output_dir: Path, data: dict[str, Any]) -> None:
    required_templates = ["index.html", "assets/navimow-map.css", "assets/navimow-map.js"]
    missing = [relative for relative in required_templates if not (TEMPLATE_DIR / relative).exists()]
    if missing:
        raise SystemExit(f"Missing viewer template asset(s): {', '.join(missing)}")
    output_dir.mkdir(parents=True, exist_ok=True)
    write_text(output_dir / "navimow-map-data.js", VIEWER_DATA_PREFIX + json.dumps(data, ensure_ascii=False, indent=2) + ";\n")
    write_live_status_file(output_dir, data)
    write_text(output_dir / "schedule-draft.json", json.dumps(data["scheduleDraft"], ensure_ascii=False, indent=2) + "\n")
    for relative in required_templates:
        source = TEMPLATE_DIR / relative
        write_text(output_dir / relative, source.read_text(encoding="utf-8"))



def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--no-satellite", action="store_true", help="Skip satellite imagery generation")
    parser.add_argument("--satellite-zoom", type=int, default=19, help="Web Mercator tile zoom for satellite mosaic")
    parser.add_argument(
        "--status-only",
        action="store_true",
        help="Build a status console from live route snapshots without requiring captured map geometry",
    )
    args = parser.parse_args()

    con = sqlite3.connect(args.db)
    con.row_factory = sqlite3.Row
    data = build_data(
        con,
        args.output,
        include_satellite=not args.no_satellite,
        satellite_zoom=args.satellite_zoom,
        status_only=args.status_only,
    )
    write_viewer(args.output, data)
    print(f"Wrote local Navimow map viewer to {args.output / 'index.html'}")


if __name__ == "__main__":
    main()
