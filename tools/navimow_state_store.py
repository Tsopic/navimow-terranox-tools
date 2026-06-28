#!/usr/bin/env python3
"""Build a local Navimow SQLite state store from captures and app logs."""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import gzip
import hashlib
import ipaddress
import json
import os
import re
import sqlite3
import ssl
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from navimow_schedule_capture import extract as extract_schedule  # noqa: E402


DEFAULT_DB = Path("data/navimow.sqlite")
DEFAULT_MAP_DIR = Path("data/maps")

LOG_PREFIX_RE = re.compile(r"^\[(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3})\]")
LOGCAT_PREFIX_RE = re.compile(r"^(?P<ts>\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3})")
GET_IOT_FILE_RE = re.compile(r"get-iot-file.*?data -> (?P<body>\{.*\})(?=$|\n)")
MAP_DETAIL_COMPRESS_RE = re.compile(r"/map/index/map-detail-compress.*?data -> (?P<body>[A-Za-z0-9+/=]+)")
MAP_LOCAL_VERSION_RE = re.compile(r"local version\s*[：:]\s*(?P<local>\d+)\s*,\s*remoteVersion\s*:\s*(?P<remote>\d+)")
VEHICLE_SN_RE = re.compile(r"\bvehicle_?sn\s*[=:]\s*(?P<sn>[A-Z0-9]{8,})", re.IGNORECASE)
GET_MAP_INFO_RE = re.compile(r"getMapInfo: vehicleSn = (?P<sn>[A-Z0-9]{8,})")
UPDATE_DEVICE_RE = re.compile(r"updateDeviceSetting: vehicle_sn = (?P<sn>[A-Z0-9]{8,}), vehicle_type = (?P<type>\d+)")
ENCRYPTED_BODY_RE = re.compile(r"NbNeteaseDecrypt.*?encryptContent: after ---> content = (?P<body>\{.*\})")
MOWER_SETTING_RE = re.compile(r"MowerSettingBean\{")
ROUTE_ALIAS_BY_PATH = {
    "/vehicle/vehicle/auth-list": "auth-list",
    "/mowerbot/vehicle/vehicle/state": "mower-state",
    "/vehicle/vehicle/get-vehicle-weather": "weather",
    "/vehicle/vehicle/get-today-plan": "today-plan",
    "/vehicle/firmware/get-new-firmware": "firmware",
    "/vehicle/vehicle/get-component-maintenance": "maintenance",
    "/map/index/map-list": "map-list",
    "/mowerbot/vehicle/common/get-iot-file": "get-iot-file",
}


def now_iso() -> str:
    return dt.datetime.now(dt.UTC).isoformat()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def short_hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:16]


def read_text(path: Path) -> str:
    return path.read_text(errors="replace")


def parse_log_ts(line: str) -> str | None:
    match = LOG_PREFIX_RE.match(line)
    if match:
        return match.group("ts")

    match = LOGCAT_PREFIX_RE.match(line)
    if match:
        current_year = dt.datetime.now().year
        return f"{current_year}-{match.group('ts')}"
    return None


def parse_json(raw: str) -> Any | None:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def parse_log_json_after_data_marker(line: str) -> Any | None:
    marker = "data -> "
    marker_index = line.find(marker)
    if marker_index < 0:
        return None
    tail = line[marker_index + len(marker) :].lstrip()
    starts = [index for index in (tail.find("{"), tail.find("[")) if index >= 0]
    if not starts:
        return None
    decoder = json.JSONDecoder()
    try:
        value, _ = decoder.raw_decode(tail[min(starts) :])
    except json.JSONDecodeError:
        return None
    return value


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    init_schema(con)
    return con


def set_sync_state(con: sqlite3.Connection, key: str, value: Any) -> None:
    con.execute(
        """
        INSERT INTO sync_state(key, value, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(key) DO UPDATE SET
            value=excluded.value,
            updated_at=excluded.updated_at
        """,
        (key, json_dumps(value), now_iso()),
    )


def init_schema(con: sqlite3.Connection) -> None:
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS sources (
            id INTEGER PRIMARY KEY,
            path TEXT NOT NULL UNIQUE,
            kind TEXT NOT NULL,
            sha256 TEXT,
            size_bytes INTEGER,
            imported_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS devices (
            vehicle_sn TEXT PRIMARY KEY,
            vehicle_hash TEXT NOT NULL,
            vehicle_type TEXT,
            name TEXT,
            model TEXT,
            first_seen TEXT NOT NULL,
            last_seen TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS http_cache_entries (
            id INTEGER PRIMARY KEY,
            source_id INTEGER NOT NULL REFERENCES sources(id),
            cache_key TEXT NOT NULL,
            url TEXT NOT NULL,
            method TEXT,
            response_date TEXT,
            trace_id TEXT,
            response_json TEXT,
            imported_at TEXT NOT NULL,
            UNIQUE(source_id, cache_key)
        );

        CREATE TABLE IF NOT EXISTS device_state_snapshots (
            id INTEGER PRIMARY KEY,
            source_id INTEGER NOT NULL REFERENCES sources(id),
            vehicle_sn TEXT REFERENCES devices(vehicle_sn),
            observed_at TEXT,
            map_version INTEGER,
            vehicle_setting_update_time INTEGER,
            vehicle_info_update_time INTEGER,
            partition_length INTEGER,
            partition_id_list_json TEXT,
            raw_json TEXT NOT NULL,
            UNIQUE(source_id, observed_at, vehicle_sn, map_version)
        );

        CREATE TABLE IF NOT EXISTS device_info_snapshots (
            id INTEGER PRIMARY KEY,
            source_id INTEGER NOT NULL REFERENCES sources(id),
            vehicle_sn TEXT REFERENCES devices(vehicle_sn),
            observed_at TEXT,
            model TEXT,
            name TEXT,
            map_area_limit REAL,
            map_max_area_limit REAL,
            sub_map_limit REAL,
            plan_max_time REAL,
            raw_json TEXT NOT NULL,
            UNIQUE(source_id, observed_at, vehicle_sn)
        );

        CREATE TABLE IF NOT EXISTS map_resource_events (
            id INTEGER PRIMARY KEY,
            source_id INTEGER NOT NULL REFERENCES sources(id),
            vehicle_sn TEXT REFERENCES devices(vehicle_sn),
            observed_at TEXT,
            event_hash TEXT NOT NULL,
            remote_version INTEGER NOT NULL,
            local_version INTEGER,
            status TEXT,
            blob_host TEXT,
            blob_path TEXT,
            url_expires_at TEXT,
            url_sha256 TEXT,
            signed_url TEXT,
            raw_json TEXT,
            UNIQUE(source_id, event_hash)
        );

        CREATE TABLE IF NOT EXISTS map_artifacts (
            id INTEGER PRIMARY KEY,
            vehicle_sn TEXT REFERENCES devices(vehicle_sn),
            version INTEGER NOT NULL,
            file_path TEXT NOT NULL,
            sha256 TEXT NOT NULL,
            size_bytes INTEGER NOT NULL,
            content_kind TEXT,
            parsed_status TEXT,
            imported_at TEXT NOT NULL,
            UNIQUE(vehicle_sn, version, sha256)
        );

        CREATE TABLE IF NOT EXISTS map_artifact_files (
            id INTEGER PRIMARY KEY,
            artifact_id INTEGER NOT NULL REFERENCES map_artifacts(id) ON DELETE CASCADE,
            member_path TEXT NOT NULL,
            sha256 TEXT,
            size_bytes INTEGER NOT NULL,
            content_kind TEXT NOT NULL,
            metadata_json TEXT,
            UNIQUE(artifact_id, member_path)
        );

        CREATE TABLE IF NOT EXISTS map_render_metadata (
            id INTEGER PRIMARY KEY,
            artifact_id INTEGER NOT NULL REFERENCES map_artifacts(id) ON DELETE CASCADE,
            member_path TEXT NOT NULL,
            width INTEGER,
            height INTEGER,
            min_x REAL,
            max_x REAL,
            min_y REAL,
            max_y REAL,
            min_z REAL,
            max_z REAL,
            pixel_per_meter REAL,
            map_init_ts REAL,
            start_timestamp REAL,
            end_timestamp REAL,
            terrain_view_image_name TEXT,
            terrain_adapt_image_name TEXT,
            raw_json TEXT NOT NULL,
            UNIQUE(artifact_id, member_path)
        );

        CREATE TABLE IF NOT EXISTS map_area_records (
            id INTEGER PRIMARY KEY,
            artifact_id INTEGER NOT NULL REFERENCES map_artifacts(id),
            area_key TEXT,
            name TEXT,
            area_size REAL,
            raw_json TEXT NOT NULL,
            UNIQUE(artifact_id, area_key, raw_json)
        );

        CREATE TABLE IF NOT EXISTS area_setting_snapshots (
            id INTEGER PRIMARY KEY,
            source_id INTEGER NOT NULL REFERENCES sources(id),
            vehicle_sn TEXT REFERENCES devices(vehicle_sn),
            observed_at TEXT,
            event_hash TEXT NOT NULL,
            partition_length INTEGER,
            partition_id_list_json TEXT,
            mowing_zone_list_text TEXT,
            mowing_zone_text TEXT,
            raw_text TEXT NOT NULL,
            UNIQUE(source_id, event_hash)
        );

        CREATE TABLE IF NOT EXISTS map_detail_snapshots (
            id INTEGER PRIMARY KEY,
            source_id INTEGER NOT NULL REFERENCES sources(id),
            vehicle_sn TEXT REFERENCES devices(vehicle_sn),
            observed_at TEXT,
            event_hash TEXT NOT NULL,
            map_id TEXT,
            map_base_id TEXT,
            map_name TEXT,
            total_area REAL,
            detail_area REAL,
            raw_json TEXT NOT NULL,
            UNIQUE(source_id, event_hash)
        );

        CREATE TABLE IF NOT EXISTS map_detail_areas (
            id INTEGER PRIMARY KEY,
            snapshot_id INTEGER NOT NULL REFERENCES map_detail_snapshots(id) ON DELETE CASCADE,
            area_id INTEGER,
            name TEXT,
            area_size REAL,
            area_type TEXT,
            element_count INTEGER,
            raw_json TEXT NOT NULL,
            UNIQUE(snapshot_id, area_id, raw_json)
        );

        CREATE TABLE IF NOT EXISTS schedule_snapshots (
            id INTEGER PRIMARY KEY,
            source_id INTEGER NOT NULL REFERENCES sources(id),
            vehicle_sn TEXT REFERENCES devices(vehicle_sn),
            observed_at TEXT,
            event_hash TEXT NOT NULL,
            plan_hex TEXT,
            raw_json TEXT NOT NULL,
            UNIQUE(source_id, event_hash)
        );

        CREATE TABLE IF NOT EXISTS schedule_days (
            id INTEGER PRIMARY KEY,
            snapshot_id INTEGER NOT NULL REFERENCES schedule_snapshots(id) ON DELETE CASCADE,
            day INTEGER NOT NULL,
            open INTEGER,
            UNIQUE(snapshot_id, day)
        );

        CREATE TABLE IF NOT EXISTS schedule_periods (
            id INTEGER PRIMARY KEY,
            day_id INTEGER NOT NULL REFERENCES schedule_days(id) ON DELETE CASCADE,
            period_index INTEGER NOT NULL,
            start_tick INTEGER NOT NULL,
            end_tick INTEGER NOT NULL,
            partition_ids_json TEXT NOT NULL,
            UNIQUE(day_id, period_index)
        );

        CREATE TABLE IF NOT EXISTS trail_time_snapshots (
            id INTEGER PRIMARY KEY,
            source_id INTEGER NOT NULL REFERENCES sources(id),
            vehicle_sn TEXT REFERENCES devices(vehicle_sn),
            observed_at TEXT,
            event_hash TEXT NOT NULL,
            entry_count INTEGER NOT NULL,
            raw_json TEXT NOT NULL,
            UNIQUE(source_id, event_hash)
        );

        CREATE TABLE IF NOT EXISTS trail_time_entries (
            id INTEGER PRIMARY KEY,
            snapshot_id INTEGER NOT NULL REFERENCES trail_time_snapshots(id) ON DELETE CASCADE,
            partition_id INTEGER,
            start_time INTEGER,
            end_time INTEGER,
            end_time_alias INTEGER,
            area_m2 REAL,
            finished_area_m2 REAL,
            partition_percentage INTEGER,
            raw_json TEXT NOT NULL,
            UNIQUE(snapshot_id, partition_id, start_time, end_time, raw_json)
        );

        CREATE TABLE IF NOT EXISTS live_location_snapshots (
            id INTEGER PRIMARY KEY,
            source_id INTEGER NOT NULL REFERENCES sources(id),
            vehicle_sn TEXT REFERENCES devices(vehicle_sn),
            observed_at TEXT,
            event_hash TEXT NOT NULL,
            posture_x REAL,
            posture_y REAL,
            posture_theta REAL,
            report_time INTEGER,
            mowing_percentage INTEGER,
            path_id INTEGER,
            subtotal_area REAL,
            mowing_week_area REAL,
            map_id TEXT,
            map_base_id INTEGER,
            map_edit_time INTEGER,
            sanitized_json TEXT NOT NULL,
            UNIQUE(source_id, event_hash)
        );

        CREATE TABLE IF NOT EXISTS route_snapshot_records (
            id INTEGER PRIMARY KEY,
            source_id INTEGER NOT NULL REFERENCES sources(id),
            vehicle_sn TEXT REFERENCES devices(vehicle_sn),
            observed_at TEXT,
            route_alias TEXT NOT NULL,
            event_hash TEXT NOT NULL,
            item_count INTEGER,
            summary_json TEXT NOT NULL,
            sanitized_json TEXT NOT NULL,
            UNIQUE(source_id, route_alias, event_hash)
        );

        CREATE TABLE IF NOT EXISTS mqtt_status_snapshots (
            id INTEGER PRIMARY KEY,
            route_snapshot_id INTEGER NOT NULL REFERENCES route_snapshot_records(id) ON DELETE CASCADE,
            observed_at TEXT,
            topic_hash TEXT,
            payload_sha256 TEXT,
            report_time INTEGER,
            state TEXT,
            task_status TEXT,
            work_status TEXT,
            battery_soc INTEGER,
            capacity_label TEXT,
            current_partition_id INTEGER,
            mowing_percentage INTEGER,
            path_id INTEGER,
            event_type TEXT,
            safe_fields_json TEXT NOT NULL,
            UNIQUE(route_snapshot_id)
        );

        CREATE TABLE IF NOT EXISTS openapi_auth_snapshots (
            id INTEGER PRIMARY KEY,
            route_snapshot_id INTEGER NOT NULL REFERENCES route_snapshot_records(id) ON DELETE CASCADE,
            observed_at TEXT,
            device_count INTEGER NOT NULL,
            devices_json TEXT NOT NULL,
            UNIQUE(route_snapshot_id)
        );

        CREATE TABLE IF NOT EXISTS openapi_status_snapshots (
            id INTEGER PRIMARY KEY,
            route_snapshot_id INTEGER NOT NULL REFERENCES route_snapshot_records(id) ON DELETE CASCADE,
            observed_at TEXT,
            device_count INTEGER NOT NULL,
            primary_device_hash TEXT,
            vehicle_state TEXT,
            capacity_percent INTEGER,
            capacity_label TEXT,
            statuses_json TEXT NOT NULL,
            UNIQUE(route_snapshot_id)
        );

        CREATE TABLE IF NOT EXISTS command_envelopes (
            id INTEGER PRIMARY KEY,
            source_id INTEGER NOT NULL REFERENCES sources(id),
            vehicle_sn TEXT REFERENCES devices(vehicle_sn),
            observed_at TEXT,
            envelope_json TEXT NOT NULL,
            envelope_sha256 TEXT NOT NULL,
            note TEXT,
            UNIQUE(source_id, observed_at, envelope_sha256)
        );

        CREATE TABLE IF NOT EXISTS sync_state (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        """
    )
    backfill_openapi_typed_snapshots(con)
    con.commit()


def add_source(con: sqlite3.Connection, path: Path, kind: str) -> int:
    data = path.read_bytes()
    digest = sha256_bytes(data)
    con.execute(
        """
        INSERT INTO sources(path, kind, sha256, size_bytes, imported_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(path) DO UPDATE SET
            kind=excluded.kind,
            sha256=excluded.sha256,
            size_bytes=excluded.size_bytes,
            imported_at=excluded.imported_at
        """,
        (str(path), kind, digest, len(data), now_iso()),
    )
    return int(con.execute("SELECT id FROM sources WHERE path = ?", (str(path),)).fetchone()["id"])


def upsert_device(
    con: sqlite3.Connection,
    vehicle_sn: str | None,
    *,
    vehicle_type: str | None = None,
    name: str | None = None,
    model: str | None = None,
    seen_at: str | None = None,
) -> None:
    if not vehicle_sn:
        return
    seen_at = seen_at or now_iso()
    con.execute(
        """
        INSERT INTO devices(vehicle_sn, vehicle_hash, vehicle_type, name, model, first_seen, last_seen)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(vehicle_sn) DO UPDATE SET
            vehicle_type=COALESCE(excluded.vehicle_type, devices.vehicle_type),
            name=COALESCE(excluded.name, devices.name),
            model=COALESCE(excluded.model, devices.model),
            last_seen=excluded.last_seen
        """,
        (vehicle_sn, short_hash(vehicle_sn), vehicle_type, name, model, seen_at, seen_at),
    )


def parse_cache_meta(meta_path: Path) -> dict[str, str]:
    lines = meta_path.read_text(errors="replace").splitlines()
    headers: dict[str, str] = {}
    for line in lines[4:]:
        if ":" in line:
            key, value = line.split(":", 1)
            headers[key.strip().lower()] = value.strip()
    return {
        "url": lines[0] if len(lines) > 0 else "",
        "method": lines[1] if len(lines) > 1 else "",
        "response_date": headers.get("date", ""),
        "trace_id": headers.get("traceid", ""),
    }


def sqlite_row_changed(cursor: sqlite3.Cursor) -> int:
    return 1 if cursor.rowcount and cursor.rowcount > 0 else 0


def event_hash(*parts: Any) -> str:
    return hashlib.sha256(json_dumps(parts).encode()).hexdigest()


def is_disk_lru_cache_dir(path: Path) -> bool:
    return path.is_dir() and (path / "journal").exists() and any(path.glob("*.1"))


def find_matching_curly(text: str, start: int) -> int | None:
    depth = 0
    for index in range(start, len(text)):
        char = text[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index
    return None


def extract_mower_setting_body(line: str) -> str | None:
    match = MOWER_SETTING_RE.search(line)
    if not match:
        return None
    start = line.find("{", match.start())
    end = find_matching_curly(line, start)
    if start < 0 or end is None:
        return None
    return line[start + 1 : end]


def extract_bean_field(body: str, key: str) -> str | None:
    marker = f"{key}="
    start = body.find(marker)
    if start < 0:
        return None
    index = start + len(marker)
    depth = 0
    while index < len(body):
        char = body[index]
        if char in "[{(":
            depth += 1
        elif char in "]})" and depth > 0:
            depth -= 1
        elif char == "," and depth == 0:
            rest = body[index + 1 :].lstrip()
            if re.match(r"[A-Za-z][A-Za-z0-9]*=", rest):
                break
        index += 1
    return body[start + len(marker) : index].strip()


def normalize_nullish(value: str | None) -> str | None:
    if value is None:
        return None
    return None if value == "null" else value


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


def bounded_int(value: Any, minimum: int, maximum: int) -> int | None:
    parsed = safe_int(value)
    if parsed is None or parsed < minimum or parsed > maximum:
        return None
    return parsed


def first_present(mapping: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        value = mapping.get(key)
        if value not in (None, ""):
            return value
    return None


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


def first_capacity_percent(device: dict[str, Any]) -> int | None:
    capacity = device.get("capacityRemaining")
    if isinstance(capacity, list):
        for item in capacity:
            if not isinstance(item, dict):
                continue
            if str(item.get("unit") or "").upper() == "PERCENTAGE":
                return bounded_int(item.get("rawValue"), 0, 100)
    return bounded_int(first_present(device, ("batterySoc", "soc", "capacityPercent", "battery")), 0, 100)


def device_hash_from_openapi(device: dict[str, Any]) -> str | None:
    value = first_present(device, ("id", "deviceId", "device_id"))
    return short_hash(str(value)) if value not in (None, "") else None


def normalize_epoch_seconds(value: Any) -> int | None:
    parsed = safe_int(value)
    if parsed is None or parsed <= 0:
        return None
    if parsed > 10_000_000_000:
        parsed = parsed // 1000
    return parsed


SENSITIVE_PAYLOAD_KEYS = {
    "account",
    "accountid",
    "auth",
    "authid",
    "authuid",
    "authorization",
    "blobpath",
    "bloburl",
    "center_gps",
    "clientid",
    "cookie",
    "email",
    "lat",
    "latitude",
    "lng",
    "longitude",
    "mqtthost",
    "mqtturl",
    "ne_gps",
    "origin_gps",
    "password",
    "phone",
    "pwd",
    "pwdinfo",
    "rtk",
    "secret",
    "session",
    "signedurl",
    "sn",
    "sw_gps",
    "token",
    "topic",
    "topiclist",
    "topics",
    "trace",
    "traceid",
    "trace_id",
    "uid",
    "url",
    "userid",
    "username",
    "usersn",
    "vehicle_sn",
    "vehiclesn",
    "subscribetopics",
    "subtopics",
}

SENSITIVE_PAYLOAD_KEY_PARTS = (
    "authorization",
    "bloburl",
    "cookie",
    "gps",
    "latitude",
    "longitude",
    "password",
    "pwd",
    "secret",
    "signed",
    "token",
    "trace",
)


def normalize_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def is_sensitive_payload_key(key: str) -> bool:
    normalized = normalize_key(key)
    return normalized in SENSITIVE_PAYLOAD_KEYS or any(part in normalized for part in SENSITIVE_PAYLOAD_KEY_PARTS)


def sanitize_operational_payload(value: Any) -> Any:
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if is_sensitive_payload_key(key_text):
                continue
            sanitized[key_text] = sanitize_operational_payload(item)
        return sanitized
    if isinstance(value, list):
        return [sanitize_operational_payload(item) for item in value]
    if isinstance(value, str) and re.search(r"\b(?:https?|wss?|mqtts?)://", value, re.IGNORECASE):
        return "<redacted-url>"
    return value


def key_list(value: Any) -> list[str]:
    if isinstance(value, dict):
        return sorted(str(key) for key in value.keys())
    if isinstance(value, list):
        keys: set[str] = set()
        for item in value:
            if isinstance(item, dict):
                keys.update(str(key) for key in item.keys())
        return sorted(keys)
    return []


def route_item_count(value: Any) -> int | None:
    if isinstance(value, list):
        return len(value)
    if isinstance(value, dict):
        for key in ("list", "data", "items", "records", "plans", "planList"):
            item = value.get(key)
            if isinstance(item, list):
                return len(item)
        return 1
    return None


def route_payload_summary(route_alias: str, data: Any, sanitized: Any, observed_at: str | None) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "routeAlias": route_alias,
        "observedAt": observed_at,
        "shape": "list" if isinstance(sanitized, list) else "object" if isinstance(sanitized, dict) else type(sanitized).__name__,
        "itemCount": route_item_count(sanitized),
        "keys": key_list(sanitized),
    }
    if route_alias == "auth-list" and isinstance(data, list):
        devices = []
        for device in data:
            if not isinstance(device, dict):
                continue
            vehicle_sn = device.get("vehicle_sn") or device.get("vehicleSn") or device.get("sn")
            devices.append(
                {
                    "vehicleHash": short_hash(str(vehicle_sn)) if vehicle_sn else None,
                    "name": device.get("selfDefinedName") or device.get("name"),
                    "vehicleType": device.get("vehicle_type") or device.get("vehicleType"),
                    "state": device.get("vehicle_state") or device.get("state"),
                    "batterySoc": safe_int(device.get("soc") or device.get("battery")),
                }
            )
        summary["devices"] = devices
    return summary


def mqtt_snapshot_classes(snapshot: dict[str, Any]) -> list[str]:
    if not isinstance(snapshot, dict):
        return []

    classes: set[str] = set()
    payload_shape = str(snapshot.get("payloadShape") or "").lower()
    if payload_shape == "binary":
        classes.add("binary")

    keys: set[str] = set()
    payload_keys = snapshot.get("payloadKeys")
    if isinstance(payload_keys, list):
        keys.update(str(key) for key in payload_keys if key not in (None, ""))

    safe_fields = snapshot.get("safeFields")
    if isinstance(safe_fields, dict):
        keys.update(str(key) for key in safe_fields.keys())

    groups = {
        "state": {"vehicleState", "state", "workStatus", "taskStatus"},
        "progress": {"currentPartitionId", "partitionId", "mowingPercentage", "pathId", "path_id"},
        "battery": {
            "soc",
            "battery",
            "batterySoc",
            "capacityPercent",
            "capacityRemaining",
            "descriptiveCapacityRemaining",
        },
        "event": {"event", "eventType"},
        "command_result": {"command", "cmd", "commandNo", "responseCode", "result", "resultCode", "errorCode"},
    }
    for class_name, class_keys in groups.items():
        if keys & class_keys:
            classes.add(class_name)

    if not classes and payload_shape == "json":
        classes.add("json")

    priority = ("state", "progress", "battery", "event", "command_result", "json", "binary")
    return [name for name in priority if name in classes]


def insert_area_setting_snapshot(
    con: sqlite3.Connection,
    *,
    source_id: int,
    vehicle_sn: str | None,
    observed_at: str | None,
    partition_length: int | None,
    partition_id_list: Any,
    mowing_zone_list_text: str | None,
    mowing_zone_text: str | None,
    raw_text: str,
) -> int:
    upsert_device(con, vehicle_sn, seen_at=observed_at)
    hash_value = event_hash(
        vehicle_sn,
        observed_at,
        partition_length,
        partition_id_list,
        mowing_zone_list_text,
        mowing_zone_text,
        raw_text,
    )
    cur = con.execute(
        """
        INSERT OR IGNORE INTO area_setting_snapshots(
            source_id, vehicle_sn, observed_at, event_hash, partition_length, partition_id_list_json,
            mowing_zone_list_text, mowing_zone_text, raw_text
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            source_id,
            vehicle_sn,
            observed_at,
            hash_value,
            partition_length,
            json_dumps(partition_id_list),
            normalize_nullish(mowing_zone_list_text),
            normalize_nullish(mowing_zone_text),
            raw_text,
        ),
    )
    if vehicle_sn:
        set_sync_state(
            con,
            f"device.{short_hash(vehicle_sn)}.area_settings",
            {
                "partition_length": partition_length,
                "partition_id_list": partition_id_list,
                "has_mowing_zone_list": normalize_nullish(mowing_zone_list_text) is not None,
                "has_mowing_zone": normalize_nullish(mowing_zone_text) is not None,
                "observed_at": observed_at,
            },
    )
    return sqlite_row_changed(cur)


def insert_device_state_snapshot(
    con: sqlite3.Connection,
    *,
    source_id: int,
    data: dict[str, Any],
    observed_at: str | None,
) -> int:
    sn = data.get("vehicle_sn")
    upsert_device(con, sn, vehicle_type=str(data.get("vehicle_type") or ""), seen_at=observed_at)
    cur = con.execute(
        """
        INSERT OR IGNORE INTO device_state_snapshots(
            source_id, vehicle_sn, observed_at, map_version, vehicle_setting_update_time,
            vehicle_info_update_time, partition_length, partition_id_list_json, raw_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            source_id,
            sn,
            observed_at,
            data.get("mapVersion"),
            data.get("vehicleSettingUpdateTime"),
            data.get("vehicle_info_update_time"),
            data.get("partitionLength"),
            json_dumps(data.get("partitionIdList")),
            json_dumps(data),
        ),
    )
    if sn:
        set_sync_state(
            con,
            f"device.{short_hash(sn)}.index2",
            {
                "map_version": data.get("mapVersion"),
                "vehicle_setting_update_time": data.get("vehicleSettingUpdateTime"),
                "vehicle_info_update_time": data.get("vehicle_info_update_time"),
                "partition_length": data.get("partitionLength"),
                "observed_at": observed_at,
            },
        )
    return sqlite_row_changed(cur)


def insert_device_info_snapshot(
    con: sqlite3.Connection,
    *,
    source_id: int,
    data: dict[str, Any],
    observed_at: str | None,
) -> int:
    sn = data.get("vehicle_sn")
    upsert_device(
        con,
        sn,
        name=data.get("selfDefinedName"),
        model=data.get("model"),
        seen_at=observed_at,
    )
    cur = con.execute(
        """
        INSERT OR IGNORE INTO device_info_snapshots(
            source_id, vehicle_sn, observed_at, model, name, map_area_limit,
            map_max_area_limit, sub_map_limit, plan_max_time, raw_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            source_id,
            sn,
            observed_at,
            data.get("model"),
            data.get("selfDefinedName"),
            data.get("map_area_limit"),
            data.get("map_max_area_limit"),
            data.get("sub_map_limit"),
            data.get("plan_max_time"),
            json_dumps(data),
        ),
    )
    if sn:
        set_sync_state(
            con,
            f"device.{short_hash(sn)}.device_info",
            {
                "model": data.get("model"),
                "map_area_limit": data.get("map_area_limit"),
                "map_max_area_limit": data.get("map_max_area_limit"),
                "sub_map_limit": data.get("sub_map_limit"),
                "plan_max_time": data.get("plan_max_time"),
                "observed_at": observed_at,
            },
        )
    return sqlite_row_changed(cur)


def sanitize_live_location(data: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "posture_x",
        "posture_y",
        "posture_theta",
        "last_posture_x",
        "last_posture_y",
        "last_posture_theta",
        "report_time",
        "mowing_percentage",
        "path_id",
        "subtotal_area",
        "mowing_week_area",
        "map_id",
        "map_base_id",
        "map_edit_time",
    }
    return {key: data.get(key) for key in sorted(allowed) if key in data}


def insert_live_location_snapshot(
    con: sqlite3.Connection,
    *,
    source_id: int,
    vehicle_sn: str | None,
    observed_at: str | None,
    line_no: int,
    data: dict[str, Any],
) -> int:
    sanitized = sanitize_live_location(data)
    if not sanitized:
        return 0
    upsert_device(con, vehicle_sn, seen_at=observed_at)
    posture_x = safe_float(sanitized.get("posture_x") if sanitized.get("posture_x") is not None else sanitized.get("last_posture_x"))
    posture_y = safe_float(sanitized.get("posture_y") if sanitized.get("posture_y") is not None else sanitized.get("last_posture_y"))
    posture_theta = safe_float(
        sanitized.get("posture_theta") if sanitized.get("posture_theta") is not None else sanitized.get("last_posture_theta")
    )
    report_time = safe_int(sanitized.get("report_time"))
    sanitized_json = json_dumps(sanitized)
    hash_value = event_hash(vehicle_sn, observed_at, line_no, sanitized_json)
    cur = con.execute(
        """
        INSERT OR IGNORE INTO live_location_snapshots(
            source_id, vehicle_sn, observed_at, event_hash, posture_x, posture_y,
            posture_theta, report_time, mowing_percentage, path_id, subtotal_area,
            mowing_week_area, map_id, map_base_id, map_edit_time, sanitized_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            source_id,
            vehicle_sn,
            observed_at,
            hash_value,
            posture_x,
            posture_y,
            posture_theta,
            report_time,
            safe_int(sanitized.get("mowing_percentage")),
            safe_int(sanitized.get("path_id")),
            safe_float(sanitized.get("subtotal_area")),
            safe_float(sanitized.get("mowing_week_area")),
            sanitized.get("map_id"),
            safe_int(sanitized.get("map_base_id")),
            safe_int(sanitized.get("map_edit_time")),
            sanitized_json,
        ),
    )
    if vehicle_sn:
        set_sync_state(
            con,
            f"device.{short_hash(vehicle_sn)}.live_location",
            {
                "posture_x": posture_x,
                "posture_y": posture_y,
                "posture_theta": posture_theta,
                "report_time": report_time,
                "mowing_percentage": safe_int(sanitized.get("mowing_percentage")),
                "observed_at": observed_at,
            },
        )
    return sqlite_row_changed(cur)


def insert_trail_time_snapshot(
    con: sqlite3.Connection,
    *,
    source_id: int,
    vehicle_sn: str | None,
    observed_at: str | None,
    line_no: int,
    entries: list[Any],
) -> tuple[int, int]:
    normalized_entries = [entry for entry in entries if isinstance(entry, dict)]
    if not normalized_entries:
        return 0, 0
    upsert_device(con, vehicle_sn, seen_at=observed_at)
    raw_json = json_dumps(normalized_entries)
    hash_value = event_hash(vehicle_sn, observed_at, line_no, raw_json)
    cur = con.execute(
        """
        INSERT OR IGNORE INTO trail_time_snapshots(
            source_id, vehicle_sn, observed_at, event_hash, entry_count, raw_json
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (source_id, vehicle_sn, observed_at, hash_value, len(normalized_entries), raw_json),
    )
    row = con.execute(
        "SELECT id FROM trail_time_snapshots WHERE source_id=? AND event_hash=?",
        (source_id, hash_value),
    ).fetchone()
    if not row:
        return sqlite_row_changed(cur), 0

    snapshot_id = int(row["id"])
    inserted_entries = 0
    for entry in normalized_entries:
        entry_json = json_dumps(entry)
        entry_cur = con.execute(
            """
            INSERT OR IGNORE INTO trail_time_entries(
                snapshot_id, partition_id, start_time, end_time, end_time_alias,
                area_m2, finished_area_m2, partition_percentage, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                snapshot_id,
                entry.get("partitionId"),
                entry.get("startTime"),
                entry.get("endTime"),
                entry.get("endTimeAlias"),
                entry.get("area"),
                entry.get("finishedArea"),
                entry.get("partitionPercentage"),
                entry_json,
            ),
        )
        inserted_entries += sqlite_row_changed(entry_cur)

    if vehicle_sn:
        set_sync_state(
            con,
            f"device.{short_hash(vehicle_sn)}.trail_time",
            {
                "snapshot_id": snapshot_id,
                "entry_count": len(normalized_entries),
                "observed_at": observed_at,
            },
        )
    return sqlite_row_changed(cur), inserted_entries


def insert_route_snapshot_record(
    con: sqlite3.Connection,
    *,
    source_id: int,
    vehicle_sn: str | None,
    observed_at: str | None,
    route_alias: str,
    data: Any,
) -> int:
    sanitized = sanitize_operational_payload(data)
    summary = route_payload_summary(route_alias, data, sanitized, observed_at)
    hash_value = event_hash(vehicle_sn, route_alias, observed_at, sanitized)
    upsert_device(con, vehicle_sn, seen_at=observed_at)
    cur = con.execute(
        """
        INSERT OR IGNORE INTO route_snapshot_records(
            source_id, vehicle_sn, observed_at, route_alias, event_hash, item_count,
            summary_json, sanitized_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            source_id,
            vehicle_sn,
            observed_at,
            route_alias,
            hash_value,
            route_item_count(sanitized),
            json_dumps(summary),
            json_dumps(sanitized),
        ),
    )
    route_row = con.execute(
        "SELECT id FROM route_snapshot_records WHERE source_id=? AND route_alias=? AND event_hash=?",
        (source_id, route_alias, hash_value),
    ).fetchone()
    if route_alias == "mqtt-message" and route_row is not None and isinstance(sanitized, dict):
        insert_mqtt_status_snapshot(
            con,
            route_snapshot_id=int(route_row["id"]),
            observed_at=observed_at,
            snapshot=sanitized,
        )
    if route_alias == "openapi-auth-list" and route_row is not None:
        insert_openapi_auth_snapshot(
            con,
            route_snapshot_id=int(route_row["id"]),
            observed_at=observed_at,
            data=data,
        )
    if route_alias == "openapi-vehicle-status" and route_row is not None:
        insert_openapi_status_snapshot(
            con,
            route_snapshot_id=int(route_row["id"]),
            observed_at=observed_at,
            data=data,
        )
    if route_alias == "auth-list" and isinstance(data, list):
        for device in data:
            if not isinstance(device, dict):
                continue
            upsert_device(
                con,
                device.get("vehicle_sn") or device.get("vehicleSn") or device.get("sn"),
                vehicle_type=str(device.get("vehicle_type") or device.get("vehicleType") or ""),
                name=device.get("selfDefinedName") or device.get("name"),
                seen_at=observed_at,
            )
    if vehicle_sn:
        set_sync_state(
            con,
            f"device.{short_hash(vehicle_sn)}.{route_alias}",
            {
                "route_alias": route_alias,
                "item_count": route_item_count(sanitized),
                "observed_at": observed_at,
            },
        )
    return sqlite_row_changed(cur)


def openapi_auth_device_summary(device: dict[str, Any]) -> dict[str, Any]:
    return {
        "deviceHash": device_hash_from_openapi(device),
        "name": device.get("name") or device.get("selfDefinedName"),
        "model": device.get("model"),
        "firmware": device.get("firmware"),
    }


def insert_openapi_auth_snapshot(
    con: sqlite3.Connection,
    *,
    route_snapshot_id: int,
    observed_at: str | None,
    data: Any,
) -> int:
    devices = [openapi_auth_device_summary(device) for device in payload_devices(data)]
    cur = con.execute(
        """
        INSERT OR IGNORE INTO openapi_auth_snapshots(
            route_snapshot_id, observed_at, device_count, devices_json
        ) VALUES (?, ?, ?, ?)
        """,
        (
            route_snapshot_id,
            observed_at,
            len(devices),
            json_dumps(devices),
        ),
    )
    return sqlite_row_changed(cur)


def openapi_status_device_summary(device: dict[str, Any]) -> dict[str, Any]:
    return {
        "deviceHash": device_hash_from_openapi(device),
        "vehicleState": device.get("vehicleState") or device.get("state"),
        "capacityPercent": first_capacity_percent(device),
        "capacityLabel": device.get("descriptiveCapacityRemaining"),
    }


def insert_openapi_status_snapshot(
    con: sqlite3.Connection,
    *,
    route_snapshot_id: int,
    observed_at: str | None,
    data: Any,
) -> int:
    statuses = [openapi_status_device_summary(device) for device in payload_devices(data)]
    primary = statuses[0] if statuses else {}
    cur = con.execute(
        """
        INSERT OR IGNORE INTO openapi_status_snapshots(
            route_snapshot_id, observed_at, device_count, primary_device_hash,
            vehicle_state, capacity_percent, capacity_label, statuses_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            route_snapshot_id,
            observed_at,
            len(statuses),
            primary.get("deviceHash"),
            primary.get("vehicleState"),
            primary.get("capacityPercent"),
            primary.get("capacityLabel"),
            json_dumps(statuses),
        ),
    )
    return sqlite_row_changed(cur)


def backfill_openapi_typed_snapshots(con: sqlite3.Connection) -> None:
    rows = con.execute(
        """
        SELECT id, route_alias, observed_at, sanitized_json
        FROM route_snapshot_records
        WHERE route_alias IN ('openapi-auth-list', 'openapi-vehicle-status')
        ORDER BY id
        """
    ).fetchall()
    for row in rows:
        payload = parse_json(row["sanitized_json"])
        if payload is None:
            continue
        if row["route_alias"] == "openapi-auth-list":
            insert_openapi_auth_snapshot(
                con,
                route_snapshot_id=int(row["id"]),
                observed_at=row["observed_at"],
                data=payload,
            )
        elif row["route_alias"] == "openapi-vehicle-status":
            insert_openapi_status_snapshot(
                con,
                route_snapshot_id=int(row["id"]),
                observed_at=row["observed_at"],
                data=payload,
            )


def insert_mqtt_status_snapshot(
    con: sqlite3.Connection,
    *,
    route_snapshot_id: int,
    observed_at: str | None,
    snapshot: dict[str, Any],
) -> int:
    safe_fields = snapshot.get("safeFields")
    if not isinstance(safe_fields, dict) or not safe_fields:
        return 0

    battery_soc = bounded_int(
        first_present(safe_fields, ("soc", "batterySoc", "capacityPercent", "capacityRemaining", "battery")),
        0,
        100,
    )
    mowing_percentage = bounded_int(safe_fields.get("mowingPercentage"), 0, 100)
    report_time = normalize_epoch_seconds(first_present(safe_fields, ("reportTime", "report_time", "timestamp", "time")))
    state = first_present(safe_fields, ("vehicleState", "state"))
    task_status = first_present(safe_fields, ("taskStatus",))
    work_status = first_present(safe_fields, ("workStatus",))
    event_type = first_present(safe_fields, ("eventType", "event"))

    cur = con.execute(
        """
        INSERT OR IGNORE INTO mqtt_status_snapshots(
            route_snapshot_id, observed_at, topic_hash, payload_sha256, report_time,
            state, task_status, work_status, battery_soc, capacity_label,
            current_partition_id, mowing_percentage, path_id, event_type,
            safe_fields_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            route_snapshot_id,
            observed_at,
            snapshot.get("topicHash"),
            snapshot.get("payloadSha256"),
            report_time,
            str(state) if state not in (None, "") else None,
            str(task_status) if task_status not in (None, "") else None,
            str(work_status) if work_status not in (None, "") else None,
            battery_soc,
            str(safe_fields.get("descriptiveCapacityRemaining")) if safe_fields.get("descriptiveCapacityRemaining") not in (None, "") else None,
            safe_int(first_present(safe_fields, ("currentPartitionId", "partitionId"))),
            mowing_percentage,
            safe_int(first_present(safe_fields, ("pathId", "path_id"))),
            str(event_type) if event_type not in (None, "") else None,
            json_dumps(safe_fields),
        ),
    )
    return sqlite_row_changed(cur)


def decode_map_detail_compress(raw: str) -> tuple[dict[str, Any], dict[str, Any]] | None:
    try:
        compressed = base64.b64decode(raw)
        decompressed = subprocess.check_output(["zstd", "-dc"], input=compressed, stderr=subprocess.DEVNULL)
        outer = json.loads(decompressed.decode())
        detail_raw = outer.get("map_detail")
        detail = json.loads(detail_raw) if isinstance(detail_raw, str) else {}
        if not isinstance(detail, dict):
            detail = {}
        return outer, detail
    except Exception:
        return None


def insert_map_detail_snapshot(
    con: sqlite3.Connection,
    *,
    source_id: int,
    vehicle_sn: str | None,
    observed_at: str | None,
    line_no: int,
    outer: dict[str, Any],
    detail: dict[str, Any],
) -> tuple[int, int]:
    hash_value = event_hash(vehicle_sn, outer.get("map_id"), outer.get("map_base_id"), outer.get("map_detail"))
    existing_row = con.execute(
        """
        SELECT id
        FROM map_detail_snapshots
        WHERE (vehicle_sn=? OR (vehicle_sn IS NULL AND ? IS NULL)) AND event_hash=?
        ORDER BY id DESC
        LIMIT 1
        """,
        (vehicle_sn, vehicle_sn, hash_value),
    ).fetchone()
    if existing_row is not None:
        if vehicle_sn:
            set_sync_state(
                con,
                f"device.{short_hash(vehicle_sn)}.map_detail",
                {
                    "map_id": outer.get("map_id"),
                    "map_base_id": outer.get("map_base_id"),
                    "map_name": outer.get("map_name") or detail.get("name"),
                    "sub_maps": len(detail.get("sub_maps") or []),
                    "detail_area": detail.get("area"),
                    "observed_at": observed_at,
                },
            )
        return 0, 0

    cur = con.execute(
        """
        INSERT OR IGNORE INTO map_detail_snapshots(
            source_id, vehicle_sn, observed_at, event_hash, map_id, map_base_id,
            map_name, total_area, detail_area, raw_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            source_id,
            vehicle_sn,
            observed_at,
            hash_value,
            outer.get("map_id"),
            outer.get("map_base_id"),
            outer.get("map_name") or detail.get("name"),
            outer.get("total_area"),
            detail.get("area"),
            json_dumps({"outer": outer, "detail": detail}),
        ),
    )
    inserted_snapshot = sqlite_row_changed(cur)
    row = con.execute(
        "SELECT id FROM map_detail_snapshots WHERE source_id=? AND event_hash=?",
        (source_id, hash_value),
    ).fetchone()
    if not row:
        return inserted_snapshot, 0

    area_count = 0
    snapshot_id = int(row["id"])
    for area in detail.get("sub_maps") or []:
        if not isinstance(area, dict):
            continue
        area_key, name, area_size = area_identity(area)
        try:
            area_id = int(area_key) if area_key is not None else None
        except ValueError:
            area_id = None
        area_cur = con.execute(
            """
            INSERT OR IGNORE INTO map_detail_areas(
                snapshot_id, area_id, name, area_size, area_type, element_count, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                snapshot_id,
                area_id,
                name,
                area_size,
                area.get("type"),
                len(area.get("elements") or []),
                json_dumps(area),
            ),
        )
        area_count += sqlite_row_changed(area_cur)

    if vehicle_sn:
        set_sync_state(
            con,
            f"device.{short_hash(vehicle_sn)}.map_detail",
            {
                "map_id": outer.get("map_id"),
                "map_base_id": outer.get("map_base_id"),
                "map_name": outer.get("map_name") or detail.get("name"),
                "sub_maps": len(detail.get("sub_maps") or []),
                "detail_area": detail.get("area"),
                "observed_at": observed_at,
            },
        )

    return inserted_snapshot, area_count


def ingest_post_cache(con: sqlite3.Connection, cache_dir: Path) -> dict[str, int]:
    source_id = add_source(con, cache_dir / "journal", "post-cache") if (cache_dir / "journal").exists() else None
    counts = {
        "http_cache_entries": 0,
        "device_state_snapshots": 0,
        "device_info_snapshots": 0,
        "area_setting_snapshots": 0,
        "route_snapshot_records": 0,
    }

    for body_path in sorted(cache_dir.glob("*.1")):
        meta_path = body_path.with_suffix(".0")
        if not meta_path.exists():
            continue

        meta = parse_cache_meta(meta_path)
        body = parse_json(body_path.read_text(errors="replace"))
        if body is None:
            continue

        if source_id is None:
            source_id = add_source(con, body_path, "post-cache")

        cache_key = body_path.stem
        cur = con.execute(
            """
            INSERT INTO http_cache_entries(
                source_id, cache_key, url, method, response_date, trace_id, response_json, imported_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_id, cache_key) DO UPDATE SET
                url=excluded.url,
                method=excluded.method,
                response_date=excluded.response_date,
                trace_id=excluded.trace_id,
                response_json=excluded.response_json,
                imported_at=excluded.imported_at
            WHERE
                http_cache_entries.url IS NOT excluded.url OR
                http_cache_entries.method IS NOT excluded.method OR
                http_cache_entries.response_date IS NOT excluded.response_date OR
                http_cache_entries.trace_id IS NOT excluded.trace_id OR
                http_cache_entries.response_json IS NOT excluded.response_json
            """,
            (
                source_id,
                cache_key,
                meta["url"],
                meta["method"],
                meta["response_date"],
                meta["trace_id"],
                json_dumps(body),
                now_iso(),
            ),
        )
        counts["http_cache_entries"] += sqlite_row_changed(cur)

        data = body.get("data") if isinstance(body, dict) else None
        path = urllib.parse.urlparse(meta["url"]).path
        observed_at = meta["response_date"] or None

        if path.endswith("/vehicle/vehicle/index2") and isinstance(data, dict):
            sn = data.get("vehicle_sn")
            upsert_device(con, sn, vehicle_type=str(data.get("vehicle_type") or ""), seen_at=observed_at)
            cur = con.execute(
                """
                INSERT OR IGNORE INTO device_state_snapshots(
                    source_id, vehicle_sn, observed_at, map_version, vehicle_setting_update_time,
                    vehicle_info_update_time, partition_length, partition_id_list_json, raw_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    source_id,
                    sn,
                    observed_at,
                    data.get("mapVersion"),
                    data.get("vehicleSettingUpdateTime"),
                    data.get("vehicle_info_update_time"),
                    data.get("partitionLength"),
                    json_dumps(data.get("partitionIdList")),
                    json_dumps(data),
                ),
            )
            counts["device_state_snapshots"] += sqlite_row_changed(cur)
            counts["area_setting_snapshots"] += insert_area_setting_snapshot(
                con,
                source_id=source_id,
                vehicle_sn=sn,
                observed_at=observed_at,
                partition_length=data.get("partitionLength"),
                partition_id_list=data.get("partitionIdList"),
                mowing_zone_list_text=None,
                mowing_zone_text=None,
                raw_text=json_dumps(
                    {
                        "mapVersion": data.get("mapVersion"),
                        "partitionLength": data.get("partitionLength"),
                        "partitionIdList": data.get("partitionIdList"),
                    }
                ),
            )
            if sn:
                set_sync_state(
                    con,
                    f"device.{short_hash(sn)}.index2",
                    {
                        "map_version": data.get("mapVersion"),
                        "vehicle_setting_update_time": data.get("vehicleSettingUpdateTime"),
                        "vehicle_info_update_time": data.get("vehicle_info_update_time"),
                        "partition_length": data.get("partitionLength"),
                        "observed_at": observed_at,
                    },
                )

        elif path.endswith("/vehicle/vehicle/get-device-info") and isinstance(data, dict):
            sn = data.get("vehicle_sn")
            upsert_device(
                con,
                sn,
                name=data.get("selfDefinedName"),
                model=data.get("model"),
                seen_at=observed_at,
            )
            cur = con.execute(
                """
                INSERT OR IGNORE INTO device_info_snapshots(
                    source_id, vehicle_sn, observed_at, model, name, map_area_limit,
                    map_max_area_limit, sub_map_limit, plan_max_time, raw_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    source_id,
                    sn,
                    observed_at,
                    data.get("model"),
                    data.get("selfDefinedName"),
                    data.get("map_area_limit"),
                    data.get("map_max_area_limit"),
                    data.get("sub_map_limit"),
                    data.get("plan_max_time"),
                    json_dumps(data),
                ),
            )
            counts["device_info_snapshots"] += sqlite_row_changed(cur)
            if sn:
                set_sync_state(
                    con,
                    f"device.{short_hash(sn)}.device_info",
                    {
                        "model": data.get("model"),
                        "map_area_limit": data.get("map_area_limit"),
                        "map_max_area_limit": data.get("map_max_area_limit"),
                        "sub_map_limit": data.get("sub_map_limit"),
                        "plan_max_time": data.get("plan_max_time"),
                        "observed_at": observed_at,
                    },
                )

        elif path.endswith("/vehicle/vehicle/auth-list") and isinstance(data, list):
            counts["route_snapshot_records"] += insert_route_snapshot_record(
                con,
                source_id=source_id,
                vehicle_sn=None,
                observed_at=observed_at,
                route_alias="auth-list",
                data=data,
            )
            for device in data:
                if isinstance(device, dict):
                    upsert_device(
                        con,
                        device.get("vehicle_sn"),
                        vehicle_type=str(device.get("vehicle_type") or ""),
                        name=device.get("selfDefinedName"),
                        seen_at=observed_at,
                    )

        elif path in ROUTE_ALIAS_BY_PATH and isinstance(data, (dict, list)):
            counts["route_snapshot_records"] += insert_route_snapshot_record(
                con,
                source_id=source_id,
                vehicle_sn=None,
                observed_at=observed_at,
                route_alias=ROUTE_ALIAS_BY_PATH[path],
                data=data,
            )

    con.commit()
    return counts


def extract_blob_url_parts(url: str) -> dict[str, str | None]:
    parsed = urllib.parse.urlparse(url)
    query = urllib.parse.parse_qs(parsed.query)
    expiry = query.get("se", [""])[0] or None
    return {
        "host": parsed.netloc,
        "path": parsed.path,
        "expires_at": expiry,
        "sha256": hashlib.sha256(url.encode()).hexdigest(),
    }


def build_ssl_context(*, insecure_tls: bool) -> ssl.SSLContext:
    if insecure_tls:
        return ssl._create_unverified_context()
    try:
        import certifi  # type: ignore

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def ingest_log(con: sqlite3.Connection, log_path: Path, *, store_signed_urls: bool) -> dict[str, int]:
    source_id = add_source(con, log_path, "log")
    counts = {
        "map_resource_events": 0,
        "map_detail_snapshots": 0,
        "map_detail_areas": 0,
        "schedule_snapshots": 0,
        "trail_time_snapshots": 0,
        "trail_time_entries": 0,
        "live_location_snapshots": 0,
        "command_envelopes": 0,
        "area_setting_snapshots": 0,
    }
    latest_sn: str | None = None
    latest_map_event_id: int | None = None
    sn_context_by_line: dict[int, str | None] = {}

    for line_no, line in enumerate(read_text(log_path).splitlines(), start=1):
        observed_at = parse_log_ts(line)
        sn_match = GET_MAP_INFO_RE.search(line) or VEHICLE_SN_RE.search(line)
        if sn_match:
            latest_sn = sn_match.group("sn")
            upsert_device(con, latest_sn, seen_at=observed_at)

        update_match = UPDATE_DEVICE_RE.search(line)
        if update_match:
            latest_sn = update_match.group("sn")
            upsert_device(con, latest_sn, vehicle_type=update_match.group("type"), seen_at=observed_at)
        sn_context_by_line[line_no] = latest_sn

        iot_match = GET_IOT_FILE_RE.search(line)
        if iot_match:
            body = parse_json(iot_match.group("body"))
            if isinstance(body, dict):
                sn = body.get("sn") or latest_sn
                upsert_device(con, sn, seen_at=observed_at)
                url = body.get("url") or ""
                parts = extract_blob_url_parts(url) if isinstance(url, str) and url else {}
                hash_value = event_hash(line_no, sn, body.get("version"), parts.get("host"), parts.get("path"))
                cur = con.execute(
                    """
                    INSERT OR IGNORE INTO map_resource_events(
                        source_id, vehicle_sn, observed_at, event_hash, remote_version, local_version, status,
                        blob_host, blob_path, url_expires_at, url_sha256, signed_url, raw_json
                    ) VALUES (?, ?, ?, ?, ?, NULL, NULL, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        source_id,
                        sn,
                        observed_at,
                        hash_value,
                        int(body.get("version") or 0),
                        parts.get("host"),
                        parts.get("path"),
                        parts.get("expires_at"),
                        parts.get("sha256"),
                        url if store_signed_urls else None,
                        json_dumps({k: ("<signed-url>" if k == "url" else v) for k, v in body.items()}),
                    ),
                )
                row = con.execute(
                    """
                    SELECT id FROM map_resource_events
                    WHERE source_id=? AND event_hash=?
                    ORDER BY id DESC LIMIT 1
                    """,
                    (source_id, hash_value),
                ).fetchone()
                latest_map_event_id = int(row["id"]) if row else None
                counts["map_resource_events"] += sqlite_row_changed(cur)
                if sn:
                    set_sync_state(
                        con,
                        f"device.{short_hash(sn)}.map_resource",
                        {
                            "remote_version": int(body.get("version") or 0),
                            "local_version": None,
                            "status": None,
                            "observed_at": observed_at,
                            "url_expires_at": parts.get("expires_at"),
                        },
                    )

        local_match = MAP_LOCAL_VERSION_RE.search(line)
        if local_match and latest_map_event_id:
            local_version = int(local_match.group("local"))
            remote_version = int(local_match.group("remote"))
            status = "current" if local_version == remote_version else "update_available"
            con.execute(
                "UPDATE map_resource_events SET local_version=?, status=? WHERE id=?",
                (local_version, status, latest_map_event_id),
            )
            row = con.execute(
                "SELECT vehicle_sn, remote_version, observed_at, url_expires_at FROM map_resource_events WHERE id=?",
                (latest_map_event_id,),
            ).fetchone()
            if row and row["vehicle_sn"]:
                set_sync_state(
                    con,
                    f"device.{short_hash(row['vehicle_sn'])}.map_resource",
                    {
                        "remote_version": int(row["remote_version"]),
                        "local_version": local_version,
                        "status": status,
                        "observed_at": row["observed_at"],
                        "url_expires_at": row["url_expires_at"],
                        },
                    )

        map_detail_match = MAP_DETAIL_COMPRESS_RE.search(line)
        if map_detail_match:
            decoded = decode_map_detail_compress(map_detail_match.group("body"))
            if decoded:
                outer, detail = decoded
                inserted, areas = insert_map_detail_snapshot(
                    con,
                    source_id=source_id,
                    vehicle_sn=latest_sn,
                    observed_at=observed_at or f"line:{line_no}",
                    line_no=line_no,
                    outer=outer,
                    detail=detail,
                )
                counts["map_detail_snapshots"] += inserted
                counts["map_detail_areas"] += areas

        if "/vehicle/trail/get-path-info-time" in line:
            trail_time_data = parse_log_json_after_data_marker(line)
            if isinstance(trail_time_data, list):
                inserted, entries = insert_trail_time_snapshot(
                    con,
                    source_id=source_id,
                    vehicle_sn=latest_sn,
                    observed_at=observed_at or f"line:{line_no}",
                    line_no=line_no,
                    entries=trail_time_data,
                )
                counts["trail_time_snapshots"] += inserted
                counts["trail_time_entries"] += entries

        if "/vehicle/vehicle/get-location" in line:
            location_data = parse_log_json_after_data_marker(line)
            if isinstance(location_data, dict):
                counts["live_location_snapshots"] += insert_live_location_snapshot(
                    con,
                    source_id=source_id,
                    vehicle_sn=latest_sn,
                    observed_at=observed_at or f"line:{line_no}",
                    line_no=line_no,
                    data=location_data,
                )

        setting_body = extract_mower_setting_body(line)
        if setting_body is not None:
            counts["area_setting_snapshots"] += insert_area_setting_snapshot(
                con,
                source_id=source_id,
                vehicle_sn=latest_sn,
                observed_at=observed_at or f"line:{line_no}",
                partition_length=None,
                partition_id_list=None,
                mowing_zone_list_text=extract_bean_field(setting_body, "mowingZoneList"),
                mowing_zone_text=extract_bean_field(setting_body, "mowingZone"),
                raw_text=setting_body,
            )

        encrypted_match = ENCRYPTED_BODY_RE.search(line)
        if encrypted_match:
            envelope = parse_json(encrypted_match.group("body"))
            if isinstance(envelope, dict):
                body_for_hash = json_dumps(envelope)
                cur = con.execute(
                    """
                    INSERT OR IGNORE INTO command_envelopes(
                        source_id, vehicle_sn, observed_at, envelope_json, envelope_sha256, note
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        source_id,
                        latest_sn,
                        observed_at,
                        body_for_hash,
                        hashlib.sha256(body_for_hash.encode()).hexdigest(),
                        "NbNeteaseDecrypt encryptContent envelope from app log",
                    ),
                )
                counts["command_envelopes"] += sqlite_row_changed(cur)

    schedule_result = extract_schedule(log_path)
    for snapshot in schedule_result.get("v2_snapshots", []):
        schedule = snapshot.get("schedule")
        line = snapshot.get("line")
        observed_at = f"line:{line}"
        try:
            sn_for_snapshot = sn_context_by_line.get(int(line), latest_sn)
        except (TypeError, ValueError):
            sn_for_snapshot = latest_sn
        raw_json = json_dumps(schedule)
        hash_value = event_hash(line, sn_for_snapshot, raw_json)
        plan_hex = None
        if schedule_result.get("latest_legacy_plan"):
            plan_hex = schedule_result["latest_legacy_plan"].get("plan_hex")
        cur = con.execute(
            """
            INSERT OR IGNORE INTO schedule_snapshots(source_id, vehicle_sn, observed_at, event_hash, plan_hex, raw_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (source_id, sn_for_snapshot, observed_at, hash_value, plan_hex, raw_json),
        )
        if cur.rowcount == 0:
            row = con.execute(
                "SELECT id FROM schedule_snapshots WHERE source_id=? AND event_hash=?",
                (source_id, hash_value),
            ).fetchone()
        else:
            row = con.execute("SELECT last_insert_rowid() AS id").fetchone()
            counts["schedule_snapshots"] += 1
            if sn_for_snapshot:
                set_sync_state(
                    con,
                    f"device.{short_hash(sn_for_snapshot)}.schedule",
                    {"snapshot_id": int(row["id"]), "observed_at": observed_at},
                )
        if not row:
            continue
        snapshot_id = int(row["id"])
        for day in schedule or []:
            con.execute(
                "INSERT OR IGNORE INTO schedule_days(snapshot_id, day, open) VALUES (?, ?, ?)",
                (snapshot_id, day.get("day"), day.get("open")),
            )
            day_row = con.execute(
                "SELECT id FROM schedule_days WHERE snapshot_id=? AND day=?",
                (snapshot_id, day.get("day")),
            ).fetchone()
            if not day_row:
                continue
            for index, period in enumerate(day.get("periods") or []):
                con.execute(
                    """
                    INSERT OR IGNORE INTO schedule_periods(
                        day_id, period_index, start_tick, end_tick, partition_ids_json
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        int(day_row["id"]),
                        index,
                        period.get("start_tick"),
                        period.get("end_tick"),
                        json_dumps(period.get("partition_ids") or []),
                    ),
                )

    con.commit()
    return counts


def classify_member(data: bytes) -> str:
    stripped = data.lstrip()
    if stripped.startswith(b"{") or stripped.startswith(b"["):
        return "json"
    if data.startswith(b"RIFF") and b"WEBP" in data[:16]:
        return "webp"
    if data.startswith(b"\x1f\x8b"):
        return "gzip"
    if data.startswith(b"PK\x03\x04"):
        return "zip"
    return "binary"


def inspect_artifact(path: Path) -> tuple[str, list[tuple[str, Any]], list[dict[str, Any]]]:
    data = path.read_bytes()
    stripped = data.lstrip()
    docs: list[tuple[str, Any]] = []
    members: list[dict[str, Any]] = []

    if stripped.startswith(b"{") or stripped.startswith(b"["):
        parsed = parse_json(data.decode(errors="replace"))
        if parsed is not None:
            docs.append((path.name, parsed))
            members.append(
                {
                    "path": path.name,
                    "sha256": sha256_bytes(data),
                    "size_bytes": len(data),
                    "content_kind": "json",
                    "metadata": parsed if isinstance(parsed, dict) else None,
                }
            )
            return "json", docs, members
        return "json-invalid", docs, members

    if data.startswith(b"\x1f\x8b"):
        try:
            decompressed = gzip.decompress(data)
        except OSError:
            return "gzip-invalid", docs, members
        parsed = parse_json(decompressed.decode(errors="replace"))
        if parsed is not None:
            docs.append((path.name + ":gzip", parsed))
            members.append(
                {
                    "path": path.name + ":gzip",
                    "sha256": sha256_bytes(decompressed),
                    "size_bytes": len(decompressed),
                    "content_kind": "json",
                    "metadata": parsed if isinstance(parsed, dict) else None,
                }
            )
            return "gzip-json", docs, members
        return "gzip-binary", docs, members

    if data.startswith(b"PK\x03\x04"):
        with zipfile.ZipFile(path) as zf:
            for name in zf.namelist():
                if name.endswith("/"):
                    continue
                content = zf.read(name)
                member_kind = classify_member(content)
                metadata = None
                parsed = parse_json(content.decode(errors="replace"))
                if parsed is not None:
                    docs.append((name, parsed))
                    member_kind = "json"
                    metadata = parsed if isinstance(parsed, dict) else None
                members.append(
                    {
                        "path": name,
                        "sha256": sha256_bytes(content),
                        "size_bytes": len(content),
                        "content_kind": member_kind,
                        "metadata": metadata,
                    }
                )
        return ("zip-json" if docs else "zip"), docs, members

    return "binary", docs, members


AREA_KEY_NAMES = (
    "partitionId",
    "partition_id",
    "partitionID",
    "subMapId",
    "sub_map_id",
    "areaId",
    "area_id",
    "zoneId",
    "zone_id",
    "id",
)
AREA_KEY_SET = set(AREA_KEY_NAMES)


def walk_area_candidates(value: Any) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    if isinstance(value, dict):
        keys = set(value)
        areaish = bool(keys & AREA_KEY_SET) and any(
            key.lower() in {"name", "area", "size", "boundary", "points", "polygon", "vertexes", "vertices"}
            or "area" in key.lower()
            or "partition" in key.lower()
            for key in keys
        )
        if areaish:
            candidates.append(value)
        for item in value.values():
            candidates.extend(walk_area_candidates(item))
    elif isinstance(value, list):
        for item in value:
            candidates.extend(walk_area_candidates(item))
    return candidates


def area_identity(area: dict[str, Any]) -> tuple[str | None, str | None, float | None]:
    area_key = None
    for key in AREA_KEY_NAMES:
        if key in area:
            area_key = str(area[key])
            break
    name = None
    for key in ("name", "areaName", "partitionName", "zoneName"):
        if key in area and area[key]:
            name = str(area[key])
            break
    area_size = None
    for key in ("area", "areaSize", "size", "acreage"):
        if key in area:
            try:
                area_size = float(area[key])
            except (TypeError, ValueError):
                pass
            break
    return area_key, name, area_size


def store_artifact_members(
    con: sqlite3.Connection,
    artifact_id: int,
    members: list[dict[str, Any]],
) -> None:
    for member in members:
        metadata = member.get("metadata")
        con.execute(
            """
            INSERT INTO map_artifact_files(
                artifact_id, member_path, sha256, size_bytes, content_kind, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(artifact_id, member_path) DO UPDATE SET
                sha256=excluded.sha256,
                size_bytes=excluded.size_bytes,
                content_kind=excluded.content_kind,
                metadata_json=excluded.metadata_json
            """,
            (
                artifact_id,
                member["path"],
                member.get("sha256"),
                member["size_bytes"],
                member["content_kind"],
                json_dumps(metadata) if metadata is not None else None,
            ),
        )
        if isinstance(metadata, dict) and (
            "pixel_per_meter" in metadata or "terrain_view_image_name" in metadata
        ):
            con.execute(
                """
                INSERT INTO map_render_metadata(
                    artifact_id, member_path, width, height, min_x, max_x, min_y, max_y,
                    min_z, max_z, pixel_per_meter, map_init_ts, start_timestamp,
                    end_timestamp, terrain_view_image_name, terrain_adapt_image_name, raw_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(artifact_id, member_path) DO UPDATE SET
                    width=excluded.width,
                    height=excluded.height,
                    min_x=excluded.min_x,
                    max_x=excluded.max_x,
                    min_y=excluded.min_y,
                    max_y=excluded.max_y,
                    min_z=excluded.min_z,
                    max_z=excluded.max_z,
                    pixel_per_meter=excluded.pixel_per_meter,
                    map_init_ts=excluded.map_init_ts,
                    start_timestamp=excluded.start_timestamp,
                    end_timestamp=excluded.end_timestamp,
                    terrain_view_image_name=excluded.terrain_view_image_name,
                    terrain_adapt_image_name=excluded.terrain_adapt_image_name,
                    raw_json=excluded.raw_json
                """,
                (
                    artifact_id,
                    member["path"],
                    metadata.get("width"),
                    metadata.get("height"),
                    metadata.get("minX"),
                    metadata.get("maxX"),
                    metadata.get("minY"),
                    metadata.get("maxY"),
                    metadata.get("minZ"),
                    metadata.get("maxZ"),
                    metadata.get("pixel_per_meter"),
                    metadata.get("map_init_ts"),
                    metadata.get("start_timestamp_10"),
                    metadata.get("end_timestamp_10"),
                    metadata.get("terrain_view_image_name"),
                    metadata.get("terrain_adapt_image_name"),
                    json_dumps(metadata),
                ),
            )


def validate_blob_download_url(url: str) -> tuple[bool, str | None]:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https":
        return False, "download URL is not HTTPS"
    host = parsed.hostname or ""
    if not host.endswith(".blob.core.windows.net"):
        return False, "download host is not an Azure Blob host"
    try:
        ip = ipaddress.ip_address(host)
        if ip.is_private or ip.is_loopback or ip.is_link_local:
            return False, "download host resolves to a local/private IP literal"
    except ValueError:
        pass
    return True, None


def discard_signed_map_urls(con: sqlite3.Connection, *, vehicle_sn: str | None = None, version: int | None = None) -> int:
    if vehicle_sn is not None and version is not None:
        cur = con.execute(
            "UPDATE map_resource_events SET signed_url=NULL WHERE vehicle_sn=? AND remote_version=? AND signed_url IS NOT NULL",
            (vehicle_sn, version),
        )
    else:
        cur = con.execute("UPDATE map_resource_events SET signed_url=NULL WHERE signed_url IS NOT NULL")
    return max(int(cur.rowcount or 0), 0)


def safe_download_error(exc: BaseException) -> dict[str, str]:
    if isinstance(exc, urllib.error.URLError):
        return {"type": "url_error", "reason": exc.__class__.__name__}
    if isinstance(exc, ssl.SSLError):
        return {"type": "tls_error", "reason": exc.__class__.__name__}
    if isinstance(exc, OSError):
        return {"type": "io_error", "reason": exc.__class__.__name__}
    return {"type": "download_error", "reason": exc.__class__.__name__}


def download_map_artifacts(
    con: sqlite3.Connection,
    map_dir: Path,
    *,
    insecure_tls: bool = False,
) -> dict[str, int]:
    rows = con.execute(
        """
        SELECT * FROM map_resource_events
        WHERE signed_url IS NOT NULL
        ORDER BY observed_at DESC, id DESC
        """
    ).fetchall()
    counts = {"downloaded": 0, "skipped": 0, "areas": 0, "failed": 0}
    seen: set[tuple[str, int]] = set()
    ssl_context = build_ssl_context(insecure_tls=insecure_tls)

    for row in rows:
        sn = row["vehicle_sn"]
        version = int(row["remote_version"])
        if not sn or (sn, version) in seen:
            continue
        seen.add((sn, version))

        existing = con.execute(
            "SELECT id, file_path, sha256 FROM map_artifacts WHERE vehicle_sn=? AND version=? ORDER BY id DESC LIMIT 1",
            (sn, version),
        ).fetchone()
        if existing and Path(existing["file_path"]).exists():
            if sha256_file(Path(existing["file_path"])) == existing["sha256"]:
                counts["skipped"] += 1
                discard_signed_map_urls(con, vehicle_sn=sn, version=version)
                continue

        target_dir = map_dir / short_hash(sn) / str(version)
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / "resource.bin"
        temp_target = target.with_suffix(".tmp")

        ok, reason = validate_blob_download_url(row["signed_url"])
        if not ok:
            counts["failed"] += 1
            set_sync_state(
                con,
                f"device.{short_hash(sn)}.map_download_error",
                {
                    "version": version,
                    "error": reason,
                    "observed_at": row["observed_at"],
                },
            )
            discard_signed_map_urls(con, vehicle_sn=sn, version=version)
            continue
        request = urllib.request.Request(row["signed_url"], headers={"User-Agent": "Navimow/4.02.0"})
        try:
            with urllib.request.urlopen(request, timeout=60, context=ssl_context) as response:
                temp_target.write_bytes(response.read())
        except Exception as exc:
            counts["failed"] += 1
            if temp_target.exists():
                temp_target.unlink()
            error = safe_download_error(exc)
            set_sync_state(
                con,
                f"device.{short_hash(sn)}.map_download_error",
                {
                    "version": version,
                    "error": error["type"],
                    "reason": error["reason"],
                    "observed_at": row["observed_at"],
                },
            )
            discard_signed_map_urls(con, vehicle_sn=sn, version=version)
            continue

        temp_target.replace(target)
        digest = sha256_file(target)
        try:
            content_kind, docs, members = inspect_artifact(target)
        except Exception as exc:
            counts["failed"] += 1
            error = safe_download_error(exc)
            set_sync_state(
                con,
                f"device.{short_hash(sn)}.map_download_error",
                {
                    "version": version,
                    "error": error["type"],
                    "reason": error["reason"],
                    "observed_at": row["observed_at"],
                },
            )
            discard_signed_map_urls(con, vehicle_sn=sn, version=version)
            continue
        parsed_status = "parsed" if docs else "stored-unparsed"
        con.execute(
            """
            INSERT INTO map_artifacts(
                vehicle_sn, version, file_path, sha256, size_bytes, content_kind, parsed_status, imported_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(vehicle_sn, version, sha256) DO UPDATE SET
                file_path=excluded.file_path,
                size_bytes=excluded.size_bytes,
                content_kind=excluded.content_kind,
                parsed_status=excluded.parsed_status,
                imported_at=excluded.imported_at
            """,
            (sn, version, str(target), digest, target.stat().st_size, content_kind, parsed_status, now_iso()),
        )
        artifact_id = int(
            con.execute(
                "SELECT id FROM map_artifacts WHERE vehicle_sn=? AND version=? AND sha256=?",
                (sn, version, digest),
            ).fetchone()["id"]
        )
        counts["downloaded"] += 1
        store_artifact_members(con, artifact_id, members)
        set_sync_state(
            con,
            f"device.{short_hash(sn)}.map_artifact",
            {
                "version": version,
                "sha256": digest,
                "size_bytes": target.stat().st_size,
                "content_kind": content_kind,
                "parsed_status": parsed_status,
                "file_path": str(target),
            },
        )
        discard_signed_map_urls(con, vehicle_sn=sn, version=version)

        for doc_name, doc in docs:
            for index, area in enumerate(walk_area_candidates(doc)):
                area_key, name, area_size = area_identity(area)
                cur = con.execute(
                    """
                    INSERT OR IGNORE INTO map_area_records(
                        artifact_id, area_key, name, area_size, raw_json
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        artifact_id,
                        area_key or f"{doc_name}:{index}",
                        name,
                        area_size,
                        json_dumps(area),
                    ),
                )
                counts["areas"] += sqlite_row_changed(cur)

    con.commit()
    return counts


def ingest_path(con: sqlite3.Connection, path: Path, *, store_signed_urls: bool) -> dict[str, int]:
    totals: dict[str, int] = {}

    def add_counts(counts: dict[str, int]) -> None:
        for key, value in counts.items():
            totals[key] = totals.get(key, 0) + value

    if path.is_dir():
        if is_disk_lru_cache_dir(path):
            add_counts(ingest_post_cache(con, path))
            return totals
        for child in sorted(path.iterdir()):
            if child.is_dir():
                add_counts(ingest_path(con, child, store_signed_urls=store_signed_urls))
            elif child.suffix in {".log", ".txt"} or child.name.endswith("-decoded.log"):
                add_counts(ingest_log(con, child, store_signed_urls=store_signed_urls))
    else:
        if path.suffix in {".log", ".txt"} or path.name.endswith("-decoded.log"):
            add_counts(ingest_log(con, path, store_signed_urls=store_signed_urls))
    return totals


def print_summary(con: sqlite3.Connection) -> None:
    for table in [
        "devices",
        "device_state_snapshots",
        "device_info_snapshots",
        "map_resource_events",
        "map_artifacts",
        "map_artifact_files",
        "map_render_metadata",
        "map_area_records",
        "map_detail_snapshots",
        "map_detail_areas",
        "area_setting_snapshots",
        "schedule_snapshots",
        "trail_time_snapshots",
        "trail_time_entries",
        "live_location_snapshots",
        "route_snapshot_records",
        "mqtt_status_snapshots",
        "command_envelopes",
    ]:
        count = con.execute(f"SELECT COUNT(*) AS c FROM {table}").fetchone()["c"]
        print(f"{table}: {count}")

    print("\nLatest map resources:")
    for row in con.execute(
        """
        SELECT d.vehicle_hash, e.remote_version, e.local_version, e.status, e.observed_at, e.blob_host, e.blob_path
        FROM map_resource_events e
        LEFT JOIN devices d ON d.vehicle_sn = e.vehicle_sn
        ORDER BY e.observed_at DESC, e.id DESC
        LIMIT 5
        """
    ):
        print(
            f"  device={row['vehicle_hash'] or '<unknown>'} remote={row['remote_version']} "
            f"local={row['local_version']} status={row['status']} observed={row['observed_at']}"
        )

    print("\nSchedule snapshots:")
    rows = con.execute(
        """
        SELECT s.id, d.vehicle_hash, s.observed_at, src.path
        FROM schedule_snapshots s
        JOIN sources src ON src.id = s.source_id
        LEFT JOIN devices d ON d.vehicle_sn = s.vehicle_sn
        ORDER BY s.id DESC LIMIT 5
        """
    ).fetchall()
    if rows:
        for row in rows:
            print(
                f"  snapshot={row['id']} device={row['vehicle_hash'] or '<unknown>'} "
                f"observed={row['observed_at']} source={row['path']}"
            )
            day_bits = []
            for day in con.execute("SELECT * FROM schedule_days WHERE snapshot_id=? ORDER BY day", (row["id"],)):
                periods = con.execute(
                    "SELECT * FROM schedule_periods WHERE day_id=? ORDER BY period_index",
                    (day["id"],),
                ).fetchall()
                pretty = ",".join(f"{p['start_tick']}-{p['end_tick']}" for p in periods) or "closed"
                day_bits.append(f"d{day['day']}:{pretty}")
            print(f"    {'; '.join(day_bits)}")
    else:
        print("  none")



def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    subparsers = parser.add_subparsers(dest="command", required=True)

    ingest_parser = subparsers.add_parser("ingest")
    ingest_parser.add_argument("paths", type=Path, nargs="+")
    ingest_parser.add_argument("--store-signed-urls", action="store_true")
    ingest_parser.add_argument("--download-maps", action="store_true")
    ingest_parser.add_argument("--map-dir", type=Path, default=DEFAULT_MAP_DIR)
    ingest_parser.add_argument("--insecure-downloads", action="store_true")

    download_parser = subparsers.add_parser("download-maps")
    download_parser.add_argument("--map-dir", type=Path, default=DEFAULT_MAP_DIR)
    download_parser.add_argument("--insecure-downloads", action="store_true")

    subparsers.add_parser("summary")

    args = parser.parse_args()
    con = connect(args.db)
    try:
        if args.command == "ingest":
            totals: dict[str, int] = {}
            for path in args.paths:
                counts = ingest_path(con, path, store_signed_urls=args.store_signed_urls or args.download_maps)
                for key, value in counts.items():
                    totals[key] = totals.get(key, 0) + value
            if args.download_maps:
                counts = download_map_artifacts(
                    con,
                    args.map_dir,
                    insecure_tls=args.insecure_downloads,
                )
                for key, value in counts.items():
                    totals[f"map_{key}"] = totals.get(f"map_{key}", 0) + value
            for key in sorted(totals):
                print(f"{key}: {totals[key]}")
        elif args.command == "download-maps":
            counts = download_map_artifacts(
                con,
                args.map_dir,
                insecure_tls=args.insecure_downloads,
            )
            for key in sorted(counts):
                print(f"{key}: {counts[key]}")
        elif args.command == "summary":
            print_summary(con)
    finally:
        con.close()


if __name__ == "__main__":
    main()
