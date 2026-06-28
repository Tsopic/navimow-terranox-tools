import json
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import navimow_viewer_server as viewer_server
import navimow_live_status as live_status
import build_navimow_map_viewer as viewer
import navimow_live_sync as live_sync
from test_navimow_map_viewer import create_fixture_db


def make_viewer_dir(tmp_path: Path) -> Path:
    root = tmp_path / "viewer"
    (root / "assets").mkdir(parents=True)
    (root / "index.html").write_text("<!doctype html><title>viewer</title>", encoding="utf-8")
    (root / "navimow-map-data.js").write_text("window.NAVIMOW_MAP_DATA = {secret: 'do-not-export'};", encoding="utf-8")
    (root / "navimow-live-status.json").write_text(
        json.dumps(
            {
                "version": 1,
                "generatedAt": "2026-06-27T12:00:00+00:00",
                "layoutVersion": "layout-a",
                "map": {"name": "Fixture", "areaCount": 3, "secret": "do-not-export"},
                "schedule": {"baseSnapshotId": 5, "baseObservedAt": "2026-06-27T12:00:00+00:00"},
                "areaStatus": {
                    "2": {
                        "id": 2,
                        "name": "do-not-export",
                        "points": [[1, 2]],
                        "lastMow": {
                            "status": "partial",
                            "lastAt": "2026-06-27T11:55:00+00:00",
                            "partitionPercentage": 45,
                            "finishedAreaM2": 45.0,
                            "areaM2": 100.0,
                            "raw_json": "do-not-export",
                        },
                        "cutting": {
                            "areaHeightMm": None,
                            "effectiveHeightMm": 70,
                            "source": "global mower height",
                            "heightRaw": "do-not-export",
                        },
                        "live": {
                            "active": True,
                            "state": "isRunning",
                            "mowingPercentage": 55,
                            "currentPartitionId": 2,
                            "observedAt": "2026-06-27T12:01:00+00:00",
                            "latitude": "do-not-export",
                        },
                    }
                },
                "mower": {
                    "name": "do-not-export",
                    "stateCode": "0102",
                    "battery": {"soc": 73, "secret": "do-not-export"},
                    "cutting": {"heightMm": 70, "supportedMm": [60, 70], "heightRaw": "do-not-export"},
                    "liveLocation": {"positionPixel": [10, 20], "mowingPercentage": 62, "latitude": "do-not-export"},
                    "routeInsights": {
                        "openapiAuth": {
                            "observedAt": "2026-06-27T12:00:00+00:00",
                            "deviceCount": 1,
                            "devices": [{"deviceHash": "do-not-export"}],
                        },
                        "mqtt": {
                            "observedAt": "2026-06-27T12:00:00+00:00",
                            "configured": True,
                            "topicCount": 2,
                            "topics": ["do-not-export"],
                            "userHash": "do-not-export",
                        },
                        "mqttMessages": {
                            "observedAt": "2026-06-27T12:01:00+00:00",
                            "totalMessages": 3,
                            "observedTopicCount": 2,
                            "payloadShapes": {"json": 3},
                            "messageClasses": {"state": 2, "battery": 1},
                            "statusSnapshotCount": 2,
                            "latest": {
                                "observedAt": "2026-06-27T12:01:00+00:00",
                                "payloadShape": "json",
                                "messageClasses": ["state", "battery"],
                                "payloadKeys": ["vehicleState", "soc"],
                                "payloadBytes": 123,
                                "topicHash": "do-not-export",
                                "payloadSha256": "do-not-export",
                            },
                            "topicHash": "do-not-export",
                            "payloadSha256": "do-not-export",
                        },
                    },
                    "routeSnapshots": {
                        "openapi-mqtt-info": {
                            "observedAt": "2026-06-27T12:00:00+00:00",
                            "itemCount": 1,
                            "shape": "object",
                            "keys": ["do-not-export"],
                        },
                        "do-not-export": {
                            "observedAt": "2026-06-27T12:00:00+00:00",
                            "itemCount": 99,
                        },
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    (root / "assets" / "navimow-map.js").write_text("console.log('viewer');", encoding="utf-8")
    (root / "assets" / "navimow-map.css").write_text("body { color: white; }", encoding="utf-8")
    (root / "config.local.json").write_text('{"token":"do-not-serve"}', encoding="utf-8")
    (root / ".hidden").write_text("do-not-serve", encoding="utf-8")
    return root


def start_server(root: Path):
    server = ThreadingHTTPServer(("127.0.0.1", 0), viewer_server.make_handler(root))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread, f"http://127.0.0.1:{server.server_address[1]}"


def stop_server(server, thread) -> None:
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


def parse_sse_events(text: str) -> list[dict[str, object]]:
    events: list[dict[str, object]] = []
    for block in text.strip().split("\n\n"):
        if not block:
            continue
        event_name = "message"
        data_lines: list[str] = []
        for line in block.splitlines():
            if line.startswith("event: "):
                event_name = line.removeprefix("event: ").strip()
            elif line.startswith("data: "):
                data_lines.append(line.removeprefix("data: "))
        data = json.loads("\n".join(data_lines)) if data_lines else {}
        events.append({"event": event_name, "data": data})
    return events


def test_viewer_status_is_metadata_only(tmp_path):
    root = make_viewer_dir(tmp_path)

    status = viewer_server.viewer_status(root)
    encoded = json.dumps(status)

    assert status["version"]
    assert status["reloadVersion"]
    assert status["liveStatusVersion"]
    assert status["layoutVersion"] == "layout-a"
    assert {item["path"] for item in status["files"]} == set(viewer_server.RELOAD_FILES)
    assert status["liveStatus"]["path"] == viewer_server.LIVE_STATUS_FILE
    assert "do-not-export" not in encoded
    assert "Metadata only" in status["privacy"]


def test_status_endpoint_serves_metadata_json(tmp_path):
    root = make_viewer_dir(tmp_path)
    server, thread, base_url = start_server(root)
    try:
        with urllib.request.urlopen(f"{base_url}/__navimow/status", timeout=2) as response:
            payload = json.loads(response.read().decode())
    finally:
        stop_server(server, thread)

    assert payload["version"]
    assert payload["files"][0]["path"] == "index.html"
    assert payload["liveStatus"]["available"] is True
    assert "do-not-export" not in json.dumps(payload)


def test_live_status_endpoint_serves_sanitized_status_json(tmp_path):
    root = make_viewer_dir(tmp_path)
    server, thread, base_url = start_server(root)
    try:
        with urllib.request.urlopen(f"{base_url}/__navimow/live-status", timeout=2) as response:
            payload = json.loads(response.read().decode())
    finally:
        stop_server(server, thread)

    encoded = json.dumps(payload)
    assert payload["layoutVersion"] == "layout-a"
    assert payload["mower"]["battery"]["soc"] == 73
    assert payload["mower"]["cutting"]["heightMm"] == 70
    assert payload["mower"]["liveLocation"]["mowingPercentage"] == 62
    assert payload["mower"]["routeInsights"]["mqtt"]["topicCount"] == 2
    assert payload["mower"]["routeInsights"]["mqttMessages"]["messageClasses"]["state"] == 2
    assert payload["mower"]["routeInsights"]["mqttMessages"]["latest"]["payloadKeys"] == ["vehicleState", "soc"]
    assert payload["mower"]["routeInsights"]["openapiAuth"]["deviceCount"] == 1
    assert payload["areaStatus"]["2"]["lastMow"]["partitionPercentage"] == 45
    assert payload["areaStatus"]["2"]["cutting"]["effectiveHeightMm"] == 70
    assert payload["areaStatus"]["2"]["live"]["active"] is True
    assert payload["areaStatus"]["2"]["live"]["mowingPercentage"] == 55
    assert "do-not-export" not in encoded
    assert "deviceHash" not in encoded
    assert "userHash" not in encoded
    assert "topics" not in encoded
    assert "topicHash" not in encoded
    assert "payloadSha256" not in encoded
    assert "points" not in encoded


def test_viewer_server_uses_shared_live_status_sanitizer(tmp_path):
    root = make_viewer_dir(tmp_path)

    server_payload, server_available = viewer_server.safe_live_status_payload(root)
    shared_payload, shared_available = live_status.safe_live_status_payload(root)

    assert server_available is True
    assert shared_available is True
    assert server_payload == shared_payload
    assert "do-not-export" not in json.dumps(server_payload)


def test_events_endpoint_emits_live_status_when_status_file_changes(tmp_path):
    root = make_viewer_dir(tmp_path)
    server, thread, base_url = start_server(root)

    def update_status_file() -> None:
        time.sleep(0.25)
        path = root / "navimow-live-status.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["generatedAt"] = "2026-06-27T12:02:00+00:00"
        payload["mower"]["battery"]["soc"] = 72
        path.write_text(json.dumps(payload), encoding="utf-8")

    updater = threading.Thread(target=update_status_file, daemon=True)
    updater.start()
    try:
        with urllib.request.urlopen(f"{base_url}/__navimow/events?interval=0.1&max=3", timeout=3) as response:
            body = response.read().decode()
    finally:
        stop_server(server, thread)
        updater.join(timeout=1)

    assert body.count("event: live-status") >= 2
    assert "do-not-export" not in body


def test_live_status_endpoint_handles_missing_file(tmp_path):
    root = make_viewer_dir(tmp_path)
    (root / viewer_server.LIVE_STATUS_FILE).unlink()
    server, thread, base_url = start_server(root)
    try:
        try:
            urllib.request.urlopen(f"{base_url}/__navimow/live-status", timeout=2)
        except urllib.error.HTTPError as exc:
            payload = json.loads(exc.read().decode())
            assert exc.code == 404
        else:
            raise AssertionError("missing live status file should return 404")
    finally:
        stop_server(server, thread)

    assert payload["available"] is False
    assert "Metadata only" in payload["privacy"]


def test_events_endpoint_emits_single_viewer_update(tmp_path):
    root = make_viewer_dir(tmp_path)
    server, thread, base_url = start_server(root)
    try:
        with urllib.request.urlopen(f"{base_url}/__navimow/events?max=1&interval=0.1", timeout=2) as response:
            text = response.read().decode()
    finally:
        stop_server(server, thread)

    assert "event: viewer-update" in text
    assert "data:" in text
    assert "do-not-export" not in text
    assert "files" not in text


def test_events_endpoint_emits_compact_live_status_event(tmp_path):
    root = make_viewer_dir(tmp_path)
    server, thread, base_url = start_server(root)
    try:
        with urllib.request.urlopen(f"{base_url}/__navimow/events?max=2&interval=0.1", timeout=2) as response:
            text = response.read().decode()
    finally:
        stop_server(server, thread)

    assert "event: viewer-update" in text
    assert "event: live-status" in text
    assert "layout-a" in text
    assert "do-not-export" not in text
    assert "files" not in text


def test_events_endpoint_can_embed_sanitized_live_status_payload(tmp_path):
    root = make_viewer_dir(tmp_path)
    server, thread, base_url = start_server(root)
    try:
        with urllib.request.urlopen(f"{base_url}/__navimow/events?max=2&interval=0.1&live=full", timeout=2) as response:
            text = response.read().decode()
    finally:
        stop_server(server, thread)

    live_events = [event for event in parse_sse_events(text) if event["event"] == "live-status"]
    assert live_events
    payload = live_events[0]["data"]
    assert payload["status"]["mower"]["battery"]["soc"] == 73
    assert payload["status"]["mower"]["routeInsights"]["mqttMessages"]["totalMessages"] == 3
    assert "do-not-export" not in text
    assert "topicHash" not in text
    assert "payloadSha256" not in text


def test_mqtt_replay_live_status_is_served_and_evented_without_secrets(tmp_path):
    db = create_fixture_db(tmp_path)
    root = tmp_path / "viewer"
    con = live_sync.store.connect(db)
    data = viewer.build_data(con, root, include_satellite=False)
    viewer.write_viewer(root, data)
    live_sync.ingest_mqtt_message(
        db=db,
        topic="secret/topic/replay",
        payload=json.dumps(
            {
                "vehicleState": "isRunning",
                "workStatus": "MOWING",
                "soc": 82,
                "descriptiveCapacityRemaining": "HIGH",
                "currentPartitionId": 3,
                "mowingPercentage": 91,
                "reportTime": 1782570000000,
                "token": "secret-token",
                "signedUrl": "https://signed.example/private",
                "latitude": "57.0",
            }
        ).encode(),
        observed_at="2026-06-27T12:50:00+00:00",
    )
    live_sync.update_live_status_artifact(db=db, output=root)

    server, thread, base_url = start_server(root)
    try:
        with urllib.request.urlopen(f"{base_url}/__navimow/live-status", timeout=2) as response:
            payload = json.loads(response.read().decode())
        with urllib.request.urlopen(f"{base_url}/__navimow/events?max=2&interval=0.1", timeout=2) as response:
            events = response.read().decode()
    finally:
        stop_server(server, thread)

    encoded = json.dumps(payload, ensure_ascii=False) + events
    assert payload["mower"]["routeInsights"]["mqttStatus"]["batterySoc"] == 82
    assert payload["mower"]["routeInsights"]["mqttStatus"]["currentPartitionId"] == 3
    assert payload["mower"]["routeInsights"]["mqttStatus"]["mowingPercentage"] == 91
    assert payload["areaStatus"]["3"]["live"]["active"] is True
    assert payload["areaStatus"]["3"]["live"]["mowingPercentage"] == 91
    assert "event: live-status" in events
    for secret in [
        "secret/topic/replay",
        "secret-token",
        "signed.example",
        "latitude",
        "topicHash",
        "payloadSha256",
        "deviceHash",
        "topics",
        "points",
    ]:
        assert secret not in encoded


def test_events_stream_observes_mqtt_refresh_after_stream_is_open(tmp_path):
    db = create_fixture_db(tmp_path)
    root = tmp_path / "viewer"
    con = live_sync.store.connect(db)
    data = viewer.build_data(con, root, include_satellite=False)
    viewer.write_viewer(root, data)
    server, thread, base_url = start_server(root)

    def update_from_mqtt() -> None:
        time.sleep(0.25)
        live_sync.ingest_mqtt_message(
            db=db,
            topic="secret/topic/stream",
            payload=json.dumps(
                {
                    "vehicleState": "isRunning",
                    "workStatus": "MOWING",
                    "soc": 83,
                    "currentPartitionId": 3,
                    "mowingPercentage": 92,
                    "reportTime": 1782573000000,
                    "token": "secret-token",
                    "signedUrl": "https://signed.example/private",
                    "latitude": "57.0",
                }
            ).encode(),
            observed_at="2026-06-27T13:10:00+00:00",
        )
        live_sync.update_live_status_artifact(db=db, output=root)

    updater = threading.Thread(target=update_from_mqtt, daemon=True)
    updater.start()
    try:
        with urllib.request.urlopen(f"{base_url}/__navimow/events?interval=0.1&max=3&live=full", timeout=3) as response:
            events = response.read().decode()
        with urllib.request.urlopen(f"{base_url}/__navimow/live-status", timeout=2) as response:
            payload = json.loads(response.read().decode())
    finally:
        stop_server(server, thread)
        updater.join(timeout=1)

    encoded = events + json.dumps(payload, ensure_ascii=False)
    assert events.count("event: live-status") >= 2
    live_event_payloads = [event["data"].get("status") for event in parse_sse_events(events) if event["event"] == "live-status"]
    assert any(
        event_payload
        and event_payload["mower"]["routeInsights"]["mqttStatus"]["batterySoc"] == 83
        and event_payload["areaStatus"]["3"]["live"]["mowingPercentage"] == 92
        for event_payload in live_event_payloads
    )
    assert payload["mower"]["routeInsights"]["mqttStatus"]["batterySoc"] == 83
    assert payload["mower"]["routeInsights"]["mqttStatus"]["currentPartitionId"] == 3
    assert payload["areaStatus"]["3"]["live"]["active"] is True
    assert payload["areaStatus"]["3"]["live"]["mowingPercentage"] == 92
    for secret in ["secret/topic/stream", "secret-token", "signed.example", "latitude", "topicHash", "payloadSha256"]:
        assert secret not in encoded


def test_default_host_is_localhost():
    parser = viewer_server.build_parser()
    args = parser.parse_args([])

    assert args.host == "127.0.0.1"
    assert args.auto_port is False


def test_parser_accepts_auto_port():
    parser = viewer_server.build_parser()
    args = parser.parse_args(["--auto-port"])

    assert args.auto_port is True


def test_create_server_auto_port_skips_busy_port(tmp_path):
    root = make_viewer_dir(tmp_path)
    busy_server, busy_thread, _ = start_server(root)
    try:
        busy_port = busy_server.server_address[1]
        server = viewer_server.create_server(
            directory=root.resolve(),
            host="127.0.0.1",
            port=busy_port,
            auto_port=True,
            max_port_attempts=3,
        )
        try:
            assert server.server_address[1] != busy_port
            assert busy_port < server.server_address[1] <= busy_port + 2
        finally:
            server.server_close()
    finally:
        stop_server(busy_server, busy_thread)


def test_private_paths_are_forbidden(tmp_path):
    root = make_viewer_dir(tmp_path)
    server, thread, base_url = start_server(root)
    try:
        for path in ["/config.local.json", "/.hidden", "/../config/navimow-live-sync.local.json"]:
            try:
                urllib.request.urlopen(f"{base_url}{path}", timeout=2)
            except urllib.error.HTTPError as exc:
                assert exc.code == 403
            else:
                raise AssertionError(f"{path} should be forbidden")
    finally:
        stop_server(server, thread)


def test_private_head_requests_are_forbidden(tmp_path):
    root = make_viewer_dir(tmp_path)
    server, thread, base_url = start_server(root)
    try:
        request = urllib.request.Request(f"{base_url}/config.local.json", method="HEAD")
        try:
            urllib.request.urlopen(request, timeout=2)
        except urllib.error.HTTPError as exc:
            assert exc.code == 403
        else:
            raise AssertionError("private HEAD request should be forbidden")
    finally:
        stop_server(server, thread)


def test_static_files_are_served_with_no_store_headers(tmp_path):
    root = make_viewer_dir(tmp_path)
    server, thread, base_url = start_server(root)
    try:
        with urllib.request.urlopen(f"{base_url}/index.html", timeout=2) as response:
            assert response.headers["Cache-Control"] == "no-store"
        with urllib.request.urlopen(f"{base_url}/navimow-map-data.js", timeout=2) as response:
            assert response.headers["Cache-Control"] == "no-store"
    finally:
        stop_server(server, thread)
