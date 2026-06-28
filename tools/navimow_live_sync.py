#!/usr/bin/env python3
"""Read-only live sync runner for the local Navimow Terranox store."""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import email.utils
import hashlib
import json
import os
import re
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import navimow_state_store as store  # noqa: E402
import navimow_mqtt_client as mqtt_client  # noqa: E402
import navimow_live_status as live_status  # noqa: E402


DEFAULT_CONFIG = Path("config/navimow-live-sync.local.json")
DEFAULT_DB = Path("data/navimow.sqlite")
DEFAULT_BASE_URL = "https://navimow-fra.ninebot.com"
DEFAULT_OAUTH_TOKEN_FILE = Path("config/navimow-oauth.local.json")
OAUTH_CLIENT_ID = "homeassistant"
OAUTH_CLIENT_SECRET = "57056e15-722e-42be-bbaa-b0cbfb208a52"
OAUTH_REDIRECT_URI = "http://localhost:1/callback"
OAUTH_LOGIN_URL = (
    "https://navimow-h5-fra.willand.com/smartHome/login?"
    + urllib.parse.urlencode(
        {
            "channel": OAUTH_CLIENT_ID,
            "client_id": OAUTH_CLIENT_ID,
            "response_type": "code",
            "redirect_uri": OAUTH_REDIRECT_URI,
        }
    )
)
ENV_REF_RE = re.compile(r"\$\{(?P<name>[A-Za-z_][A-Za-z0-9_]*)\}")
OPENAPI_DISCOVERY_ROUTES = ["openapi-auth-list", "openapi-mqtt-info"]
OPENAPI_STATUS_ROUTES = ["openapi-auth-list", "openapi-vehicle-status", "openapi-mqtt-info"]
MQTT_MESSAGE_ALIAS = "mqtt-message"
MQTT_SAMPLE_REPORT_PRIVACY = (
    "Sanitized MQTT status fields only; no topics, payload hashes, raw payloads, "
    "credentials, device IDs, exact GPS, or broker metadata."
)
MQTT_READINESS_PRIVACY = (
    "Redacted MQTT readiness only; no topics, payload hashes, raw payloads, "
    "credentials, device IDs, exact GPS, or broker metadata."
)
MQTT_UI_REPORT_PRIVACY = (
    "Redacted MQTT-to-UI readiness only; no topics, payload hashes, raw payloads, "
    "credentials, device IDs, exact GPS, broker metadata, or area geometry."
)
MQTT_SAMPLE_COVERAGE_FIELDS = {
    "batterySoc": "battery_soc",
    "capacityLabel": "capacity_label",
    "currentPartitionId": "current_partition_id",
    "mowingPercentage": "mowing_percentage",
    "pathId": "path_id",
    "reportTime": "report_time",
}
MQTT_READINESS_REQUIRED_FIELDS = ("batterySoc", "currentPartitionId", "mowingPercentage")
MQTT_READINESS_MAX_AGE_SECONDS = 15 * 60
MQTT_READINESS_STILL_POLLING = (
    "map geometry/layout",
    "schedule structure",
    "local mower pose until real MQTT pose fields are mapped",
    "consumer settings/routes not present in MQTT",
)
MQTT_SAMPLE_ENUM_FIELDS = {
    "state": "state",
    "taskStatus": "task_status",
    "workStatus": "work_status",
    "eventType": "event_type",
}
MAP_SYNC_PLAN_PRIVACY = (
    "Redacted map metadata only; no serials, map names, area geometry, signed URLs, "
    "blob paths, raw payloads, or GPS anchors."
)
MAP_DELTA_ROUTES = ("index2", "map-list", "map-detail", "get-iot-file")
MAP_DELTA_CONSUMER_SESSION_STEPS = (
    "make live-auth-discover",
    "make live-android-doctor",
    "make live-android-capture",
    (
        "python tools/navimow_android_live_setup.py run --duration 60 --include-values "
        "--i-understand-local-secrets --write-config config/navimow-live-sync.local.json"
    ),
)
MAP_DETAIL_ACTION_IDS = {"sync-map-detail", "refresh-map-detail"}
MAP_ARTIFACT_ACTION_IDS = {
    "sync-map-artifact-metadata",
    "download-map-artifact",
    "redownload-map-artifact",
    "refresh-map-artifact",
}
COMPLETION_REFRESH_ROUTES = ("trail-time", "trail-data")
ACTIVITY_STATE_MAX_AGE_SECONDS = 15 * 60
LIVE_HEALTH_STALE_MULTIPLIER = 4.0
LIVE_STATUS_MAX_AGE_SECONDS = 5 * 60
LIVE_HEALTH_FUTURE_SKEW_SECONDS = 60
SETUP_REMAINING_GAPS = [
    {
        "id": "real-mqtt-samples",
        "status": "needed",
        "detail": "Capture real MQTT status samples to map enum semantics and decide which polling routes MQTT can safely replace.",
    },
    {
        "id": "consumer-session-headers",
        "status": "needed",
        "detail": "Refresh consumer-app session headers locally before unattended consumer route polling can run without Android capture.",
    },
    {
        "id": "schedule-write-envelope",
        "status": "blocked",
        "detail": "Schedule/settings writes remain dry-run until the consumer app command envelope, signing, response polling, and rollback behavior are trusted.",
    },
    {
        "id": "trail-replay-decode",
        "status": "needed",
        "detail": "Decode trail path payloads for exact per-area mow path replay beyond summary last-mow attribution.",
    },
    {
        "id": "map-delta-sync",
        "status": "partial",
        "detail": "Auto viewer refresh can rebuild on map/settings/capability changes; version-specific map artifact downloads and fewer false-positive rebuilds still need live evidence.",
    },
]
TRAIL_REPLAY_PRIVACY = (
    "Redacted trail readiness only; no compressed trail blobs, payload hashes, "
    "area names, local point geometry, exact GPS, device IDs, signed URLs, or raw payloads."
)
ROUTE_COVERAGE_PRIVACY = (
    "Route coverage metadata only; no tokens, device ids, MQTT credentials/topics, "
    "signed URLs, exact GPS, area geometry, or raw payloads."
)
CONSUMER_SESSION_REPORT_PRIVACY = (
    "Redacted consumer-session readiness only; no header values, tokens, cookies, "
    "device ids, signed URLs, exact GPS, area geometry, or raw payloads."
)
CONSUMER_SESSION_CORE_ROUTES = (
    "index2",
    "device-info",
    "get-location",
    "set-list",
    "trail-time",
    "trail-data",
    "auth-list",
    "mower-state",
    "weather",
    "today-plan",
    "firmware",
    "maintenance",
    "map-list",
    "map-detail",
    "get-iot-file",
)
VIEWER_INSIGHT_ALIASES = {
    "mower-state": "consumerLiveState",
    "weather": "weather",
    "today-plan": "todayPlan",
    "firmware": "firmwareUpdate",
    "maintenance": "maintenance",
    "openapi-auth-list": "openapiAuth",
    "openapi-vehicle-status": "openapiStatus",
    "openapi-mqtt-info": "mqtt",
}
PROMOTION_CANDIDATE_ALIASES = {
    "mower-state",
    "weather",
    "today-plan",
    "firmware",
    "maintenance",
    "auth-list",
    "openapi-auth-list",
    "openapi-vehicle-status",
}
SAFE_LIVE_STATUS_INSIGHT_KEYS = {
    "consumerLiveState",
    "firmwareUpdate",
    "maintenance",
    "mqtt",
    "mqttMessages",
    "mqttStatus",
    "openapiAuth",
    "openapiStatus",
    "todayPlan",
    "weather",
}
DIAGNOSTIC_HEALTH_ALIASES = ("mqtt-message",)
AUTO_VIEWER_REBUILD_COUNT_KEYS = {
    "map_areas",
    "map_detail_areas",
    "map_detail_snapshots",
    "map_downloaded",
    "schedule_days",
    "schedule_periods",
    "schedule_snapshots",
}
AUTO_VIEWER_LIVE_STATUS_COUNT_KEYS = {
    "area_setting_snapshots",
    "device_info_snapshots",
    "device_state_snapshots",
    "live_location_snapshots",
    "mqtt_status_snapshots",
    "route_snapshot_records",
    "trail_time_entries",
    "trail_time_snapshots",
}

READ_ROUTES: dict[str, dict[str, Any]] = {
    "index2": {
        "path": "/vehicle/vehicle/index2",
        "kind": "device_state",
        "cadenceSeconds": 45,
        "activeCadenceSeconds": 30,
        "idleCadenceSeconds": 90,
        "description": "battery, state, network, map version",
    },
    "device-info": {
        "path": "/vehicle/vehicle/get-device-info",
        "kind": "device_info",
        "cadenceSeconds": 3600,
        "description": "capabilities, firmware, cutting-height list",
    },
    "get-location": {
        "path": "/vehicle/vehicle/get-location",
        "kind": "live_location",
        "cadenceSeconds": 10,
        "activeCadenceSeconds": 5,
        "idleCadenceSeconds": 60,
        "description": "sanitized local mower pose/progress",
    },
    "set-list": {
        "path": "/vehicle/vehicle/set-list",
        "kind": "settings",
        "cadenceSeconds": 300,
        "description": "settings snapshot, including current cutting height",
    },
    "trail-time": {
        "path": "/vehicle/trail/get-path-info-time",
        "kind": "trail_time",
        "cadenceSeconds": 900,
        "description": "per-area last mow time and completion index",
    },
    "trail-data": {
        "path": "/vehicle/trail/get-path-info-data-compress",
        "kind": "route_snapshot",
        "cadenceSeconds": 900,
        "description": "compressed trail/path data snapshot for future local path replay",
    },
    "auth-list": {
        "path": "/vehicle/vehicle/auth-list",
        "kind": "route_snapshot",
        "cadenceSeconds": 900,
        "description": "authorized mower cards and top-level mower selection state",
    },
    "mower-state": {
        "path": "/mowerbot/vehicle/vehicle/state",
        "kind": "route_snapshot",
        "cadenceSeconds": 15,
        "activeCadenceSeconds": 10,
        "idleCadenceSeconds": 60,
        "description": "richer live mower state/status semantics",
    },
    "weather": {
        "path": "/vehicle/vehicle/get-vehicle-weather",
        "kind": "route_snapshot",
        "cadenceSeconds": 1200,
        "description": "weather risk flags used by mowing decisions",
    },
    "today-plan": {
        "path": "/vehicle/vehicle/get-today-plan",
        "kind": "route_snapshot",
        "cadenceSeconds": 120,
        "activeCadenceSeconds": 60,
        "idleCadenceSeconds": 300,
        "description": "current-day task/plan status",
    },
    "firmware": {
        "path": "/vehicle/firmware/get-new-firmware",
        "kind": "route_snapshot",
        "cadenceSeconds": 86400,
        "description": "firmware update availability",
    },
    "maintenance": {
        "path": "/vehicle/vehicle/get-component-maintenance",
        "kind": "route_snapshot",
        "cadenceSeconds": 86400,
        "description": "component maintenance counters",
    },
    "map-list": {
        "path": "/map/index/map-list",
        "kind": "route_snapshot",
        "cadenceSeconds": 3600,
        "description": "map list for direct area/map refresh",
    },
    "map-detail": {
        "path": "/map/index/map-detail-compress",
        "kind": "map_detail",
        "cadenceSeconds": 3600,
        "description": "compressed map detail for area geometry refresh",
    },
    "get-iot-file": {
        "path": "/mowerbot/vehicle/common/get-iot-file",
        "kind": "route_snapshot",
        "cadenceSeconds": 3600,
        "description": "map artifact metadata; signed URLs stay transient",
    },
    "openapi-auth-list": {
        "path": "/openapi/smarthome/authList",
        "method": "GET",
        "kind": "route_snapshot",
        "cadenceSeconds": 900,
        "description": "OpenAPI authorized mower list",
    },
    "openapi-vehicle-status": {
        "path": "/openapi/smarthome/getVehicleStatus",
        "method": "POST",
        "kind": "route_snapshot",
        "cadenceSeconds": 45,
        "activeCadenceSeconds": 30,
        "idleCadenceSeconds": 120,
        "description": "OpenAPI mower status cards",
    },
    "openapi-mqtt-info": {
        "path": "/openapi/mqtt/userInfo/get/v2",
        "method": "GET",
        "kind": "route_snapshot",
        "cadenceSeconds": 3600,
        "description": "OpenAPI MQTT connection metadata; credentials stay local",
    },
    "openapi-response-commands": {
        "path": "/openapi/smarthome/responseCommands",
        "method": "POST",
        "kind": "route_snapshot",
        "cadenceSeconds": 5,
        "description": "OpenAPI command-result polling; read-after-command only",
    },
}
HEALTH_ROUTE_ALIASES = tuple(READ_ROUTES.keys()) + DIAGNOSTIC_HEALTH_ALIASES
TYPED_ROUTE_TABLES = {
    "device_state": ("device_state_snapshots", "1"),
    "device_info": ("device_info_snapshots", "1"),
    "live_location": ("live_location_snapshots", "1"),
    "settings": ("area_setting_snapshots", "1"),
    "trail_time": ("trail_time_snapshots", "entry_count"),
    "map_detail": ("map_detail_snapshots", "1"),
}
TYPED_ROUTE_ALIAS_TABLES = {
    "openapi-auth-list": ("openapi_auth_snapshots", "device_count"),
    "openapi-vehicle-status": ("openapi_status_snapshots", "device_count"),
}

WRITE_ROUTE_PARTS = (
    "/set/send",
    "/set/save-set-data",
    "/vehicle/set/response",
    "/vehicle/set/status",
    "/vehicle/set/index",
    "/openapi/smarthome/sendCommands",
)

DOCUMENTED_WRITE_ROUTES = (
    "/mowerbot/vehicle/set/send",
    "/vehicle/set/send",
    "/vehicle/set/response",
    "/vehicle/set/save-set-data",
    "/vehicle/set/index",
    "/vehicle/set/status",
    "/openapi/smarthome/sendCommands",
)

SENSITIVE_HEADER_PARTS = (
    "authorization",
    "cookie",
    "token",
    "secret",
    "password",
    "auth",
)

CONFIG_TEMPLATE: dict[str, Any] = {
    "baseUrl": DEFAULT_BASE_URL,
    "vehicleSn": "",
    "headers": {
        "User-Agent": "NavimowLocalSync/0.1",
        "Content-Type": "application/json",
        "Authorization": "${NAVIMOW_AUTHORIZATION}",
    },
    "routes": ["index2", "device-info", "get-location", "set-list", "trail-time"],
    "requestBodies": {
        "index2": {},
        "device-info": {},
        "get-location": {},
        "set-list": {},
        "trail-time": {},
        "trail-data": {},
        "auth-list": {},
        "mower-state": {},
        "weather": {},
        "today-plan": {},
        "firmware": {},
        "maintenance": {},
        "map-list": {},
        "map-detail": {},
        "get-iot-file": {},
        "openapi-auth-list": {},
        "openapi-vehicle-status": {"devices": []},
        "openapi-mqtt-info": {},
        "openapi-response-commands": {},
    },
    "auth": {
        "provider": "manual",
        "tokenFile": str(DEFAULT_OAUTH_TOKEN_FILE),
    },
}


def now_iso() -> str:
    return dt.datetime.now(dt.UTC).isoformat()


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def parse_iso_datetime(value: Any) -> dt.datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.UTC)
    return parsed


def parse_observed_datetime(value: Any) -> dt.datetime | None:
    parsed = parse_iso_datetime(value)
    if parsed is not None:
        return parsed
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.UTC)
    return parsed


def iso_timestamp_seconds(value: Any) -> int:
    parsed = parse_observed_datetime(value)
    if parsed is None:
        return 0
    return int(parsed.timestamp())


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def seconds_since(value: Any, now: dt.datetime) -> int | None:
    parsed = parse_iso_datetime(value)
    if parsed is None:
        return None
    return int((now - parsed).total_seconds())


def safe_json_loads(value: Any, default: Any) -> Any:
    if not isinstance(value, str):
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


def classify_activity_text(value: Any) -> str | None:
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

    active_exact = {
        "active",
        "isrunning",
        "running",
        "mowing",
        "mow",
        "working",
        "work",
        "returning",
        "return",
        "returnhome",
        "paused",
        "pause",
        "moving",
        "tasking",
    }
    idle_exact = {
        "notrunning",
        "idle",
        "docked",
        "dock",
        "charging",
        "charge",
        "standby",
        "parked",
        "park",
        "stopped",
        "stop",
        "offline",
        "ready",
        "complete",
        "completed",
        "sleep",
    }
    if normalized in idle_exact:
        return "idle"
    if normalized in active_exact:
        return "active"
    if "not" in tokens and ("running" in tokens or "mowing" in tokens or "working" in tokens):
        return "idle"
    if tokens & {"idle", "docked", "dock", "charging", "charge", "standby", "parked", "park", "stopped", "stop", "offline", "ready", "complete", "completed", "sleep"}:
        return "idle"
    if tokens & {"running", "mowing", "mow", "working", "work", "returning", "return", "paused", "pause", "moving", "active", "tasking"}:
        return "active"
    if normalized.startswith("return_") or normalized.endswith("_returning"):
        return "active"
    return None


def live_activity_state(
    db: Path,
    *,
    max_age_seconds: int = ACTIVITY_STATE_MAX_AGE_SECONDS,
    now_epoch: int | None = None,
) -> dict[str, Any]:
    candidates: list[dict[str, Any]] = []
    now_epoch = int(time.time()) if now_epoch is None else now_epoch
    now_dt = dt.datetime.fromtimestamp(now_epoch, dt.UTC)
    mqtt_ready = mqtt_readiness_report(db, now=now_dt).get("ready")

    con = store.connect(db)
    if mqtt_ready and sqlite_table_exists(con, "mqtt_status_snapshots"):
        row = con.execute(
            """
            SELECT observed_at, report_time, state, task_status, work_status, event_type
            FROM mqtt_status_snapshots
            ORDER BY COALESCE(report_time, CAST(strftime('%s', observed_at) AS INTEGER), 0) DESC, id DESC
            LIMIT 1
            """
        ).fetchone()
        if row is not None:
            timestamp = mqtt_epoch_seconds(row["report_time"]) or iso_timestamp_seconds(row["observed_at"])
            for key in ("state", "task_status", "work_status", "event_type"):
                if row[key] not in (None, ""):
                    candidates.append(
                        {
                            "timestamp": timestamp,
                            "source": "mqtt-message",
                            "field": key,
                            "value": row[key],
                            "observedAt": row["observed_at"],
                        }
                    )

    if sqlite_table_exists(con, "openapi_status_snapshots"):
        row = con.execute(
            """
            SELECT observed_at, vehicle_state
            FROM openapi_status_snapshots
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()
        if row is not None and row["vehicle_state"] not in (None, ""):
            candidates.append(
                {
                    "timestamp": iso_timestamp_seconds(row["observed_at"]),
                    "source": "openapi-vehicle-status",
                    "field": "vehicle_state",
                    "value": row["vehicle_state"],
                    "observedAt": row["observed_at"],
                }
            )

    if sqlite_table_exists(con, "route_snapshot_records"):
        for alias, keys in (
            ("openapi-vehicle-status", ("vehicleState", "state", "status", "taskStatus", "workStatus")),
            ("mower-state", ("vehicleState", "state", "status", "taskStatus", "workStatus")),
            ("today-plan", ("status", "taskStatus", "planStatus", "workStatus", "state")),
        ):
            row = con.execute(
                """
                SELECT observed_at, sanitized_json
                FROM route_snapshot_records
                WHERE route_alias=?
                ORDER BY id DESC
                LIMIT 1
                """,
                (alias,),
            ).fetchone()
            if row is None:
                continue
            data = safe_json_loads(row["sanitized_json"], {})
            value = find_first_value(data, keys)
            if value not in (None, ""):
                candidates.append(
                    {
                        "timestamp": iso_timestamp_seconds(row["observed_at"]),
                        "source": alias,
                        "field": "status",
                        "value": value,
                        "observedAt": row["observed_at"],
                    }
                )

    candidates.sort(key=lambda item: item["timestamp"], reverse=True)
    for candidate in candidates:
        timestamp = int(candidate.get("timestamp") or 0)
        if max_age_seconds > 0 and (
            not timestamp
            or now_epoch - timestamp > max_age_seconds
            or timestamp - now_epoch > LIVE_HEALTH_FUTURE_SKEW_SECONDS
        ):
            continue
        state = classify_activity_text(candidate.get("value"))
        if state:
            return {
                "state": state,
                "source": candidate.get("source"),
                "field": candidate.get("field"),
                "observedAt": candidate.get("observedAt"),
            }
    return {"state": "unknown", "source": None, "field": None, "observedAt": None}


def route_cadence_seconds(alias: str, activity: dict[str, Any] | None, *, activity_aware: bool) -> float:
    route = READ_ROUTES[alias]
    if activity_aware:
        state = (activity or {}).get("state")
        if state == "active" and route.get("activeCadenceSeconds") is not None:
            return float(route["activeCadenceSeconds"])
        if state == "idle" and route.get("idleCadenceSeconds") is not None:
            return float(route["idleCadenceSeconds"])
    return float(route["cadenceSeconds"])


def append_unique_routes(base: list[str], additions: list[str]) -> list[str]:
    seen = set(base)
    result = list(base)
    for alias in additions:
        if alias not in seen:
            result.append(alias)
            seen.add(alias)
    return result


def completion_refresh_routes(
    routes: list[str],
    due_routes: list[str],
    previous_activity_state: str | None,
    current_activity_state: str | None,
) -> list[str]:
    if previous_activity_state != "active" or current_activity_state != "idle":
        return []
    due = set(due_routes)
    return [alias for alias in COMPLETION_REFRESH_ROUTES if alias in routes and alias not in due]


def safe_header_name(name: str) -> str:
    normalized = name.strip()
    if not normalized:
        return ""
    lowered = normalized.lower()
    if any(part in lowered for part in SENSITIVE_HEADER_PARTS):
        return f"{normalized} <sensitive-name>"
    return normalized


def endpoint_path(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    return parsed.path or url


def scan_capture_auth_hints(path: Path) -> dict[str, Any]:
    header_counts: dict[str, int] = {}
    endpoint_counts: dict[str, int] = {}
    sensitive_name_hits: dict[str, int] = {}
    request_auth_value_candidates = 0
    meta_files = 0
    log_files = 0

    for meta_path in path.glob("**/*.0"):
        if not meta_path.is_file():
            continue
        lines = meta_path.read_text(errors="replace").splitlines()
        if lines:
            endpoint = endpoint_path(lines[0])
            endpoint_counts[endpoint] = endpoint_counts.get(endpoint, 0) + 1
        for line in lines[2:]:
            if ":" not in line:
                continue
            name = line.split(":", 1)[0].strip()
            if not name or name.lower().startswith("http/"):
                continue
            safe_name = safe_header_name(name)
            header_counts[safe_name] = header_counts.get(safe_name, 0) + 1
            if "<sensitive-name>" in safe_name:
                sensitive_name_hits[safe_name] = sensitive_name_hits.get(safe_name, 0) + 1
                request_auth_value_candidates += 1
        meta_files += 1

    for log_path in list(path.glob("**/*.log")) + list(path.glob("**/*.txt")):
        if not log_path.is_file():
            continue
        text = log_path.read_text(errors="replace")
        log_files += 1
        for marker in ("Authorization", "authorization", "Cookie", "cookie", "token", "Token"):
            if marker in text:
                safe_name = safe_header_name(marker)
                sensitive_name_hits[safe_name] = sensitive_name_hits.get(safe_name, 0) + 1

    return {
        "path": str(path),
        "metaFiles": meta_files,
        "logFiles": log_files,
        "headerNames": dict(sorted(header_counts.items())),
        "endpointPaths": dict(sorted(endpoint_counts.items())),
        "sensitiveNameHits": dict(sorted(sensitive_name_hits.items())),
        "requestAuthValueCandidates": request_auth_value_candidates,
        "finding": (
            "request-auth-values-present"
            if request_auth_value_candidates
            else "no-request-auth-values-found-in-cache-metadata"
        ),
    }


def print_auth_discovery(summary: dict[str, Any]) -> None:
    print(f"path: {summary['path']}")
    print(f"metadata files: {summary['metaFiles']}")
    print(f"log files: {summary['logFiles']}")
    print(f"finding: {summary['finding']}")
    print("endpoint paths:")
    for path, count in list(summary["endpointPaths"].items())[:30]:
        print(f"  {path}: {count}")
    print("header names:")
    for name, count in summary["headerNames"].items():
        print(f"  {name}: {count}")
    if summary["sensitiveNameHits"]:
        print("sensitive-name hints:")
        for name, count in summary["sensitiveNameHits"].items():
            print(f"  {name}: {count}")
    else:
        print("sensitive-name hints: none")


def collect_env_refs(value: Any) -> set[str]:
    refs: set[str] = set()
    if isinstance(value, str):
        refs.update(match.group("name") for match in ENV_REF_RE.finditer(value))
    elif isinstance(value, dict):
        for item in value.values():
            refs.update(collect_env_refs(item))
    elif isinstance(value, list):
        for item in value:
            refs.update(collect_env_refs(item))
    return refs


def resolve_env_refs(value: Any, *, strict: bool) -> Any:
    if isinstance(value, str):
        def replace(match: re.Match[str]) -> str:
            name = match.group("name")
            resolved = os.environ.get(name)
            if resolved is None:
                if strict:
                    raise SystemExit(f"Missing environment variable {name}")
                return match.group(0)
            return resolved

        return ENV_REF_RE.sub(replace, value)
    if isinstance(value, dict):
        return {key: resolve_env_refs(item, strict=strict) for key, item in value.items()}
    if isinstance(value, list):
        return [resolve_env_refs(item, strict=strict) for item in value]
    return value


def load_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        return copy.deepcopy(CONFIG_TEMPLATE)
    config = load_json(path)
    if not isinstance(config, dict):
        raise SystemExit(f"{path} must contain a JSON object")
    merged = copy.deepcopy(CONFIG_TEMPLATE)
    merged.update(config)
    merged["headers"] = {**CONFIG_TEMPLATE["headers"], **(config.get("headers") or {})}
    merged["requestBodies"] = {**CONFIG_TEMPLATE["requestBodies"], **(config.get("requestBodies") or {})}
    merged["auth"] = {**CONFIG_TEMPLATE["auth"], **(config.get("auth") or {})}
    if str((merged.get("auth") or {}).get("provider") or "manual") != "manual":
        if merged["headers"].get("Authorization") == CONFIG_TEMPLATE["headers"]["Authorization"]:
            merged["headers"].pop("Authorization", None)
    return merged


def openapi_config_from(base: dict[str, Any]) -> dict[str, Any]:
    config = copy.deepcopy(base)
    config["vehicleSn"] = ""
    config["routes"] = list(OPENAPI_DISCOVERY_ROUTES)
    headers = dict(config.get("headers") or {})
    headers.pop("Authorization", None)
    config["headers"] = headers
    request_bodies = dict(config.get("requestBodies") or {})
    request_bodies.setdefault("openapi-auth-list", {})
    request_bodies["openapi-vehicle-status"] = {"devices": []}
    request_bodies.setdefault("openapi-mqtt-info", {})
    config["requestBodies"] = request_bodies
    auth = dict(config.get("auth") or {})
    auth["provider"] = "navimow-oauth"
    auth.setdefault("tokenFile", str(DEFAULT_OAUTH_TOKEN_FILE))
    config["auth"] = auth
    return config


def auth_config(config: dict[str, Any]) -> dict[str, Any]:
    auth = config.get("auth") or {}
    return auth if isinstance(auth, dict) else {}


def auth_provider(config: dict[str, Any]) -> str:
    return str(auth_config(config).get("provider") or "manual")


def oauth_token_file(config: dict[str, Any], override: Path | None = None) -> Path:
    if override is not None:
        return override
    return Path(str(auth_config(config).get("tokenFile") or DEFAULT_OAUTH_TOKEN_FILE))


def extract_auth_code(value: str) -> str:
    candidate = value.strip()
    if candidate.startswith("http://") or candidate.startswith("https://"):
        parsed = urllib.parse.urlparse(candidate)
        code = urllib.parse.parse_qs(parsed.query).get("code", [""])[0]
        if code:
            return code
    return candidate


def token_with_metadata(token: dict[str, Any], *, observed_at: str | None = None) -> dict[str, Any]:
    result = dict(token)
    result["obtained_at"] = observed_at or now_iso()
    expires_in = result.get("expires_in")
    try:
        expires_seconds = int(expires_in)
    except (TypeError, ValueError):
        expires_seconds = None
    if expires_seconds is not None and expires_seconds > 0:
        base = parse_iso_datetime(result["obtained_at"]) or dt.datetime.now(dt.UTC)
        result["expires_at"] = (base + dt.timedelta(seconds=expires_seconds)).isoformat()
    return result


def load_oauth_token(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    token = load_json(path)
    if not isinstance(token, dict):
        raise SystemExit(f"{path} must contain a JSON object")
    return token


def write_oauth_token(path: Path, token: dict[str, Any]) -> None:
    write_json(path, token)


def token_expiry(token: dict[str, Any]) -> dt.datetime | None:
    expires_at = parse_iso_datetime(token.get("expires_at"))
    if expires_at is not None:
        return expires_at
    obtained_at = parse_iso_datetime(token.get("obtained_at"))
    if obtained_at is None:
        return None
    try:
        expires_seconds = int(token.get("expires_in"))
    except (TypeError, ValueError):
        return None
    return obtained_at + dt.timedelta(seconds=expires_seconds)


def token_expired_at(token: dict[str, Any], now: dt.datetime) -> bool:
    expiry = token_expiry(token)
    return bool(expiry is not None and expiry <= now)


def token_needs_refresh_at(token: dict[str, Any], now: dt.datetime, *, window_seconds: int = 300) -> bool:
    expiry = token_expiry(token)
    if expiry is None:
        return False
    return expiry <= now + dt.timedelta(seconds=window_seconds)


def token_needs_refresh(token: dict[str, Any], *, window_seconds: int = 300) -> bool:
    return token_needs_refresh_at(token, utc_now(), window_seconds=window_seconds)


def openapi_preflight_checks(config_path: Path, token_file: Path | None = None) -> tuple[list[str], list[str], dict[str, Any]]:
    next_steps: list[str] = []
    errors: list[str] = []
    config_exists = config_path.exists()
    config = load_config(config_path)
    provider = auth_provider(config)
    resolved_token_file = oauth_token_file(config, token_file)
    routes = requested_routes(config, None)
    status_body = (config.get("requestBodies") or {}).get("openapi-vehicle-status")
    devices = status_body.get("devices") if isinstance(status_body, dict) else []
    token = load_oauth_token(resolved_token_file)

    if not config_exists:
        errors.append(f"OpenAPI config is missing: {config_path}")
        next_steps.append(f"python3 tools/navimow_live_sync.py init-openapi-config --output {config_path}")
        next_steps.append("python3 tools/navimow_live_sync.py oauth-login-url")
        next_steps.append(f"python3 tools/navimow_live_sync.py oauth-exchange-code --config {config_path} --code '<LOCALHOST_REDIRECT_URL>'")
    if provider != "navimow-oauth":
        errors.append(f"auth provider is {provider}; expected navimow-oauth")
        next_steps.append(f"python3 tools/navimow_live_sync.py init-openapi-config --output {config_path} --force")
    missing_routes = [alias for alias in OPENAPI_DISCOVERY_ROUTES if alias not in routes]
    if missing_routes:
        errors.append(f"OpenAPI discovery routes missing from config: {', '.join(missing_routes)}")
        next_steps.append(f"python3 tools/navimow_live_sync.py init-openapi-config --output {config_path} --force")
    if not resolved_token_file.exists():
        errors.append(f"OAuth token file is missing: {resolved_token_file}")
        next_steps.append("python3 tools/navimow_live_sync.py oauth-login-url")
        next_steps.append(f"python3 tools/navimow_live_sync.py oauth-exchange-code --config {config_path} --code '<LOCALHOST_REDIRECT_URL>'")
    elif not token.get("access_token"):
        errors.append("OAuth access token is missing")
        next_steps.append(f"python3 tools/navimow_live_sync.py oauth-exchange-code --config {config_path} --code '<LOCALHOST_REDIRECT_URL>'")
    elif token_needs_refresh(token):
        next_steps.append(f"python3 tools/navimow_live_sync.py oauth-refresh --config {config_path}")
    if "openapi-vehicle-status" in routes and not has_openapi_status_devices(status_body):
        errors.append("OpenAPI vehicle-status request body has no configured devices")
        next_steps.append(
            f"python3 tools/navimow_live_sync.py sync-once --config {config_path} --routes openapi-auth-list,openapi-mqtt-info"
        )
        next_steps.append(f"python3 tools/navimow_live_sync.py configure-openapi-status --config {config_path}")

    unique_steps = list(dict.fromkeys(next_steps))
    summary = {
        "configExists": config_exists,
        "provider": provider,
        "tokenFile": str(resolved_token_file),
        "tokenExists": resolved_token_file.exists(),
        "accessTokenPresent": bool(token.get("access_token")),
        "refreshTokenPresent": bool(token.get("refresh_token")),
        "refreshDue": bool(token.get("access_token") and token_needs_refresh(token)),
        "routes": routes,
        "openapiStatusDevices": len(devices) if isinstance(devices, list) else 0,
    }
    return errors, unique_steps, summary


def oauth_token_url(config: dict[str, Any]) -> str:
    base_url = str(config.get("baseUrl") or DEFAULT_BASE_URL).rstrip("/")
    return urllib.parse.urljoin(base_url + "/", "openapi/oauth/getAccessToken")


def build_ssl_context() -> ssl.SSLContext:
    try:
        import certifi  # type: ignore

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def oauth_form_request(config: dict[str, Any], form: dict[str, str], *, timeout: float) -> dict[str, Any]:
    request = urllib.request.Request(
        oauth_token_url(config),
        data=urllib.parse.urlencode(form).encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    context = build_ssl_context()
    with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
        payload = json.loads(response.read().decode())
    if not isinstance(payload, dict):
        raise SystemExit("OAuth token response was not a JSON object")
    return payload


def exchange_oauth_code(config: dict[str, Any], code_or_url: str, *, timeout: float) -> dict[str, Any]:
    code = extract_auth_code(code_or_url)
    if not code:
        raise SystemExit("OAuth code is empty")
    token = oauth_form_request(
        config,
        {
            "grant_type": "authorization_code",
            "code": code,
            "client_id": OAUTH_CLIENT_ID,
            "client_secret": OAUTH_CLIENT_SECRET,
            "redirect_uri": OAUTH_REDIRECT_URI,
        },
        timeout=timeout,
    )
    if not token.get("access_token"):
        raise SystemExit("OAuth token exchange returned no access token")
    return token_with_metadata(token)


def refresh_oauth_token(config: dict[str, Any], refresh_token: str, *, timeout: float) -> dict[str, Any]:
    if not refresh_token:
        raise SystemExit("OAuth refresh token is empty")
    token = oauth_form_request(
        config,
        {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": OAUTH_CLIENT_ID,
            "client_secret": OAUTH_CLIENT_SECRET,
        },
        timeout=timeout,
    )
    if not token.get("access_token"):
        raise SystemExit("OAuth refresh returned no access token")
    if not token.get("refresh_token"):
        token["refresh_token"] = refresh_token
    return token_with_metadata(token)


def prepare_config_for_network(config: dict[str, Any], *, timeout: float) -> dict[str, Any]:
    prepared = copy.deepcopy(config)
    provider = auth_provider(prepared)
    if provider in {"navimow-oauth", "oauth"}:
        token_path = oauth_token_file(prepared)
        token = load_oauth_token(token_path)
        if not token.get("access_token"):
            raise SystemExit(f"OAuth token file has no access token: {token_path}")
        if token_needs_refresh(token) and token.get("refresh_token"):
            token = refresh_oauth_token(prepared, str(token["refresh_token"]), timeout=timeout)
            write_oauth_token(token_path, token)
        prepared.setdefault("headers", {})
        prepared["headers"]["Authorization"] = "Bearer " + str(token["access_token"])
    prepared.setdefault("headers", {})
    if prepared["headers"].get("Authorization"):
        prepared["headers"].setdefault("requestId", str(uuid.uuid4()))
    return prepared


def requested_routes(config: dict[str, Any], route_arg: str | None) -> list[str]:
    routes = [item.strip() for item in (route_arg.split(",") if route_arg else config.get("routes", [])) if item.strip()]
    unknown = [route for route in routes if route not in READ_ROUTES]
    if unknown:
        raise SystemExit(f"Unknown read route alias: {', '.join(unknown)}")
    return routes


def validate_config(config: dict[str, Any], routes: list[str], *, require_env: bool) -> list[str]:
    errors: list[str] = []
    if not isinstance(config.get("headers"), dict):
        errors.append("config.headers must be an object")
    if not isinstance(config.get("requestBodies"), dict):
        errors.append("config.requestBodies must be an object")
        return errors
    request_bodies = config.get("requestBodies") or {}
    for alias in routes:
        body = request_bodies.get(alias, {})
        if not isinstance(body, dict):
            errors.append(f"requestBodies.{alias} must be an object")
    if require_env:
        for name in sorted(ref for ref in collect_env_refs(config) if os.environ.get(ref) is None):
            errors.append(f"missing environment variable {name}")
    return errors


def has_openapi_status_devices(body: Any) -> bool:
    if not isinstance(body, dict):
        return False
    devices = body.get("devices")
    if not isinstance(devices, list) or not devices:
        return False
    return all(isinstance(device, dict) and isinstance(device.get("id"), str) and device.get("id") for device in devices)


def config_warnings(config: dict[str, Any], routes: list[str]) -> list[str]:
    warnings: list[str] = []
    provider = auth_provider(config)
    if provider in {"navimow-oauth", "oauth"}:
        consumer_routes = [alias for alias in routes if not alias.startswith("openapi-")]
        if consumer_routes:
            warnings.append(
                "OAuth/OpenAPI auth is configured, but these selected routes use consumer-app endpoints: "
                + ", ".join(consumer_routes)
            )
    if "openapi-vehicle-status" in routes:
        body = (config.get("requestBodies") or {}).get("openapi-vehicle-status")
        if not has_openapi_status_devices(body):
            warnings.append(
                "openapi-vehicle-status has no configured devices; run configure-openapi-status after syncing openapi-auth-list"
            )
    return warnings


def map_delta_consumer_auth_blockers(config: dict[str, Any]) -> list[str]:
    provider = auth_provider(config)
    if provider not in {"navimow-oauth", "oauth"}:
        return []
    routes = ", ".join(MAP_DELTA_ROUTES)
    lines = [
        f"map-delta needs consumer-app session auth for consumer routes: {routes}",
        "auth.provider=navimow-oauth only injects an OpenAPI bearer token and cannot authenticate consumer map routes.",
        "Use --responses-dir for local fixtures, or capture a local ignored consumer-session config before live map sync.",
        "next local commands:",
    ]
    lines.extend(f"  {step}" for step in MAP_DELTA_CONSUMER_SESSION_STEPS)
    lines.append("Do not paste captured header values, tokens, device IDs, signed URLs, or raw payloads into chat.")
    return lines


def sqlite_table_exists(con: Any, table: str) -> bool:
    row = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    return row is not None


def sqlite_table_count(con: Any, table: str) -> int | None:
    if not sqlite_table_exists(con, table):
        return None
    return int(con.execute(f"SELECT COUNT(*) AS c FROM {table}").fetchone()["c"])


def latest_route_snapshot_summary(con: Any, alias: str) -> dict[str, Any]:
    if not sqlite_table_exists(con, "route_snapshot_records"):
        return {"present": False}
    row = con.execute(
        """
        SELECT observed_at, item_count
        FROM route_snapshot_records
        WHERE route_alias=?
        ORDER BY id DESC
        LIMIT 1
        """,
        (alias,),
    ).fetchone()
    if row is None:
        return {"present": False}
    return {
        "present": True,
        "observedAt": row["observed_at"],
        "itemCount": row["item_count"],
        "source": "route_snapshot_records",
    }


def latest_typed_route_summary(con: Any, alias: str) -> dict[str, Any]:
    route = READ_ROUTES.get(alias)
    if not route:
        return latest_route_snapshot_summary(con, alias)
    alias_table_info = TYPED_ROUTE_ALIAS_TABLES.get(alias)
    if alias_table_info is not None:
        table, item_expr = alias_table_info
        if not sqlite_table_exists(con, table):
            return latest_route_snapshot_summary(con, alias)
        row = con.execute(
            f"""
            SELECT observed_at, {item_expr} AS item_count
            FROM {table}
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()
        if row is None:
            return latest_route_snapshot_summary(con, alias)
        return {
            "present": True,
            "observedAt": row["observed_at"],
            "itemCount": row["item_count"],
            "source": table,
        }
    kind = str(route.get("kind") or "")
    if kind == "route_snapshot":
        return latest_route_snapshot_summary(con, alias)
    table_info = TYPED_ROUTE_TABLES.get(kind)
    if table_info is None:
        return {"present": False, "source": kind or "unknown"}
    table, item_expr = table_info
    if not sqlite_table_exists(con, table):
        return {"present": False, "source": table}
    row = con.execute(
        f"""
        SELECT observed_at, {item_expr} AS item_count
        FROM {table}
        ORDER BY id DESC
        LIMIT 1
        """
    ).fetchone()
    if row is None:
        return {"present": False, "source": table}
    return {
        "present": True,
        "observedAt": row["observed_at"],
        "itemCount": row["item_count"],
        "source": table,
    }


def parse_json_value(raw: str | None, fallback: Any = None) -> Any:
    if raw in (None, ""):
        return fallback
    try:
        return json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return fallback


def trail_time_readiness(con: Any) -> dict[str, Any]:
    if not sqlite_table_exists(con, "trail_time_snapshots") or not sqlite_table_exists(con, "trail_time_entries"):
        return {"present": False, "source": "trail_time_entries"}
    snapshot_count = sqlite_table_count(con, "trail_time_snapshots") or 0
    entry_count = sqlite_table_count(con, "trail_time_entries") or 0
    latest = con.execute(
        """
        SELECT s.observed_at AS observed_at, COUNT(e.id) AS item_count
        FROM trail_time_snapshots s
        LEFT JOIN trail_time_entries e ON e.snapshot_id = s.id
        GROUP BY s.id
        ORDER BY s.id DESC
        LIMIT 1
        """
    ).fetchone()
    partition_count = int(
        con.execute(
            "SELECT COUNT(DISTINCT partition_id) AS c FROM trail_time_entries WHERE partition_id IS NOT NULL"
        ).fetchone()["c"]
    )
    latest_end = con.execute(
        "SELECT MAX(end_time) AS end_time FROM trail_time_entries WHERE end_time IS NOT NULL"
    ).fetchone()["end_time"]
    complete_count = int(
        con.execute(
            """
            SELECT COUNT(*) AS c
            FROM trail_time_entries
            WHERE COALESCE(partition_percentage, 0) >= 98
               OR (area_m2 IS NOT NULL AND finished_area_m2 IS NOT NULL AND area_m2 > 0 AND finished_area_m2 >= area_m2 * 0.98)
            """
        ).fetchone()["c"]
    )
    partial_count = int(
        con.execute(
            """
            SELECT COUNT(*) AS c
            FROM trail_time_entries
            WHERE COALESCE(partition_percentage, 0) > 0
              AND COALESCE(partition_percentage, 0) < 98
            """
        ).fetchone()["c"]
    )
    return {
        "present": snapshot_count > 0 and entry_count > 0,
        "source": "trail_time_entries",
        "snapshotCount": snapshot_count,
        "entryCount": entry_count,
        "latestObservedAt": latest["observed_at"] if latest else None,
        "latestItemCount": int(latest["item_count"] or 0) if latest else 0,
        "partitionCount": partition_count,
        "latestEndAt": mqtt_epoch_to_iso(latest_end),
        "completedEntryCount": complete_count,
        "partialEntryCount": partial_count,
    }


def trail_data_readiness(con: Any) -> dict[str, Any]:
    if not sqlite_table_exists(con, "route_snapshot_records"):
        return {"present": False, "source": "route_snapshot_records"}
    row = con.execute(
        """
        SELECT observed_at, item_count, sanitized_json
        FROM route_snapshot_records
        WHERE route_alias='trail-data'
        ORDER BY id DESC
        LIMIT 1
        """
    ).fetchone()
    if row is None:
        return {"present": False, "source": "route_snapshot_records"}
    payload = parse_json_value(row["sanitized_json"], {})
    payload = payload if isinstance(payload, dict) else {}
    return {
        "present": True,
        "source": "route_snapshot_records",
        "observedAt": row["observed_at"],
        "itemCount": row["item_count"],
        "status": payload.get("status"),
        "payloadBytes": store.safe_int(payload.get("payloadBytes")),
        "hasPayloadHash": bool(payload.get("payloadSha256")),
        "decodeStatus": "not_decoded",
    }


def count_map_detail_snapshot_areas(con: Any) -> int:
    if not sqlite_table_exists(con, "map_detail_snapshots"):
        return 0
    rows = con.execute(
        """
        SELECT raw_json
        FROM map_detail_snapshots
        ORDER BY id DESC
        LIMIT 3
        """
    ).fetchall()
    for row in rows:
        payload = parse_json_value(row["raw_json"], {})
        if not isinstance(payload, dict):
            continue
        detail = payload.get("detail") if isinstance(payload.get("detail"), dict) else payload
        sub_maps = detail.get("sub_maps") if isinstance(detail, dict) else None
        if isinstance(sub_maps, list):
            count = sum(1 for item in sub_maps if isinstance(item, dict))
            if count:
                return count
    return 0


def trail_map_context_readiness(con: Any) -> dict[str, Any]:
    map_snapshots = sqlite_table_count(con, "map_detail_snapshots") if sqlite_table_exists(con, "map_detail_snapshots") else None
    map_areas = sqlite_table_count(con, "map_detail_areas") if sqlite_table_exists(con, "map_detail_areas") else None
    decoded_snapshot_areas = count_map_detail_snapshot_areas(con)
    render_rows = sqlite_table_count(con, "map_render_metadata") if sqlite_table_exists(con, "map_render_metadata") else None
    artifact_rows = sqlite_table_count(con, "map_artifacts") if sqlite_table_exists(con, "map_artifacts") else None
    area_count = map_areas or decoded_snapshot_areas or 0
    return {
        "mapDetailSnapshots": map_snapshots or 0,
        "areaCount": area_count,
        "promotedAreaRows": map_areas or 0,
        "decodedSnapshotAreaCount": decoded_snapshot_areas,
        "renderMetadataRows": render_rows or 0,
        "artifactCount": artifact_rows or 0,
        "hasGeometry": area_count > 0,
        "hasRenderCalibration": bool(render_rows and render_rows > 0),
    }


def trail_replay_report(*, db_path: Path) -> dict[str, Any]:
    report: dict[str, Any] = {
        "generatedAt": utc_now().isoformat(),
        "status": "blocked",
        "readyForDecoder": False,
        "privacy": TRAIL_REPLAY_PRIVACY,
        "db": {"path": str(db_path), "present": db_path.exists()},
        "trailTime": {"present": False},
        "trailData": {"present": False},
        "mapContext": {},
        "nextSteps": [],
        "missing": [],
    }
    if not db_path.exists():
        report["missing"].append("database")
        report["nextSteps"].append("make quickstart-live")
        return report
    con = store.connect(db_path)
    try:
        report["trailTime"] = trail_time_readiness(con)
        report["trailData"] = trail_data_readiness(con)
        report["mapContext"] = trail_map_context_readiness(con)
    finally:
        con.close()

    if not report["trailTime"].get("present"):
        report["missing"].append("trail-time")
        report["nextSteps"].append("python tools/navimow_live_sync.py sync-once --config config/navimow-live-sync.local.json --db data/navimow.sqlite --routes trail-time")
    if not report["trailData"].get("present"):
        report["missing"].append("trail-data")
        report["nextSteps"].append("python tools/navimow_live_sync.py sync-once --config config/navimow-live-sync.local.json --db data/navimow.sqlite --routes trail-data")
    map_context = report["mapContext"]
    if not map_context.get("hasGeometry") or not map_context.get("hasRenderCalibration"):
        report["missing"].append("map-context")
        report["nextSteps"].append("make live-map-plan")
    if report["trailTime"].get("present") and report["trailData"].get("present") and not report["missing"]:
        report["status"] = "ready_for_decoder"
        report["readyForDecoder"] = True
        report["nextSteps"].append("decode compressed trail fixture into local point/segment tables")
    else:
        report["status"] = "needs_capture"
    report["nextSteps"] = list(dict.fromkeys(report["nextSteps"]))
    return report


def print_trail_replay_report(report: dict[str, Any]) -> None:
    print("trail replay readiness:", report["status"])
    print(f"privacy: {report['privacy']}")
    print(f"database: {'present' if report['db'].get('present') else 'missing'}")
    trail_time = report.get("trailTime") or {}
    print(
        "trail-time: {state}; snapshots={snapshots}; entries={entries}; partitions={partitions}; latest={latest}".format(
            state="present" if trail_time.get("present") else "missing",
            snapshots=trail_time.get("snapshotCount", 0),
            entries=trail_time.get("entryCount", 0),
            partitions=trail_time.get("partitionCount", 0),
            latest=trail_time.get("latestObservedAt") or "n/a",
        )
    )
    trail_data = report.get("trailData") or {}
    print(
        "trail-data: {state}; status={status}; bytes={bytes}; payload-hash={hash_state}; observed={observed}".format(
            state="present" if trail_data.get("present") else "missing",
            status=trail_data.get("status") or "n/a",
            bytes=trail_data.get("payloadBytes") if trail_data.get("payloadBytes") is not None else "n/a",
            hash_state="present" if trail_data.get("hasPayloadHash") else "missing",
            observed=trail_data.get("observedAt") or "n/a",
        )
    )
    map_context = report.get("mapContext") or {}
    print(
        "map-context: geometry={geometry}; render-calibration={render}; areas={areas}; artifacts={artifacts}".format(
            geometry="present" if map_context.get("hasGeometry") else "missing",
            render="present" if map_context.get("hasRenderCalibration") else "missing",
            areas=map_context.get("areaCount", 0),
            artifacts=map_context.get("artifactCount", 0),
        )
    )
    missing = report.get("missing") or []
    print(f"missing: {', '.join(missing) if missing else 'none'}")
    print("next steps:")
    for step in report.get("nextSteps") or []:
        print(f"  - {step}")


def live_status_summary(viewer_output: Path) -> dict[str, Any]:
    data_path = viewer_output / "navimow-map-data.js"
    live_path = viewer_output / "navimow-live-status.json"
    summary: dict[str, Any] = {
        "viewerData": data_path.exists(),
        "liveStatus": live_path.exists(),
    }
    if not live_path.exists():
        return summary
    try:
        payload = json.loads(live_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        summary["liveStatusReadable"] = False
        return summary
    if not isinstance(payload, dict):
        summary["liveStatusReadable"] = False
        return summary
    insights = ((payload.get("mower") or {}).get("routeInsights") or {})
    summary.update(
        {
            "liveStatusReadable": True,
            "generatedAt": payload.get("generatedAt"),
            "layoutVersion": payload.get("layoutVersion"),
            "insights": sorted(str(key) for key in insights.keys() if str(key) in SAFE_LIVE_STATUS_INSIGHT_KEYS)
            if isinstance(insights, dict)
            else [],
        }
    )
    return summary


def route_health_summary(alias: str, latest: dict[str, Any], *, now: dt.datetime, stale_multiplier: float) -> dict[str, Any]:
    route = READ_ROUTES.get(alias) or {}
    cadence = float(route["cadenceSeconds"]) if route.get("cadenceSeconds") is not None else None
    threshold = max(1, int(cadence * stale_multiplier)) if cadence is not None else None
    age = seconds_since(latest.get("observedAt"), now) if latest.get("present") else None
    future_skew = bool(age is not None and age < -LIVE_HEALTH_FUTURE_SKEW_SECONDS)
    stale = bool(latest.get("present") and threshold is not None and (age is None or age > threshold))
    return {
        **latest,
        "cadenceSeconds": int(cadence) if cadence is not None else None,
        "staleThresholdSeconds": threshold,
        "ageSeconds": age,
        "futureSkew": future_skew,
        "futureSkewSeconds": abs(age) if future_skew and age is not None else None,
        "stale": stale,
    }


def viewer_health_summary(
    viewer_output: Path,
    *,
    now: dt.datetime,
    live_status_max_age_seconds: int,
) -> dict[str, Any]:
    summary = live_status_summary(viewer_output)
    age = seconds_since(summary.get("generatedAt"), now) if summary.get("liveStatusReadable") else None
    future_skew = bool(age is not None and age < -LIVE_HEALTH_FUTURE_SKEW_SECONDS)
    stale = bool(summary.get("liveStatusReadable") and (age is None or age > live_status_max_age_seconds))
    return {
        **summary,
        "path": str(viewer_output),
        "liveStatusAgeSeconds": age,
        "liveStatusMaxAgeSeconds": live_status_max_age_seconds,
        "liveStatusFutureSkew": future_skew,
        "liveStatusFutureSkewSeconds": abs(age) if future_skew and age is not None else None,
        "liveStatusStale": stale,
    }


def live_health_report(
    *,
    config_path: Path,
    db_path: Path,
    route_arg: str | None,
    token_file: Path | None,
    viewer_output: Path,
    strict: bool,
    stale_multiplier: float,
    live_status_max_age_seconds: int,
    now: dt.datetime,
) -> dict[str, Any]:
    config_exists = config_path.exists()
    config = load_config(config_path)
    routes = requested_routes(config, route_arg)
    errors = validate_config(config, routes, require_env=False)
    warnings = config_warnings(config, routes)
    provider = auth_provider(config)
    env_refs = sorted(collect_env_refs(config))
    env_status = {name: ("set" if os.environ.get(name) else "missing") for name in env_refs}
    report: dict[str, Any] = {
        "generatedAt": now.isoformat(),
        "strict": strict,
        "ready": False,
        "status": "needs_attention",
        "config": {
            "path": str(config_path),
            "present": config_exists,
            "authProvider": provider,
            "routes": routes,
        },
        "envRefs": env_status,
        "oauth": None,
        "openapi": {"statusDevices": 0},
        "db": {"path": str(db_path), "present": db_path.exists(), "tableCount": None, "tables": {}, "routes": {}},
        "viewer": {},
        "warnings": warnings,
        "errors": errors,
    }
    if strict and not config_exists:
        errors.append(f"config is missing: {config_path}")

    if provider in {"navimow-oauth", "oauth"}:
        resolved_token_file = oauth_token_file(config, token_file)
        token = load_oauth_token(resolved_token_file)
        expiry = token_expiry(token)
        token_present = bool(token.get("access_token"))
        refresh_due = bool(token_present and token_needs_refresh_at(token, now))
        expired = bool(token_present and token_expired_at(token, now))
        expiry_unknown = bool(token_present and expiry is None)
        report["oauth"] = {
            "tokenFile": str(resolved_token_file),
            "tokenFilePresent": resolved_token_file.exists(),
            "accessTokenPresent": token_present,
            "refreshTokenPresent": bool(token.get("refresh_token")),
            "expiresAt": expiry.isoformat() if expiry else None,
            "expiryUnknown": expiry_unknown,
            "expired": expired,
            "refreshDue": refresh_due,
        }
        if strict and not resolved_token_file.exists():
            errors.append("OAuth token file is missing")
        if not token.get("access_token"):
            errors.append("OAuth access token is missing")
        if strict and expiry_unknown:
            errors.append("OAuth token expiry is unknown")
        if strict and expired:
            errors.append("OAuth access token is expired")
        if strict and refresh_due:
            errors.append("OAuth access token is due for refresh")

    status_body = (config.get("requestBodies") or {}).get("openapi-vehicle-status")
    devices = status_body.get("devices") if isinstance(status_body, dict) else []
    status_device_count = len(devices) if isinstance(devices, list) else 0
    report["openapi"] = {"statusDevices": status_device_count}
    if strict and "openapi-vehicle-status" in routes and not has_openapi_status_devices(status_body):
        errors.append("OpenAPI vehicle-status request body has no configured devices")

    if db_path.exists():
        con = store.connect(db_path)
        table_count = int(con.execute("SELECT COUNT(*) AS c FROM sqlite_master WHERE type='table'").fetchone()["c"])
        report["db"]["tableCount"] = table_count
        for table in (
            "device_state_snapshots",
            "live_location_snapshots",
            "route_snapshot_records",
            "mqtt_status_snapshots",
        ):
            report["db"]["tables"][table] = sqlite_table_count(con, table)
        for alias in HEALTH_ROUTE_ALIASES:
            latest = latest_typed_route_summary(con, alias)
            health = route_health_summary(alias, latest, now=now, stale_multiplier=stale_multiplier)
            report["db"]["routes"][alias] = health
            if alias in routes:
                if not health.get("present"):
                    message = f"{alias} snapshot is missing"
                    if strict:
                        errors.append(message)
                    else:
                        warnings.append(message)
                elif health.get("futureSkew"):
                    message = f"{alias} snapshot timestamp is in the future: skew={health.get('futureSkewSeconds')}s"
                    if strict:
                        errors.append(message)
                    else:
                        warnings.append(message)
                elif health.get("stale"):
                    message = (
                        f"{alias} snapshot is stale: age={health.get('ageSeconds')}s "
                        f"threshold={health.get('staleThresholdSeconds')}s"
                    )
                    if strict:
                        errors.append(message)
                    else:
                        warnings.append(message)
        if table_count == 0:
            errors.append("SQLite database is not initialized")
    else:
        errors.append("SQLite database is missing")

    viewer = viewer_health_summary(
        viewer_output,
        now=now,
        live_status_max_age_seconds=live_status_max_age_seconds,
    )
    report["viewer"] = viewer
    if strict:
        if not viewer.get("viewerData"):
            errors.append("viewer data is missing")
        if not viewer.get("liveStatus"):
            errors.append("viewer live status is missing")
        elif not viewer.get("liveStatusReadable"):
            errors.append("viewer live status is unreadable")
        elif viewer.get("liveStatusAgeSeconds") is None:
            errors.append("viewer live status generatedAt is missing or invalid")
        elif viewer.get("liveStatusFutureSkew"):
            errors.append(f"viewer live status timestamp is in the future: skew={viewer.get('liveStatusFutureSkewSeconds')}s")
        elif viewer.get("liveStatusStale"):
            errors.append(
                f"viewer live status is stale: age={viewer.get('liveStatusAgeSeconds')}s "
                f"threshold={viewer.get('liveStatusMaxAgeSeconds')}s"
            )

    report["warnings"] = list(dict.fromkeys(warnings))
    report["errors"] = list(dict.fromkeys(errors))
    report["ready"] = not report["errors"]
    report["status"] = "ok" if report["ready"] else "needs_attention"
    return report


def walk_openapi_device_ids(value: Any) -> list[str]:
    found: set[str] = set()

    def walk(item: Any) -> None:
        if isinstance(item, dict):
            for key in ("id", "device_id", "deviceId"):
                value = item.get(key)
                if isinstance(value, str) and value:
                    found.add(value)
            for child in item.values():
                walk(child)
        elif isinstance(item, list):
            for child in item:
                walk(child)

    walk(value)
    return sorted(found)


def unwrap_payload(value: Any) -> Any:
    if isinstance(value, dict):
        for key in ("payload", "data", "result"):
            item = value.get(key)
            if item is not None:
                return item
    return value


def assert_read_only_path(path: str) -> None:
    if any(part in path for part in WRITE_ROUTE_PARTS):
        raise SystemExit(f"Refusing write/command route: {path}")


def add_live_source(con: Any, alias: str, response: Any, observed_at: str) -> int:
    digest = hashlib.sha256(json_dumps(response).encode()).hexdigest()
    path = f"live-sync/{observed_at}/{alias}/{digest[:16]}"
    con.execute(
        """
        INSERT OR IGNORE INTO sources(path, kind, sha256, size_bytes, imported_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (path, "live-sync", digest, len(json_dumps(response).encode()), now_iso()),
    )
    return int(con.execute("SELECT id FROM sources WHERE path=?", (path,)).fetchone()["id"])


def unwrap_response(response: Any) -> Any:
    if isinstance(response, dict) and "data" in response:
        return response["data"]
    return response


def extract_map_detail_compressed(data: Any) -> str | None:
    if isinstance(data, str):
        return data
    if not isinstance(data, dict):
        return None
    for key in ("map_detail_compress", "mapDetailCompress", "map_detail", "mapDetail", "detail"):
        value = data.get(key)
        if isinstance(value, str):
            return value
    return None


def trail_data_snapshot_summary(data: Any) -> dict[str, Any]:
    raw = json_dumps(data) if isinstance(data, (dict, list)) else str(data)
    digest = hashlib.sha256(raw.encode()).hexdigest()
    result: dict[str, Any] = {
        "status": "captured_not_decoded",
        "payloadKind": type(data).__name__,
        "payloadBytes": len(raw.encode()),
        "payloadSha256": digest,
        "note": "Compressed trail payload is intentionally not stored in viewer snapshots until local path decoding/redaction is implemented.",
    }
    if isinstance(data, dict):
        result["keys"] = sorted(str(key) for key in data.keys() if not store.is_sensitive_payload_key(str(key)))
        for key in ("total", "count", "size", "version"):
            value = data.get(key)
            if isinstance(value, (int, float, bool)) or value is None:
                result[key] = value
    elif isinstance(data, list):
        result["itemCount"] = len(data)
    return result


def mqtt_metadata_route_snapshot(data: Any) -> dict[str, Any]:
    try:
        metadata = parse_mqtt_metadata({"data": data})
    except SystemExit:
        return {
            "configured": False,
            "topicCount": 0,
            "transport": None,
            "tls": None,
            "broker": "missing",
            "websocketPath": "missing",
            "credentialStatus": "missing",
        }
    return {
        "configured": bool(metadata.get("host")),
        "topicCount": len(metadata.get("topics") or []),
        "transport": metadata.get("transport"),
        "tls": bool(metadata.get("tls")),
        "broker": "present" if metadata.get("host") else "missing",
        "websocketPath": "present" if metadata.get("path") else "missing",
        "credentialStatus": "present" if metadata.get("username") or metadata.get("password") else "missing",
    }


def insert_live_map_resource_event(
    con: Any,
    *,
    source_id: int,
    vehicle_sn: str | None,
    observed_at: str,
    data: Any,
    retain_signed_url: bool,
) -> int:
    if not isinstance(data, dict):
        return 0

    url = data.get("url")
    version = store.safe_int(data.get("version") or data.get("remoteVersion") or data.get("mapVersion"))
    if version is None:
        return 0

    sn = data.get("vehicle_sn") or data.get("vehicleSn") or data.get("sn") or vehicle_sn
    store.upsert_device(con, sn, seen_at=observed_at)
    parts = store.extract_blob_url_parts(url) if isinstance(url, str) and url else {}
    hash_value = store.event_hash(sn, version, parts.get("host"), parts.get("path"), parts.get("sha256"))
    raw_json = json_dumps({key: ("<signed-url>" if key == "url" else value) for key, value in data.items()})
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
            version,
            parts.get("host"),
            parts.get("path"),
            parts.get("expires_at"),
            parts.get("sha256"),
            url if retain_signed_url and isinstance(url, str) else None,
            raw_json,
        ),
    )
    if sn:
        store.set_sync_state(
            con,
            f"device.{store.short_hash(sn)}.map_resource",
            {
                "remote_version": version,
                "local_version": None,
                "status": None,
                "observed_at": observed_at,
                "url_expires_at": parts.get("expires_at"),
            },
        )
    return store.sqlite_row_changed(cur)


def discard_map_signed_urls(con: Any) -> int:
    cur = con.execute("UPDATE map_resource_events SET signed_url=NULL WHERE signed_url IS NOT NULL")
    con.commit()
    return max(int(cur.rowcount or 0), 0)


def latest_vehicle_row(con: Any, table: str, vehicle_sn: str, order: str = "id DESC") -> Any | None:
    if not sqlite_table_exists(con, table):
        return None
    return con.execute(
        f"""
        SELECT *
        FROM {table}
        WHERE vehicle_sn=?
        ORDER BY {order}
        LIMIT 1
        """,
        (vehicle_sn,),
    ).fetchone()


def map_sync_vehicle_sns(con: Any) -> list[str]:
    sns: set[str] = set()
    for table in ("device_state_snapshots", "map_detail_snapshots", "map_resource_events", "map_artifacts"):
        if not sqlite_table_exists(con, table):
            continue
        for row in con.execute(f"SELECT DISTINCT vehicle_sn FROM {table} WHERE vehicle_sn IS NOT NULL"):
            if row["vehicle_sn"]:
                sns.add(str(row["vehicle_sn"]))
    if sqlite_table_exists(con, "devices"):
        for row in con.execute("SELECT vehicle_sn FROM devices WHERE vehicle_sn IS NOT NULL"):
            if row["vehicle_sn"]:
                sns.add(str(row["vehicle_sn"]))
    return sorted(sns, key=store.short_hash)


def epoch_iso(value: Any) -> str | None:
    seconds = store.normalize_epoch_seconds(value)
    if seconds is None:
        return None
    return dt.datetime.fromtimestamp(seconds, dt.UTC).isoformat()


def observed_after_epoch(observed_at: Any, epoch_value: Any) -> bool:
    observed = parse_observed_datetime(observed_at)
    epoch_seconds = store.normalize_epoch_seconds(epoch_value)
    if observed is None or epoch_seconds is None:
        return False
    return epoch_seconds > int(observed.timestamp())


def file_exists_from_row(row: Any | None) -> bool:
    if row is None or row["file_path"] in (None, ""):
        return False
    return Path(str(row["file_path"])).exists()


def map_sync_plan_for_device(con: Any, vehicle_sn: str) -> dict[str, Any]:
    state = latest_vehicle_row(con, "device_state_snapshots", vehicle_sn)
    detail = latest_vehicle_row(con, "map_detail_snapshots", vehicle_sn)
    resource = latest_vehicle_row(con, "map_resource_events", vehicle_sn)
    latest_artifact = latest_vehicle_row(con, "map_artifacts", vehicle_sn)
    artifact_for_resource = None
    if resource is not None and resource["remote_version"] is not None and sqlite_table_exists(con, "map_artifacts"):
        artifact_for_resource = con.execute(
            """
            SELECT *
            FROM map_artifacts
            WHERE vehicle_sn=? AND version=?
            ORDER BY id DESC
            LIMIT 1
            """,
            (vehicle_sn, resource["remote_version"]),
        ).fetchone()

    actions: list[dict[str, str]] = []
    if state is None:
        actions.append(
            {
                "id": "sync-state",
                "reason": "No mower state snapshot with map version is present.",
                "command": "python tools/navimow_live_sync.py sync-once --config config/navimow-live-sync.local.json --db data/navimow.sqlite --routes index2",
            }
        )
    if detail is None:
        actions.append(
            {
                "id": "sync-map-detail",
                "reason": "No decoded map-detail snapshot is present.",
                "command": "python tools/navimow_live_sync.py sync-once --config config/navimow-live-sync.local.json --db data/navimow.sqlite --routes map-list,map-detail",
            }
        )

    if resource is None:
        actions.append(
            {
                "id": "sync-map-artifact-metadata",
                "reason": "No map artifact metadata snapshot is present.",
                "command": "make live-map-artifacts",
            }
        )
    else:
        artifact_missing = artifact_for_resource is None
        artifact_file_missing = artifact_for_resource is not None and not file_exists_from_row(artifact_for_resource)
        resource_not_current = (
            resource["remote_version"] is not None
            and resource["local_version"] is not None
            and store.safe_int(resource["remote_version"]) != store.safe_int(resource["local_version"])
        )
        status_not_current = str(resource["status"] or "").lower() not in {"", "current", "ok"}
        if artifact_missing:
            actions.append(
                {
                    "id": "download-map-artifact",
                    "reason": "Latest map artifact metadata has no downloaded artifact for its remote version.",
                    "command": "make live-map-artifacts",
                }
            )
        elif artifact_file_missing:
            actions.append(
                {
                    "id": "redownload-map-artifact",
                    "reason": "Downloaded map artifact record exists but the local file is missing.",
                    "command": "make live-map-artifacts",
                }
            )
        elif resource_not_current or status_not_current:
            actions.append(
                {
                    "id": "refresh-map-artifact",
                    "reason": "Map artifact metadata is not marked current.",
                    "command": "make live-map-artifacts",
                }
            )

    if actions and any(action["id"] in {"sync-map-detail", "refresh-map-detail", "download-map-artifact", "redownload-map-artifact", "refresh-map-artifact"} for action in actions):
        actions.append(
            {
                "id": "rebuild-viewer-after-map-change",
                "reason": "Map geometry or terrain artifacts affect the static viewer bundle.",
                "command": "make viewer",
            }
        )

    return {
        "deviceHash": store.short_hash(vehicle_sn),
        "state": {
            "present": state is not None,
            "observedAt": state["observed_at"] if state is not None else None,
            "mapVersion": store.safe_int(state["map_version"]) if state is not None else None,
            "settingUpdateAt": epoch_iso(state["vehicle_setting_update_time"]) if state is not None else None,
            "infoUpdateAt": epoch_iso(state["vehicle_info_update_time"]) if state is not None else None,
        },
        "mapDetail": {
            "present": detail is not None,
            "observedAt": detail["observed_at"] if detail is not None else None,
            "settingUpdateAfterDetail": bool(state is not None and detail is not None and observed_after_epoch(detail["observed_at"], state["vehicle_setting_update_time"])),
        },
        "artifact": {
            "metadataPresent": resource is not None,
            "metadataObservedAt": resource["observed_at"] if resource is not None else None,
            "remoteVersion": store.safe_int(resource["remote_version"]) if resource is not None else None,
            "localVersion": store.safe_int(resource["local_version"]) if resource is not None else None,
            "status": resource["status"] if resource is not None else None,
            "transientUrlRetained": bool(resource is not None and resource["signed_url"]),
            "downloaded": artifact_for_resource is not None,
            "filePresent": file_exists_from_row(artifact_for_resource),
            "parsedStatus": artifact_for_resource["parsed_status"] if artifact_for_resource is not None else None,
            "importedAt": artifact_for_resource["imported_at"] if artifact_for_resource is not None else None,
            "latestDownloadedVersion": store.safe_int(latest_artifact["version"]) if latest_artifact is not None else None,
        },
        "recommendedActions": actions,
    }


def map_sync_plan(db: Path) -> dict[str, Any]:
    con = store.connect(db)
    devices = [map_sync_plan_for_device(con, vehicle_sn) for vehicle_sn in map_sync_vehicle_sns(con)]
    next_commands: list[str] = []
    for device in devices:
        for action in device["recommendedActions"]:
            command = action["command"]
            if command not in next_commands:
                next_commands.append(command)
    if not devices:
        next_commands = ["make ingest", "make viewer"]
    status = "no_map_state" if not devices else "needs_attention" if next_commands else "current"
    return {
        "generatedAt": now_iso(),
        "privacy": MAP_SYNC_PLAN_PRIVACY,
        "status": status,
        "ready": status == "current",
        "deviceCount": len(devices),
        "devices": devices,
        "nextCommands": next_commands,
    }


def format_map_sync_plan(plan: dict[str, Any]) -> str:
    lines = [
        f"map sync plan: {plan.get('status')}",
        f"devices: {plan.get('deviceCount', 0)}",
        f"privacy: {plan.get('privacy')}",
    ]
    devices = plan.get("devices") if isinstance(plan.get("devices"), list) else []
    for device in devices:
        lines.append(f"device {device.get('deviceHash')}:")
        state = device.get("state") if isinstance(device.get("state"), dict) else {}
        detail = device.get("mapDetail") if isinstance(device.get("mapDetail"), dict) else {}
        artifact = device.get("artifact") if isinstance(device.get("artifact"), dict) else {}
        lines.append(
            "  state: "
            + f"mapVersion={state.get('mapVersion') or 'n/a'} "
            + f"observed={state.get('observedAt') or 'n/a'}"
        )
        lines.append(
            "  map detail: "
            + ("present" if detail.get("present") else "missing")
            + f" observed={detail.get('observedAt') or 'n/a'}"
            + (" setting-update-newer" if detail.get("settingUpdateAfterDetail") else "")
        )
        lines.append(
            "  artifact: "
            + f"metadata={'present' if artifact.get('metadataPresent') else 'missing'} "
            + f"remote={artifact.get('remoteVersion') or 'n/a'} "
            + f"local={artifact.get('localVersion') or 'n/a'} "
            + f"downloaded={bool(artifact.get('downloaded'))} "
            + f"file={bool(artifact.get('filePresent'))} "
            + f"parsed={artifact.get('parsedStatus') or 'n/a'}"
        )
        actions = device.get("recommendedActions") if isinstance(device.get("recommendedActions"), list) else []
        if actions:
            lines.append("  actions:")
            for action in actions:
                lines.append(f"    - {action.get('id')}: {action.get('reason')}")
        else:
            lines.append("  actions: none")
    commands = plan.get("nextCommands") if isinstance(plan.get("nextCommands"), list) else []
    if commands:
        lines.append("next commands:")
        for command in commands:
            lines.append(f"  {command}")
    return "\n".join(lines)


def map_delta_routes_from_plan(plan: dict[str, Any]) -> list[str]:
    routes: list[str] = []
    for device in plan.get("devices") or []:
        if not isinstance(device, dict):
            continue
        for action in device.get("recommendedActions") or []:
            if not isinstance(action, dict):
                continue
            action_id = action.get("id")
            if action_id in MAP_DETAIL_ACTION_IDS:
                for alias in ("map-list", "map-detail"):
                    if alias not in routes:
                        routes.append(alias)
            elif action_id in MAP_ARTIFACT_ACTION_IDS and "get-iot-file" not in routes:
                routes.append("get-iot-file")
    return routes


def add_counts(totals: dict[str, int], counts: dict[str, int]) -> None:
    for key, value in counts.items():
        totals[key] = totals.get(key, 0) + value


def add_map_download_counts(totals: dict[str, int], counts: dict[str, int]) -> None:
    for key, value in counts.items():
        output_key = "map_signed_urls_discarded" if key == "signed_urls_discarded" else f"map_{key}"
        totals[output_key] = totals.get(output_key, 0) + value


def download_map_artifacts_and_discard(
    con: Any,
    map_dir: Path,
    *,
    insecure_tls: bool = False,
) -> dict[str, int]:
    counts: dict[str, int] = {}
    try:
        counts = store.download_map_artifacts(con, map_dir, insecure_tls=insecure_tls)
        return counts
    finally:
        discarded = discard_map_signed_urls(con)
        if discarded:
            counts["signed_urls_discarded"] = counts.get("signed_urls_discarded", 0) + discarded


def map_delta_summary(routes: list[str], totals: dict[str, int]) -> str:
    route_label = ",".join(routes) if routes else "none"
    count_summary = summarize_counts(totals)
    return f"map_delta_routes={route_label}; {count_summary}"


def ingest_route_response(
    con: Any,
    *,
    alias: str,
    response: Any,
    observed_at: str,
    vehicle_sn: str | None,
    retain_map_signed_urls: bool = False,
) -> dict[str, int]:
    source_id = add_live_source(con, alias, response, observed_at)
    data = unwrap_response(response)
    counts: dict[str, int] = {}

    route_kind = READ_ROUTES[alias]["kind"]

    if alias == "index2" and isinstance(data, dict):
        counts["device_state_snapshots"] = store.insert_device_state_snapshot(
            con,
            source_id=source_id,
            data=data,
            observed_at=observed_at,
        )
        counts["area_setting_snapshots"] = store.insert_area_setting_snapshot(
            con,
            source_id=source_id,
            vehicle_sn=data.get("vehicle_sn") or vehicle_sn,
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
    elif alias == "device-info" and isinstance(data, dict):
        counts["device_info_snapshots"] = store.insert_device_info_snapshot(
            con,
            source_id=source_id,
            data=data,
            observed_at=observed_at,
        )
    elif alias == "get-location" and isinstance(data, dict):
        counts["live_location_snapshots"] = store.insert_live_location_snapshot(
            con,
            source_id=source_id,
            vehicle_sn=data.get("vehicle_sn") or vehicle_sn,
            observed_at=observed_at,
            line_no=0,
            data=data,
        )
    elif alias == "set-list":
        raw_text = json_dumps(data) if isinstance(data, (dict, list)) else str(data)
        counts["area_setting_snapshots"] = store.insert_area_setting_snapshot(
            con,
            source_id=source_id,
            vehicle_sn=vehicle_sn,
            observed_at=observed_at,
            partition_length=data.get("partitionLength") if isinstance(data, dict) else None,
            partition_id_list=data.get("partitionIdList") if isinstance(data, dict) else None,
            mowing_zone_list_text=None,
            mowing_zone_text=None,
            raw_text=raw_text,
        )
    elif alias == "trail-time" and isinstance(data, list):
        snapshots, entries = store.insert_trail_time_snapshot(
            con,
            source_id=source_id,
            vehicle_sn=vehicle_sn,
            observed_at=observed_at,
            line_no=0,
            entries=data,
        )
        counts["trail_time_snapshots"] = snapshots
        counts["trail_time_entries"] = entries
    elif route_kind == "map_detail":
        compressed = extract_map_detail_compressed(data)
        decoded = store.decode_map_detail_compress(compressed) if compressed else None
        if decoded:
            outer, detail = decoded
            snapshots, areas = store.insert_map_detail_snapshot(
                con,
                source_id=source_id,
                vehicle_sn=vehicle_sn,
                observed_at=observed_at,
                line_no=0,
                outer=outer,
                detail=detail,
            )
            counts["map_detail_snapshots"] = snapshots
            counts["map_detail_areas"] = areas
        else:
            counts["ignored"] = 1
    elif route_kind == "route_snapshot":
        sn = vehicle_sn
        if isinstance(data, dict):
            sn = data.get("vehicle_sn") or data.get("vehicleSn") or data.get("sn") or sn
        if alias == "trail-data":
            snapshot_data = trail_data_snapshot_summary(data)
        elif alias == "openapi-mqtt-info":
            snapshot_data = mqtt_metadata_route_snapshot(data)
        else:
            snapshot_data = data
        if alias == "get-iot-file":
            counts["map_resource_events"] = insert_live_map_resource_event(
                con,
                source_id=source_id,
                vehicle_sn=sn,
                observed_at=observed_at,
                data=data,
                retain_signed_url=retain_map_signed_urls,
            )
        counts["route_snapshot_records"] = store.insert_route_snapshot_record(
            con,
            source_id=source_id,
            vehicle_sn=sn,
            observed_at=observed_at,
            route_alias=alias,
            data=snapshot_data,
        )
    else:
        counts["ignored"] = 1

    con.commit()
    return counts


def read_response_file(response_dir: Path, alias: str) -> Any:
    path = response_dir / f"{alias}.json"
    if not path.exists():
        raise SystemExit(f"Missing response fixture: {path}")
    return load_json(path)


def request_route(config: dict[str, Any], alias: str, *, timeout: float) -> Any:
    route = READ_ROUTES[alias]
    path = route["path"]
    assert_read_only_path(path)
    base_url = str(config.get("baseUrl") or DEFAULT_BASE_URL).rstrip("/")
    url = urllib.parse.urljoin(base_url + "/", path.lstrip("/"))
    headers = resolve_env_refs(config.get("headers") or {}, strict=True)
    headers = {key: value for key, value in headers.items() if value and not str(value).startswith("${")}
    body = resolve_env_refs((config.get("requestBodies") or {}).get(alias, {}), strict=True)
    if alias == "openapi-vehicle-status" and not has_openapi_status_devices(body):
        raise SystemExit(
            "openapi-vehicle-status requires configured devices; "
            "run configure-openapi-status after syncing openapi-auth-list"
        )
    method = str(route.get("method") or "POST").upper()
    data = None if method == "GET" else json.dumps(body, ensure_ascii=False).encode()
    request = urllib.request.Request(
        url,
        data=data,
        headers=headers,
        method=method,
    )
    context = build_ssl_context()
    with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
        return json.loads(response.read().decode())


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


def collect_string_values(value: Any, keys: tuple[str, ...]) -> list[str]:
    found: list[str] = []

    def walk(item: Any) -> None:
        if isinstance(item, dict):
            for key, child in item.items():
                if key in keys:
                    if isinstance(child, list):
                        found.extend(str(entry) for entry in child if entry)
                    elif child:
                        found.append(str(child))
                walk(child)
        elif isinstance(item, list):
            for child in item:
                walk(child)

    walk(value)
    return sorted(set(found))


def parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on", "ssl", "tls"}
    return False


parse_mqtt_metadata = mqtt_client.parse_mqtt_metadata
mqtt_metadata_summary = mqtt_client.mqtt_metadata_summary
print_mqtt_metadata_summary = mqtt_client.print_mqtt_metadata_summary


def load_mqtt_metadata_for_command(args: argparse.Namespace, config: dict[str, Any]) -> dict[str, Any]:
    if args.responses_dir:
        response = read_response_file(args.responses_dir, "openapi-mqtt-info")
    else:
        prepared = prepare_config_for_network(config, timeout=args.timeout)
        response = request_route(prepared, "openapi-mqtt-info", timeout=args.timeout)
    return parse_mqtt_metadata(response)


mqtt_capacity_percent = mqtt_client.mqtt_capacity_percent
mqtt_message_snapshot = mqtt_client.mqtt_message_snapshot


def ingest_mqtt_message(
    *,
    db: Path,
    topic: str,
    payload: bytes,
    observed_at: str | None = None,
) -> int:
    observed_at = observed_at or now_iso()
    data = mqtt_message_snapshot(topic, payload)
    con = store.connect(db)
    source_id = add_live_source(con, MQTT_MESSAGE_ALIAS, data, observed_at)
    count = store.insert_route_snapshot_record(
        con,
        source_id=source_id,
        vehicle_sn=None,
        observed_at=observed_at,
        route_alias=MQTT_MESSAGE_ALIAS,
        data=data,
    )
    con.commit()
    return count


def ingest_mqtt_message_and_refresh_status(
    *,
    db: Path,
    topic: str,
    payload: bytes,
    update_live_status: bool,
    viewer_output: Path,
    observed_at: str | None = None,
) -> dict[str, Any]:
    count = ingest_mqtt_message(db=db, topic=topic, payload=payload, observed_at=observed_at)
    result: dict[str, Any] = {
        "route_snapshot_records": count,
        "live_status_updated": False,
    }
    if not update_live_status:
        return result
    try:
        path = update_live_status_artifact(db=db, output=viewer_output)
    except (Exception, SystemExit) as exc:
        result["live_status_error"] = exc.__class__.__name__
        return result
    result["live_status_updated"] = True
    result["live_status_path"] = str(path)
    return result


def mqtt_ingest_result_summary(prefix: str, result: dict[str, Any]) -> str:
    summary = f"{prefix}; route_snapshot_records={result.get('route_snapshot_records', 0)}"
    if result.get("live_status_updated"):
        return append_live_status_summary(summary, True)
    if result.get("live_status_error"):
        return f"{summary}; live_status=failed ({result['live_status_error']})"
    return summary


def build_mqtt_replay_payload(args: argparse.Namespace) -> dict[str, Any]:
    report_time = args.report_time
    if report_time is None:
        report_time = int(utc_now().timestamp()) * 1000
    area_id = args.area_id
    if area_id is None:
        area_id = replay_area_id_from_viewer(args.viewer_output) or 1
    return {
        "vehicleState": args.state,
        "workStatus": args.work_status,
        "soc": args.battery_soc,
        "descriptiveCapacityRemaining": args.capacity_label,
        "currentPartitionId": area_id,
        "mowingPercentage": args.mowing_percentage,
        "reportTime": report_time,
        "eventType": "LOCAL_MQTT_REPLAY_SMOKE",
        "token": "local-secret-redaction-check",
        "signedUrl": "https://signed.example/local-smoke",
        "latitude": "57.0000000",
    }


def replay_area_id_from_viewer(viewer_output: Path) -> int | None:
    try:
        import build_navimow_map_viewer as viewer

        data = viewer.read_viewer_data(viewer_output)
    except (OSError, SystemExit, json.JSONDecodeError):
        return None
    area_ids: list[int] = []
    for area in data.get("areas") or []:
        if not isinstance(area, dict):
            continue
        area_id = store.safe_int(area.get("id"))
        if area_id is not None:
            area_ids.append(area_id)
    return min(area_ids) if area_ids else None


def run_mqtt_listener(
    *,
    metadata: dict[str, Any],
    db: Path,
    max_messages: int,
    duration: float,
    update_live_status: bool,
    viewer_output: Path,
) -> int:
    def handle_mqtt_message(topic: str, payload: bytes, received: int) -> str:
        result = ingest_mqtt_message_and_refresh_status(
            db=db,
            topic=topic,
            payload=payload,
            update_live_status=update_live_status,
            viewer_output=viewer_output,
        )
        return mqtt_ingest_result_summary(f"mqtt_message={received}", result)

    return mqtt_client.run_mqtt_listener(
        metadata=metadata,
        max_messages=max_messages,
        duration=duration,
        on_message=handle_mqtt_message,
    )


def mqtt_epoch_to_iso(value: Any) -> str | None:
    seconds = store.safe_int(value)
    if seconds is None or seconds <= 0:
        return None
    if seconds > 10_000_000_000:
        seconds //= 1000
    return dt.datetime.fromtimestamp(seconds, dt.UTC).isoformat()


def mqtt_epoch_seconds(value: Any) -> int | None:
    seconds = store.safe_int(value)
    if seconds is None or seconds <= 0:
        return None
    if seconds > 10_000_000_000:
        seconds //= 1000
    return seconds


def redacted_mqtt_enum_value(value: Any) -> str | None:
    if value in (None, ""):
        return None
    text = str(value).strip()
    if not text:
        return None
    lower = text.lower()
    if len(text) > 72:
        return "<redacted-long-value>"
    if re.search(r"\b(?:https?|wss?|mqtts?)://", text, re.IGNORECASE):
        return "<redacted-sensitive-value>"
    if any(part in lower for part in ("authorization", "bearer", "clientid", "deviceid", "password", "secret", "signed", "token")):
        return "<redacted-sensitive-value>"
    if re.fullmatch(r"[A-Fa-f0-9]{16,}", text):
        return "<redacted-identifier>"
    return text


def increment_report_count(counts: dict[str, int], key: str | None) -> None:
    if key in (None, ""):
        return
    counts[key] = counts.get(key, 0) + 1


def mqtt_message_count(con: Any) -> int:
    if not sqlite_table_exists(con, "route_snapshot_records"):
        return 0
    return int(
        con.execute(
            "SELECT COUNT(*) FROM route_snapshot_records WHERE route_alias=?",
            (MQTT_MESSAGE_ALIAS,),
        ).fetchone()[0]
    )


def load_mqtt_sample_rows(con: Any) -> list[Any]:
    if not sqlite_table_exists(con, "mqtt_status_snapshots"):
        return []
    return con.execute(
        """
        SELECT
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
        ORDER BY COALESCE(report_time, CAST(strftime('%s', observed_at) AS INTEGER), 0), id
        """
    ).fetchall()


def mqtt_row_activity_class(row: Any) -> str:
    for key in ("state", "work_status", "task_status", "event_type"):
        activity_class = classify_activity_text(row[key])
        if activity_class:
            return activity_class
    return "unknown"


def enum_report_entries(counts: dict[str, int], *, include_activity_class: bool) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for value, count in sorted(counts.items(), key=lambda item: (-item[1], item[0])):
        entry: dict[str, Any] = {"value": value, "count": count}
        if include_activity_class:
            entry["activityClass"] = classify_activity_text(value) or "unknown"
        entries.append(entry)
    return entries


def mqtt_sample_gaps(
    *,
    message_count: int,
    sample_count: int,
    synthetic_count: int,
    field_coverage: dict[str, int],
    activity_classes: dict[str, int],
    enum_values: dict[str, list[dict[str, Any]]],
) -> list[str]:
    gaps: list[str] = []
    if sample_count == 0:
        gaps.append("no typed MQTT status samples captured yet")
        if message_count:
            gaps.append(f"{message_count} MQTT message(s) captured without allowlisted status fields")
        else:
            gaps.append("run make mqtt-listen while the mower changes state")
        return gaps
    if synthetic_count == sample_count:
        gaps.append("only synthetic replay samples captured; run make mqtt-listen during real mower state changes")
    if not activity_classes.get("active"):
        gaps.append("no active/mowing/returning status sample captured")
    if not activity_classes.get("idle"):
        gaps.append("no idle/docked/ready status sample captured")
    if not enum_values.get("state") and not enum_values.get("workStatus") and not enum_values.get("taskStatus"):
        gaps.append("no mower state/task/work enum values mapped")
    for label in ("batterySoc", "currentPartitionId", "mowingPercentage"):
        if not field_coverage.get(label):
            gaps.append(f"no {label} field observed in typed MQTT samples")
    return gaps


def mqtt_sample_report(db: Path, *, generated_at: str | None = None) -> dict[str, Any]:
    con = store.connect(db)
    rows = load_mqtt_sample_rows(con)
    message_count = mqtt_message_count(con)
    sample_count = len(rows)
    synthetic_count = 0
    coverage = {label: 0 for label in MQTT_SAMPLE_COVERAGE_FIELDS}
    enum_counts: dict[str, dict[str, int]] = {label: {} for label in MQTT_SAMPLE_ENUM_FIELDS}
    activity_classes: dict[str, int] = {}
    observed_values: list[str] = []
    report_values: list[int] = []

    for row in rows:
        observed_at = row["observed_at"]
        if observed_at:
            observed_values.append(str(observed_at))
        report_time = store.safe_int(row["report_time"])
        if report_time is not None:
            report_values.append(report_time)
        if str(row["event_type"] or "").startswith("LOCAL_"):
            synthetic_count += 1
        for label, column in MQTT_SAMPLE_COVERAGE_FIELDS.items():
            if row[column] not in (None, ""):
                coverage[label] += 1
        for label, column in MQTT_SAMPLE_ENUM_FIELDS.items():
            value = redacted_mqtt_enum_value(row[column])
            if value:
                increment_report_count(enum_counts[label], value)
        increment_report_count(activity_classes, mqtt_row_activity_class(row))

    enum_values = {
        label: enum_report_entries(counts, include_activity_class=label in {"state", "taskStatus", "workStatus"})
        for label, counts in enum_counts.items()
    }
    if not rows:
        status = "no_samples"
    elif synthetic_count == sample_count:
        status = "synthetic_only"
    else:
        status = "samples_available"

    observed = {
        "firstAt": min(observed_values) if observed_values else None,
        "lastAt": max(observed_values) if observed_values else None,
        "firstReportAt": mqtt_epoch_to_iso(min(report_values)) if report_values else None,
        "lastReportAt": mqtt_epoch_to_iso(max(report_values)) if report_values else None,
        "sources": [MQTT_MESSAGE_ALIAS] if rows or message_count else [],
    }
    gaps = mqtt_sample_gaps(
        message_count=message_count,
        sample_count=sample_count,
        synthetic_count=synthetic_count,
        field_coverage=coverage,
        activity_classes=activity_classes,
        enum_values=enum_values,
    )
    return {
        "generatedAt": generated_at or now_iso(),
        "privacy": MQTT_SAMPLE_REPORT_PRIVACY,
        "status": status,
        "messageCount": message_count,
        "sampleCount": sample_count,
        "syntheticSampleCount": synthetic_count,
        "observed": observed,
        "fieldCoverage": coverage,
        "activityClasses": dict(sorted(activity_classes.items())),
        "enumValues": enum_values,
        "sampleGaps": gaps,
    }


def format_mqtt_sample_report(report: dict[str, Any]) -> str:
    lines = [
        f"mqtt sample report: {report.get('status')}",
        f"messages: {report.get('messageCount', 0)}",
        f"typed status samples: {report.get('sampleCount', 0)} (synthetic: {report.get('syntheticSampleCount', 0)})",
        f"privacy: {report.get('privacy')}",
    ]
    observed = report.get("observed") if isinstance(report.get("observed"), dict) else {}
    if observed.get("firstAt") or observed.get("lastAt"):
        lines.append(f"observed: {observed.get('firstAt') or 'n/a'} -> {observed.get('lastAt') or 'n/a'}")
    coverage = report.get("fieldCoverage") if isinstance(report.get("fieldCoverage"), dict) else {}
    if coverage:
        lines.append("field coverage:")
        for label, count in coverage.items():
            lines.append(f"  {label}: {count}")
    activity = report.get("activityClasses") if isinstance(report.get("activityClasses"), dict) else {}
    if activity:
        lines.append("activity classes:")
        for label, count in activity.items():
            lines.append(f"  {label}: {count}")
    enum_values = report.get("enumValues") if isinstance(report.get("enumValues"), dict) else {}
    if enum_values:
        lines.append("enum values:")
        for label, entries in enum_values.items():
            if not entries:
                continue
            lines.append(f"  {label}:")
            for entry in entries:
                suffix = ""
                if entry.get("activityClass"):
                    suffix = f" ({entry['activityClass']})"
                lines.append(f"    {entry.get('value')}: {entry.get('count')}{suffix}")
    gaps = report.get("sampleGaps") if isinstance(report.get("sampleGaps"), list) else []
    if gaps:
        lines.append("sample gaps:")
        for gap in gaps:
            lines.append(f"  - {gap}")
    return "\n".join(lines)


def mqtt_real_sample_evidence(db: Path, *, now: dt.datetime | None = None) -> dict[str, Any]:
    con = store.connect(db)
    try:
        rows = load_mqtt_sample_rows(con)
    finally:
        con.close()
    coverage = {label: 0 for label in MQTT_SAMPLE_COVERAGE_FIELDS}
    enum_counts: dict[str, dict[str, int]] = {label: {} for label in MQTT_SAMPLE_ENUM_FIELDS}
    activity_classes: dict[str, int] = {}
    observed_values: list[str] = []
    timestamps: list[int] = []
    real_count = 0
    now = now or utc_now()
    for row in rows:
        if str(row["event_type"] or "").startswith("LOCAL_"):
            continue
        real_count += 1
        if row["observed_at"]:
            observed_values.append(str(row["observed_at"]))
        timestamp = mqtt_epoch_seconds(row["report_time"])
        if timestamp is None:
            timestamp = iso_timestamp_seconds(row["observed_at"])
        if timestamp:
            timestamps.append(timestamp)
        for label, column in MQTT_SAMPLE_COVERAGE_FIELDS.items():
            if row[column] not in (None, ""):
                coverage[label] += 1
        for label, column in MQTT_SAMPLE_ENUM_FIELDS.items():
            value = redacted_mqtt_enum_value(row[column])
            if value:
                increment_report_count(enum_counts[label], value)
        increment_report_count(activity_classes, mqtt_row_activity_class(row))
    latest_timestamp = max(timestamps) if timestamps else None
    age_seconds = int(now.timestamp()) - latest_timestamp if latest_timestamp is not None else None
    future_skew = bool(age_seconds is not None and age_seconds < -LIVE_HEALTH_FUTURE_SKEW_SECONDS)
    stale = bool(age_seconds is None or age_seconds > MQTT_READINESS_MAX_AGE_SECONDS) if real_count else False
    return {
        "sampleCount": real_count,
        "fieldCoverage": coverage,
        "activityClasses": dict(sorted(activity_classes.items())),
        "enumValues": {
            label: enum_report_entries(counts, include_activity_class=label in {"state", "taskStatus", "workStatus"})
            for label, counts in enum_counts.items()
        },
        "observed": {
            "firstAt": min(observed_values) if observed_values else None,
            "lastAt": max(observed_values) if observed_values else None,
            "latestAt": mqtt_epoch_to_iso(latest_timestamp) if latest_timestamp is not None else None,
            "ageSeconds": age_seconds,
            "maxAgeSeconds": MQTT_READINESS_MAX_AGE_SECONDS,
            "futureSkew": future_skew,
            "futureSkewSeconds": abs(age_seconds) if future_skew and age_seconds is not None else None,
            "stale": stale,
        },
    }


def has_meaningful_mqtt_state_enum(enum_values: dict[str, Any]) -> bool:
    for label in ("state", "workStatus", "taskStatus"):
        entries = enum_values.get(label)
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            value = str(entry.get("value") or "")
            if value.startswith("<redacted"):
                continue
            if entry.get("activityClass") in {"active", "idle"}:
                return True
    return False


def mqtt_readiness_gaps(sample_report: dict[str, Any], real_evidence: dict[str, Any]) -> list[str]:
    gaps: list[str] = []
    sample_count = store.safe_int(sample_report.get("sampleCount")) or 0
    synthetic_count = store.safe_int(sample_report.get("syntheticSampleCount")) or 0
    real_count_value = store.safe_int(real_evidence.get("sampleCount"))
    real_count = real_count_value if real_count_value is not None else max(sample_count - synthetic_count, 0)
    activity = real_evidence.get("activityClasses") if isinstance(real_evidence.get("activityClasses"), dict) else {}
    enum_values = real_evidence.get("enumValues") if isinstance(real_evidence.get("enumValues"), dict) else {}
    coverage = real_evidence.get("fieldCoverage") if isinstance(real_evidence.get("fieldCoverage"), dict) else {}

    if sample_count <= 0:
        gaps.append("no typed MQTT status samples captured yet")
    if real_count <= 0:
        gaps.append("no real MQTT status samples captured; synthetic replay does not prove mower semantics")
    if not activity.get("active"):
        gaps.append("no real active/mowing/returning status sample captured")
    if not activity.get("idle"):
        gaps.append("no real idle/docked/ready status sample captured")
    if not has_meaningful_mqtt_state_enum(enum_values):
        gaps.append("no mower state/task/work enum values mapped")
    for field in MQTT_READINESS_REQUIRED_FIELDS:
        if not coverage.get(field):
            gaps.append(f"no {field} field observed in typed MQTT samples")
    observed = real_evidence.get("observed") if isinstance(real_evidence.get("observed"), dict) else {}
    if real_count > 0 and observed.get("futureSkew"):
        gaps.append(f"latest real MQTT sample timestamp is in the future: skew={observed.get('futureSkewSeconds')}s")
    elif real_count > 0 and observed.get("stale"):
        gaps.append(
            "latest real MQTT sample is stale: age={age}s threshold={threshold}s".format(
                age=observed.get("ageSeconds"),
                threshold=observed.get("maxAgeSeconds"),
            )
        )
    return list(dict.fromkeys(gaps))


def mqtt_readiness_report(db: Path, *, generated_at: str | None = None, now: dt.datetime | None = None) -> dict[str, Any]:
    now = now or utc_now()
    sample_report = mqtt_sample_report(db, generated_at=generated_at)
    sample_count = store.safe_int(sample_report.get("sampleCount")) or 0
    synthetic_count = store.safe_int(sample_report.get("syntheticSampleCount")) or 0
    real_evidence = mqtt_real_sample_evidence(db, now=now)
    real_count_value = store.safe_int(real_evidence.get("sampleCount"))
    real_count = real_count_value if real_count_value is not None else max(sample_count - synthetic_count, 0)
    gaps = mqtt_readiness_gaps(sample_report, real_evidence)
    ready = not gaps
    return {
        "generatedAt": generated_at or sample_report.get("generatedAt") or now_iso(),
        "privacy": MQTT_READINESS_PRIVACY,
        "status": "ready" if ready else "needs_real_samples",
        "ready": ready,
        "sampleStatus": sample_report.get("status"),
        "messageCount": sample_report.get("messageCount", 0),
        "sampleCount": sample_count,
        "realSampleCount": real_count,
        "syntheticSampleCount": synthetic_count,
        "requiredFields": list(MQTT_READINESS_REQUIRED_FIELDS),
        "fieldCoverage": real_evidence.get("fieldCoverage", {}),
        "activityClasses": real_evidence.get("activityClasses", {}),
        "enumValues": real_evidence.get("enumValues", {}),
        "sampleFieldCoverage": sample_report.get("fieldCoverage", {}),
        "sampleActivityClasses": sample_report.get("activityClasses", {}),
        "observed": real_evidence.get("observed", {}),
        "sampleObserved": sample_report.get("observed", {}),
        "blockingGaps": gaps,
        "readySurfaces": ["mower-panel", "current-zone-progress", "activity-aware-cadence"] if ready else [],
        "stillNeedsPolling": list(MQTT_READINESS_STILL_POLLING),
        "nextSteps": [] if ready else ["make mqtt-listen MAX_MESSAGES=500 DURATION=600", "make mqtt-readiness --strict"],
    }


def format_mqtt_readiness_report(report: dict[str, Any]) -> str:
    lines = [
        f"mqtt readiness: {report.get('status')}",
        f"ready: {str(bool(report.get('ready'))).lower()}",
        f"messages: {report.get('messageCount', 0)}",
        f"typed status samples: {report.get('sampleCount', 0)} (real: {report.get('realSampleCount', 0)}, synthetic: {report.get('syntheticSampleCount', 0)})",
        f"privacy: {report.get('privacy')}",
    ]
    coverage = report.get("fieldCoverage") if isinstance(report.get("fieldCoverage"), dict) else {}
    if coverage:
        lines.append("required field coverage:")
        for field in report.get("requiredFields") or []:
            lines.append(f"  {field}: {coverage.get(field, 0)}")
    activity = report.get("activityClasses") if isinstance(report.get("activityClasses"), dict) else {}
    if activity:
        lines.append("activity classes:")
        for label, count in activity.items():
            lines.append(f"  {label}: {count}")
    gaps = report.get("blockingGaps") if isinstance(report.get("blockingGaps"), list) else []
    if gaps:
        lines.append("blocking gaps:")
        for gap in gaps:
            lines.append(f"  - {gap}")
    surfaces = report.get("readySurfaces") if isinstance(report.get("readySurfaces"), list) else []
    if surfaces:
        lines.append(f"ready surfaces: {', '.join(str(item) for item in surfaces)}")
    still_polling = report.get("stillNeedsPolling") if isinstance(report.get("stillNeedsPolling"), list) else []
    if still_polling:
        lines.append("still needs polling:")
        for item in still_polling:
            lines.append(f"  - {item}")
    next_steps = report.get("nextSteps") if isinstance(report.get("nextSteps"), list) else []
    if next_steps:
        lines.append("next steps:")
        for step in next_steps:
            lines.append(f"  - {step}")
    return "\n".join(lines)


def mqtt_ui_status_summary(
    viewer_output: Path,
    *,
    now: dt.datetime,
    live_status_max_age_seconds: int = LIVE_STATUS_MAX_AGE_SECONDS,
) -> dict[str, Any]:
    viewer = viewer_health_summary(
        viewer_output,
        now=now,
        live_status_max_age_seconds=live_status_max_age_seconds,
    )
    payload, available = live_status.safe_live_status_payload(viewer_output)
    summary: dict[str, Any] = {
        "viewerData": bool(viewer.get("viewerData")),
        "liveStatus": bool(viewer.get("liveStatus")),
        "liveStatusReadable": bool(viewer.get("liveStatusReadable")),
        "liveStatusAgeSeconds": viewer.get("liveStatusAgeSeconds"),
        "liveStatusMaxAgeSeconds": viewer.get("liveStatusMaxAgeSeconds"),
        "liveStatusFresh": bool(
            viewer.get("liveStatusReadable")
            and not viewer.get("liveStatusStale")
            and not viewer.get("liveStatusFutureSkew")
            and viewer.get("liveStatusAgeSeconds") is not None
        ),
        "liveStatusFutureSkew": bool(viewer.get("liveStatusFutureSkew")),
        "layoutVersion": viewer.get("layoutVersion"),
        "mqttMetadataVisible": False,
        "mqttStatusVisible": False,
        "mqttMessagesVisible": False,
        "mqttMessageCount": 0,
        "activeAreaCount": 0,
        "mqttStatusFields": {field: False for field in MQTT_READINESS_REQUIRED_FIELDS},
        "browserFeedReady": False,
        "endpoints": {
            "liveStatus": "/__navimow/live-status",
            "events": "/__navimow/events",
        },
    }
    if not available or not isinstance(payload, dict):
        return summary
    insights = ((payload.get("mower") or {}).get("routeInsights") or {})
    if not isinstance(insights, dict):
        insights = {}
    mqtt_status = insights.get("mqttStatus") if isinstance(insights.get("mqttStatus"), dict) else {}
    mqtt_messages = insights.get("mqttMessages") if isinstance(insights.get("mqttMessages"), dict) else {}
    area_status = payload.get("areaStatus") if isinstance(payload.get("areaStatus"), dict) else {}
    active_area_count = 0
    for item in area_status.values():
        if isinstance(item, dict) and ((item.get("live") or {}).get("active")):
            active_area_count += 1
    summary.update(
        {
            "mqttMetadataVisible": bool(insights.get("mqtt")),
            "mqttStatusVisible": bool(mqtt_status),
            "mqttMessagesVisible": bool(mqtt_messages),
            "mqttMessageCount": store.safe_int(mqtt_messages.get("totalMessages")) or 0,
            "activeAreaCount": active_area_count,
            "mqttStatusFields": {
                field: mqtt_status.get(field) not in (None, "")
                for field in MQTT_READINESS_REQUIRED_FIELDS
            },
        }
    )
    summary["browserFeedReady"] = bool(
        summary["liveStatusFresh"]
        and (summary["mqttMetadataVisible"] or summary["mqttStatusVisible"] or summary["mqttMessagesVisible"])
    )
    return summary


def mqtt_ui_report_status(
    *,
    metadata_summary: dict[str, Any] | None,
    metadata_error: str | None,
    listener: dict[str, Any],
    readiness: dict[str, Any],
    ui: dict[str, Any],
    live_status_refresh: dict[str, Any],
) -> str:
    if metadata_error or not metadata_summary:
        return "metadata_missing"
    if listener.get("mode") == "failed":
        return "listener_failed"
    if live_status_refresh.get("status") == "failed":
        return "live_status_refresh_failed"
    if not ui.get("viewerData") or not ui.get("liveStatusReadable"):
        return "viewer_missing"
    if not ui.get("liveStatusFresh"):
        return "viewer_stale"
    if readiness.get("ready") and ui.get("mqttStatusVisible"):
        return "ready"
    if listener.get("mode") == "listen" and listener.get("messageCount") == 0:
        return "waiting_for_messages"
    return "needs_real_samples"


def mqtt_ui_next_steps(report: dict[str, Any]) -> list[str]:
    status = report.get("status")
    readiness = report.get("mqttReadiness") or {}
    ui = report.get("ui") or {}
    steps: list[str] = []
    if status == "metadata_missing":
        steps.extend(["make openapi-refresh-status", "make mqtt-doctor"])
    if status in {"viewer_missing", "live_status_refresh_failed"}:
        steps.append("make quickstart-live")
    if status == "viewer_stale":
        steps.append("make openapi-refresh-status")
    if not readiness.get("ready"):
        steps.extend(readiness.get("nextSteps") or ["make mqtt-listen MAX_MESSAGES=500 DURATION=600", "make mqtt-readiness --strict"])
    if not ui.get("browserFeedReady"):
        steps.append("make mqtt-replay-smoke")
        steps.append("make live-ui-smoke")
    if status == "ready":
        steps.append("make live-console")
    else:
        steps.append("make mqtt-ui-report")
    return list(dict.fromkeys(steps))


def mqtt_ui_report(
    *,
    db: Path,
    viewer_output: Path,
    metadata_summary: dict[str, Any] | None,
    metadata_error: str | None,
    listener: dict[str, Any],
    live_status_refresh: dict[str, Any],
    generated_at: str | None = None,
    now: dt.datetime | None = None,
    live_status_max_age_seconds: int = LIVE_STATUS_MAX_AGE_SECONDS,
) -> dict[str, Any]:
    now = now or utc_now()
    readiness = mqtt_readiness_report(db, generated_at=generated_at or now.isoformat(), now=now)
    ui = mqtt_ui_status_summary(
        viewer_output,
        now=now,
        live_status_max_age_seconds=live_status_max_age_seconds,
    )
    report = {
        "generatedAt": generated_at or now.isoformat(),
        "privacy": MQTT_UI_REPORT_PRIVACY,
        "metadata": metadata_summary,
        "metadataError": metadata_error,
        "listener": listener,
        "liveStatusRefresh": live_status_refresh,
        "ui": ui,
        "mqttReadiness": readiness,
    }
    report["status"] = mqtt_ui_report_status(
        metadata_summary=metadata_summary,
        metadata_error=metadata_error,
        listener=listener,
        readiness=readiness,
        ui=ui,
        live_status_refresh=live_status_refresh,
    )
    report["ready"] = bool(report["status"] == "ready")
    report["nextSteps"] = mqtt_ui_next_steps(report)
    return report


def format_mqtt_ui_report(report: dict[str, Any]) -> str:
    metadata = report.get("metadata") or {}
    listener = report.get("listener") or {}
    refresh = report.get("liveStatusRefresh") or {}
    ui = report.get("ui") or {}
    readiness = report.get("mqttReadiness") or {}
    lines = [
        f"mqtt UI report: {report.get('status')}",
        f"ready: {str(bool(report.get('ready'))).lower()}",
        f"privacy: {report.get('privacy')}",
    ]
    if report.get("metadataError"):
        lines.append(f"metadata: failed ({report['metadataError']})")
    else:
        lines.append(
            "metadata: ok; transport={transport}; tls={tls}; websocket_path={path}; topics={topics}".format(
                transport=metadata.get("transport") or "unknown",
                tls=metadata.get("tls"),
                path=metadata.get("websocketPath") or "unknown",
                topics=metadata.get("topicCount", 0),
            )
        )
    if listener.get("mode") == "dry-run":
        lines.append(f"listener: dry-run; would_subscribe={listener.get('wouldSubscribeTopicCount', 0)}")
    elif listener.get("mode") == "listen":
        lines.append(
            "listener: listen; messages={messages}; duration={duration}s; max_messages={max_messages}".format(
                messages=listener.get("messageCount", 0),
                duration=listener.get("durationSeconds", 0),
                max_messages=listener.get("maxMessages", 0),
            )
        )
    elif listener.get("mode") == "failed":
        lines.append(f"listener: failed ({listener.get('error') or 'unknown'})")
    else:
        lines.append("listener: skipped")
    lines.append(f"live-status refresh: {refresh.get('status', 'skipped')}")
    lines.append(
        "ui feed: browser_ready={ready}; readable={readable}; fresh={fresh}; age={age}s; mqtt_metadata={metadata}; mqtt_status={status}; mqtt_messages={messages}; active_areas={areas}".format(
            ready=str(bool(ui.get("browserFeedReady"))).lower(),
            readable=str(bool(ui.get("liveStatusReadable"))).lower(),
            fresh=str(bool(ui.get("liveStatusFresh"))).lower(),
            age=ui.get("liveStatusAgeSeconds") if ui.get("liveStatusAgeSeconds") is not None else "unknown",
            metadata=str(bool(ui.get("mqttMetadataVisible"))).lower(),
            status=str(bool(ui.get("mqttStatusVisible"))).lower(),
            messages=str(bool(ui.get("mqttMessagesVisible"))).lower(),
            areas=ui.get("activeAreaCount", 0),
        )
    )
    fields = ui.get("mqttStatusFields") if isinstance(ui.get("mqttStatusFields"), dict) else {}
    if fields:
        lines.append(
            "ui status fields: "
            + ", ".join(f"{field}={str(bool(fields.get(field))).lower()}" for field in MQTT_READINESS_REQUIRED_FIELDS)
        )
    lines.append(
        "readiness: {status}; ready={ready}; real_samples={real}; synthetic_samples={synthetic}".format(
            status=readiness.get("status"),
            ready=str(bool(readiness.get("ready"))).lower(),
            real=readiness.get("realSampleCount", 0),
            synthetic=readiness.get("syntheticSampleCount", 0),
        )
    )
    gaps = readiness.get("blockingGaps") if isinstance(readiness.get("blockingGaps"), list) else []
    if gaps:
        lines.append("blocking gaps:")
        for gap in gaps:
            lines.append(f"  - {gap}")
    next_steps = report.get("nextSteps") if isinstance(report.get("nextSteps"), list) else []
    if next_steps:
        lines.append("next steps:")
        for step in next_steps:
            lines.append(f"  - {step}")
    return "\n".join(lines)


def summarize_counts(all_counts: dict[str, int]) -> str:
    if not all_counts:
        return "no rows changed"
    return ", ".join(f"{key}={value}" for key, value in sorted(all_counts.items()) if value)


def update_live_status_artifact(*, db: Path, output: Path) -> Path:
    return live_status.refresh_live_status_artifact(db=db, output=output)


def update_live_status_artifact_for_mqtt(*, db: Path, output: Path) -> bool:
    try:
        update_live_status_artifact(db=db, output=output)
    except (Exception, SystemExit) as exc:
        print(f"live_status=failed ({exc.__class__.__name__})")
        return False
    return True


def append_live_status_summary(summary: str, updated: bool) -> str:
    return f"{summary}; live_status=updated" if updated else summary


def cmd_init_config(args: argparse.Namespace) -> int:
    if args.output.exists() and not args.force:
        print(f"{args.output} already exists; use --force to overwrite", file=sys.stderr)
        return 1
    write_json(args.output, CONFIG_TEMPLATE)
    print(args.output)
    return 0


def cmd_init_openapi_config(args: argparse.Namespace) -> int:
    if args.output.exists() and not args.force:
        print(f"{args.output} already exists; use --force to overwrite", file=sys.stderr)
        return 1
    base = load_config(args.from_config) if args.from_config else copy.deepcopy(CONFIG_TEMPLATE)
    write_json(args.output, openapi_config_from(base))
    print(args.output)
    return 0


def cmd_auth_discover(args: argparse.Namespace) -> int:
    summary = scan_capture_auth_hints(args.path)
    if args.output:
        write_json(args.output, summary)
        print(args.output)
    else:
        print_auth_discovery(summary)
    return 0


def route_surface(path: str) -> str:
    if path.startswith("/openapi/"):
        return "openapi"
    if path.startswith("/fleet/"):
        return "fleet"
    return "consumer"


def route_catalog_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for alias, route in READ_ROUTES.items():
        path = str(route["path"])
        rows.append(
            {
                "alias": alias,
                "method": str(route.get("method") or "POST").upper(),
                "path": path,
                "surface": route_surface(path),
                "kind": route.get("kind"),
                "cadenceSeconds": route.get("cadenceSeconds"),
                "activeCadenceSeconds": route.get("activeCadenceSeconds"),
                "idleCadenceSeconds": route.get("idleCadenceSeconds"),
                "description": route.get("description"),
                "readOnly": True,
            }
        )
    return rows


def blocked_write_catalog_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for pattern in WRITE_ROUTE_PARTS:
        rows.append(
            {
                "pathPattern": pattern,
                "path": None,
                "surface": route_surface(pattern),
                "readOnly": False,
                "status": "refused",
                "reason": "write/control route pattern; command envelope and rollback behavior are not trusted",
            }
        )
    for path in DOCUMENTED_WRITE_ROUTES:
        rows.append(
            {
                "pathPattern": None,
                "path": path,
                "surface": route_surface(path),
                "readOnly": False,
                "status": "refused",
                "reason": "observed write/control route; dry-run only until command lifecycle is trusted",
            }
        )
    return rows


def route_catalog_payload() -> dict[str, Any]:
    return {
        "generatedAt": utc_now().isoformat(),
        "privacy": "Route metadata only; no tokens, device ids, MQTT credentials, signed URLs, or raw payloads.",
        "readRoutes": route_catalog_rows(),
        "blockedWriteRoutes": blocked_write_catalog_rows(),
    }


def print_route_catalog_markdown(payload: dict[str, Any]) -> None:
    print("# Navimow Live Sync Route Catalog")
    print()
    print(payload["privacy"])
    print()
    print("## Read Routes")
    print()
    print("| Alias | Surface | Method | Path | Kind | Cadence | Active | Idle |")
    print("|---|---|---:|---|---|---:|---:|---:|")
    for route in payload["readRoutes"]:
        print(
            "| {alias} | {surface} | {method} | `{path}` | {kind} | {cadenceSeconds} | {active} | {idle} |".format(
                alias=route["alias"],
                surface=route["surface"],
                method=route["method"],
                path=route["path"],
                kind=route["kind"],
                cadenceSeconds=route["cadenceSeconds"],
                active=route.get("activeCadenceSeconds") if route.get("activeCadenceSeconds") is not None else "",
                idle=route.get("idleCadenceSeconds") if route.get("idleCadenceSeconds") is not None else "",
            )
        )
    print()
    print("## Refused Write/Command Patterns")
    print()
    print("| Pattern | Surface | Status | Reason |")
    print("|---|---|---|---|")
    for route in payload["blockedWriteRoutes"]:
        value = route.get("path") or route.get("pathPattern")
        print(f"| `{value}` | {route['surface']} | {route['status']} | {route['reason']} |")


def cmd_route_catalog(args: argparse.Namespace) -> int:
    payload = route_catalog_payload()
    if args.format == "json":
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print_route_catalog_markdown(payload)
    return 0


def route_storage_for(alias: str, route: dict[str, Any]) -> dict[str, Any]:
    alias_table_info = TYPED_ROUTE_ALIAS_TABLES.get(alias)
    if alias_table_info is not None:
        return {"mode": "typed_table", "source": alias_table_info[0], "typed": True}
    kind = str(route.get("kind") or "")
    if kind == "route_snapshot":
        return {"mode": "route_snapshot", "source": "route_snapshot_records", "typed": False}
    table_info = TYPED_ROUTE_TABLES.get(kind)
    if table_info is not None:
        return {"mode": "typed_table", "source": table_info[0], "typed": True}
    return {"mode": "unknown", "source": kind or "unknown", "typed": False}


def route_coverage_rows(db_path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    con = store.connect(db_path) if db_path.exists() else None
    try:
        for alias, route in READ_ROUTES.items():
            storage = route_storage_for(alias, route)
            latest = latest_typed_route_summary(con, alias) if con is not None else {"present": False, "source": storage["source"]}
            viewer_insight = VIEWER_INSIGHT_ALIASES.get(alias)
            promotion_status = "typed" if storage["typed"] else "snapshot_only"
            if not storage["typed"] and viewer_insight:
                promotion_status = "snapshot_with_viewer_insight"
            if alias == "trail-data":
                promotion_status = "needs_decoder"
            elif alias in {"map-list", "get-iot-file", "openapi-mqtt-info", "openapi-response-commands"} and not storage["typed"]:
                promotion_status = "keep_snapshot_until_shape_stable"
            elif alias == "auth-list" and not storage["typed"]:
                promotion_status = "candidate"
            elif alias in PROMOTION_CANDIDATE_ALIASES and not storage["typed"]:
                promotion_status = "candidate"
            rows.append(
                {
                    "alias": alias,
                    "surface": route_surface(str(route["path"])),
                    "method": str(route.get("method") or "POST").upper(),
                    "kind": route.get("kind"),
                    "storageMode": storage["mode"],
                    "storageSource": latest.get("source") or storage["source"],
                    "typed": bool(storage["typed"]),
                    "present": bool(latest.get("present")),
                    "observedAt": latest.get("observedAt"),
                    "itemCount": latest.get("itemCount"),
                    "viewerInsight": viewer_insight,
                    "viewerBacked": bool(viewer_insight),
                    "promotionStatus": promotion_status,
                    "description": route.get("description"),
                }
            )
    finally:
        if con is not None:
            con.close()
    return rows


def route_coverage_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_storage: dict[str, int] = {}
    by_surface: dict[str, int] = {}
    by_status: dict[str, int] = {}
    for row in rows:
        increment_mapping_count(by_storage, row.get("storageMode"))
        increment_mapping_count(by_surface, row.get("surface"))
        increment_mapping_count(by_status, row.get("promotionStatus"))
    promotion_candidates = [
        row["alias"]
        for row in rows
        if row.get("promotionStatus") == "candidate"
    ]
    present_candidates = [
        row["alias"]
        for row in rows
        if row.get("promotionStatus") == "candidate" and row.get("present")
    ]
    return {
        "readRouteCount": len(rows),
        "presentRouteCount": sum(1 for row in rows if row.get("present")),
        "typedRouteCount": sum(1 for row in rows if row.get("typed")),
        "snapshotRouteCount": sum(1 for row in rows if row.get("storageMode") == "route_snapshot"),
        "viewerBackedRouteCount": sum(1 for row in rows if row.get("viewerBacked")),
        "byStorage": dict(sorted(by_storage.items())),
        "bySurface": dict(sorted(by_surface.items())),
        "byPromotionStatus": dict(sorted(by_status.items())),
        "promotionCandidates": promotion_candidates,
        "presentPromotionCandidates": present_candidates,
    }


def route_coverage_report(db_path: Path) -> dict[str, Any]:
    rows = route_coverage_rows(db_path)
    summary = route_coverage_summary(rows)
    next_steps: list[str] = []
    if summary["presentPromotionCandidates"]:
        next_steps.append("promote present candidate snapshots into typed tables after shape review")
    else:
        next_steps.append("sync candidate routes with live config or fixtures before promotion")
    if "trail-data" in [row["alias"] for row in rows if row.get("promotionStatus") == "needs_decoder" and row.get("present")]:
        next_steps.append("decode trail-data before promoting path replay tables")
    return {
        "generatedAt": utc_now().isoformat(),
        "privacy": ROUTE_COVERAGE_PRIVACY,
        "db": {"path": str(db_path), "present": db_path.exists()},
        "summary": summary,
        "routes": rows,
        "nextSteps": next_steps,
    }


def print_route_coverage_markdown(report: dict[str, Any]) -> None:
    summary = report["summary"]
    print("# Navimow Route Coverage Report")
    print()
    print(report["privacy"])
    print()
    print(f"- Database: {report['db']['path']} ({'present' if report['db']['present'] else 'missing'})")
    print(f"- Read routes: {summary['readRouteCount']}")
    print(f"- Present routes: {summary['presentRouteCount']}")
    print(f"- Typed routes: {summary['typedRouteCount']}")
    print(f"- Snapshot routes: {summary['snapshotRouteCount']}")
    print(f"- Viewer-backed snapshot/route insights: {summary['viewerBackedRouteCount']}")
    print(f"- Promotion candidates: {', '.join(summary['promotionCandidates']) if summary['promotionCandidates'] else 'none'}")
    print()
    print("| Alias | Surface | Storage | Present | Viewer | Promotion |")
    print("|---|---|---|---:|---|---|")
    for row in report["routes"]:
        print(
            "| {alias} | {surface} | {storage} | {present} | {viewer} | {status} |".format(
                alias=row["alias"],
                surface=row["surface"],
                storage=row["storageMode"],
                present="yes" if row["present"] else "no",
                viewer=row.get("viewerInsight") or "",
                status=row["promotionStatus"],
            )
        )
    print()
    print("## Next Steps")
    print()
    for step in report["nextSteps"]:
        print(f"- {step}")


def cmd_route_coverage(args: argparse.Namespace) -> int:
    report = route_coverage_report(args.db)
    if args.json_output:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print_route_coverage_markdown(report)
    return 0


def consumer_read_aliases() -> list[str]:
    return [
        alias
        for alias, route in READ_ROUTES.items()
        if route_surface(str(route["path"])) == "consumer"
    ]


def header_readiness_summary(config: dict[str, Any]) -> dict[str, Any]:
    headers = config.get("headers") if isinstance(config.get("headers"), dict) else {}
    safe_names = [safe_header_name(str(name)) for name in headers.keys()]
    env_refs = {name: ("set" if os.environ.get(name) else "missing") for name in sorted(collect_env_refs(headers))}
    auth_values = [
        value
        for name, value in headers.items()
        if any(part in str(name).lower() for part in ("authorization", "cookie"))
    ]
    missing_env_refs = [name for name, status in env_refs.items() if status != "set"]
    has_sensitive_auth_header = bool(auth_values)
    has_unresolved_auth_header = bool(
        missing_env_refs and any(isinstance(value, str) and "${" in value for value in auth_values)
    )
    has_usable_auth_header = bool(has_sensitive_auth_header and not missing_env_refs and not has_unresolved_auth_header)
    if not has_sensitive_auth_header:
        auth_status = "missing"
    elif missing_env_refs:
        auth_status = "env_missing"
    elif has_unresolved_auth_header:
        auth_status = "unresolved_env_ref"
    else:
        auth_status = "present"
    return {
        "safeHeaderNames": sorted(name for name in safe_names if name),
        "envRefs": env_refs,
        "authHeaderStatus": auth_status,
        "hasSensitiveAuthHeader": has_sensitive_auth_header,
        "hasUsableAuthHeader": has_usable_auth_header,
    }


def consumer_request_body_summary(config: dict[str, Any], aliases: list[str]) -> dict[str, Any]:
    bodies = config.get("requestBodies") if isinstance(config.get("requestBodies"), dict) else {}
    configured = [alias for alias in aliases if isinstance(bodies.get(alias, {}), dict)]
    invalid = [alias for alias in aliases if not isinstance(bodies.get(alias, {}), dict)]
    return {
        "configuredCount": len(configured),
        "invalidAliases": invalid,
    }


def consumer_session_next_steps(report: dict[str, Any]) -> list[str]:
    steps: list[str] = []
    if report.get("status") in {"openapi_only", "missing_consumer_auth", "missing_env", "invalid_config"}:
        steps.extend(MAP_DELTA_CONSUMER_SESSION_STEPS)
    if report.get("canSyncConsumerRoutes"):
        steps.extend(["make live-doctor", "make live-map-plan", "make live-map-delta"])
    else:
        steps.append("make consumer-session-report")
    steps.append("make live-route-coverage")
    return list(dict.fromkeys(steps))


def consumer_session_report(
    *,
    config_path: Path,
    db_path: Path,
    capture_path: Path,
    route_arg: str | None,
    now: dt.datetime | None = None,
) -> dict[str, Any]:
    now = now or utc_now()
    config_exists = config_path.exists()
    config = load_config(config_path)
    provider = auth_provider(config)
    selected_routes = requested_routes(config, route_arg)
    selected_consumer_routes = [
        alias for alias in selected_routes if route_surface(str(READ_ROUTES[alias]["path"])) == "consumer"
    ]
    selected_openapi_routes = [alias for alias in selected_routes if alias.startswith("openapi-")]
    consumer_aliases = consumer_read_aliases()
    header_summary = header_readiness_summary(config)
    body_summary = consumer_request_body_summary(config, consumer_aliases)
    coverage_rows = [row for row in route_coverage_rows(db_path) if row.get("surface") == "consumer"]
    coverage_present = [row["alias"] for row in coverage_rows if row.get("present")]
    capture_summary = scan_capture_auth_hints(capture_path)
    blockers: list[str] = []
    if not config_exists:
        blockers.append(f"config is missing: {config_path}")
    if provider in {"navimow-oauth", "oauth"}:
        blockers.append("auth.provider is OpenAPI/OAuth; consumer routes need captured consumer-app session auth")
    if not header_summary["hasSensitiveAuthHeader"]:
        blockers.append("no Authorization/Cookie-style consumer auth header configured")
    elif header_summary["authHeaderStatus"] in {"env_missing", "unresolved_env_ref"}:
        blockers.append("consumer auth header references missing or unresolved local environment variables")
    if body_summary["invalidAliases"]:
        blockers.append("one or more consumer route request bodies are not JSON objects")

    can_sync = not blockers
    if provider in {"navimow-oauth", "oauth"}:
        status = "openapi_only"
    elif body_summary["invalidAliases"]:
        status = "invalid_config"
    elif header_summary["authHeaderStatus"] in {"env_missing", "unresolved_env_ref"}:
        status = "missing_env"
    elif not header_summary["hasSensitiveAuthHeader"]:
        status = "missing_consumer_auth"
    elif can_sync:
        status = "ready_for_consumer_sync"
    else:
        status = "needs_attention"

    report = {
        "generatedAt": now.isoformat(),
        "privacy": CONSUMER_SESSION_REPORT_PRIVACY,
        "status": status,
        "ready": can_sync,
        "canSyncConsumerRoutes": can_sync,
        "config": {
            "path": str(config_path),
            "present": config_exists,
            "authProvider": provider,
            "selectedConsumerRoutes": selected_consumer_routes,
            "selectedOpenapiRoutes": selected_openapi_routes,
            "configuredConsumerRouteCount": len(selected_consumer_routes),
            "knownConsumerRouteCount": len(consumer_aliases),
        },
        "headers": header_summary,
        "requestBodies": body_summary,
        "captureHints": {
            "path": capture_summary["path"],
            "metaFiles": capture_summary["metaFiles"],
            "logFiles": capture_summary["logFiles"],
            "finding": capture_summary["finding"],
            "sensitiveNameHints": capture_summary["sensitiveNameHits"],
            "requestAuthValueCandidates": capture_summary["requestAuthValueCandidates"],
            "endpointCount": len(capture_summary["endpointPaths"]),
        },
        "routeCoverage": {
            "consumerReadRouteCount": len(coverage_rows),
            "presentConsumerRouteCount": len(coverage_present),
            "presentConsumerRoutes": coverage_present,
            "missingConsumerRoutes": [row["alias"] for row in coverage_rows if not row.get("present")],
        },
        "blockers": blockers,
    }
    report["nextSteps"] = consumer_session_next_steps(report)
    return report


def print_consumer_session_report(report: dict[str, Any]) -> None:
    print("# Navimow Consumer Session Report")
    print()
    print(report["privacy"])
    print()
    print(f"Status: {report['status']}")
    print(f"Ready: {report['ready']}")
    config = report["config"]
    print()
    print("## Config")
    print()
    print(f"- Config: {config['path']} ({'present' if config['present'] else 'missing'})")
    print(f"- Auth provider: {config['authProvider']}")
    print(f"- Selected consumer routes: {', '.join(config['selectedConsumerRoutes']) if config['selectedConsumerRoutes'] else 'none'}")
    print(f"- Selected OpenAPI routes: {', '.join(config['selectedOpenapiRoutes']) if config['selectedOpenapiRoutes'] else 'none'}")
    headers = report["headers"]
    print()
    print("## Headers")
    print()
    print(f"- Consumer auth header: {headers['authHeaderStatus']}")
    print(f"- Header names: {', '.join(headers['safeHeaderNames']) if headers['safeHeaderNames'] else 'none'}")
    print(f"- Env refs: {count_text(headers.get('envRefs')) if headers.get('envRefs') else 'none'}")
    capture = report["captureHints"]
    print()
    print("## Capture Hints")
    print()
    print(f"- Capture path: {capture['path']}")
    print(f"- Metadata files: {capture['metaFiles']}")
    print(f"- Log files: {capture['logFiles']}")
    print(f"- Finding: {capture['finding']}")
    print(f"- Sensitive-name hints: {count_text(capture.get('sensitiveNameHints')) if capture.get('sensitiveNameHints') else 'none'}")
    coverage = report["routeCoverage"]
    print()
    print("## Consumer Route Coverage")
    print()
    print(f"- Present consumer routes: {coverage['presentConsumerRouteCount']} / {coverage['consumerReadRouteCount']}")
    print(f"- Missing consumer routes: {', '.join(coverage['missingConsumerRoutes']) if coverage['missingConsumerRoutes'] else 'none'}")
    if report["blockers"]:
        print()
        print("## Blockers")
        print()
        for blocker in report["blockers"]:
            print(f"- {blocker}")
    print()
    print("## Next Steps")
    print()
    for step in report["nextSteps"]:
        print(f"- `{step}`")


def cmd_consumer_session_report(args: argparse.Namespace) -> int:
    now = parse_iso_datetime(args.now) if args.now else utc_now()
    if now is None:
        raise SystemExit("--now must be an ISO-8601 timestamp")
    report = consumer_session_report(
        config_path=args.config,
        db_path=args.db,
        capture_path=args.capture_path,
        route_arg=args.routes,
        now=now,
    )
    if args.json_output:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print_consumer_session_report(report)
    return 1 if args.strict and not report["ready"] else 0


def increment_mapping_count(mapping: dict[str, int], key: Any) -> None:
    text = str(key or "unknown")
    mapping[text] = mapping.get(text, 0) + 1


def route_catalog_summary(payload: dict[str, Any]) -> dict[str, Any]:
    read_routes = payload.get("readRoutes") if isinstance(payload.get("readRoutes"), list) else []
    blocked_routes = payload.get("blockedWriteRoutes") if isinstance(payload.get("blockedWriteRoutes"), list) else []
    surfaces: dict[str, int] = {}
    kinds: dict[str, int] = {}
    for route in read_routes:
        if not isinstance(route, dict):
            continue
        increment_mapping_count(surfaces, route.get("surface"))
        increment_mapping_count(kinds, route.get("kind"))
    blocked_surfaces: dict[str, int] = {}
    for route in blocked_routes:
        if isinstance(route, dict):
            increment_mapping_count(blocked_surfaces, route.get("surface"))
    return {
        "readRouteCount": len(read_routes),
        "blockedWriteRouteCount": len(blocked_routes),
        "readSurfaces": dict(sorted(surfaces.items())),
        "readKinds": dict(sorted(kinds.items())),
        "blockedWriteSurfaces": dict(sorted(blocked_surfaces.items())),
        "openapiReadAliases": sorted(
            str(route.get("alias"))
            for route in read_routes
            if isinstance(route, dict) and route.get("surface") == "openapi" and route.get("alias")
        ),
        "consumerReadAliases": sorted(
            str(route.get("alias"))
            for route in read_routes
            if isinstance(route, dict) and route.get("surface") == "consumer" and route.get("alias")
        ),
    }


def setup_report_next_steps(
    *,
    preflight_errors: list[str],
    preflight_next_steps: list[str],
    health: dict[str, Any],
    mqtt_readiness: dict[str, Any] | None = None,
    consumer_session: dict[str, Any] | None = None,
) -> list[str]:
    steps: list[str] = []
    steps.extend(preflight_next_steps)
    oauth = health.get("oauth") or {}
    viewer = health.get("viewer") or {}
    db = health.get("db") or {}
    routes = (health.get("config") or {}).get("routes") or []
    db_routes = db.get("routes") or {}

    if oauth.get("accessTokenPresent") and oauth.get("refreshDue"):
        steps.append("make oauth-refresh")
    if "openapi-vehicle-status" in routes and not (health.get("openapi") or {}).get("statusDevices"):
        steps.append("make openapi-discover")
        steps.append("make openapi-configure-status")
    if any(
        (db_routes.get(alias) or {}).get("stale") or not (db_routes.get(alias) or {}).get("present")
        for alias in ("openapi-auth-list", "openapi-vehicle-status", "openapi-mqtt-info")
        if alias in routes
    ):
        steps.append("make openapi-refresh-status")
    if not viewer.get("viewerData") or not viewer.get("liveStatus"):
        steps.append("make quickstart-live")
    elif viewer.get("liveStatusStale") or not viewer.get("liveStatusReadable"):
        steps.append("make openapi-refresh-status")
    mqtt_readiness = mqtt_readiness or {}
    if mqtt_readiness and not mqtt_readiness.get("ready"):
        steps.extend(mqtt_readiness.get("nextSteps") or ["make mqtt-readiness"])
    consumer_session = consumer_session or {}
    if consumer_session and not consumer_session.get("ready"):
        steps.append("make consumer-session-report")
    steps.append("make trail-replay-report")
    if not preflight_errors and not health.get("errors"):
        steps.append("make live-console")
    steps.append("make live-route-catalog")
    steps.append("make live-route-coverage")
    return list(dict.fromkeys(steps))


def setup_report_readiness_summary(
    *,
    preflight_errors: list[str],
    health: dict[str, Any],
    mqtt_readiness: dict[str, Any],
    consumer_session: dict[str, Any],
    trail_replay: dict[str, Any],
    next_steps: list[str],
) -> dict[str, Any]:
    oauth = health.get("oauth") or {}
    viewer = health.get("viewer") or {}
    health_errors = health.get("errors") or []
    health_warnings = health.get("warnings") or []
    oauth_refresh_due = bool(oauth.get("refreshDue"))
    viewer_ready = bool(viewer.get("viewerData") and viewer.get("liveStatusReadable"))
    live_status_fresh = bool(
        viewer_ready
        and not viewer.get("liveStatusStale")
        and not viewer.get("liveStatusFutureSkew")
        and viewer.get("liveStatusAgeSeconds") is not None
    )
    openapi_refresh_recommended = "make openapi-refresh-status" in next_steps
    strict_live_data_ready = bool(
        not preflight_errors
        and not health_errors
        and not health_warnings
        and not oauth_refresh_due
        and live_status_fresh
    )
    can_open_console = bool(not preflight_errors and not health_errors and viewer_ready)
    mqtt_ready = bool(mqtt_readiness.get("ready"))
    consumer_session_ready = bool(consumer_session.get("ready"))
    trail_ready = bool(trail_replay.get("readyForDecoder"))

    if preflight_errors or health_errors:
        status = "blocked"
    elif oauth_refresh_due or openapi_refresh_recommended or not live_status_fresh:
        status = "needs_refresh"
    elif not mqtt_ready or not trail_ready:
        status = "usable_with_gaps"
    else:
        status = "ready"

    recommended_next_step = next_steps[0] if next_steps else ("make live-console" if can_open_console else None)
    notes: list[str] = []
    if oauth_refresh_due:
        notes.append("OAuth token is due for refresh before relying on live OpenAPI calls.")
    if openapi_refresh_recommended:
        notes.append("OpenAPI status/discovery snapshots should be refreshed.")
    if not live_status_fresh:
        notes.append("Viewer live-status is missing, stale, unreadable, or timestamped unexpectedly.")
    if not mqtt_ready:
        notes.append("MQTT is still gated until real typed mower samples cover state, battery, current area, and progress.")
    if not consumer_session_ready:
        notes.append("Consumer-app session auth is still needed before live map/settings/trail consumer route refresh.")
    if not trail_ready:
        notes.append("Trail replay still needs trail-data plus decoded map context before exact path replay.")
    if not notes and can_open_console:
        notes.append("Live console can be started with the current local setup.")

    return {
        "status": status,
        "canOpenConsole": can_open_console,
        "strictLiveDataReady": strict_live_data_ready,
        "oauthRefreshDue": oauth_refresh_due,
        "openapiRefreshRecommended": openapi_refresh_recommended,
        "viewerReady": viewer_ready,
        "liveStatusFresh": live_status_fresh,
        "mqttReady": mqtt_ready,
        "consumerSessionReady": consumer_session_ready,
        "trailReplayReady": trail_ready,
        "recommendedNextStep": recommended_next_step,
        "notes": notes,
    }


def documentation_readiness_summary() -> dict[str, Any]:
    quickstart = Path("QUICKSTART.md")
    readme = Path("README.md")
    makefile = Path("Makefile")
    quickstart_text = quickstart.read_text(encoding="utf-8") if quickstart.exists() else ""
    readme_text = readme.read_text(encoding="utf-8") if readme.exists() else ""
    makefile_text = makefile.read_text(encoding="utf-8") if makefile.exists() else ""
    required_commands = (
        "quickstart-live",
        "live-console",
        "live-setup-report",
        "completion-report",
        "completion-report-strict",
        "mqtt-listen",
        "consumer-session-report",
    )
    missing_commands = [
        command for command in required_commands if command not in makefile_text or command not in quickstart_text
    ]
    return {
        "quickstartPresent": quickstart.exists(),
        "readmePresent": readme.exists(),
        "makefilePresent": makefile.exists(),
        "missingCommandDocs": missing_commands,
        "mentionsLiveConsole": "make live-console" in quickstart_text and "make live-console" in readme_text,
        "mentionsPrivacy": "redacted" in quickstart_text.lower() and "privacy" in readme_text.lower(),
    }


def repo_hygiene_summary() -> dict[str, Any]:
    summary: dict[str, Any] = {
        "gitPresent": (Path(".git") / "HEAD").exists(),
        "trackedSourceFiles": 0,
        "untrackedFiles": 0,
        "modifiedFiles": 0,
        "ignoredPrivatePatterns": [
            "config/*.local.json",
            "captures/",
            "data/",
            "viewer/",
        ],
        "status": "unknown",
        "blockers": [],
    }
    if not summary["gitPresent"]:
        summary["status"] = "missing_git"
        summary["blockers"] = ["git repository is not initialized"]
        return summary
    try:
        tracked = subprocess.run(
            ["git", "ls-files"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception as exc:
        summary["status"] = "unavailable"
        summary["blockers"] = [f"git status unavailable: {exc.__class__.__name__}"]
        return summary
    tracked_files = [line for line in tracked.stdout.splitlines() if line.strip()] if tracked.returncode == 0 else []
    status_lines = [line for line in status.stdout.splitlines() if line.strip()] if status.returncode == 0 else []
    untracked = [line for line in status_lines if line.startswith("?? ")]
    modified = [line for line in status_lines if not line.startswith("?? ")]
    summary["trackedSourceFiles"] = len(tracked_files)
    summary["untrackedFiles"] = len(untracked)
    summary["modifiedFiles"] = len(modified)
    blockers: list[str] = []
    if not tracked_files:
        blockers.append("no tracked source baseline exists yet")
    if untracked:
        blockers.append("source files are still untracked")
    if modified:
        blockers.append("tracked source files have uncommitted changes")
    summary["blockers"] = blockers
    summary["status"] = "ready" if not blockers else "needs_baseline"
    return summary


def completion_audit_item(
    *,
    item_id: str,
    title: str,
    status: str,
    evidence: list[str],
    blockers: list[str] | None = None,
    next_steps: list[str] | None = None,
    blocks_completion: bool = True,
) -> dict[str, Any]:
    blockers = blockers or []
    next_steps = next_steps or []
    return {
        "id": item_id,
        "title": title,
        "status": status,
        "complete": status == "complete",
        "blocksCompletion": bool(blocks_completion and status != "complete"),
        "evidence": evidence,
        "blockers": blockers,
        "nextSteps": next_steps,
    }


def setup_completion_audit(report: dict[str, Any]) -> dict[str, Any]:
    readiness = report.get("readinessSummary") or {}
    coverage = report.get("routeCoverageSummary") or {}
    mqtt = report.get("mqttReadiness") or {}
    consumer = report.get("consumerSession") or {}
    consumer_coverage = consumer.get("routeCoverage") or {}
    trail = report.get("trailReplay") or {}
    documentation = documentation_readiness_summary()
    repo_hygiene = repo_hygiene_summary()

    missing_docs: list[str] = []
    if not documentation["quickstartPresent"]:
        missing_docs.append("QUICKSTART.md is missing")
    if not documentation["readmePresent"]:
        missing_docs.append("README.md is missing")
    if not documentation["makefilePresent"]:
        missing_docs.append("Makefile is missing")
    if documentation["missingCommandDocs"]:
        missing_docs.append("quickstart is missing command docs: " + ", ".join(documentation["missingCommandDocs"]))
    if not documentation["mentionsLiveConsole"]:
        missing_docs.append("README/QUICKSTART do not both mention make live-console")
    if not documentation["mentionsPrivacy"]:
        missing_docs.append("README/QUICKSTART do not both cover redacted/privacy handling")

    route_present = store.safe_int(coverage.get("presentRouteCount")) or 0
    route_total = store.safe_int(coverage.get("readRouteCount")) or route_present
    consumer_present = store.safe_int(consumer_coverage.get("presentConsumerRouteCount")) or 0
    consumer_total = store.safe_int(consumer_coverage.get("consumerReadRouteCount")) or consumer_present

    items = [
        completion_audit_item(
            item_id="quickstart-guide",
            title="Quick start guide and local command surface",
            status="complete" if not missing_docs else "incomplete",
            evidence=[
                f"QUICKSTART.md={'present' if documentation['quickstartPresent'] else 'missing'}",
                f"README.md={'present' if documentation['readmePresent'] else 'missing'}",
                f"Makefile={'present' if documentation['makefilePresent'] else 'missing'}",
            ],
            blockers=missing_docs,
            next_steps=["document missing quickstart commands and privacy boundaries"] if missing_docs else [],
        ),
        completion_audit_item(
            item_id="repo-baseline",
            title="Repository has a tracked source baseline",
            status="complete" if repo_hygiene.get("status") == "ready" else "incomplete",
            evidence=[
                f"gitPresent={repo_hygiene.get('gitPresent')}",
                f"trackedSourceFiles={repo_hygiene.get('trackedSourceFiles', 0)}",
                f"untrackedFiles={repo_hygiene.get('untrackedFiles', 0)}",
                f"modifiedFiles={repo_hygiene.get('modifiedFiles', 0)}",
            ],
            blockers=repo_hygiene.get("blockers") or [],
            next_steps=["review git status, keep ignored private artifacts local, then commit the source baseline"]
            if repo_hygiene.get("status") != "ready"
            else [],
        ),
        completion_audit_item(
            item_id="openapi-live-console",
            title="OpenAPI live console can run safely",
            status="complete" if readiness.get("canOpenConsole") and readiness.get("strictLiveDataReady") else "incomplete",
            evidence=[
                f"canOpenConsole={readiness.get('canOpenConsole')}",
                f"strictLiveDataReady={readiness.get('strictLiveDataReady')}",
                f"liveStatusFresh={readiness.get('liveStatusFresh')}",
            ],
            blockers=[] if readiness.get("canOpenConsole") and readiness.get("strictLiveDataReady") else readiness.get("notes") or [],
            next_steps=report.get("nextSteps") or [],
        ),
        completion_audit_item(
            item_id="mqtt-realtime-ui",
            title="MQTT realtime updates are proven with real mower samples",
            status="complete" if mqtt.get("ready") else "incomplete",
            evidence=[
                f"mqttReadiness={mqtt.get('status')}",
                f"realSamples={mqtt.get('realSampleCount', 0)}",
                f"requiredFields={', '.join(mqtt.get('requiredFields') or [])}",
            ],
            blockers=mqtt.get("blockingGaps") or [],
            next_steps=mqtt.get("nextSteps") or ["make mqtt-listen MAX_MESSAGES=500 DURATION=600"],
        ),
        completion_audit_item(
            item_id="consumer-session-live-routes",
            title="Consumer-app session read routes can sync unattended",
            status="complete" if consumer.get("ready") else "incomplete",
            evidence=[
                f"consumerSession={consumer.get('status')}",
                f"authHeader={(consumer.get('headers') or {}).get('authHeaderStatus')}",
                f"consumerRoutesPresent={consumer_present}/{consumer_total}",
            ],
            blockers=consumer.get("blockers") or [],
            next_steps=consumer.get("nextSteps") or ["make consumer-session-report"],
        ),
        completion_audit_item(
            item_id="route-coverage",
            title="All read routes have local evidence/storage coverage",
            status="complete" if route_total and route_present >= route_total else "partial",
            evidence=[
                f"readRoutes={route_total}",
                f"presentRoutes={route_present}",
                f"typedRoutes={coverage.get('typedRouteCount', 0)}",
                f"snapshotRoutes={coverage.get('snapshotRouteCount', 0)}",
            ],
            blockers=[] if route_total and route_present >= route_total else ["some read routes are missing local snapshots"],
            next_steps=["make live-route-coverage", "make consumer-session-report"],
        ),
        completion_audit_item(
            item_id="trail-replay",
            title="Per-area trail replay is ready for decoding",
            status="complete" if trail.get("readyForDecoder") else "incomplete",
            evidence=[
                f"trailReplay={trail.get('status')}",
                "missing=" + ",".join(trail.get("missing") or []),
            ],
            blockers=trail.get("missing") or [],
            next_steps=trail.get("nextSteps") or ["make trail-replay-report"],
        ),
        completion_audit_item(
            item_id="schedule-write-envelope",
            title="Native mower schedule/settings write envelope is mapped",
            status="blocked",
            evidence=[
                "local planner/CLI can create dry-run planList payloads",
                "direct write routes remain refused by the read-only client",
            ],
            blockers=[
                "consumer app command envelope/signing and rollback behavior are not trusted yet",
            ],
            next_steps=[
                "capture one reversible app schedule/settings write with Android include-values mode",
                "keep sendCommands and consumer write routes refused until envelope mapping is proven",
            ],
        ),
    ]
    blocking = [item for item in items if item.get("blocksCompletion")]
    return {
        "status": "complete" if not blocking else "incomplete",
        "ready": not blocking,
        "privacy": (
            "Redacted completion audit; no tokens, device ids, MQTT topics/credentials, signed URLs, "
            "raw payloads, or exact GPS are included."
        ),
        "documentation": documentation,
        "repo": repo_hygiene,
        "items": items,
        "blockingItemIds": [item["id"] for item in blocking],
        "nextSteps": list(
            dict.fromkeys(step for item in blocking for step in (item.get("nextSteps") or []))
        ),
    }


def setup_report_payload(
    *,
    config_path: Path,
    db_path: Path,
    route_arg: str | None,
    token_file: Path | None,
    viewer_output: Path,
    strict: bool,
    stale_multiplier: float,
    live_status_max_age_seconds: int,
    now: dt.datetime,
) -> dict[str, Any]:
    preflight_errors, preflight_next_steps, preflight_summary = openapi_preflight_checks(config_path, token_file)
    health = live_health_report(
        config_path=config_path,
        db_path=db_path,
        route_arg=route_arg,
        token_file=token_file,
        viewer_output=viewer_output,
        strict=strict,
        stale_multiplier=stale_multiplier,
        live_status_max_age_seconds=live_status_max_age_seconds,
        now=now,
    )
    catalog = route_catalog_payload()
    coverage = route_coverage_report(db_path)
    trail_replay = trail_replay_report(db_path=db_path)
    mqtt_readiness = mqtt_readiness_report(db_path, generated_at=now.isoformat(), now=now)
    consumer_session = consumer_session_report(
        config_path=config_path,
        db_path=db_path,
        capture_path=Path("captures"),
        route_arg=route_arg,
        now=now,
    )
    status = "ok" if not preflight_errors and not health["errors"] else "needs_attention"
    next_steps = setup_report_next_steps(
        preflight_errors=preflight_errors,
        preflight_next_steps=preflight_next_steps,
        health=health,
        mqtt_readiness=mqtt_readiness,
        consumer_session=consumer_session,
    )
    readiness_summary = setup_report_readiness_summary(
        preflight_errors=preflight_errors,
        health=health,
        mqtt_readiness=mqtt_readiness,
        consumer_session=consumer_session,
        trail_replay=trail_replay,
        next_steps=next_steps,
    )
    report = {
        "generatedAt": now.isoformat(),
        "status": status,
        "ready": status == "ok",
        "strict": strict,
        "privacy": (
            "Redacted local setup report; no tokens, device ids, MQTT credentials/topics, "
            "signed URLs, raw payloads, or GPS arrays are included."
        ),
        "config": health["config"],
        "openapiPreflight": {
            "summary": preflight_summary,
            "errors": preflight_errors,
            "nextSteps": preflight_next_steps,
        },
        "liveHealth": health,
        "routeCatalogSummary": route_catalog_summary(catalog),
        "routeCoverageSummary": coverage["summary"],
        "readinessSummary": readiness_summary,
        "mqttReadiness": mqtt_readiness,
        "consumerSession": consumer_session,
        "trailReplay": trail_replay,
        "nextSteps": next_steps,
        "remainingGaps": copy.deepcopy(SETUP_REMAINING_GAPS),
    }
    report["completionAudit"] = setup_completion_audit(report)
    return report


def count_text(value: dict[str, int] | None) -> str:
    if not value:
        return "none"
    return ", ".join(f"{key}: {count}" for key, count in sorted(value.items()))


def print_setup_report_markdown(report: dict[str, Any]) -> None:
    health = report["liveHealth"]
    preflight = report["openapiPreflight"]
    route_summary = report["routeCatalogSummary"]
    oauth = health.get("oauth") or {}
    db = health.get("db") or {}
    viewer = health.get("viewer") or {}

    print("# Navimow Live Setup Report")
    print()
    print(report["privacy"])
    print()
    print(f"Status: {report['status']}")
    print(f"Generated: {report['generatedAt']}")
    print(f"Strict: {report['strict']}")
    print()
    readiness = report.get("readinessSummary") or {}
    print("## Readiness Summary")
    print()
    print(f"- Setup status: {readiness.get('status', 'unknown')}")
    print(f"- Can open console: {readiness.get('canOpenConsole')}")
    print(f"- Strict live data ready: {readiness.get('strictLiveDataReady')}")
    print(f"- OAuth refresh due: {readiness.get('oauthRefreshDue')}")
    print(f"- OpenAPI refresh recommended: {readiness.get('openapiRefreshRecommended')}")
    print(f"- Viewer live-status fresh: {readiness.get('liveStatusFresh')}")
    print(f"- MQTT ready for polling/UI decisions: {readiness.get('mqttReady')}")
    print(f"- Consumer session ready: {readiness.get('consumerSessionReady')}")
    print(f"- Trail replay ready: {readiness.get('trailReplayReady')}")
    if readiness.get("recommendedNextStep"):
        print(f"- Recommended next step: `{readiness['recommendedNextStep']}`")
    notes = readiness.get("notes") or []
    for note in notes:
        print(f"- {note}")
    print()
    print("## Config")
    print()
    config = report["config"]
    print(f"- Config: {config['path']} ({'present' if config['present'] else 'template-defaults'})")
    print(f"- Auth provider: {config['authProvider']}")
    print(f"- Routes: {', '.join(config['routes']) if config['routes'] else 'none'}")
    print(f"- Env refs: {count_text(health.get('envRefs')) if health.get('envRefs') else 'none'}")
    print()
    print("## OAuth And OpenAPI")
    print()
    print(f"- Preflight: {'ok' if not preflight['errors'] else 'needs attention'}")
    print(f"- OAuth token file: {oauth.get('tokenFile') or preflight['summary'].get('tokenFile')} ({'present' if oauth.get('tokenFilePresent') or preflight['summary'].get('tokenExists') else 'missing'})")
    print(f"- OAuth access token: {'present' if oauth.get('accessTokenPresent') or preflight['summary'].get('accessTokenPresent') else 'missing'}")
    print(f"- OAuth refresh token: {'present' if oauth.get('refreshTokenPresent') or preflight['summary'].get('refreshTokenPresent') else 'missing'}")
    print(f"- OAuth refresh due: {oauth.get('refreshDue', preflight['summary'].get('refreshDue'))}")
    print(f"- OpenAPI status devices: {(health.get('openapi') or {}).get('statusDevices', preflight['summary'].get('openapiStatusDevices'))}")
    print()
    print("## Live Data")
    print()
    print(f"- Database: {db.get('path')} ({'present' if db.get('present') else 'missing'})")
    print(f"- Tables: {db.get('tableCount') if db.get('tableCount') is not None else 'n/a'}")
    for table in ("device_state_snapshots", "live_location_snapshots", "route_snapshot_records", "mqtt_status_snapshots"):
        print(f"- {table}: {db.get('tables', {}).get(table, 'missing')}")
    print(f"- Viewer data: {'present' if viewer.get('viewerData') else 'missing'}")
    print(f"- Live status: {'readable' if viewer.get('liveStatusReadable') else 'missing/unreadable'}")
    print(f"- Live status age: {viewer.get('liveStatusAgeSeconds') if viewer.get('liveStatusAgeSeconds') is not None else 'unknown'}s")
    insights = viewer.get("insights") or []
    print(f"- Live status insights: {', '.join(insights) if insights else 'none'}")
    print()
    print("## Route Coverage")
    print()
    print(f"- Read routes: {route_summary['readRouteCount']} ({count_text(route_summary['readSurfaces'])})")
    print(f"- Refused write/control entries: {route_summary['blockedWriteRouteCount']} ({count_text(route_summary['blockedWriteSurfaces'])})")
    print(f"- OpenAPI read aliases: {', '.join(route_summary['openapiReadAliases']) if route_summary['openapiReadAliases'] else 'none'}")
    coverage_summary = report.get("routeCoverageSummary") or {}
    print()
    print("## Route Storage Coverage")
    print()
    print(f"- Present routes: {coverage_summary.get('presentRouteCount', 0)} / {coverage_summary.get('readRouteCount', route_summary['readRouteCount'])}")
    print(f"- Typed routes: {coverage_summary.get('typedRouteCount', 0)}")
    print(f"- Snapshot routes: {coverage_summary.get('snapshotRouteCount', 0)}")
    print(f"- Viewer-backed routes: {coverage_summary.get('viewerBackedRouteCount', 0)}")
    candidates = coverage_summary.get("promotionCandidates") or []
    print(f"- Promotion candidates: {', '.join(candidates) if candidates else 'none'}")
    mqtt_readiness = report.get("mqttReadiness") or {}
    print()
    print("## MQTT Readiness")
    print()
    print(f"- Status: {mqtt_readiness.get('status', 'unknown')}")
    print(f"- Real samples: {mqtt_readiness.get('realSampleCount', 0)}")
    print(f"- Synthetic samples: {mqtt_readiness.get('syntheticSampleCount', 0)}")
    gaps = mqtt_readiness.get("blockingGaps") or []
    print(f"- Blocking gaps: {', '.join(gaps) if gaps else 'none'}")
    consumer_session = report.get("consumerSession") or {}
    consumer_config = consumer_session.get("config") or {}
    consumer_headers = consumer_session.get("headers") or {}
    consumer_coverage = consumer_session.get("routeCoverage") or {}
    print()
    print("## Consumer Session")
    print()
    print(f"- Status: {consumer_session.get('status', 'unknown')}")
    print(f"- Ready: {consumer_session.get('ready')}")
    print(f"- Auth provider: {consumer_config.get('authProvider', 'unknown')}")
    print(f"- Consumer auth header: {consumer_headers.get('authHeaderStatus', 'unknown')}")
    print(
        "- Consumer routes present: {present} / {total}".format(
            present=consumer_coverage.get("presentConsumerRouteCount", 0),
            total=consumer_coverage.get("consumerReadRouteCount", 0),
        )
    )
    blockers = consumer_session.get("blockers") or []
    print(f"- Blockers: {', '.join(blockers) if blockers else 'none'}")
    trail_replay = report.get("trailReplay") or {}
    trail_time = trail_replay.get("trailTime") or {}
    trail_data = trail_replay.get("trailData") or {}
    map_context = trail_replay.get("mapContext") or {}
    print()
    print("## Trail Replay")
    print()
    print(f"- Status: {trail_replay.get('status', 'unknown')}")
    print(f"- Trail-time entries: {trail_time.get('entryCount', 0)}")
    print(f"- Trail-data: {'present' if trail_data.get('present') else 'missing'}")
    print(f"- Map context: geometry={'present' if map_context.get('hasGeometry') else 'missing'}, render={'present' if map_context.get('hasRenderCalibration') else 'missing'}")
    print()
    if health["warnings"]:
        print("## Warnings")
        print()
        for warning in health["warnings"]:
            print(f"- {warning}")
        print()
    combined_errors = list(dict.fromkeys(preflight["errors"] + health["errors"]))
    if combined_errors:
        print("## Errors")
        print()
        for error in combined_errors:
            print(f"- {error}")
        print()
    completion = report.get("completionAudit") or {}
    print("## Completion Audit")
    print()
    print(f"- Status: {completion.get('status', 'unknown')}")
    print(f"- Ready for full goal completion: {completion.get('ready')}")
    blocking = completion.get("blockingItemIds") or []
    print(f"- Blocking items: {', '.join(blocking) if blocking else 'none'}")
    for item in completion.get("items") or []:
        print(f"- {item.get('id')} ({item.get('status')}): {item.get('title')}")
    print()
    print("## Next Steps")
    print()
    for step in report["nextSteps"]:
        print(f"- `{step}`")
    print()
    print("## Remaining Gaps")
    print()
    for gap in report["remainingGaps"]:
        print(f"- {gap['id']} ({gap['status']}): {gap['detail']}")


def cmd_setup_report(args: argparse.Namespace) -> int:
    now = parse_iso_datetime(args.now) if args.now else utc_now()
    if now is None:
        raise SystemExit("--now must be an ISO-8601 timestamp")
    report = setup_report_payload(
        config_path=args.config,
        db_path=args.db,
        route_arg=args.routes,
        token_file=args.token_file,
        viewer_output=args.viewer_output,
        strict=args.strict,
        stale_multiplier=args.stale_multiplier,
        live_status_max_age_seconds=args.live_status_max_age_seconds,
        now=now,
    )
    if args.json_output:
        args.format = "json"
    if args.format == "json":
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print_setup_report_markdown(report)
    return 1 if args.strict and not report["ready"] else 0


def print_completion_report_markdown(report: dict[str, Any]) -> None:
    audit = report["completionAudit"]
    print("# Navimow Completion Report")
    print()
    print(audit["privacy"])
    print()
    print(f"Status: {audit['status']}")
    print(f"Ready: {audit['ready']}")
    print(f"Generated: {report['generatedAt']}")
    print()
    print("## Items")
    print()
    for item in audit["items"]:
        print(f"- {item['id']} ({item['status']}): {item['title']}")
        evidence = item.get("evidence") or []
        if evidence:
            print(f"  Evidence: {'; '.join(evidence)}")
        blockers = item.get("blockers") or []
        if blockers:
            print(f"  Blockers: {'; '.join(blockers)}")
    print()
    print("## Blocking Items")
    print()
    blocking = audit.get("blockingItemIds") or []
    if blocking:
        for item_id in blocking:
            print(f"- {item_id}")
    else:
        print("- none")
    print()
    print("## Next Steps")
    print()
    next_steps = audit.get("nextSteps") or []
    if next_steps:
        for step in next_steps:
            print(f"- `{step}`")
    else:
        print("- none")


def cmd_completion_report(args: argparse.Namespace) -> int:
    now = parse_iso_datetime(args.now) if args.now else utc_now()
    if now is None:
        raise SystemExit("--now must be an ISO-8601 timestamp")
    report = setup_report_payload(
        config_path=args.config,
        db_path=args.db,
        route_arg=args.routes,
        token_file=args.token_file,
        viewer_output=args.viewer_output,
        strict=True,
        stale_multiplier=args.stale_multiplier,
        live_status_max_age_seconds=args.live_status_max_age_seconds,
        now=now,
    )
    audit = report["completionAudit"]
    if args.json_output:
        print(json.dumps(audit, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print_completion_report_markdown(report)
    return 1 if args.strict and not audit["ready"] else 0


def cmd_trail_replay_report(args: argparse.Namespace) -> int:
    report = trail_replay_report(db_path=args.db)
    if args.json_output:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print_trail_replay_report(report)
    return 0


def cmd_plan(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    routes = requested_routes(config, args.routes)
    missing_env = sorted(ref for ref in collect_env_refs(config) if os.environ.get(ref) is None)
    warnings = config_warnings(config, routes)
    print(f"config: {args.config}")
    print(f"baseUrl: {config.get('baseUrl') or DEFAULT_BASE_URL}")
    print("routes:")
    for alias in routes:
        route = READ_ROUTES[alias]
        print(f"  {alias}: {route.get('method', 'POST')} {route['path']} every {route['cadenceSeconds']}s - {route['description']}")
    if warnings:
        print("warnings:")
        for warning in warnings:
            print(f"  {warning}")
    if missing_env:
        print("missing env:")
        for name in missing_env:
            print(f"  {name}")
    else:
        print("missing env: none")
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    routes = requested_routes(config, args.routes)
    errors = validate_config(config, routes, require_env=not args.allow_missing_env)
    warnings = config_warnings(config, routes)
    print(f"config: {args.config}")
    print(f"db: {args.db}")
    print(f"routes: {', '.join(routes) if routes else 'none'}")
    if args.db.exists():
        con = store.connect(args.db)
        table_count = con.execute("SELECT COUNT(*) AS c FROM sqlite_master WHERE type='table'").fetchone()["c"]
        print(f"db tables: {table_count}")
    else:
        print("db tables: not initialized")
    env_refs = sorted(collect_env_refs(config))
    if env_refs:
        print("env refs:")
        for name in env_refs:
            print(f"  {name}: {'set' if os.environ.get(name) else 'missing'}")
    else:
        print("env refs: none")
    if warnings:
        print("warnings:")
        for warning in warnings:
            print(f"  {warning}")
    if errors:
        print("errors:")
        for error in errors:
            print(f"  {error}")
        return 1
    print("doctor: ok")
    return 0


def cmd_openapi_preflight(args: argparse.Namespace) -> int:
    errors, next_steps, summary = openapi_preflight_checks(args.config, args.token_file)
    print(f"config: {args.config} ({'present' if summary['configExists'] else 'missing'})")
    print(f"auth provider: {summary['provider']}")
    print(f"routes: {', '.join(summary['routes']) if summary['routes'] else 'none'}")
    print(f"oauth token file: {summary['tokenFile']} ({'present' if summary['tokenExists'] else 'missing'})")
    print(f"oauth access token: {'present' if summary['accessTokenPresent'] else 'missing'}")
    print(f"oauth refresh token: {'present' if summary['refreshTokenPresent'] else 'missing'}")
    print(f"oauth refresh due: {summary['refreshDue']}")
    print(f"openapi status devices: {summary['openapiStatusDevices']}")
    if errors:
        print("errors:")
        for error in errors:
            print(f"  {error}")
        print("next steps:")
        for step in next_steps:
            print(f"  {step}")
        print("openapi preflight: needs attention")
        return 1
    if next_steps:
        print("suggested maintenance:")
        for step in next_steps:
            print(f"  {step}")
    print("openapi preflight: ok")
    return 0


def cmd_live_health(args: argparse.Namespace) -> int:
    now = parse_iso_datetime(args.now) if args.now else utc_now()
    if now is None:
        raise SystemExit("--now must be an ISO-8601 timestamp")
    report = live_health_report(
        config_path=args.config,
        db_path=args.db,
        route_arg=args.routes,
        token_file=args.token_file,
        viewer_output=args.viewer_output,
        strict=args.strict,
        stale_multiplier=args.stale_multiplier,
        live_status_max_age_seconds=args.live_status_max_age_seconds,
        now=now,
    )
    if args.json_output:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return 1 if report["errors"] else 0

    config = report["config"]
    print(f"live health status: {report['status']}")
    print(f"config: {config['path']} ({'present' if config['present'] else 'template-defaults'})")
    print(f"auth provider: {config['authProvider']}")
    print(f"routes: {', '.join(config['routes']) if config['routes'] else 'none'}")
    if report["envRefs"]:
        print("env refs:")
        for name, status in report["envRefs"].items():
            print(f"  {name}: {status}")
    else:
        print("env refs: none")

    oauth = report.get("oauth")
    if oauth:
        print(f"oauth token file: {oauth['tokenFile']} ({'present' if oauth['tokenFilePresent'] else 'missing'})")
        print(f"oauth access token: {'present' if oauth['accessTokenPresent'] else 'missing'}")
        print(f"oauth refresh token: {'present' if oauth['refreshTokenPresent'] else 'missing'}")
        print(f"oauth refresh due: {oauth['refreshDue']}")
        print(f"oauth expired: {oauth['expired']}")
        print(f"oauth expiry unknown: {oauth['expiryUnknown']}")
        if oauth.get("expiresAt"):
            print(f"oauth expires at: {oauth['expiresAt']}")

    print(f"openapi status devices: {report['openapi']['statusDevices']}")

    db = report["db"]
    if db["present"]:
        print(f"db: {db['path']} ({db['tableCount']} tables)")
        for table in (
            "device_state_snapshots",
            "live_location_snapshots",
            "route_snapshot_records",
            "mqtt_status_snapshots",
        ):
            count = db["tables"].get(table)
            print(f"{table}: {'missing' if count is None else count}")
        print("latest route snapshots:")
        for alias in HEALTH_ROUTE_ALIASES:
            latest = db["routes"].get(alias) or {"present": False}
            if latest.get("present"):
                stale_text = " stale" if latest.get("stale") else ""
                skew_text = " future-skew" if latest.get("futureSkew") else ""
                age = latest.get("ageSeconds")
                threshold = latest.get("staleThresholdSeconds")
                if age is None:
                    age_text = ""
                elif threshold is None:
                    age_text = f" age={age}s"
                else:
                    age_text = f" age={age}s threshold={threshold}s"
                print(
                    f"  {alias}: present observed={latest.get('observedAt') or 'unknown'} "
                    f"itemCount={latest.get('itemCount')}{age_text}{stale_text}{skew_text}"
                )
            else:
                print(f"  {alias}: missing")
    else:
        print(f"db: {db['path']} (missing)")

    viewer = report["viewer"]
    print(f"viewer data: {'present' if viewer.get('viewerData') else 'missing'}")
    print(f"live status: {'present' if viewer.get('liveStatus') else 'missing'}")
    if viewer.get("liveStatus"):
        print(f"live status readable: {viewer.get('liveStatusReadable', False)}")
        if viewer.get("liveStatusReadable"):
            stale_text = " stale" if viewer.get("liveStatusStale") else ""
            skew_text = " future-skew" if viewer.get("liveStatusFutureSkew") else ""
            age = viewer.get("liveStatusAgeSeconds")
            age_text = "unknown" if age is None else f"{age}s"
            print(f"live status generated: {viewer.get('generatedAt') or 'unknown'}")
            print(f"live status layout: {viewer.get('layoutVersion') or 'unknown'}")
            print(
                "live status age: "
                f"{age_text} "
                f"threshold={viewer.get('liveStatusMaxAgeSeconds')}s{stale_text}{skew_text}"
            )
            insights = viewer.get("insights") or []
            print(f"live status insights: {', '.join(insights) if insights else 'none'}")

    if report["warnings"]:
        print("warnings:")
        for warning in report["warnings"]:
            print(f"  {warning}")
    if report["errors"]:
        print("errors:")
        for error in report["errors"]:
            print(f"  {error}")
        print("live health: needs attention")
        return 1
    print("live health: ok")
    return 0


def print_oauth_token_summary(path: Path, token: dict[str, Any]) -> None:
    expiry = token_expiry(token)
    print(f"token file: {path}")
    print(f"access token: {'present' if token.get('access_token') else 'missing'}")
    print(f"refresh token: {'present' if token.get('refresh_token') else 'missing'}")
    print(f"expires at: {expiry.isoformat() if expiry else 'unknown'}")
    print(f"refresh due: {token_needs_refresh(token)}")


def cmd_oauth_login_url(_args: argparse.Namespace) -> int:
    print(OAUTH_LOGIN_URL)
    return 0


def cmd_oauth_exchange_code(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    token_file = oauth_token_file(config, args.token_file)
    token = exchange_oauth_code(config, args.code, timeout=args.timeout)
    write_oauth_token(token_file, token)
    print_oauth_token_summary(token_file, token)
    return 0


def cmd_oauth_refresh(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    token_file = oauth_token_file(config, args.token_file)
    token = load_oauth_token(token_file)
    refreshed = refresh_oauth_token(config, str(token.get("refresh_token") or ""), timeout=args.timeout)
    write_oauth_token(token_file, refreshed)
    print_oauth_token_summary(token_file, refreshed)
    return 0


def cmd_oauth_doctor(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    token_file = oauth_token_file(config, args.token_file)
    provider = auth_provider(config)
    print(f"provider: {provider}")
    if not token_file.exists():
        print(f"token file: missing ({token_file})")
        return 1
    token = load_oauth_token(token_file)
    print_oauth_token_summary(token_file, token)
    return 0 if token.get("access_token") else 1


def cmd_configure_openapi_status(args: argparse.Namespace) -> int:
    con = store.connect(args.db)
    row = con.execute(
        """
        SELECT sanitized_json
        FROM route_snapshot_records
        WHERE route_alias=?
        ORDER BY id DESC
        LIMIT 1
        """,
        (args.auth_list_alias,),
    ).fetchone()
    if not row:
        raise SystemExit(f"No {args.auth_list_alias} snapshot found in {args.db}")
    try:
        payload = json.loads(row["sanitized_json"])
    except (TypeError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Latest {args.auth_list_alias} snapshot is not valid sanitized JSON") from exc
    device_ids = walk_openapi_device_ids(payload)
    if not device_ids:
        raise SystemExit(f"No OpenAPI device ids found in latest {args.auth_list_alias} snapshot")

    config = load_config(args.config)
    config = openapi_config_from(config)
    config["routes"] = list(OPENAPI_STATUS_ROUTES)
    config.setdefault("requestBodies", {})["openapi-vehicle-status"] = {
        "devices": [{"id": device_id} for device_id in device_ids]
    }
    write_json(args.config, config)
    print(f"configured {len(device_ids)} OpenAPI device id(s)")
    return 0


def cmd_sync_once(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    routes = list(MAP_DELTA_ROUTES) if args.map_delta else requested_routes(config, args.routes)
    errors = validate_config(config, routes, require_env=not args.responses_dir and not args.dry_run)
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1
    map_delta_blockers = map_delta_consumer_auth_blockers(config) if args.map_delta and not args.responses_dir else []
    if map_delta_blockers:
        stream = sys.stdout if args.dry_run else sys.stderr
        for line in map_delta_blockers:
            print(line, file=stream)
        return 0 if args.dry_run else 1
    if args.dry_run:
        if args.map_delta:
            print("would run map delta sync: index2, then needed map-list/map-detail/get-iot-file routes from map-sync-plan")
            if args.download_map_artifacts:
                print(f"would download map artifacts into {args.map_dir}")
            return 0
        for alias in routes:
            route = READ_ROUTES[alias]
            print(f"would {route.get('method', 'POST')} {route['path']}")
        if args.update_live_status:
            print(f"would write live status {args.viewer_output / 'navimow-live-status.json'}")
        if args.download_map_artifacts:
            print(f"would download map artifacts into {args.map_dir}")
        return 0

    if not args.responses_dir:
        config = prepare_config_for_network(config, timeout=args.timeout)
    if args.map_delta:
        result = run_map_delta_sync(
            config=config,
            db=args.db,
            responses_dir=args.responses_dir,
            observed_at=args.observed_at,
            timeout=args.timeout,
            download_map_artifacts=args.download_map_artifacts,
            map_dir=args.map_dir,
            insecure_downloads=args.insecure_downloads,
        )
        live_status_updated = False
        if args.update_live_status:
            update_live_status_artifact(db=args.db, output=args.viewer_output)
            live_status_updated = True
        print(append_live_status_summary(map_delta_summary(result["routes"], result["totals"]), live_status_updated))
        return 0

    con = store.connect(args.db)
    observed_at = args.observed_at or now_iso()
    vehicle_sn = config.get("vehicleSn") or None
    totals: dict[str, int] = {}
    for alias in routes:
        response = read_response_file(args.responses_dir, alias) if args.responses_dir else request_route(config, alias, timeout=args.timeout)
        counts = ingest_route_response(
            con,
            alias=alias,
            response=response,
            observed_at=observed_at,
            vehicle_sn=vehicle_sn,
            retain_map_signed_urls=args.download_map_artifacts,
        )
        add_counts(totals, counts)
    if args.download_map_artifacts:
        add_map_download_counts(
            totals,
            download_map_artifacts_and_discard(con, args.map_dir, insecure_tls=args.insecure_downloads),
        )
    live_status_updated = False
    if args.update_live_status:
        update_live_status_artifact(db=args.db, output=args.viewer_output)
        live_status_updated = True
    print(append_live_status_summary(summarize_counts(totals), live_status_updated))
    return 0


def cmd_mqtt_doctor(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    metadata = load_mqtt_metadata_for_command(args, config)
    print_mqtt_metadata_summary(metadata)
    return 0


def cmd_mqtt_listen(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    metadata = load_mqtt_metadata_for_command(args, config)
    print_mqtt_metadata_summary(metadata)
    if args.dry_run:
        print(f"would subscribe to {len(metadata.get('topics') or [])} MQTT topic(s)")
        return 0
    count = run_mqtt_listener(
        metadata=metadata,
        db=args.db,
        max_messages=args.max_messages,
        duration=args.duration,
        update_live_status=args.update_live_status,
        viewer_output=args.viewer_output,
    )
    print(f"mqtt listener stopped; messages={count}")
    return 0


def cmd_mqtt_replay_smoke(args: argparse.Namespace) -> int:
    payload = build_mqtt_replay_payload(args)
    result = ingest_mqtt_message_and_refresh_status(
        db=args.db,
        topic="local/mqtt-replay-smoke",
        payload=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
        update_live_status=args.update_live_status,
        viewer_output=args.viewer_output,
        observed_at=args.observed_at,
    )
    print(mqtt_ingest_result_summary("mqtt_replay_message=1", result))
    return 0


def clear_mqtt_replay_smoke(*, db: Path) -> dict[str, int]:
    con = store.connect(db)
    snapshot_count = int(
        con.execute(
            """
            SELECT COUNT(*)
            FROM route_snapshot_records
            WHERE route_alias=? AND sanitized_json LIKE ?
            """,
            (MQTT_MESSAGE_ALIAS, "%LOCAL_MQTT_REPLAY_SMOKE%"),
        ).fetchone()[0]
    )
    status_count = int(
        con.execute(
            "SELECT COUNT(*) FROM mqtt_status_snapshots WHERE event_type=?",
            ("LOCAL_MQTT_REPLAY_SMOKE",),
        ).fetchone()[0]
    )
    con.execute("DELETE FROM mqtt_status_snapshots WHERE event_type=?", ("LOCAL_MQTT_REPLAY_SMOKE",))
    con.execute(
        """
        DELETE FROM route_snapshot_records
        WHERE route_alias=? AND sanitized_json LIKE ?
        """,
        (MQTT_MESSAGE_ALIAS, "%LOCAL_MQTT_REPLAY_SMOKE%"),
    )
    con.commit()
    return {"mqtt_status_snapshots": status_count, "route_snapshot_records": snapshot_count}


def cmd_mqtt_replay_clear(args: argparse.Namespace) -> int:
    counts = clear_mqtt_replay_smoke(db=args.db)
    live_status_updated = False
    if args.update_live_status:
        update_live_status_artifact(db=args.db, output=args.viewer_output)
        live_status_updated = True
    print(append_live_status_summary(summarize_counts(counts), live_status_updated))
    return 0


def cmd_mqtt_sample_report(args: argparse.Namespace) -> int:
    report = mqtt_sample_report(args.db)
    if args.json_output:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(format_mqtt_sample_report(report))
    return 0


def cmd_mqtt_readiness(args: argparse.Namespace) -> int:
    now = parse_iso_datetime(args.now) if args.now else utc_now()
    if now is None:
        raise SystemExit("--now must be an ISO-8601 timestamp")
    report = mqtt_readiness_report(args.db, now=now)
    if args.json_output:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(format_mqtt_readiness_report(report))
    return 1 if args.strict and not report["ready"] else 0


def cmd_mqtt_ui_report(args: argparse.Namespace) -> int:
    fixed_now = parse_iso_datetime(args.now) if args.now else None
    if args.now and fixed_now is None:
        raise SystemExit("--now must be an ISO-8601 timestamp")
    metadata: dict[str, Any] | None = None
    metadata_summary: dict[str, Any] | None = None
    metadata_error: str | None = None
    listener: dict[str, Any] = {"mode": "skipped"}
    live_status_refresh: dict[str, Any] = {"status": "skipped"}
    config = load_config(args.config)

    try:
        metadata = load_mqtt_metadata_for_command(args, config)
        metadata_summary = mqtt_metadata_summary(metadata)
    except (Exception, SystemExit) as exc:
        metadata_error = exc.__class__.__name__

    if metadata is not None:
        if args.listen:
            listener = {
                "mode": "listen",
                "maxMessages": args.max_messages,
                "durationSeconds": args.duration,
                "messageCount": 0,
            }
            try:
                count = run_mqtt_listener(
                    metadata=metadata,
                    db=args.db,
                    max_messages=args.max_messages,
                    duration=args.duration,
                    update_live_status=args.update_live_status,
                    viewer_output=args.viewer_output,
                )
                listener["messageCount"] = count
            except (Exception, SystemExit) as exc:
                listener = {"mode": "failed", "error": exc.__class__.__name__}
        elif args.dry_run:
            listener = {
                "mode": "dry-run",
                "wouldSubscribeTopicCount": len(metadata.get("topics") or []),
            }

    if args.update_live_status:
        try:
            path = update_live_status_artifact(db=args.db, output=args.viewer_output)
            live_status_refresh = {"status": "updated", "path": str(path)}
        except (Exception, SystemExit) as exc:
            live_status_refresh = {"status": "failed", "error": exc.__class__.__name__}

    now = fixed_now or utc_now()
    report = mqtt_ui_report(
        db=args.db,
        viewer_output=args.viewer_output,
        metadata_summary=metadata_summary,
        metadata_error=metadata_error,
        listener=listener,
        live_status_refresh=live_status_refresh,
        generated_at=now.isoformat(),
        now=now,
        live_status_max_age_seconds=args.live_status_max_age_seconds,
    )
    if args.json_output:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(format_mqtt_ui_report(report))
    return 1 if args.strict and not report["ready"] else 0


def cmd_map_sync_plan(args: argparse.Namespace) -> int:
    plan = map_sync_plan(args.db)
    if args.json_output:
        print(json.dumps(plan, ensure_ascii=False, indent=2))
    else:
        print(format_map_sync_plan(plan))
    return 0


def run_sync_pass(
    *,
    config: dict[str, Any],
    db: Path,
    routes: list[str],
    responses_dir: Path | None,
    observed_at: str | None,
    timeout: float,
) -> dict[str, int]:
    con = store.connect(db)
    pass_observed_at = observed_at or now_iso()
    vehicle_sn = config.get("vehicleSn") or None
    totals: dict[str, int] = {}
    for alias in routes:
        response = read_response_file(responses_dir, alias) if responses_dir else request_route(config, alias, timeout=timeout)
        counts = ingest_route_response(
            con,
            alias=alias,
            response=response,
            observed_at=pass_observed_at,
            vehicle_sn=vehicle_sn,
        )
        add_counts(totals, counts)
    return totals


def run_map_delta_sync(
    *,
    config: dict[str, Any],
    db: Path,
    responses_dir: Path | None,
    observed_at: str | None,
    timeout: float,
    download_map_artifacts: bool,
    map_dir: Path,
    insecure_downloads: bool,
) -> dict[str, Any]:
    con = store.connect(db)
    pass_observed_at = observed_at or now_iso()
    vehicle_sn = config.get("vehicleSn") or None
    totals: dict[str, int] = {}
    routes_run: list[str] = []

    def ingest_alias(alias: str, *, retain_signed_url: bool = False) -> None:
        response = read_response_file(responses_dir, alias) if responses_dir else request_route(config, alias, timeout=timeout)
        counts = ingest_route_response(
            con,
            alias=alias,
            response=response,
            observed_at=pass_observed_at,
            vehicle_sn=vehicle_sn,
            retain_map_signed_urls=retain_signed_url,
        )
        routes_run.append(alias)
        add_counts(totals, counts)

    ingest_alias("index2")
    plan = map_sync_plan(db)
    delta_routes = map_delta_routes_from_plan(plan)
    for alias in delta_routes:
        ingest_alias(alias, retain_signed_url=download_map_artifacts and alias == "get-iot-file")

    if download_map_artifacts and "get-iot-file" in delta_routes:
        add_map_download_counts(
            totals,
            download_map_artifacts_and_discard(con, map_dir, insecure_tls=insecure_downloads),
        )

    return {"routes": routes_run, "totals": totals, "plan": plan}


def rebuild_viewer(*, db: Path, output: Path, no_satellite: bool) -> None:
    command = [
        sys.executable,
        str(Path(__file__).resolve().parent / "build_navimow_map_viewer.py"),
        "--db",
        str(db),
        "--output",
        str(output),
    ]
    if no_satellite:
        command.append("--no-satellite")
    subprocess.run(command, check=True)


def auto_viewer_refresh_action(totals: dict[str, int], due_routes: list[str]) -> str:
    changed_counts = {key for key, value in totals.items() if value}
    if not changed_counts:
        return "none"
    if changed_counts & AUTO_VIEWER_REBUILD_COUNT_KEYS:
        return "rebuild"
    if changed_counts & AUTO_VIEWER_LIVE_STATUS_COUNT_KEYS:
        return "live-status"
    return "none"


def cmd_poll(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    routes = requested_routes(config, args.routes)
    errors = validate_config(config, routes, require_env=not args.responses_dir)
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1
    if args.max_iterations < 1:
        raise SystemExit("--max-iterations must be at least 1")
    if args.interval < 0:
        raise SystemExit("--interval must be non-negative")
    if args.refresh_trails_on_completion and not args.activity_aware_cadence:
        raise SystemExit("--refresh-trails-on-completion requires --activity-aware-cadence")
    if not args.responses_dir:
        config = prepare_config_for_network(config, timeout=args.timeout)

    use_route_cadence = args.use_route_cadence or args.activity_aware_cadence
    next_due: dict[str, float] = {alias: 0.0 for alias in routes}
    previous_activity_state: str | None = None

    for iteration in range(1, args.max_iterations + 1):
        observed_at = now_iso()
        due_routes = routes
        activity = live_activity_state(args.db) if args.activity_aware_cadence else None
        completion_routes: list[str] = []
        if use_route_cadence:
            monotonic_now = time.monotonic()
            activity_state = (activity or {}).get("state")
            if args.activity_aware_cadence and activity_state == "active" and previous_activity_state not in (None, "active"):
                for alias in routes:
                    if READ_ROUTES[alias].get("activeCadenceSeconds") is not None:
                        next_due[alias] = min(next_due[alias], monotonic_now)
            due_routes = [alias for alias in routes if monotonic_now >= next_due[alias]]
            if args.refresh_trails_on_completion:
                completion_routes = completion_refresh_routes(routes, due_routes, previous_activity_state, activity_state)
                due_routes = append_unique_routes(due_routes, completion_routes)

        totals: dict[str, int] = {}
        if due_routes:
            totals = run_sync_pass(
                config=config,
                db=args.db,
                routes=due_routes,
                responses_dir=args.responses_dir,
                observed_at=observed_at,
                timeout=args.timeout,
            )
            if use_route_cadence:
                activity = live_activity_state(args.db) if args.activity_aware_cadence else activity
                if args.refresh_trails_on_completion:
                    post_sync_completion_routes = completion_refresh_routes(
                        routes,
                        due_routes,
                        previous_activity_state,
                        (activity or {}).get("state"),
                    )
                    if post_sync_completion_routes:
                        extra_counts = run_sync_pass(
                            config=config,
                            db=args.db,
                            routes=post_sync_completion_routes,
                            responses_dir=args.responses_dir,
                            observed_at=observed_at,
                            timeout=args.timeout,
                        )
                        for key, value in extra_counts.items():
                            totals[key] = totals.get(key, 0) + value
                        due_routes = append_unique_routes(due_routes, post_sync_completion_routes)
                        completion_routes = append_unique_routes(completion_routes, post_sync_completion_routes)
                        activity = live_activity_state(args.db) if args.activity_aware_cadence else activity
                monotonic_now = time.monotonic()
                for alias in due_routes:
                    next_due[alias] = monotonic_now + route_cadence_seconds(
                        alias,
                        activity,
                        activity_aware=args.activity_aware_cadence,
                    )

        live_status_updated = False
        viewer_rebuilt = False
        auto_refresh_action = "none"
        if args.rebuild_viewer and due_routes:
            rebuild_viewer(db=args.db, output=args.viewer_output, no_satellite=args.no_satellite)
            viewer_rebuilt = True
        elif args.auto_viewer_refresh and due_routes:
            auto_refresh_action = auto_viewer_refresh_action(totals, due_routes)
            if auto_refresh_action == "rebuild":
                rebuild_viewer(db=args.db, output=args.viewer_output, no_satellite=args.no_satellite)
                viewer_rebuilt = True
            elif auto_refresh_action == "live-status":
                update_live_status_artifact(db=args.db, output=args.viewer_output)
                live_status_updated = True
        elif args.update_live_status and due_routes:
            update_live_status_artifact(db=args.db, output=args.viewer_output)
            live_status_updated = True
        due_label = ",".join(due_routes) if due_routes else "none"
        summary = append_live_status_summary(summarize_counts(totals), live_status_updated)
        if viewer_rebuilt:
            summary = f"{summary}; viewer=rebuilt"
        elif auto_refresh_action == "none" and args.auto_viewer_refresh and due_routes:
            summary = f"{summary}; viewer=unchanged"
        activity_label = f" activity={(activity or {}).get('state', 'unknown')}" if args.activity_aware_cadence else ""
        completion_label = f" completion_refresh={','.join(completion_routes)}" if completion_routes else ""
        print(f"poll {iteration}/{args.max_iterations} routes={due_label}{activity_label}{completion_label}: {summary}")
        previous_activity_state = (activity or {}).get("state") if args.activity_aware_cadence else previous_activity_state
        if iteration < args.max_iterations:
            time.sleep(args.interval)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_config = subparsers.add_parser("init-config", help="Write a local config template")
    init_config.add_argument("--output", type=Path, default=DEFAULT_CONFIG)
    init_config.add_argument("--force", action="store_true")
    init_config.set_defaults(func=cmd_init_config)

    init_openapi_config = subparsers.add_parser("init-openapi-config", help="Write an OAuth/OpenAPI local config template")
    init_openapi_config.add_argument("--output", type=Path, default=DEFAULT_CONFIG)
    init_openapi_config.add_argument("--from-config", type=Path, help="Merge from an existing config before applying OpenAPI defaults")
    init_openapi_config.add_argument("--force", action="store_true")
    init_openapi_config.set_defaults(func=cmd_init_openapi_config)

    auth_discover = subparsers.add_parser("auth-discover", help="Inspect captures for redacted auth/header discovery hints")
    auth_discover.add_argument("--path", type=Path, default=Path("captures"))
    auth_discover.add_argument("--output", type=Path, help="Write redacted discovery JSON")
    auth_discover.set_defaults(func=cmd_auth_discover)

    plan = subparsers.add_parser("plan", help="Show read-only sync routes and missing environment variables")
    plan.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    plan.add_argument("--routes", help="Comma-separated route aliases")
    plan.set_defaults(func=cmd_plan)

    route_catalog = subparsers.add_parser("route-catalog", help="Print the redacted read/write route catalog")
    route_catalog.add_argument("--format", choices=("markdown", "json"), default="markdown")
    route_catalog.set_defaults(func=cmd_route_catalog)

    route_coverage = subparsers.add_parser("route-coverage", help="Print redacted local route storage/viewer/promotion coverage")
    route_coverage.add_argument("--db", type=Path, default=DEFAULT_DB)
    route_coverage.add_argument("--json", dest="json_output", action="store_true", help="Emit redacted machine-readable JSON")
    route_coverage.set_defaults(func=cmd_route_coverage)

    consumer_session = subparsers.add_parser(
        "consumer-session-report",
        help="Check whether local config/captures are ready for consumer-app read routes",
    )
    consumer_session.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    consumer_session.add_argument("--db", type=Path, default=DEFAULT_DB)
    consumer_session.add_argument("--capture-path", type=Path, default=Path("captures"))
    consumer_session.add_argument("--routes", help="Comma-separated route aliases")
    consumer_session.add_argument("--json", dest="json_output", action="store_true", help="Emit redacted machine-readable JSON")
    consumer_session.add_argument("--strict", action="store_true", help="Return non-zero until consumer-session sync is ready")
    consumer_session.add_argument("--now", help=argparse.SUPPRESS)
    consumer_session.set_defaults(func=cmd_consumer_session_report)

    map_sync_plan_parser = subparsers.add_parser("map-sync-plan", help="Inspect local map/detail/artifact versions and print the next safe sync steps")
    map_sync_plan_parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    map_sync_plan_parser.add_argument("--json", dest="json_output", action="store_true", help="Emit redacted machine-readable JSON")
    map_sync_plan_parser.set_defaults(func=cmd_map_sync_plan)

    trail_replay = subparsers.add_parser("trail-replay-report", help="Inspect local readiness for decoded trail/path replay without exporting trail payloads")
    trail_replay.add_argument("--db", type=Path, default=DEFAULT_DB)
    trail_replay.add_argument("--json", dest="json_output", action="store_true", help="Emit redacted machine-readable JSON")
    trail_replay.set_defaults(func=cmd_trail_replay_report)

    setup_report = subparsers.add_parser("setup-report", help="Print a redacted setup/debug report for live Navimow sync")
    setup_report.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    setup_report.add_argument("--db", type=Path, default=DEFAULT_DB)
    setup_report.add_argument("--routes", help="Comma-separated route aliases")
    setup_report.add_argument("--token-file", type=Path)
    setup_report.add_argument("--viewer-output", type=Path, default=Path("viewer/navimow-map"))
    setup_report.add_argument("--strict", action="store_true", help="Return non-zero when the combined readiness report is not ready")
    setup_report.add_argument("--format", choices=("markdown", "json"), default="markdown")
    setup_report.add_argument("--json", dest="json_output", action="store_true", help="Emit a redacted machine-readable setup report")
    setup_report.add_argument("--stale-multiplier", type=float, default=LIVE_HEALTH_STALE_MULTIPLIER)
    setup_report.add_argument("--live-status-max-age-seconds", type=int, default=LIVE_STATUS_MAX_AGE_SECONDS)
    setup_report.add_argument("--now", help=argparse.SUPPRESS)
    setup_report.set_defaults(func=cmd_setup_report)

    completion_report = subparsers.add_parser(
        "completion-report",
        help="Audit whether the repo/live setup satisfies the full Navimow goal",
    )
    completion_report.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    completion_report.add_argument("--db", type=Path, default=DEFAULT_DB)
    completion_report.add_argument("--routes", help="Comma-separated route aliases")
    completion_report.add_argument("--token-file", type=Path)
    completion_report.add_argument("--viewer-output", type=Path, default=Path("viewer/navimow-map"))
    completion_report.add_argument("--strict", action="store_true", help="Return non-zero until the full completion audit is ready")
    completion_report.add_argument("--json", dest="json_output", action="store_true", help="Emit a redacted machine-readable completion audit")
    completion_report.add_argument("--stale-multiplier", type=float, default=LIVE_HEALTH_STALE_MULTIPLIER)
    completion_report.add_argument("--live-status-max-age-seconds", type=int, default=LIVE_STATUS_MAX_AGE_SECONDS)
    completion_report.add_argument("--now", help=argparse.SUPPRESS)
    completion_report.set_defaults(func=cmd_completion_report)

    doctor = subparsers.add_parser("doctor", help="Validate local config and live-sync readiness without network calls")
    doctor.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    doctor.add_argument("--db", type=Path, default=DEFAULT_DB)
    doctor.add_argument("--routes", help="Comma-separated route aliases")
    doctor.add_argument("--allow-missing-env", action="store_true")
    doctor.set_defaults(func=cmd_doctor)

    openapi_preflight = subparsers.add_parser("openapi-preflight", help="Validate local OAuth/OpenAPI quickstart readiness without network calls")
    openapi_preflight.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    openapi_preflight.add_argument("--token-file", type=Path)
    openapi_preflight.set_defaults(func=cmd_openapi_preflight)

    live_health = subparsers.add_parser("live-health", help="Summarize local live-data readiness without printing secret values")
    live_health.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    live_health.add_argument("--db", type=Path, default=DEFAULT_DB)
    live_health.add_argument("--routes", help="Comma-separated route aliases")
    live_health.add_argument("--token-file", type=Path)
    live_health.add_argument("--viewer-output", type=Path, default=Path("viewer/navimow-map"))
    live_health.add_argument("--strict", action="store_true", help="Fail when configured route snapshots or the live-status feed are stale or missing")
    live_health.add_argument("--json", dest="json_output", action="store_true", help="Emit a redacted machine-readable health report")
    live_health.add_argument("--stale-multiplier", type=float, default=LIVE_HEALTH_STALE_MULTIPLIER)
    live_health.add_argument("--live-status-max-age-seconds", type=int, default=LIVE_STATUS_MAX_AGE_SECONDS)
    live_health.add_argument("--now", help=argparse.SUPPRESS)
    live_health.set_defaults(func=cmd_live_health)

    oauth_login_url = subparsers.add_parser("oauth-login-url", help="Print the Navimow OAuth login URL")
    oauth_login_url.set_defaults(func=cmd_oauth_login_url)

    oauth_exchange = subparsers.add_parser("oauth-exchange-code", help="Exchange a copied Navimow OAuth redirect/code for a local token file")
    oauth_exchange.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    oauth_exchange.add_argument("--code", required=True, help="Authorization code or full localhost redirect URL")
    oauth_exchange.add_argument("--token-file", type=Path)
    oauth_exchange.add_argument("--timeout", type=float, default=20.0)
    oauth_exchange.set_defaults(func=cmd_oauth_exchange_code)

    oauth_refresh = subparsers.add_parser("oauth-refresh", help="Refresh the local Navimow OAuth token file")
    oauth_refresh.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    oauth_refresh.add_argument("--token-file", type=Path)
    oauth_refresh.add_argument("--timeout", type=float, default=20.0)
    oauth_refresh.set_defaults(func=cmd_oauth_refresh)

    oauth_doctor = subparsers.add_parser("oauth-doctor", help="Inspect local OAuth token readiness without printing token values")
    oauth_doctor.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    oauth_doctor.add_argument("--token-file", type=Path)
    oauth_doctor.set_defaults(func=cmd_oauth_doctor)

    openapi_status = subparsers.add_parser(
        "configure-openapi-status",
        help="Populate OpenAPI status device request bodies from the latest sanitized auth-list snapshot",
    )
    openapi_status.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    openapi_status.add_argument("--db", type=Path, default=DEFAULT_DB)
    openapi_status.add_argument("--auth-list-alias", default="openapi-auth-list")
    openapi_status.set_defaults(func=cmd_configure_openapi_status)

    mqtt_doctor = subparsers.add_parser("mqtt-doctor", help="Fetch and validate OpenAPI MQTT metadata without printing credential values")
    mqtt_doctor.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    mqtt_doctor.add_argument("--responses-dir", type=Path, help="Read openapi-mqtt-info.json from a local fixture directory")
    mqtt_doctor.add_argument("--timeout", type=float, default=20.0)
    mqtt_doctor.set_defaults(func=cmd_mqtt_doctor)

    mqtt_listen = subparsers.add_parser("mqtt-listen", help="Subscribe to OpenAPI MQTT topics and store sanitized message summaries")
    mqtt_listen.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    mqtt_listen.add_argument("--db", type=Path, default=DEFAULT_DB)
    mqtt_listen.add_argument("--responses-dir", type=Path, help="Read openapi-mqtt-info.json from a local fixture directory")
    mqtt_listen.add_argument("--timeout", type=float, default=20.0)
    mqtt_listen.add_argument("--max-messages", type=int, default=0, help="Stop after this many messages; 0 means no message limit")
    mqtt_listen.add_argument("--duration", type=float, default=0.0, help="Stop after this many seconds; 0 waits until max messages or interruption")
    mqtt_listen.add_argument("--dry-run", action="store_true", help="Validate metadata and print what would be subscribed without connecting")
    mqtt_listen.add_argument("--update-live-status", action="store_true", help="Refresh navimow-live-status.json after each stored message")
    mqtt_listen.add_argument("--viewer-output", type=Path, default=Path("viewer/navimow-map"))
    mqtt_listen.set_defaults(func=cmd_mqtt_listen)

    mqtt_replay = subparsers.add_parser(
        "mqtt-replay-smoke",
        help="Inject one synthetic sanitized MQTT status event for local UI/SSE smoke testing",
    )
    mqtt_replay.add_argument("--db", type=Path, default=DEFAULT_DB)
    mqtt_replay.add_argument("--viewer-output", type=Path, default=Path("viewer/navimow-map"))
    mqtt_replay.add_argument("--area-id", type=int, help="Synthetic current partition/area id; defaults to the first area in generated viewer data")
    mqtt_replay.add_argument("--state", default="isRunning")
    mqtt_replay.add_argument("--work-status", default="MOWING")
    mqtt_replay.add_argument("--battery-soc", type=int, default=66)
    mqtt_replay.add_argument("--capacity-label", default="MEDIUM")
    mqtt_replay.add_argument("--mowing-percentage", type=int, default=55)
    mqtt_replay.add_argument("--report-time", type=int, help="Synthetic MQTT reportTime in milliseconds")
    mqtt_replay.add_argument("--observed-at", help="Synthetic observed_at timestamp; defaults to current UTC time")
    mqtt_replay.add_argument("--update-live-status", action="store_true", help="Refresh navimow-live-status.json after injecting the event")
    mqtt_replay.set_defaults(func=cmd_mqtt_replay_smoke)

    mqtt_replay_clear = subparsers.add_parser(
        "mqtt-replay-clear",
        help="Remove synthetic mqtt-replay-smoke rows and optionally refresh live status",
    )
    mqtt_replay_clear.add_argument("--db", type=Path, default=DEFAULT_DB)
    mqtt_replay_clear.add_argument("--viewer-output", type=Path, default=Path("viewer/navimow-map"))
    mqtt_replay_clear.add_argument("--update-live-status", action="store_true", help="Refresh navimow-live-status.json after cleanup")
    mqtt_replay_clear.set_defaults(func=cmd_mqtt_replay_clear)

    mqtt_sample_report_parser = subparsers.add_parser(
        "mqtt-sample-report",
        help="Summarize sanitized MQTT samples for enum and UI coverage mapping",
    )
    mqtt_sample_report_parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    mqtt_sample_report_parser.add_argument("--json", dest="json_output", action="store_true", help="Emit redacted machine-readable JSON")
    mqtt_sample_report_parser.set_defaults(func=cmd_mqtt_sample_report)

    mqtt_readiness = subparsers.add_parser(
        "mqtt-readiness",
        help="Gate whether real MQTT samples are sufficient for UI/polling decisions",
    )
    mqtt_readiness.add_argument("--db", type=Path, default=DEFAULT_DB)
    mqtt_readiness.add_argument("--json", dest="json_output", action="store_true", help="Emit redacted machine-readable JSON")
    mqtt_readiness.add_argument("--strict", action="store_true", help="Return non-zero until real MQTT samples cover required UI/polling fields")
    mqtt_readiness.add_argument("--now", help=argparse.SUPPRESS)
    mqtt_readiness.set_defaults(func=cmd_mqtt_readiness)

    mqtt_ui = subparsers.add_parser(
        "mqtt-ui-report",
        help="Summarize MQTT metadata, optional listener results, live-status freshness, and browser UI readiness",
    )
    mqtt_ui.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    mqtt_ui.add_argument("--db", type=Path, default=DEFAULT_DB)
    mqtt_ui.add_argument("--responses-dir", type=Path, help="Read openapi-mqtt-info.json from a local fixture directory")
    mqtt_ui.add_argument("--timeout", type=float, default=20.0)
    mqtt_ui.add_argument("--viewer-output", type=Path, default=Path("viewer/navimow-map"))
    mqtt_ui.add_argument("--listen", action="store_true", help="Run a bounded MQTT listener before printing the report")
    mqtt_ui.add_argument("--dry-run", action="store_true", help="Validate metadata and report subscription count without connecting")
    mqtt_ui.add_argument("--max-messages", type=int, default=0, help="Stop listener after this many messages; 0 means no message limit")
    mqtt_ui.add_argument("--duration", type=float, default=0.0, help="Stop listener after this many seconds; 0 waits until max messages or interruption")
    mqtt_ui.add_argument("--update-live-status", action="store_true", help="Refresh navimow-live-status.json before printing the report")
    mqtt_ui.add_argument("--live-status-max-age-seconds", type=int, default=LIVE_STATUS_MAX_AGE_SECONDS)
    mqtt_ui.add_argument("--json", dest="json_output", action="store_true", help="Emit redacted machine-readable JSON")
    mqtt_ui.add_argument("--strict", action="store_true", help="Return non-zero until MQTT and UI readiness are both proven")
    mqtt_ui.add_argument("--now", help=argparse.SUPPRESS)
    mqtt_ui.set_defaults(func=cmd_mqtt_ui_report)

    sync_once = subparsers.add_parser("sync-once", help="Run one read-only sync pass")
    sync_once.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    sync_once.add_argument("--db", type=Path, default=DEFAULT_DB)
    sync_once.add_argument("--routes", help="Comma-separated route aliases")
    sync_once.add_argument("--responses-dir", type=Path, help="Read alias.json responses from a local directory instead of the network")
    sync_once.add_argument("--observed-at")
    sync_once.add_argument("--timeout", type=float, default=20.0)
    sync_once.add_argument("--dry-run", action="store_true")
    sync_once.add_argument(
        "--map-delta",
        action="store_true",
        help="Run index2 first, then only the needed map-list/map-detail/get-iot-file routes from the local map-sync plan",
    )
    sync_once.add_argument(
        "--download-map-artifacts",
        action="store_true",
        help="For get-iot-file syncs, download map artifacts from transient signed URLs and discard those URLs after the attempt",
    )
    sync_once.add_argument("--map-dir", type=Path, default=store.DEFAULT_MAP_DIR)
    sync_once.add_argument("--insecure-downloads", action="store_true")
    sync_once.add_argument(
        "--update-live-status",
        "--write-live-status",
        action="store_true",
        help="Refresh only viewer/navimow-live-status.json after syncing",
    )
    sync_once.add_argument("--viewer-output", type=Path, default=Path("viewer/navimow-map"))
    sync_once.set_defaults(func=cmd_sync_once)

    poll = subparsers.add_parser("poll", help="Run repeated read-only sync passes")
    poll.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    poll.add_argument("--db", type=Path, default=DEFAULT_DB)
    poll.add_argument("--routes", help="Comma-separated route aliases")
    poll.add_argument("--responses-dir", type=Path, help="Read alias.json responses from a local directory instead of the network")
    poll.add_argument("--timeout", type=float, default=20.0)
    poll.add_argument("--interval", type=float, default=10.0)
    poll.add_argument("--max-iterations", type=int, default=1)
    poll.add_argument("--use-route-cadence", action="store_true", help="Run each route only when its configured cadence is due")
    poll.add_argument(
        "--activity-aware-cadence",
        action="store_true",
        help="Adjust selected route cadences from sanitized latest mower activity state; implies --use-route-cadence",
    )
    poll.add_argument(
        "--refresh-trails-on-completion",
        action="store_true",
        help="Force selected trail-time/trail-data read routes when sanitized activity changes from active to idle",
    )
    poll_viewer = poll.add_mutually_exclusive_group()
    poll_viewer.add_argument("--rebuild-viewer", action="store_true")
    poll_viewer.add_argument(
        "--update-live-status",
        "--write-live-status",
        action="store_true",
        help="Refresh only viewer/navimow-live-status.json instead of rebuilding the full viewer",
    )
    poll_viewer.add_argument(
        "--auto-viewer-refresh",
        action="store_true",
        help="Refresh live status for status-only changes and rebuild the viewer for map/settings/capability changes",
    )
    poll.add_argument("--viewer-output", type=Path, default=Path("viewer/navimow-map"))
    poll.add_argument("--no-satellite", action="store_true")
    poll.set_defaults(func=cmd_poll)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
