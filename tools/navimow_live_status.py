#!/usr/bin/env python3
"""Shared live-status helpers for the local Navimow viewer."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any


LIVE_STATUS_FILE = "navimow-live-status.json"
SAFE_SNAPSHOT_ALIASES = {
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
}


def now_iso() -> str:
    return dt.datetime.now(dt.UTC).isoformat()


def file_record(root: Path, relative: str) -> dict[str, Any]:
    path = root / relative
    try:
        stat = path.stat()
    except FileNotFoundError:
        return {"path": relative, "exists": False}
    return {
        "path": relative,
        "exists": True,
        "mtimeNs": stat.st_mtime_ns,
        "sizeBytes": stat.st_size,
    }


def records_version(records: list[dict[str, Any]]) -> str:
    digest_input = json.dumps(records, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(digest_input).hexdigest()[:16]


def pick_existing(source: Any, keys: tuple[str, ...]) -> dict[str, Any]:
    if not isinstance(source, dict):
        return {}
    return {key: source.get(key) for key in keys if key in source}


def safe_area_id(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def safe_mqtt_messages_payload(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    payload = pick_existing(
        value,
        (
            "observedAt",
            "totalMessages",
            "observedTopicCount",
            "payloadShapes",
            "messageClasses",
            "statusSnapshotCount",
            "privacy",
        ),
    )
    if isinstance(value.get("latest"), dict):
        payload["latest"] = pick_existing(
            value["latest"],
            ("observedAt", "payloadShape", "messageClasses", "payloadKeys", "payloadBytes"),
        )
    return payload


def safe_area_status_payload(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    payload: dict[str, Any] = {}
    for key, item in value.items():
        if not isinstance(item, dict):
            continue
        area_id = safe_area_id(item.get("id"))
        if area_id is None:
            area_id = safe_area_id(key)
        if area_id is None:
            continue
        entry: dict[str, Any] = {"id": area_id}
        last_mow = pick_existing(
            item.get("lastMow"),
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
        if last_mow:
            entry["lastMow"] = last_mow
        cutting = pick_existing(item.get("cutting"), ("areaHeightMm", "effectiveHeightMm", "source"))
        if cutting:
            entry["cutting"] = cutting
        live = pick_existing(
            item.get("live"),
            (
                "active",
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
        if live:
            live["active"] = bool(live.get("active"))
            entry["live"] = live
        payload[str(area_id)] = entry
    return payload


def safe_live_mower_payload(mower: Any) -> dict[str, Any]:
    if not isinstance(mower, dict):
        return {}
    insights = mower.get("routeInsights") or {}
    snapshots = mower.get("routeSnapshots") or {}
    sync = mower.get("sync") or {}
    safe_insights = {
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
        "mqttMessages": safe_mqtt_messages_payload(insights.get("mqttMessages")),
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
        "cutting": pick_existing(mower.get("cutting"), ("heightMm", "cutterHeightCode", "supportedMm", "sourceRoute", "observedAt")),
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
            alias: pick_existing(value, ("observedAt", "itemCount", "shape", "routeAlias"))
            for alias, value in snapshots.items()
            if alias in SAFE_SNAPSHOT_ALIASES and isinstance(value, dict)
        },
        "sync": {
            "batteryAndState": pick_existing(
                sync.get("batteryAndState"),
                ("route", "cadenceSeconds", "activeCadenceSeconds", "idleCadenceSeconds", "status"),
            ),
            "liveLocation": pick_existing(
                sync.get("liveLocation"),
                ("route", "cadenceSeconds", "activeCadenceSeconds", "idleCadenceSeconds", "status"),
            ),
            "lastMowPerArea": pick_existing(sync.get("lastMowPerArea"), ("routes", "cadenceSeconds", "status")),
        },
    }


def safe_live_status_payload(root: Path) -> tuple[dict[str, Any], bool]:
    path = root / LIVE_STATUS_FILE
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {
            "available": False,
            "privacy": "Metadata only; live status file is not present.",
        }, False
    except (OSError, json.JSONDecodeError):
        return {
            "available": False,
            "privacy": "Metadata only; live status file could not be parsed.",
        }, False
    if not isinstance(raw, dict):
        return {
            "available": False,
            "privacy": "Metadata only; live status file has an unsupported shape.",
        }, False

    payload = {
        "version": raw.get("version"),
        "generatedAt": raw.get("generatedAt"),
        "layoutVersion": raw.get("layoutVersion"),
        "map": pick_existing(raw.get("map"), ("name", "areaCount", "obstacleCount", "customSelectedAreaM2")),
        "schedule": pick_existing(raw.get("schedule"), ("baseSnapshotId", "baseObservedAt")),
        "areaStatus": safe_area_status_payload(raw.get("areaStatus")),
        "mower": safe_live_mower_payload(raw.get("mower")),
        "privacy": (
            "Sanitized local status only; no polygons, raw route payloads, tokens, "
            "serials, signed URLs, MQTT credentials, or GPS arrays are included."
        ),
    }
    return payload, True


def live_status_summary(root: Path) -> dict[str, Any]:
    payload, available = safe_live_status_payload(root)
    return {
        "available": available,
        "generatedAt": payload.get("generatedAt"),
        "layoutVersion": payload.get("layoutVersion"),
    }


def refresh_live_status_artifact(*, db: Path, output: Path) -> Path:
    import build_navimow_map_viewer as viewer
    import navimow_state_store as store

    con: sqlite3.Connection = store.connect(db)
    try:
        return viewer.refresh_live_status_file(output, con)
    finally:
        con.close()
