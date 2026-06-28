#!/usr/bin/env python3
"""MQTT metadata parsing, sanitization, and listener helpers for Navimow sync."""

from __future__ import annotations

import contextlib
import json
import ssl
import sys
import threading
import urllib.parse
from collections.abc import Callable
from typing import Any

import navimow_state_store as store


MQTT_SAFE_VALUE_KEYS = {
    "battery",
    "batterySoc",
    "capacityPercent",
    "capacityRemaining",
    "currentPartitionId",
    "descriptiveCapacityRemaining",
    "event",
    "eventType",
    "mowingPercentage",
    "partitionId",
    "pathId",
    "path_id",
    "reportTime",
    "report_time",
    "soc",
    "state",
    "taskStatus",
    "time",
    "timestamp",
    "vehicleState",
    "workStatus",
}


def unwrap_payload(response: Any) -> Any:
    if isinstance(response, dict):
        for key in ("data", "result", "payload"):
            value = response.get(key)
            if value not in (None, ""):
                return value
    return response


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


def parse_mqtt_metadata(response: Any) -> dict[str, Any]:
    payload = unwrap_payload(response)
    if not isinstance(payload, dict):
        raise SystemExit("OpenAPI MQTT metadata response was not an object")

    host_value = find_first_value(payload, ("mqttHost", "mqttUrl", "mqttUri", "url", "host", "endpoint"))
    if not host_value:
        raise SystemExit("OpenAPI MQTT metadata has no MQTT host/url field")
    host_text = str(host_value)
    parsed = urllib.parse.urlparse(host_text if "://" in host_text else f"mqtt://{host_text}")
    host = parsed.hostname or host_text.split("/")[0].split(":")[0]
    if not host:
        raise SystemExit("OpenAPI MQTT metadata host could not be parsed")

    path_value = find_first_value(payload, ("mqttUrl", "websocketPath", "wsPath", "path"))
    path = ""
    if path_value:
        path_text = str(path_value)
        path_parsed = urllib.parse.urlparse(path_text)
        path = path_parsed.path if path_parsed.scheme else path_text
    if not path and parsed.path and parsed.path != "/":
        path = parsed.path
    if path and not path.startswith("/"):
        path = "/" + path

    scheme = (parsed.scheme or "").lower()
    transport = "websockets" if scheme in {"ws", "wss"} or path else "tcp"
    tls = scheme in {"wss", "mqtts", "ssl"} or parse_bool(find_first_value(payload, ("tls", "ssl", "useTls")))
    if parsed.port:
        port = parsed.port
    elif scheme == "wss":
        port = 443
    elif tls:
        port = 8883
    else:
        port = 1883

    topics = collect_string_values(payload, ("subTopics", "topics", "topicList", "subscribeTopics", "topic"))
    username = find_first_value(payload, ("userName", "username", "userId", "mqttUserName", "mqttUsername"))
    password = find_first_value(payload, ("pwdInfo", "password", "mqttPassword", "passWord"))
    client_id = find_first_value(payload, ("clientId", "clientID", "mqttClientId"))

    return {
        "host": host,
        "port": int(port),
        "transport": transport,
        "tls": tls,
        "path": path or None,
        "username": str(username) if username not in (None, "") else None,
        "password": str(password) if password not in (None, "") else None,
        "clientId": str(client_id) if client_id not in (None, "") else None,
        "topics": topics,
    }


def mqtt_metadata_summary(metadata: dict[str, Any]) -> dict[str, Any]:
    return {
        "host": "present" if metadata.get("host") else "missing",
        "port": "present" if metadata.get("port") else "missing",
        "transport": metadata.get("transport") or "unknown",
        "tls": bool(metadata.get("tls")),
        "websocketPath": "present" if metadata.get("path") else "missing",
        "username": "present" if metadata.get("username") else "missing",
        "password": "present" if metadata.get("password") else "missing",
        "topicCount": len(metadata.get("topics") or []),
    }


def print_mqtt_metadata_summary(metadata: dict[str, Any]) -> None:
    summary = mqtt_metadata_summary(metadata)
    print("mqtt metadata: ok")
    print(f"host: {summary['host']}")
    print(f"port: {summary['port']}")
    print(f"transport: {summary['transport']}")
    print(f"tls: {summary['tls']}")
    print(f"websocket path: {summary['websocketPath']}")
    print(f"username: {summary['username']}")
    print(f"password: {summary['password']}")
    print(f"topics: {summary['topicCount']}")


def mqtt_capacity_percent(value: Any) -> int | None:
    if isinstance(value, dict):
        for key in ("soc", "batterySoc", "capacityPercent", "battery"):
            parsed = store.bounded_int(value.get(key), 0, 100)
            if parsed is not None:
                return parsed

        unit = str(value.get("unit") or value.get("type") or "").upper()
        if unit in {"PERCENTAGE", "PERCENT", "%"}:
            for key in ("rawValue", "value", "percent", "percentage"):
                parsed = store.bounded_int(value.get(key), 0, 100)
                if parsed is not None:
                    return parsed

        for key in ("capacityRemaining", "batteryRemaining", "capacity"):
            parsed = mqtt_capacity_percent(value.get(key))
            if parsed is not None:
                return parsed

        for item in value.values():
            parsed = mqtt_capacity_percent(item)
            if parsed is not None:
                return parsed
    elif isinstance(value, list):
        for item in value:
            parsed = mqtt_capacity_percent(item)
            if parsed is not None:
                return parsed
    return None


def mqtt_message_snapshot(topic: str, payload: bytes) -> dict[str, Any]:
    import hashlib

    payload_hash = hashlib.sha256(payload).hexdigest()
    snapshot: dict[str, Any] = {
        "topicHash": hashlib.sha256(topic.encode("utf-8")).hexdigest()[:16],
        "payloadBytes": len(payload),
        "payloadSha256": payload_hash,
    }
    try:
        parsed = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        snapshot["payloadShape"] = "binary"
        snapshot["messageClasses"] = store.mqtt_snapshot_classes(snapshot)
        return snapshot
    snapshot["payloadShape"] = "json"
    sanitized = store.sanitize_operational_payload(parsed)
    snapshot["payloadKeys"] = store.key_list(sanitized)
    safe_fields: dict[str, Any] = {}

    def walk(item: Any) -> None:
        if isinstance(item, dict):
            for key, value in item.items():
                if (
                    key in MQTT_SAFE_VALUE_KEYS
                    and isinstance(value, (str, int, float, bool))
                    and not store.is_sensitive_payload_key(key)
                ):
                    safe_fields.setdefault(key, value)
                walk(value)
        elif isinstance(item, list):
            for child in item:
                walk(child)

    walk(sanitized)
    capacity_percent = mqtt_capacity_percent(sanitized)
    if capacity_percent is not None:
        safe_fields.setdefault("capacityPercent", capacity_percent)
    if safe_fields:
        snapshot["safeFields"] = safe_fields
    snapshot["messageClasses"] = store.mqtt_snapshot_classes(snapshot)
    return snapshot


def mqtt_client_kwargs(mqtt_module: Any, metadata: dict[str, Any]) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "client_id": metadata.get("clientId") or "",
        "transport": metadata.get("transport") or "tcp",
    }
    callback_api = getattr(mqtt_module, "CallbackAPIVersion", None)
    callback_v2 = getattr(callback_api, "VERSION2", None) if callback_api is not None else None
    if callback_v2 is not None:
        kwargs["callback_api_version"] = callback_v2
    return kwargs


def mqtt_connect_success(reason_code: Any) -> bool:
    if isinstance(reason_code, int):
        return reason_code == 0
    try:
        return int(reason_code) == 0
    except (TypeError, ValueError):
        return str(reason_code).lower() in {"success", "0"}


def build_ssl_context() -> ssl.SSLContext:
    try:
        import certifi  # type: ignore

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def run_mqtt_listener(
    *,
    metadata: dict[str, Any],
    max_messages: int,
    duration: float,
    on_message: Callable[[str, bytes, int], str | None],
) -> int:
    try:
        import paho.mqtt.client as mqtt  # type: ignore
    except ModuleNotFoundError as exc:
        raise SystemExit("paho-mqtt is not installed; run python -m pip install -r requirements.txt") from exc

    topics = metadata.get("topics") or []
    if not topics:
        raise SystemExit("MQTT metadata has no subscription topics")

    received = 0
    connect_failed = False
    done = threading.Event()
    client = mqtt.Client(**mqtt_client_kwargs(mqtt, metadata))
    if metadata.get("username") or metadata.get("password"):
        client.username_pw_set(metadata.get("username"), metadata.get("password"))
    if metadata.get("path") and hasattr(client, "ws_set_options"):
        client.ws_set_options(path=metadata["path"])
    if metadata.get("tls"):
        client.tls_set_context(build_ssl_context())

    def handle_connect(client, userdata, flags, reason_code, properties=None):  # noqa: ANN001
        nonlocal connect_failed
        if not mqtt_connect_success(reason_code):
            connect_failed = True
            print("mqtt connect failed", file=sys.stderr)
            done.set()
            return
        for topic in topics:
            client.subscribe(topic)
        print(f"mqtt connected; subscribed_topics={len(topics)}")

    def handle_message(client, userdata, message):  # noqa: ANN001
        nonlocal received
        received += 1
        summary = on_message(message.topic, bytes(message.payload), received)
        if summary:
            print(summary)
        if max_messages and received >= max_messages:
            done.set()

    client.on_connect = handle_connect
    client.on_message = handle_message
    try:
        client.connect(str(metadata["host"]), int(metadata["port"]), keepalive=60)
        client.loop_start()
        done.wait(timeout=duration if duration > 0 else None)
    except Exception as exc:
        raise SystemExit(f"MQTT listener failed ({exc.__class__.__name__})") from exc
    finally:
        with contextlib.suppress(Exception):
            client.loop_stop()
        with contextlib.suppress(Exception):
            client.disconnect()
    if connect_failed:
        raise SystemExit("MQTT listener failed (ConnectFailed)")
    return received
