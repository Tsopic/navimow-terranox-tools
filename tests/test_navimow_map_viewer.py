import json
import sqlite3
import zipfile
from pathlib import Path

import sys

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import build_navimow_map_viewer as viewer
import navimow_state_store as store


def make_terrain_artifact(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (120, 120), "#375f34")
    image_path = path.parent / "terrain.webp"
    image.save(image_path, "WEBP")
    with zipfile.ZipFile(path, "w") as archive:
        archive.write(image_path, "terrain.webp")


def create_fixture_db(tmp_path: Path) -> Path:
    db = tmp_path / "navimow.sqlite"
    con = store.connect(db)
    source_id = store.add_source(con, ROOT / "README.md", "fixture")
    store.upsert_device(con, "TESTSN12345", vehicle_type="300000043", name="Fixture mower", model="CM120M1")
    artifact = tmp_path / "resource.bin"
    make_terrain_artifact(artifact)

    con.execute(
        """
        INSERT INTO map_artifacts(vehicle_sn, version, file_path, sha256, size_bytes, content_kind, parsed_status, imported_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        ("TESTSN12345", 1, str(artifact), "fixture", artifact.stat().st_size, "zip", "parsed", "now"),
    )
    artifact_id = con.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    con.execute(
        """
        INSERT INTO map_render_metadata(
            artifact_id, member_path, width, height, min_x, max_x, min_y, max_y,
            pixel_per_meter, terrain_view_image_name, raw_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (artifact_id, "meta.json", 120, 120, 0, 12, 0, 12, 10, "terrain.webp", "{}"),
    )

    detail = {
        "name": "Fixture map",
        "area": 300.0,
        "sub_maps": [
            {
                "id": 1,
                "name": "Front",
                "area": 100.0,
                "type": "SUB_MAP",
                "contain_obstacles_id": [1],
                "elements": [
                    {
                        "id": 1,
                        "name": "Front",
                        "type": "BOUNDARY",
                        "area": 100.0,
                        "height_set": 60,
                        "mow_edge": 1,
                        "obstacle_mow_edge": 1,
                        "boundary_type": 5,
                        "rec_base_angle": 0,
                        "clock_direction": 1,
                        "avai_segs": 63,
                        "points": [[1, 1, 1], [5, 1, 1], [5, 5, 1], [1, 5, 1]],
                    }
                ],
            },
            {
                "id": 2,
                "name": "Back",
                "area": 100.0,
                "type": "SUB_MAP",
                "contain_obstacles_id": [],
                "elements": [
                    {
                        "id": 2,
                        "name": "Back",
                        "type": "BOUNDARY",
                        "area": 100.0,
                        "height_set": 256,
                        "mow_edge": 1,
                        "obstacle_mow_edge": 1,
                        "boundary_type": 5,
                        "rec_base_angle": 0,
                        "clock_direction": 1,
                        "avai_segs": 63,
                        "points": [[6, 1, 1], [10, 1, 1], [10, 5, 1], [6, 5, 1]],
                    }
                ],
            },
            {
                "id": 3,
                "name": "Orchard",
                "area": 100.0,
                "type": "SUB_MAP",
                "contain_obstacles_id": [],
                "elements": [
                    {
                        "id": 3,
                        "name": "Orchard",
                        "type": "BOUNDARY",
                        "area": 100.0,
                        "height_set": 256,
                        "mow_edge": 1,
                        "obstacle_mow_edge": 1,
                        "boundary_type": 5,
                        "rec_base_angle": 0,
                        "clock_direction": 1,
                        "avai_segs": 63,
                        "points": [[1, 6, 1], [5, 6, 1], [5, 10, 1], [1, 10, 1]],
                    }
                ],
            },
        ],
        "obstacles": [
            {
                "id": 1,
                "type": "OBSTACLE",
                "area": 2.0,
                "status": 1,
                "points": [[2, 2, 1], [3, 2, 1], [3, 3, 1], [2, 3, 1]],
            }
        ],
    }
    con.execute(
        """
        INSERT INTO map_detail_snapshots(source_id, vehicle_sn, observed_at, event_hash, map_name, total_area, detail_area, raw_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (source_id, "TESTSN12345", "2026-06-27T12:00:00+00:00", "map", "Fixture map", 300.0, 300.0, json.dumps({"detail": detail})),
    )

    con.execute(
        """
        INSERT INTO schedule_snapshots(source_id, vehicle_sn, observed_at, event_hash, raw_json)
        VALUES (?, ?, ?, ?, ?)
        """,
        (source_id, "TESTSN12345", "2026-06-27T12:00:00+00:00", "schedule", "[]"),
    )
    snapshot_id = con.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    for day in range(1, 8):
        con.execute("INSERT INTO schedule_days(snapshot_id, day, open) VALUES (?, ?, ?)", (snapshot_id, day, 1 if day == 3 else 0))
        day_id = con.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
        if day == 3:
            con.execute(
                "INSERT INTO schedule_periods(day_id, period_index, start_tick, end_tick, partition_ids_json) VALUES (?, ?, ?, ?, ?)",
                (day_id, 0, 16, 88, json.dumps([1, 2])),
            )

    store.insert_device_state_snapshot(
        con,
        source_id=source_id,
        data={"vehicle_sn": "TESTSN12345", "vehicle_type": 1, "soc": "73", "soh": 100, "vehicle_state": "0102"},
        observed_at="2026-06-27T12:00:00+00:00",
    )
    store.insert_device_info_snapshot(
        con,
        source_id=source_id,
        data={
            "vehicle_sn": "TESTSN12345",
            "selfDefinedName": "Fixture mower",
            "model": "CM120M1",
            "mowingHeightList": [20, 25, 30, 60, 70],
            "nonstandardVehicleConfig": {"hasScreen": "1", "firmwareVersion": {"ECU": "1"}},
        },
        observed_at="2026-06-27T12:00:00+00:00",
    )
    store.insert_area_setting_snapshot(
        con,
        source_id=source_id,
        vehicle_sn="TESTSN12345",
        observed_at="2026-06-27T12:00:00+00:00",
        partition_length=None,
        partition_id_list=None,
        mowing_zone_list_text=None,
        mowing_zone_text=None,
        raw_text=json.dumps({"height": 70, "cutterHeight": 0}),
    )
    store.insert_trail_time_snapshot(
        con,
        source_id=source_id,
        vehicle_sn="TESTSN12345",
        observed_at="2026-06-27T12:00:00+00:00",
        line_no=1,
        entries=[
            {"partitionId": 1, "startTime": 1782560000, "endTime": 1782563600, "area": 100.0, "finishedArea": 100.0, "partitionPercentage": 100},
            {"partitionId": 2, "startTime": 1782560000, "endTime": 1782561800, "area": 100.0, "finishedArea": 45.0, "partitionPercentage": 45},
            {"partitionId": 3, "startTime": 0, "endTime": 0, "area": 100.0, "finishedArea": 0.0, "partitionPercentage": 0},
        ],
    )
    store.insert_live_location_snapshot(
        con,
        source_id=source_id,
        vehicle_sn="TESTSN12345",
        observed_at="2026-06-27T12:00:00+00:00",
        line_no=1,
        data={"posture_x": "2.5", "posture_y": "2.5", "posture_theta": "0.1", "mowing_percentage": 62, "latitude": "57.0"},
    )
    store.insert_route_snapshot_record(
        con,
        source_id=source_id,
        vehicle_sn="TESTSN12345",
        observed_at="2026-06-27T12:00:00+00:00",
        route_alias="weather",
        data={
            "rainState": 0,
            "frostState": 0,
            "latitude": "57.0",
            "token": "secret-token",
            "signedUrl": "https://signed.example/private",
        },
    )
    store.insert_route_snapshot_record(
        con,
        source_id=source_id,
        vehicle_sn=None,
        observed_at="2026-06-27T12:00:00+00:00",
        route_alias="auth-list",
        data=[
            {
                "vehicle_sn": "TESTSN12345",
                "selfDefinedName": "Fixture mower",
                "vehicle_state": "0102",
                "soc": 73,
                "auth_uid": "secret-owner",
            }
        ],
    )
    store.insert_route_snapshot_record(
        con,
        source_id=source_id,
        vehicle_sn=None,
        observed_at="2026-06-27T12:01:00+00:00",
        route_alias="openapi-auth-list",
        data={
            "payload": {
                "devices": [
                    {
                        "id": "openapi-secret-device",
                        "name": "Fixture mower",
                        "model": "CM120M1",
                        "firmware": "1.2.3",
                    }
                ]
            },
            "requestId": "request-id-secret",
        },
    )
    store.insert_route_snapshot_record(
        con,
        source_id=source_id,
        vehicle_sn=None,
        observed_at="2026-06-27T12:02:00+00:00",
        route_alias="openapi-vehicle-status",
        data={
            "payload": {
                "devices": [
                    {
                        "id": "openapi-secret-device",
                        "vehicleState": "isRunning",
                        "capacityRemaining": [{"rawValue": 57, "unit": "PERCENTAGE"}],
                        "descriptiveCapacityRemaining": "MEDIUM",
                    }
                ]
            },
            "requestId": "request-id-secret",
        },
    )
    store.insert_route_snapshot_record(
        con,
        source_id=source_id,
        vehicle_sn=None,
        observed_at="2026-06-27T12:03:00+00:00",
        route_alias="openapi-mqtt-info",
        data={
            "configured": True,
            "topicCount": 2,
            "transport": "websockets",
            "tls": True,
            "broker": "present",
            "websocketPath": "present",
            "credentialStatus": "present",
        },
    )
    store.insert_route_snapshot_record(
        con,
        source_id=source_id,
        vehicle_sn=None,
        observed_at="2026-06-27T12:04:00+00:00",
        route_alias="mqtt-message",
        data={
            "topicHash": "topic-hash",
            "payloadBytes": 128,
            "payloadSha256": "payload-hash",
            "payloadShape": "json",
            "payloadKeys": ["currentPartitionId", "mowingPercentage", "soc", "vehicleState"],
            "safeFields": {
                "vehicleState": "isRunning",
                "soc": 66,
                "descriptiveCapacityRemaining": "MEDIUM",
                "currentPartitionId": 2,
                "mowingPercentage": 55,
                "reportTime": 1782563600000,
            },
        },
    )
    con.commit()
    return db


def create_status_only_db(tmp_path: Path) -> Path:
    db = tmp_path / "navimow.sqlite"
    con = store.connect(db)
    source_id = store.add_source(con, ROOT / "README.md", "status-only-fixture")
    store.upsert_device(con, "STATUSSN12345", vehicle_type="300000043", name="Status mower", model="CM120M1")
    store.insert_device_state_snapshot(
        con,
        source_id=source_id,
        data={"vehicle_sn": "STATUSSN12345", "vehicle_type": 1, "soc": "64", "soh": 98, "vehicle_state": "0102"},
        observed_at="2026-06-27T13:00:00+00:00",
    )
    store.insert_device_info_snapshot(
        con,
        source_id=source_id,
        data={
            "vehicle_sn": "STATUSSN12345",
            "selfDefinedName": "Status mower",
            "model": "CM120M1",
            "mowingHeightList": [20, 25, 30, 55, 65],
            "nonstandardVehicleConfig": {"hasScreen": "1", "firmwareVersion": {"ECU": "1"}},
        },
        observed_at="2026-06-27T13:00:00+00:00",
    )
    store.insert_area_setting_snapshot(
        con,
        source_id=source_id,
        vehicle_sn="STATUSSN12345",
        observed_at="2026-06-27T13:00:00+00:00",
        partition_length=None,
        partition_id_list=None,
        mowing_zone_list_text=None,
        mowing_zone_text=None,
        raw_text=json.dumps({"height": 55, "cutterHeight": 0}),
    )
    store.insert_live_location_snapshot(
        con,
        source_id=source_id,
        vehicle_sn="STATUSSN12345",
        observed_at="2026-06-27T13:00:00+00:00",
        line_no=1,
        data={"posture_x": "2.5", "posture_y": "2.5", "posture_theta": "0.1", "mowing_percentage": 31},
    )
    store.insert_route_snapshot_record(
        con,
        source_id=source_id,
        vehicle_sn=None,
        observed_at="2026-06-27T13:01:00+00:00",
        route_alias="openapi-auth-list",
        data={
            "payload": {
                "devices": [
                    {
                        "id": "status-secret-device",
                        "name": "Status mower",
                        "model": "CM120M1",
                    }
                ]
            }
        },
    )
    store.insert_route_snapshot_record(
        con,
        source_id=source_id,
        vehicle_sn=None,
        observed_at="2026-06-27T13:02:00+00:00",
        route_alias="openapi-vehicle-status",
        data={
            "payload": {
                "devices": [
                    {
                        "id": "status-secret-device",
                        "vehicleState": "READY",
                        "capacityRemaining": [{"rawValue": 64, "unit": "PERCENTAGE"}],
                        "descriptiveCapacityRemaining": "HIGH",
                    }
                ]
            }
        },
    )
    store.insert_route_snapshot_record(
        con,
        source_id=source_id,
        vehicle_sn=None,
        observed_at="2026-06-27T13:03:00+00:00",
        route_alias="openapi-mqtt-info",
        data={"configured": True, "topicCount": 1, "credentialStatus": "present"},
    )
    con.commit()
    return db


def test_build_data_exports_sanitized_local_map(tmp_path):
    db = create_fixture_db(tmp_path)
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row

    data = viewer.build_data(con, tmp_path / "out", include_satellite=False)

    assert data["map"]["areaCount"] == 3
    assert data["map"]["obstacleCount"] == 1
    assert data["map"]["backgrounds"]["terrain"] == "assets/terrain.webp"
    assert data["map"]["backgrounds"]["satellite"] is None
    assert data["scheduleDraft"]["days"][2]["dayName"] == "Tuesday"
    optimizer = data["scheduleOptimizer"]
    assert optimizer["dryRunOnly"] is True
    assert optimizer["status"] == "browser_and_cli_preview_only"
    assert optimizer["defaultDay"] == 3
    assert optimizer["defaultStart"] == "04:00"
    assert optimizer["defaultEnd"] == "22:00"
    assert optimizer["defaultM2PerHour"] == 250
    assert optimizer["defaultMaxPeriodsPerDay"] == 4
    assert optimizer["defaultMinDaysBetween"] == 2
    assert optimizer["baseSnapshotId"] == data["scheduleDraft"]["baseSnapshotId"]
    assert optimizer["baseObservedAt"] == data["scheduleDraft"]["baseObservedAt"]
    assert "weather flags" in optimizer["inputs"]
    assert "/mowerbot/vehicle/set/send" in optimizer["blockedWrites"]
    assert data["mower"]["battery"]["soc"] == 73
    assert data["mower"]["cutting"]["heightMm"] == 70
    assert data["mower"]["liveLocation"]["mowingPercentage"] == 62
    assert data["mower"]["routeSnapshots"]["weather"]["itemCount"] == 1
    assert "rainState" in data["mower"]["routeSnapshots"]["weather"]["keys"]
    assert data["mower"]["routeSnapshots"]["auth-list"]["devices"][0]["vehicleHash"]
    assert data["mower"]["routeInsights"]["weather"]["flags"]["rainState"] == 0
    assert data["mower"]["routeInsights"]["openapiAuth"]["deviceCount"] == 1
    assert data["mower"]["routeInsights"]["openapiAuth"]["devices"][0]["deviceHash"]
    assert data["mower"]["routeInsights"]["openapiStatus"]["vehicleState"] == "isRunning"
    assert data["mower"]["routeInsights"]["openapiStatus"]["capacityPercent"] == 57
    assert data["mower"]["routeInsights"]["mqtt"]["topicCount"] == 2
    assert "userHash" not in data["mower"]["routeInsights"]["mqtt"]
    assert "topics" not in data["mower"]["routeInsights"]["mqtt"]
    assert data["mower"]["routeInsights"]["mqttStatus"]["state"] == "isRunning"
    assert data["mower"]["routeInsights"]["mqttStatus"]["batterySoc"] == 66
    assert data["mower"]["routeInsights"]["mqttStatus"]["currentPartitionId"] == 2
    assert data["mower"]["routeInsights"]["mqttStatus"]["mowingPercentage"] == 55
    assert data["mower"]["routeInsights"]["mqttStatus"]["reportAt"] == "2026-06-27T12:33:20+00:00"
    assert data["mower"]["routeInsights"]["mqttMessages"]["totalMessages"] == 1
    assert data["mower"]["routeInsights"]["mqttMessages"]["observedTopicCount"] == 1
    assert data["mower"]["routeInsights"]["mqttMessages"]["payloadShapes"] == {"json": 1}
    assert data["mower"]["routeInsights"]["mqttMessages"]["messageClasses"] == {
        "battery": 1,
        "progress": 1,
        "state": 1,
    }
    assert data["mower"]["routeInsights"]["mqttMessages"]["latest"]["messageClasses"] == [
        "state",
        "progress",
        "battery",
    ]
    assert data["mower"]["sync"]["batteryAndState"]["route"] == "/vehicle/vehicle/index2"
    assert data["mower"]["sync"]["batteryAndState"]["activeCadenceSeconds"] == 30
    assert data["mower"]["sync"]["liveLocation"]["activeCadenceSeconds"] == 5
    assert data["mower"]["sync"]["lastMowPerArea"]["status"] == "trail_time_index_normalized"
    assert any(area["cutting"]["areaHeightMm"] == 60 for area in data["areas"])
    assert data["areaStatus"]["1"]["cutting"]["effectiveHeightMm"] == 60
    assert data["areaStatus"]["2"]["cutting"]["effectiveHeightMm"] == 70
    assert data["areaStatus"]["2"]["live"]["active"] is True
    assert data["areaStatus"]["2"]["live"]["mowingPercentage"] == 55
    assert data["areaStatus"]["1"]["lastMow"]["partitionPercentage"] == 100
    last_mow_statuses = {area["lastMow"]["status"] for area in data["areas"]}
    assert {"completed", "partial", "no_mow_in_history"} <= last_mow_statuses
    assert data["liveStatus"]["layoutVersion"]

    encoded = json.dumps(data, ensure_ascii=False)
    forbidden = [
        "vehicle_sn",
        "signed_url",
        "auth_uid",
        "trace_id",
        "center_gps",
        "origin_gps",
        "ne_gps",
        "sw_gps",
        "blob.core.windows.net",
        "latitude",
        "secret-token",
        "secret-owner",
        "signed.example",
        "openapi-secret-device",
        "request-id-secret",
        "mqtt-secret-user",
        "mqtt.example",
    ]
    for token in forbidden:
        assert token not in encoded

    live_status = viewer.build_live_status(data)
    live_encoded = json.dumps(live_status, ensure_ascii=False)
    assert live_status["layoutVersion"] == data["liveStatus"]["layoutVersion"]
    assert live_status["mower"]["battery"]["soc"] == 73
    assert live_status["mower"]["cutting"]["heightMm"] == 70
    assert live_status["mower"]["liveLocation"]["mowingPercentage"] == 62
    assert live_status["mower"]["routeInsights"]["openapiAuth"]["deviceCount"] == 1
    assert live_status["mower"]["routeInsights"]["openapiStatus"]["capacityPercent"] == 57
    assert live_status["mower"]["routeInsights"]["mqtt"]["topicCount"] == 2
    assert live_status["mower"]["routeInsights"]["mqttStatus"]["batterySoc"] == 66
    assert live_status["mower"]["routeInsights"]["mqttStatus"]["currentPartitionId"] == 2
    assert live_status["mower"]["routeInsights"]["mqttMessages"]["observedTopicCount"] == 1
    assert live_status["mower"]["routeInsights"]["mqttMessages"]["messageClasses"]["state"] == 1
    assert live_status["mower"]["sync"]["liveLocation"]["activeCadenceSeconds"] == 5
    assert live_status["areaStatus"]["2"]["live"]["active"] is True
    assert live_status["areaStatus"]["2"]["live"]["mowingPercentage"] == 55
    assert live_status["areaStatus"]["2"]["lastMow"]["partitionPercentage"] == 45
    assert live_status["areaStatus"]["2"]["cutting"]["effectiveHeightMm"] == 70
    assert "deviceHash" not in live_encoded
    assert "userHash" not in live_encoded
    assert "topic-hash" not in live_encoded
    assert "payload-hash" not in live_encoded
    assert "topicHash" not in live_encoded
    assert "payloadSha256" not in live_encoded
    assert "topics" not in live_encoded
    assert "Fixture mower" not in live_encoded
    assert "CM120M1" not in live_encoded
    assert "Back" not in live_encoded
    for token in forbidden:
        assert token not in live_encoded


def test_build_data_uses_typed_openapi_rows_when_route_json_is_compacted(tmp_path):
    db = create_fixture_db(tmp_path)
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    con.execute(
        """
        UPDATE route_snapshot_records
        SET sanitized_json='{}'
        WHERE route_alias IN ('openapi-auth-list', 'openapi-vehicle-status')
        """
    )
    con.commit()

    data = viewer.build_data(con, tmp_path / "out", include_satellite=False)

    assert data["mower"]["routeInsights"]["openapiAuth"]["deviceCount"] == 1
    assert data["mower"]["routeInsights"]["openapiAuth"]["devices"][0]["deviceHash"]
    assert data["mower"]["routeInsights"]["openapiStatus"]["vehicleState"] == "isRunning"
    assert data["mower"]["routeInsights"]["openapiStatus"]["capacityPercent"] == 57
    encoded = json.dumps(data, ensure_ascii=False)
    assert "openapi-secret-device" not in encoded

    live_status = viewer.build_live_status(data)
    live_encoded = json.dumps(live_status, ensure_ascii=False)
    assert live_status["mower"]["routeInsights"]["openapiAuth"]["deviceCount"] == 1
    assert live_status["mower"]["routeInsights"]["openapiStatus"]["capacityPercent"] == 57
    assert "deviceHash" not in live_encoded
    assert "openapi-secret-device" not in live_encoded


def test_build_data_exports_status_only_without_map_rows(tmp_path):
    db = create_status_only_db(tmp_path)
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row

    data = viewer.build_data(con, tmp_path / "out", include_satellite=False, status_only=True)

    assert data["map"]["mode"] == "status_only"
    assert data["map"]["statusOnly"] is True
    assert data["map"]["areaCount"] == 0
    assert data["areas"] == []
    assert data["obstacles"] == []
    assert data["scheduleDraft"]["baseSnapshotId"] is None
    assert data["mower"]["battery"]["soc"] == 64
    assert data["mower"]["cutting"]["heightMm"] == 55
    assert data["mower"]["liveLocation"]["mowingPercentage"] == 31
    assert data["mower"]["liveLocation"]["positionPixel"] is None
    assert data["mower"]["routeInsights"]["openapiStatus"]["vehicleState"] == "READY"
    assert data["mower"]["routeInsights"]["openapiStatus"]["capacityPercent"] == 64
    assert data["mower"]["routeInsights"]["mqtt"]["topicCount"] == 1
    assert data["liveStatus"]["layoutVersion"]

    encoded = json.dumps(data, ensure_ascii=False)
    assert "status-secret-device" not in encoded
    assert "STATUSSN12345" not in encoded

    live_status = viewer.build_live_status(data)
    live_encoded = json.dumps(live_status, ensure_ascii=False)
    assert live_status["map"]["mode"] == "status_only"
    assert live_status["map"]["statusOnly"] is True
    assert live_status["mower"]["battery"]["soc"] == 64
    assert live_status["mower"]["routeInsights"]["openapiStatus"]["capacityPercent"] == 64
    assert "status-secret-device" not in live_encoded
    assert "STATUSSN12345" not in live_encoded


def test_build_data_status_only_tolerates_empty_sqlite_db(tmp_path):
    db = tmp_path / "empty.sqlite"
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row

    data = viewer.build_data(con, tmp_path / "out", include_satellite=False, status_only=True)

    assert data["map"]["statusOnly"] is True
    assert data["areas"] == []
    assert data["scheduleDraft"]["baseSnapshotId"] is None
    assert data["mower"]["battery"]["soc"] is None
    assert data["mower"]["routeInsights"] == {}
    live_status = viewer.build_live_status(data)
    assert live_status["map"]["statusOnly"] is True


def test_area_status_does_not_mark_stale_or_idle_mqtt_as_active():
    data = {
        "generatedAt": "2026-06-27T12:00:00+00:00",
        "areas": [
            {"id": 2, "cutting": {}, "lastMow": {}},
            {"id": 3, "cutting": {}, "lastMow": {}},
        ],
        "mower": {
            "routeInsights": {
                "mqttStatus": {
                    "state": "isRunning",
                    "currentPartitionId": 2,
                    "mowingPercentage": 55,
                    "observedAt": "2026-06-25T12:00:00+00:00",
                }
            }
        },
    }

    status = viewer.build_area_status(data)

    assert status["2"]["live"]["active"] is False

    data["mower"]["routeInsights"]["mqttStatus"] = {
        "state": "notRunning",
        "currentPartitionId": 3,
        "mowingPercentage": 55,
        "observedAt": "2026-06-27T11:59:00+00:00",
    }
    status = viewer.build_area_status(data)

    assert status["3"]["live"]["active"] is False


def test_latest_mqtt_status_uses_observed_at_when_report_time_missing(tmp_path):
    db = create_fixture_db(tmp_path)
    con = store.connect(db)
    source_id = store.add_source(con, ROOT / "README.md", "mqtt-no-report-time")
    store.insert_route_snapshot_record(
        con,
        source_id=source_id,
        vehicle_sn=None,
        observed_at="2026-06-27T12:40:00+00:00",
        route_alias="mqtt-message",
        data={
            "topicHash": "new-topic-hash",
            "payloadBytes": 64,
            "payloadSha256": "new-payload-hash",
            "payloadShape": "json",
            "payloadKeys": ["soc", "vehicleState"],
            "safeFields": {
                "vehicleState": "READY",
                "soc": 77,
            },
        },
    )
    con.commit()

    status = viewer.load_latest_mqtt_status(con)

    assert status["state"] == "READY"
    assert status["batterySoc"] == 77
    assert status["reportAt"] is None
    assert status["observedAt"] == "2026-06-27T12:40:00+00:00"


def test_write_viewer_outputs_expected_files(tmp_path):
    db = create_fixture_db(tmp_path)
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    data = viewer.build_data(con, tmp_path / "build", include_satellite=False)

    viewer.write_viewer(tmp_path / "build", data)

    assert (tmp_path / "build" / "index.html").exists()
    assert (tmp_path / "build" / "navimow-map-data.js").exists()
    assert (tmp_path / "build" / "navimow-live-status.json").exists()
    assert (tmp_path / "build" / "schedule-draft.json").exists()
    assert (tmp_path / "build" / "assets" / "navimow-map.js").exists()
    assert (tmp_path / "build" / "assets" / "terrain.webp").exists()
    html = (tmp_path / "build" / "index.html").read_text(encoding="utf-8")
    live_status = json.loads((tmp_path / "build" / "navimow-live-status.json").read_text(encoding="utf-8"))
    assert live_status["mower"]["battery"]["soc"] == 73
    assert live_status["layoutVersion"] == data["liveStatus"]["layoutVersion"]

    js = (tmp_path / "build" / "assets" / "navimow-map.js").read_text(encoding="utf-8")
    css = (tmp_path / "build" / "assets" / "navimow-map.css").read_text(encoding="utf-8")
    assert 'id="liveStatusStrip"' in html
    assert "Optimizer dry run" in js
    assert "Apply to draft" in js
    assert "renderOptimizerPreview" in js
    assert "refreshAreaShapeClasses" in js
    assert "bindLiveReload" in js
    assert "applyLiveStatus" in js
    assert "fetchLiveStatus();" in js
    assert "LIVE_EVENT_INTERVAL_SECONDS = 0.25" in js
    assert "/__navimow/events?interval=" in js
    assert "LIVE_REPLACE_INSIGHT_KEYS" in js
    assert "mergeLiveMowerStatus" in js
    assert "delete output.routeInsights[key]" in js
    assert "area.live = structuredClone(patch.live)" in js
    assert "mergeLiveAreaStatus" in js
    assert "mergeLiveStatusData" in js
    assert "renderLiveStatusPatch" in js
    assert "renderLiveStatusStrip" in js
    assert 'state.liveConnection = "connected"' in js
    assert "applyAreaStatusSnapshot" in js
    assert "renderAreaLiveState" in js
    assert "formatActivityState" in js
    assert 'state.liveConnection = "reconnecting"' in js
    assert "fetchLiveStatus();" in js
    assert "liveAreaText" in js
    assert "live-badge" in css
    assert ".live-status-strip" in css
    assert ".live-pill.is-ok::before" in css
    assert ".optimizer-panel" in css
    assert ".optimizer-warning" in css


def test_write_live_status_file_replaces_json_atomically(tmp_path):
    output = tmp_path / "build"
    output.mkdir()
    status_path = output / "navimow-live-status.json"
    status_path.write_text("{\"version\":\"old\"}\n", encoding="utf-8")
    data = {
        "generatedAt": "2026-06-27T12:00:00+00:00",
        "map": {"name": "Atomic", "areaCount": 0, "obstacleCount": 0},
        "areas": [],
        "scheduleDraft": {"baseSnapshotId": 3, "baseObservedAt": "2026-06-27T12:00:00+00:00"},
        "mower": {"battery": {"soc": 64}},
        "liveStatus": {"layoutVersion": "layout-atomic"},
    }

    path = viewer.write_live_status_file(output, data)

    assert path == status_path
    payload = json.loads(status_path.read_text(encoding="utf-8"))
    assert payload["layoutVersion"] == "layout-atomic"
    assert payload["mower"]["battery"]["soc"] == 64
    assert list(output.glob(".navimow-live-status.json.*.tmp")) == []


def test_write_viewer_requires_template_assets_before_writing(tmp_path, monkeypatch):
    monkeypatch.setattr(viewer, "TEMPLATE_DIR", tmp_path / "missing-template")
    output = tmp_path / "build"
    data = {
        "generatedAt": "2026-06-27T12:00:00+00:00",
        "map": {"name": "Missing template", "areaCount": 0, "obstacleCount": 0},
        "areas": [],
        "scheduleDraft": {},
        "mower": {},
    }

    try:
        viewer.write_viewer(output, data)
    except SystemExit as exc:
        message = str(exc)
    else:
        raise AssertionError("missing template assets should fail loudly")

    assert "Missing viewer template asset" in message
    assert not output.exists()


def test_write_viewer_outputs_status_only_files(tmp_path):
    db = create_status_only_db(tmp_path)
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    data = viewer.build_data(con, tmp_path / "build", include_satellite=False, status_only=True)

    viewer.write_viewer(tmp_path / "build", data)

    assert (tmp_path / "build" / "index.html").exists()
    assert (tmp_path / "build" / "navimow-map-data.js").exists()
    assert (tmp_path / "build" / "navimow-live-status.json").exists()
    assert (tmp_path / "build" / "schedule-draft.json").exists()
    assert (tmp_path / "build" / "assets" / "navimow-map.js").exists()
    assert not (tmp_path / "build" / "assets" / "terrain.webp").exists()

    bundle = viewer.read_viewer_data(tmp_path / "build")
    assert bundle["map"]["statusOnly"] is True
    live_status = json.loads((tmp_path / "build" / "navimow-live-status.json").read_text(encoding="utf-8"))
    assert live_status["map"]["statusOnly"] is True
    js = (tmp_path / "build" / "assets" / "navimow-map.js").read_text(encoding="utf-8")
    assert "map-empty-state" in js


def test_refresh_live_status_file_updates_status_without_rewriting_map_bundle(tmp_path):
    db = create_fixture_db(tmp_path)
    con = store.connect(db)
    data = viewer.build_data(con, tmp_path / "build", include_satellite=False)
    viewer.write_viewer(tmp_path / "build", data)
    before_bundle = (tmp_path / "build" / "navimow-map-data.js").read_text(encoding="utf-8")
    before_status = json.loads((tmp_path / "build" / "navimow-live-status.json").read_text(encoding="utf-8"))

    source_id = store.add_source(con, ROOT / "README.md", "status-refresh")
    store.insert_device_state_snapshot(
        con,
        source_id=source_id,
        data={"vehicle_sn": "TESTSN12345", "vehicle_type": 1, "soc": "41", "soh": 99, "vehicle_state": "0201"},
        observed_at="2026-06-27T12:10:00+00:00",
    )
    store.insert_live_location_snapshot(
        con,
        source_id=source_id,
        vehicle_sn="TESTSN12345",
        observed_at="2026-06-27T12:10:00+00:00",
        line_no=2,
        data={"posture_x": "4.0", "posture_y": "4.0", "posture_theta": "0.5", "mowing_percentage": 88},
    )
    store.insert_trail_time_snapshot(
        con,
        source_id=source_id,
        vehicle_sn="TESTSN12345",
        observed_at="2026-06-27T12:10:00+00:00",
        line_no=3,
        entries=[
            {"partitionId": 2, "startTime": 1782563600, "endTime": 1782567200, "area": 100.0, "finishedArea": 100.0, "partitionPercentage": 100},
        ],
    )
    store.insert_route_snapshot_record(
        con,
        source_id=source_id,
        vehicle_sn=None,
        observed_at="2026-06-27T12:10:00+00:00",
        route_alias="mqtt-message",
        data={
            "topicHash": "new-topic-hash",
            "payloadSha256": "new-payload-hash",
            "payloadShape": "json",
            "safeFields": {
                "vehicleState": "isRunning",
                "currentPartitionId": 3,
                "mowingPercentage": 88,
                "reportTime": 1782567200000,
            },
        },
    )

    path = viewer.refresh_live_status_file(tmp_path / "build", con)

    assert path == tmp_path / "build" / "navimow-live-status.json"
    after_bundle = (tmp_path / "build" / "navimow-map-data.js").read_text(encoding="utf-8")
    after_status = json.loads(path.read_text(encoding="utf-8"))
    assert after_bundle == before_bundle
    assert after_status["layoutVersion"] == before_status["layoutVersion"]
    assert after_status["mower"]["battery"]["soc"] == 41
    assert after_status["mower"]["stateCode"] == "0201"
    assert after_status["mower"]["liveLocation"]["mowingPercentage"] == 88
    assert after_status["areaStatus"]["2"]["lastMow"]["partitionPercentage"] == 100
    assert after_status["areaStatus"]["2"]["live"]["active"] is False
    assert after_status["areaStatus"]["3"]["live"]["active"] is True
    assert after_status["areaStatus"]["3"]["live"]["mowingPercentage"] == 88


def test_refresh_live_status_file_status_only_does_not_require_render_metadata(tmp_path):
    db = create_status_only_db(tmp_path)
    con = store.connect(db)
    data = viewer.build_data(con, tmp_path / "build", include_satellite=False, status_only=True)
    viewer.write_viewer(tmp_path / "build", data)
    before_bundle = (tmp_path / "build" / "navimow-map-data.js").read_text(encoding="utf-8")
    before_status = json.loads((tmp_path / "build" / "navimow-live-status.json").read_text(encoding="utf-8"))

    source_id = store.add_source(con, ROOT / "README.md", "status-only-refresh")
    store.insert_device_state_snapshot(
        con,
        source_id=source_id,
        data={"vehicle_sn": "STATUSSN12345", "vehicle_type": 1, "soc": "22", "soh": 98, "vehicle_state": "0201"},
        observed_at="2026-06-27T13:10:00+00:00",
    )
    store.insert_live_location_snapshot(
        con,
        source_id=source_id,
        vehicle_sn="STATUSSN12345",
        observed_at="2026-06-27T13:10:00+00:00",
        line_no=2,
        data={"posture_x": "4.0", "posture_y": "4.0", "posture_theta": "0.5", "mowing_percentage": 81},
    )

    path = viewer.refresh_live_status_file(tmp_path / "build", con)

    assert path == tmp_path / "build" / "navimow-live-status.json"
    after_bundle = (tmp_path / "build" / "navimow-map-data.js").read_text(encoding="utf-8")
    after_status = json.loads(path.read_text(encoding="utf-8"))
    assert after_bundle == before_bundle
    assert after_status["layoutVersion"] == before_status["layoutVersion"]
    assert after_status["map"]["statusOnly"] is True
    assert after_status["mower"]["battery"]["soc"] == 22
    assert after_status["mower"]["liveLocation"]["mowingPercentage"] == 81
    assert after_status["mower"]["liveLocation"]["positionPixel"] is None
