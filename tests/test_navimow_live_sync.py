import json
import sqlite3
import sys
import types
import urllib.parse
from pathlib import Path

import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import navimow_live_sync as live_sync


def write_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


class FakeHttpResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return json.dumps(self.payload).encode()


def write_fixture_sync_files(tmp_path: Path):
    responses = tmp_path / "responses"
    config = tmp_path / "config.json"
    vehicle_sn = "TESTSN12345"

    write_json(
        config,
        {
            "vehicleSn": vehicle_sn,
            "routes": ["index2", "device-info", "get-location", "set-list", "trail-time"],
            "headers": {"Authorization": "${NAVIMOW_AUTHORIZATION}"},
        },
    )
    write_json(
        responses / "index2.json",
        {
            "code": 1,
            "data": {
                "vehicle_sn": vehicle_sn,
                "vehicle_type": 300000043,
                "soc": "73",
                "soh": 100,
                "vehicle_state": "0102",
                "mapVersion": 123,
                "vehicleSettingUpdateTime": "456",
                "vehicle_info_update_time": "789",
                "partitionLength": 0,
                "partitionIdList": "",
            },
        },
    )
    write_json(
        responses / "device-info.json",
        {
            "code": 1,
            "data": {
                "vehicle_sn": vehicle_sn,
                "selfDefinedName": "Fixture mower",
                "model": "CM120M1",
                "mowingHeightList": [20, 25, 30, 35],
            },
        },
    )
    write_json(
        responses / "get-location.json",
        {
            "code": 1,
            "data": {
                "latitude": "57.0000000",
                "longitude": "26.0000000",
                "rtk": "do-not-export",
                "posture_x": "1.5",
                "posture_y": "2.5",
                "posture_theta": "0.25",
                "report_time": "1782569928",
                "mowing_percentage": 62,
                "path_id": 7,
            },
        },
    )
    write_json(
        responses / "set-list.json",
        {"code": 1, "data": {"height": 65, "cutterHeight": 0}},
    )
    write_json(
        responses / "trail-time.json",
        {
            "code": 1,
            "data": [
                {
                    "partitionId": 1,
                    "startTime": 1782560000,
                    "endTime": 1782563600,
                    "area": 100.0,
                    "finishedArea": 100.0,
                    "partitionPercentage": 100,
                }
            ],
        },
    )
    return config, responses


def write_mqtt_fixture_files(tmp_path: Path):
    responses = tmp_path / "responses"
    config = tmp_path / "config.json"
    write_json(config, {"routes": ["openapi-mqtt-info"], "auth": {"provider": "navimow-oauth"}})
    write_json(
        responses / "openapi-mqtt-info.json",
        {
            "code": 1,
            "data": {
                "mqttHost": "wss://mqtt.example/private",
                "mqttUrl": "/mqtt/private-path",
                "userName": "mqtt-secret-user",
                "pwdInfo": "mqtt-secret-password",
                "clientId": "mqtt-secret-client",
                "subTopics": ["secret/topic/one", "secret/topic/two"],
            },
        },
    )
    return config, responses


def test_sync_once_ingests_fixture_responses_and_redacts_location(tmp_path):
    db = tmp_path / "navimow.sqlite"
    config, responses = write_fixture_sync_files(tmp_path)

    code = live_sync.main(
        [
            "sync-once",
            "--config",
            str(config),
            "--db",
            str(db),
            "--responses-dir",
            str(responses),
            "--observed-at",
            "2026-06-27T12:00:00+00:00",
        ]
    )

    assert code == 0
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    assert con.execute("SELECT COUNT(*) AS c FROM device_state_snapshots").fetchone()["c"] == 1
    assert con.execute("SELECT COUNT(*) AS c FROM device_info_snapshots").fetchone()["c"] == 1
    assert con.execute("SELECT COUNT(*) AS c FROM trail_time_entries").fetchone()["c"] == 1
    location = con.execute("SELECT * FROM live_location_snapshots").fetchone()
    assert location["posture_x"] == 1.5
    assert location["mowing_percentage"] == 62
    assert "latitude" not in location["sanitized_json"]
    assert "longitude" not in location["sanitized_json"]
    assert "rtk" not in location["sanitized_json"]


def write_extended_read_route_fixtures(tmp_path: Path):
    responses = tmp_path / "extended"
    config = tmp_path / "extended-config.json"
    vehicle_sn = "TESTSN12345"
    routes = [
        "auth-list",
        "mower-state",
        "weather",
        "today-plan",
        "firmware",
        "maintenance",
        "map-list",
        "trail-data",
        "get-iot-file",
    ]
    write_json(config, {"vehicleSn": vehicle_sn, "routes": routes, "headers": {"Authorization": "${NAVIMOW_AUTHORIZATION}"}})
    write_json(
        responses / "auth-list.json",
        {
            "code": 1,
            "data": [
                {
                    "vehicle_sn": vehicle_sn,
                    "auth_uid": "owner-secret",
                    "selfDefinedName": "Fixture mower",
                    "vehicle_type": 300000043,
                    "vehicle_state": "0102",
                    "soc": 73,
                    "imageUrl": "https://signed.example/private",
                }
            ],
        },
    )
    write_json(
        responses / "mower-state.json",
        {
            "code": 1,
            "data": {
                "vehicle_sn": vehicle_sn,
                "state": "mowing",
                "battery": 73,
                "currentPartitionId": 2,
                "traceId": "trace-secret",
                "origin_gps": [57.0, 26.0],
            },
        },
    )
    write_json(
        responses / "weather.json",
        {
            "code": 1,
            "data": {"rainState": 0, "frostState": 0, "latitude": 57.0, "longitude": 26.0},
        },
    )
    write_json(
        responses / "today-plan.json",
        {
            "code": 1,
            "data": {"status": "active", "partitionIds": [1, 2], "progressPercent": 42, "token": "secret-token"},
        },
    )
    write_json(
        responses / "firmware.json",
        {"code": 1, "data": {"list": [{"component": "ECU", "version": "1.2.3"}], "signedUrl": "https://signed.example/fw"}},
    )
    write_json(
        responses / "maintenance.json",
        {"code": 1, "data": {"knife": {"remaining": 12, "unit": "h"}, "accountId": "secret-account"}},
    )
    write_json(
        responses / "map-list.json",
        {"code": 1, "data": [{"mapId": "map-a", "mapName": "Home", "center_gps": [57.0, 26.0]}]},
    )
    write_json(
        responses / "trail-data.json",
        {
            "code": 1,
            "data": {
                "compressedTrail": "secret-compressed-path-points",
                "origin_gps": [57.0, 26.0],
                "count": 42,
            },
        },
    )
    write_json(
        responses / "get-iot-file.json",
        {"code": 1, "data": {"version": 123, "url": "https://signed.example/blob?sig=secret", "status": "ok"}},
    )
    return config, responses


def test_sync_once_ingests_extended_read_route_snapshots_without_sensitive_fields(tmp_path):
    db = tmp_path / "navimow.sqlite"
    config, responses = write_extended_read_route_fixtures(tmp_path)

    code = live_sync.main(
        [
            "sync-once",
            "--config",
            str(config),
            "--db",
            str(db),
            "--responses-dir",
            str(responses),
            "--observed-at",
            "2026-06-27T12:00:00+00:00",
        ]
    )

    assert code == 0
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    rows = con.execute("SELECT route_alias, summary_json, sanitized_json FROM route_snapshot_records").fetchall()
    assert {row["route_alias"] for row in rows} == {
        "auth-list",
        "mower-state",
        "weather",
        "today-plan",
        "firmware",
        "maintenance",
        "map-list",
        "trail-data",
        "get-iot-file",
    }
    exported = "\n".join(row["summary_json"] + row["sanitized_json"] for row in rows)
    assert "owner-secret" not in exported
    assert "trace-secret" not in exported
    assert "secret-token" not in exported
    assert "secret-account" not in exported
    assert "https://signed.example" not in exported
    assert "origin_gps" not in exported
    assert "secret-compressed-path-points" not in exported
    assert "latitude" not in exported
    assert "longitude" not in exported
    assert con.execute("SELECT COUNT(*) FROM devices").fetchone()[0] == 1
    trail_data = con.execute("SELECT sanitized_json FROM route_snapshot_records WHERE route_alias='trail-data'").fetchone()[0]
    assert "captured_not_decoded" in trail_data
    assert "payloadBytes" in trail_data
    assert "payloadSha256" in trail_data


def test_route_coverage_report_summarizes_typed_snapshot_and_viewer_support(tmp_path, capsys):
    db = tmp_path / "navimow.sqlite"
    config, responses = write_extended_read_route_fixtures(tmp_path)
    live_sync.main(
        [
            "sync-once",
            "--config",
            str(config),
            "--db",
            str(db),
            "--responses-dir",
            str(responses),
            "--observed-at",
            "2026-06-27T12:00:00+00:00",
        ]
    )
    capsys.readouterr()

    report = live_sync.route_coverage_report(db)

    assert report["summary"]["readRouteCount"] == len(live_sync.READ_ROUTES)
    assert report["summary"]["snapshotRouteCount"] >= 1
    assert report["summary"]["typedRouteCount"] >= 1
    assert "mower-state" in report["summary"]["promotionCandidates"]
    route_by_alias = {row["alias"]: row for row in report["routes"]}
    assert route_by_alias["index2"]["storageMode"] == "typed_table"
    assert route_by_alias["mower-state"]["storageMode"] == "route_snapshot"
    assert route_by_alias["mower-state"]["viewerInsight"] == "consumerLiveState"
    assert route_by_alias["mower-state"]["present"] is True
    assert route_by_alias["trail-data"]["promotionStatus"] == "needs_decoder"

    code = live_sync.main(["route-coverage", "--db", str(db), "--json"])

    assert code == 0
    output = capsys.readouterr().out
    exported = json.loads(output)
    assert exported["summary"]["presentRouteCount"] >= 9
    assert "route coverage metadata only" in exported["privacy"].lower()
    for secret in ["owner-secret", "trace-secret", "secret-token", "signed.example", "origin_gps", "secret-compressed"]:
        assert secret not in output


def test_get_iot_file_sync_creates_map_resource_event_without_retaining_signed_url(tmp_path):
    db = tmp_path / "navimow.sqlite"
    config = tmp_path / "config.json"
    responses = tmp_path / "responses"
    write_json(
        config,
        {
            "vehicleSn": "TESTSN12345",
            "routes": ["get-iot-file"],
            "headers": {"Authorization": "${NAVIMOW_AUTHORIZATION}"},
        },
    )
    write_json(
        responses / "get-iot-file.json",
        {
            "code": 1,
            "data": {
                "version": 456,
                "url": "https://fixture.blob.core.windows.net/maps/resource.bin?sig=secret&se=2099-01-01T00:00:00Z",
                "status": "ok",
            },
        },
    )

    code = live_sync.main(
        [
            "sync-once",
            "--config",
            str(config),
            "--db",
            str(db),
            "--responses-dir",
            str(responses),
            "--observed-at",
            "2026-06-27T12:00:00+00:00",
        ]
    )

    assert code == 0
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    event = con.execute("SELECT * FROM map_resource_events").fetchone()
    assert event["remote_version"] == 456
    assert event["blob_host"] == "fixture.blob.core.windows.net"
    assert event["url_expires_at"] == "2099-01-01T00:00:00Z"
    assert event["signed_url"] is None
    assert "sig=secret" not in event["raw_json"]
    row = con.execute("SELECT sanitized_json FROM route_snapshot_records WHERE route_alias='get-iot-file'").fetchone()
    assert "fixture.blob.core.windows.net" not in row["sanitized_json"]
    assert "sig=secret" not in row["sanitized_json"]


def test_sync_once_can_download_map_artifacts_and_discard_signed_urls(tmp_path, monkeypatch, capsys):
    db = tmp_path / "navimow.sqlite"
    config = tmp_path / "config.json"
    responses = tmp_path / "responses"
    map_dir = tmp_path / "maps"
    write_json(
        config,
        {
            "vehicleSn": "TESTSN12345",
            "routes": ["get-iot-file"],
            "headers": {"Authorization": "${NAVIMOW_AUTHORIZATION}"},
        },
    )
    write_json(
        responses / "get-iot-file.json",
        {
            "code": 1,
            "data": {
                "version": 789,
                "url": "https://fixture.blob.core.windows.net/maps/resource.bin?sig=secret",
            },
        },
    )
    calls = []

    def fake_download_map_artifacts(con, target_dir, *, insecure_tls=False):
        calls.append((target_dir, insecure_tls))
        event = con.execute("SELECT signed_url FROM map_resource_events").fetchone()
        assert event["signed_url"] == "https://fixture.blob.core.windows.net/maps/resource.bin?sig=secret"
        return {"downloaded": 1, "skipped": 0, "areas": 0, "failed": 0}

    monkeypatch.setattr(live_sync.store, "download_map_artifacts", fake_download_map_artifacts)

    code = live_sync.main(
        [
            "sync-once",
            "--config",
            str(config),
            "--db",
            str(db),
            "--responses-dir",
            str(responses),
            "--download-map-artifacts",
            "--map-dir",
            str(map_dir),
        ]
    )

    assert code == 0
    assert calls == [(map_dir, False)]
    output = capsys.readouterr().out
    assert "map_downloaded=1" in output
    assert "map_signed_urls_discarded=1" in output
    assert "fixture.blob.core.windows.net" not in output
    assert "sig=secret" not in output
    con = sqlite3.connect(db)
    assert con.execute("SELECT signed_url FROM map_resource_events").fetchone()[0] is None


def test_map_sync_plan_reports_missing_detail_and_artifact_without_sensitive_values(tmp_path, capsys):
    db = tmp_path / "navimow.sqlite"
    config = tmp_path / "config.json"
    responses = tmp_path / "responses"
    vehicle_sn = "TESTSN12345"
    write_json(config, {"vehicleSn": vehicle_sn, "routes": ["index2", "get-iot-file"], "headers": {}})
    write_json(
        responses / "index2.json",
        {
            "code": 1,
            "data": {
                "vehicle_sn": vehicle_sn,
                "vehicle_type": 300000043,
                "mapVersion": 456,
                "vehicleSettingUpdateTime": 1782566852132,
            },
        },
    )
    write_json(
        responses / "get-iot-file.json",
        {
            "code": 1,
            "data": {
                "version": 456,
                "url": "https://fixture.blob.core.windows.net/maps/resource.bin?sig=secret",
            },
        },
    )
    assert live_sync.main(["sync-once", "--config", str(config), "--db", str(db), "--responses-dir", str(responses)]) == 0
    capsys.readouterr()

    plan = live_sync.map_sync_plan(db)

    assert plan["status"] == "needs_attention"
    assert plan["deviceCount"] == 1
    device = plan["devices"][0]
    assert device["deviceHash"] == live_sync.store.short_hash(vehicle_sn)
    assert device["state"]["mapVersion"] == 456
    assert device["mapDetail"]["present"] is False
    assert device["artifact"]["remoteVersion"] == 456
    assert device["artifact"]["downloaded"] is False
    action_ids = {action["id"] for action in device["recommendedActions"]}
    assert {"sync-map-detail", "download-map-artifact", "rebuild-viewer-after-map-change"} <= action_ids

    code = live_sync.main(["map-sync-plan", "--db", str(db), "--json"])

    assert code == 0
    output = capsys.readouterr().out
    exported = json.loads(output)
    encoded = json.dumps(exported, ensure_ascii=False)
    assert "TESTSN12345" not in encoded
    assert "fixture.blob.core.windows.net" not in encoded
    assert "sig=secret" not in encoded
    assert "signedUrl" not in encoded
    assert "deviceHash" in encoded


def seed_current_map_state(
    tmp_path: Path,
    db: Path,
    vehicle_sn: str = "TESTSN12345",
    version: int = 456,
    setting_update_time: int = 1782561600000,
) -> Path:
    artifact_file = tmp_path / "maps" / "resource.bin"
    artifact_file.parent.mkdir(parents=True)
    artifact_file.write_bytes(b"fixture terrain")
    con = live_sync.store.connect(db)
    live_sync.store.upsert_device(con, vehicle_sn, seen_at="2026-06-27T12:00:00+00:00")
    source_id = live_sync.add_live_source(con, "map-sync-plan-fixture", {"fixture": True}, "2026-06-27T12:00:00+00:00")
    con.execute(
        """
        INSERT INTO device_state_snapshots(
            source_id, vehicle_sn, observed_at, map_version, vehicle_setting_update_time,
            vehicle_info_update_time, partition_length, partition_id_list_json, raw_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (source_id, vehicle_sn, "2026-06-27T12:00:00+00:00", version, setting_update_time, 1782561600000, 0, "[]", "{}"),
    )
    con.execute(
        """
        INSERT INTO map_detail_snapshots(
            source_id, vehicle_sn, observed_at, event_hash, map_id, map_base_id,
            map_name, total_area, detail_area, raw_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (source_id, vehicle_sn, "2026-06-27T12:30:00+00:00", "detail-hash", "map-id", "base-id", "Fixture", 100.0, 100.0, "{}"),
    )
    con.execute(
        """
        INSERT INTO map_resource_events(
            source_id, vehicle_sn, observed_at, event_hash, remote_version, local_version,
            status, blob_host, blob_path, url_expires_at, url_sha256, signed_url, raw_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (source_id, vehicle_sn, "2026-06-27T12:31:00+00:00", "resource-hash", version, version, "current", None, None, None, None, None, "{}"),
    )
    con.execute(
        """
        INSERT INTO map_artifacts(
            vehicle_sn, version, file_path, sha256, size_bytes, content_kind, parsed_status, imported_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (vehicle_sn, version, str(artifact_file), "fixture-sha", artifact_file.stat().st_size, "zip", "parsed", "2026-06-27T12:32:00+00:00"),
    )
    con.commit()
    return artifact_file


def test_map_sync_plan_is_current_when_detail_and_artifact_are_local(tmp_path):
    db = tmp_path / "navimow.sqlite"
    seed_current_map_state(tmp_path, db)

    plan = live_sync.map_sync_plan(db)

    assert plan["status"] == "current"
    assert plan["ready"] is True
    assert plan["nextCommands"] == []
    device = plan["devices"][0]
    assert device["recommendedActions"] == []
    assert device["artifact"]["downloaded"] is True
    assert device["artifact"]["filePresent"] is True


def test_map_sync_plan_does_not_refresh_detail_for_setting_timestamp_only(tmp_path):
    db = tmp_path / "navimow.sqlite"
    seed_current_map_state(tmp_path, db, setting_update_time=1782568800000)

    plan = live_sync.map_sync_plan(db)

    assert plan["status"] == "current"
    device = plan["devices"][0]
    assert device["mapDetail"]["settingUpdateAfterDetail"] is True
    assert device["recommendedActions"] == []
    assert live_sync.map_delta_routes_from_plan(plan) == []


def test_map_delta_routes_from_plan_are_ordered_and_deduped():
    plan = {
        "devices": [
            {
                "recommendedActions": [
                    {"id": "sync-state"},
                    {"id": "sync-map-detail"},
                    {"id": "refresh-map-detail"},
                    {"id": "download-map-artifact"},
                    {"id": "rebuild-viewer-after-map-change"},
                    {"id": "redownload-map-artifact"},
                ]
            }
        ]
    }

    assert live_sync.map_delta_routes_from_plan(plan) == ["map-list", "map-detail", "get-iot-file"]


def test_map_delta_sync_runs_only_index2_when_map_state_is_current(tmp_path, capsys):
    db = tmp_path / "navimow.sqlite"
    config = tmp_path / "config.json"
    responses = tmp_path / "responses"
    vehicle_sn = "TESTSN12345"
    seed_current_map_state(tmp_path, db, vehicle_sn=vehicle_sn, version=456)
    write_json(config, {"vehicleSn": vehicle_sn, "routes": ["index2"], "headers": {}})
    write_json(
        responses / "index2.json",
        {
            "code": 1,
            "data": {
                "vehicle_sn": vehicle_sn,
                "vehicle_type": 300000043,
                "mapVersion": 456,
                "vehicleSettingUpdateTime": 1782561600000,
                "vehicle_info_update_time": 1782561600000,
            },
        },
    )

    code = live_sync.main(
        [
            "sync-once",
            "--config",
            str(config),
            "--db",
            str(db),
            "--responses-dir",
            str(responses),
            "--map-delta",
        ]
    )

    assert code == 0
    output = capsys.readouterr().out
    assert "map_delta_routes=index2" in output
    assert "map-detail" not in output
    assert "get-iot-file" not in output


def test_map_delta_sync_runs_needed_map_routes_and_discards_signed_urls(tmp_path, monkeypatch, capsys):
    db = tmp_path / "navimow.sqlite"
    config = tmp_path / "config.json"
    responses = tmp_path / "responses"
    map_dir = tmp_path / "maps"
    vehicle_sn = "TESTSN12345"
    write_json(config, {"vehicleSn": vehicle_sn, "routes": ["index2"], "headers": {}})
    write_json(
        responses / "index2.json",
        {"code": 1, "data": {"vehicle_sn": vehicle_sn, "vehicle_type": 300000043, "mapVersion": 789, "vehicleSettingUpdateTime": 1782566852132}},
    )
    write_json(responses / "map-list.json", {"code": 1, "data": [{"mapId": "fixture", "center_gps": [57.0, 26.0]}]})
    write_json(responses / "map-detail.json", {"code": 1, "data": {}})
    write_json(
        responses / "get-iot-file.json",
        {
            "code": 1,
            "data": {"version": 789, "url": "https://fixture.blob.core.windows.net/maps/resource.bin?sig=secret"},
        },
    )
    calls = []

    def fake_download_map_artifacts(con, target_dir, *, insecure_tls=False):
        calls.append((target_dir, insecure_tls))
        event = con.execute("SELECT signed_url FROM map_resource_events").fetchone()
        assert event["signed_url"] == "https://fixture.blob.core.windows.net/maps/resource.bin?sig=secret"
        return {"downloaded": 1, "skipped": 0, "areas": 0, "failed": 0}

    monkeypatch.setattr(live_sync.store, "download_map_artifacts", fake_download_map_artifacts)

    code = live_sync.main(
        [
            "sync-once",
            "--config",
            str(config),
            "--db",
            str(db),
            "--responses-dir",
            str(responses),
            "--map-delta",
            "--download-map-artifacts",
            "--map-dir",
            str(map_dir),
        ]
    )

    assert code == 0
    assert calls == [(map_dir, False)]
    output = capsys.readouterr().out
    assert "map_delta_routes=index2,map-list,map-detail,get-iot-file" in output
    assert "map_downloaded=1" in output
    assert "map_signed_urls_discarded=1" in output
    assert "fixture.blob.core.windows.net" not in output
    assert "sig=secret" not in output
    con = sqlite3.connect(db)
    assert con.execute("SELECT signed_url FROM map_resource_events").fetchone()[0] is None


def test_map_delta_sync_discards_signed_urls_when_download_fails(tmp_path, monkeypatch):
    db = tmp_path / "navimow.sqlite"
    config = tmp_path / "config.json"
    responses = tmp_path / "responses"
    vehicle_sn = "TESTSN12345"
    write_json(config, {"vehicleSn": vehicle_sn, "routes": ["index2"], "headers": {}})
    write_json(
        responses / "index2.json",
        {"code": 1, "data": {"vehicle_sn": vehicle_sn, "vehicle_type": 300000043, "mapVersion": 789, "vehicleSettingUpdateTime": 1782566852132}},
    )
    write_json(responses / "map-list.json", {"code": 1, "data": []})
    write_json(responses / "map-detail.json", {"code": 1, "data": {}})
    write_json(
        responses / "get-iot-file.json",
        {
            "code": 1,
            "data": {"version": 789, "url": "https://fixture.blob.core.windows.net/maps/resource.bin?sig=secret"},
        },
    )

    def failing_download_map_artifacts(con, target_dir, *, insecure_tls=False):
        event = con.execute("SELECT signed_url FROM map_resource_events").fetchone()
        assert event["signed_url"] == "https://fixture.blob.core.windows.net/maps/resource.bin?sig=secret"
        raise RuntimeError("download failed")

    monkeypatch.setattr(live_sync.store, "download_map_artifacts", failing_download_map_artifacts)

    try:
        live_sync.main(
            [
                "sync-once",
                "--config",
                str(config),
                "--db",
                str(db),
                "--responses-dir",
                str(responses),
                "--map-delta",
                "--download-map-artifacts",
            ]
        )
        raise AssertionError("expected download failure")
    except RuntimeError as exc:
        assert str(exc) == "download failed"

    con = sqlite3.connect(db)
    assert con.execute("SELECT signed_url FROM map_resource_events").fetchone()[0] is None


def test_map_delta_dry_run_with_oauth_config_prints_consumer_session_steps(tmp_path, capsys):
    config = tmp_path / "config.json"
    write_json(config, {"auth": {"provider": "navimow-oauth"}, "headers": {}, "routes": ["openapi-auth-list"]})

    code = live_sync.main(["sync-once", "--config", str(config), "--db", str(tmp_path / "navimow.sqlite"), "--map-delta", "--dry-run"])

    assert code == 0
    output = capsys.readouterr().out
    assert "map-delta needs consumer-app session auth" in output
    assert "auth.provider=navimow-oauth only injects an OpenAPI bearer token" in output
    assert "make live-auth-discover" in output
    assert "make live-android-doctor" in output
    assert "make live-android-capture" in output
    assert "--i-understand-local-secrets" in output
    assert "would run map delta sync" not in output


def test_map_delta_live_with_oauth_config_refuses_before_network(tmp_path, monkeypatch, capsys):
    config = tmp_path / "config.json"
    write_json(config, {"auth": {"provider": "navimow-oauth"}, "headers": {}, "routes": ["openapi-auth-list"]})

    def fail_request_route(*args, **kwargs):
        raise AssertionError("network should not be called")

    monkeypatch.setattr(live_sync, "request_route", fail_request_route)

    code = live_sync.main(["sync-once", "--config", str(config), "--db", str(tmp_path / "navimow.sqlite"), "--map-delta"])

    captured = capsys.readouterr()
    assert code == 1
    assert "map-delta needs consumer-app session auth" in captured.err
    assert "make live-android-doctor" in captured.err
    assert captured.out == ""


def test_insert_map_detail_snapshot_is_idempotent_for_same_content(tmp_path):
    db = tmp_path / "navimow.sqlite"
    con = live_sync.store.connect(db)
    vehicle_sn = "TESTSN12345"
    live_sync.store.upsert_device(con, vehicle_sn, seen_at="2026-06-27T12:00:00+00:00")
    outer = {
        "map_id": "map-id",
        "map_base_id": "base-id",
        "map_name": "Fixture map",
        "total_area": 100.0,
        "map_detail": json.dumps({"sub_maps": [{"id": 1, "name": "Area 1", "area": 10.0, "elements": []}]}),
    }
    detail = {"area": 100.0, "sub_maps": [{"id": 1, "name": "Area 1", "area": 10.0, "elements": []}]}
    first_source = live_sync.add_live_source(con, "map-detail", {"fixture": 1}, "2026-06-27T12:00:00+00:00")
    second_source = live_sync.add_live_source(con, "map-detail", {"fixture": 2}, "2026-06-27T12:05:00+00:00")

    assert live_sync.store.insert_map_detail_snapshot(
        con,
        source_id=first_source,
        vehicle_sn=vehicle_sn,
        observed_at="2026-06-27T12:00:00+00:00",
        line_no=0,
        outer=outer,
        detail=detail,
    ) == (1, 1)
    assert live_sync.store.insert_map_detail_snapshot(
        con,
        source_id=second_source,
        vehicle_sn=vehicle_sn,
        observed_at="2026-06-27T12:05:00+00:00",
        line_no=0,
        outer=outer,
        detail=detail,
    ) == (0, 0)
    assert con.execute("SELECT COUNT(*) FROM map_detail_snapshots").fetchone()[0] == 1


def test_state_store_download_map_artifacts_discards_invalid_signed_urls(tmp_path):
    db = tmp_path / "navimow.sqlite"
    con = live_sync.store.connect(db)
    vehicle_sn = "TESTSN12345"
    live_sync.store.upsert_device(con, vehicle_sn, seen_at="2026-06-27T12:00:00+00:00")
    source_id = live_sync.add_live_source(con, "get-iot-file", {"fixture": True}, "2026-06-27T12:00:00+00:00")
    con.execute(
        """
        INSERT INTO map_resource_events(
            source_id, vehicle_sn, observed_at, event_hash, remote_version, local_version,
            status, blob_host, blob_path, url_expires_at, url_sha256, signed_url, raw_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            source_id,
            vehicle_sn,
            "2026-06-27T12:00:00+00:00",
            "resource-event",
            999,
            None,
            None,
            None,
            None,
            None,
            None,
            "http://fixture.blob.core.windows.net/maps/resource.bin?sig=secret",
            "{}",
        ),
    )
    con.commit()

    counts = live_sync.store.download_map_artifacts(con, tmp_path / "maps")

    assert counts["failed"] == 1
    assert con.execute("SELECT signed_url FROM map_resource_events").fetchone()[0] is None
    state = json.loads(con.execute("SELECT value FROM sync_state WHERE key LIKE ?", ("%map_download_error",)).fetchone()[0])
    assert state["error"] == "download URL is not HTTPS"
    assert "sig=secret" not in json.dumps(state)


def test_openapi_mqtt_snapshot_redacts_credentials_and_urls(tmp_path):
    db = tmp_path / "navimow.sqlite"
    config = tmp_path / "config.json"
    responses = tmp_path / "responses"
    write_json(config, {"routes": ["openapi-mqtt-info"]})
    write_json(
        responses / "openapi-mqtt-info.json",
        {
            "code": 1,
            "data": {
                "mqttHost": "wss://mqtt.example/private",
                "mqttUrl": "wss://mqtt.example/path",
                "userName": "local-user",
                "clientId": "mqtt-client",
                "pwdInfo": "mqtt-password",
                "subTopics": ["secret/topic/one", "secret/topic/two"],
            },
        },
    )

    code = live_sync.main(
        [
            "sync-once",
            "--config",
            str(config),
            "--db",
            str(db),
            "--responses-dir",
            str(responses),
        ]
    )

    assert code == 0
    con = sqlite3.connect(db)
    row = con.execute("SELECT summary_json, sanitized_json FROM route_snapshot_records").fetchone()
    exported = row[0] + row[1]
    assert "mqtt-password" not in exported
    assert "wss://mqtt.example" not in exported
    assert "mqtt.example" not in exported
    assert "local-user" not in exported
    assert "mqtt-client" not in exported
    assert "secret/topic" not in exported
    assert "topicCount" in exported
    assert "credentialStatus" in exported


def test_parse_mqtt_metadata_extracts_connection_shape_without_printing():
    metadata = live_sync.parse_mqtt_metadata(
        {
            "data": {
                "mqttHost": "wss://mqtt.example/private",
                "mqttUrl": "/mqtt/path",
                "userName": "mqtt-secret-user",
                "pwdInfo": "mqtt-secret-password",
                "subTopics": ["topic/a", "topic/b"],
            }
        }
    )

    assert metadata["host"] == "mqtt.example"
    assert metadata["port"] == 443
    assert metadata["transport"] == "websockets"
    assert metadata["tls"] is True
    assert metadata["path"] == "/mqtt/path"
    assert metadata["username"] == "mqtt-secret-user"
    assert metadata["password"] == "mqtt-secret-password"
    assert metadata["topics"] == ["topic/a", "topic/b"]


def test_mqtt_doctor_reports_shape_without_secrets(tmp_path, capsys):
    config, responses = write_mqtt_fixture_files(tmp_path)

    code = live_sync.main(["mqtt-doctor", "--config", str(config), "--responses-dir", str(responses)])

    assert code == 0
    output = capsys.readouterr().out
    assert "mqtt metadata: ok" in output
    assert "host: present" in output
    assert "transport: websockets" in output
    assert "topics: 2" in output
    for secret in [
        "mqtt.example",
        "mqtt-secret-user",
        "mqtt-secret-password",
        "mqtt-secret-client",
        "secret/topic",
        "/mqtt/private-path",
    ]:
        assert secret not in output


def test_mqtt_listen_dry_run_does_not_connect_or_print_secrets(tmp_path, monkeypatch, capsys):
    config, responses = write_mqtt_fixture_files(tmp_path)

    def fake_run_mqtt_listener(**kwargs):
        raise AssertionError("dry-run should not start the MQTT listener")

    monkeypatch.setattr(live_sync, "run_mqtt_listener", fake_run_mqtt_listener)

    code = live_sync.main(
        [
            "mqtt-listen",
            "--config",
            str(config),
            "--responses-dir",
            str(responses),
            "--dry-run",
        ]
    )

    assert code == 0
    output = capsys.readouterr().out
    assert "would subscribe to 2 MQTT topic(s)" in output
    for secret in [
        "mqtt.example",
        "mqtt-secret-user",
        "mqtt-secret-password",
        "mqtt-secret-client",
        "secret/topic",
        "/mqtt/private-path",
    ]:
        assert secret not in output


def test_ingest_mqtt_message_sanitizes_payload_and_hashes_topic(tmp_path):
    db = tmp_path / "navimow.sqlite"

    count = live_sync.ingest_mqtt_message(
        db=db,
        topic="secret/topic/one",
        payload=json.dumps(
            {
                "vehicleState": "isRunning",
                "soc": 88,
                "descriptiveCapacityRemaining": "HIGH",
                "currentPartitionId": 2,
                "mowingPercentage": 45,
                "reportTime": 1782563600000,
                "workStatus": "MOWING",
                "latitude": "57.0",
                "signedUrl": "https://signed.example/private",
                "token": "secret-token",
            }
        ).encode(),
        observed_at="2026-06-27T12:00:00+00:00",
    )

    assert count == 1
    con = sqlite3.connect(db)
    row = con.execute(
        "SELECT summary_json, sanitized_json FROM route_snapshot_records WHERE route_alias='mqtt-message'"
    ).fetchone()
    exported = row[0] + row[1]
    assert "secret/topic/one" not in exported
    assert "secret-token" not in exported
    assert "signed.example" not in exported
    assert "latitude" not in exported
    assert "payloadJson" not in exported
    assert "topicHash" in exported
    assert "payloadSha256" in exported
    assert "payloadKeys" in exported
    assert "safeFields" in exported
    assert '"messageClasses":["state","progress","battery"]' in exported
    assert "vehicleState" in exported
    assert "isRunning" in exported
    assert "88" in exported
    status = con.execute(
        """
        SELECT state, work_status, battery_soc, capacity_label, current_partition_id,
               mowing_percentage, report_time
        FROM mqtt_status_snapshots
        """
    ).fetchone()
    assert status == ("isRunning", "MOWING", 88, "HIGH", 2, 45, 1782563600)


def test_ingest_mqtt_message_promotes_nested_capacity_remaining(tmp_path):
    db = tmp_path / "navimow.sqlite"

    count = live_sync.ingest_mqtt_message(
        db=db,
        topic="secret/topic/one",
        payload=json.dumps(
            {
                "vehicleState": "READY",
                "capacityRemaining": [{"rawValue": "73", "unit": "PERCENTAGE"}],
                "descriptiveCapacityRemaining": "HIGH",
                "currentPartitionId": 4,
                "reportTime": 1782567200000,
                "topic": "secret/topic/inside",
            }
        ).encode(),
        observed_at="2026-06-27T12:10:00+00:00",
    )

    assert count == 1
    con = sqlite3.connect(db)
    row = con.execute("SELECT sanitized_json FROM route_snapshot_records WHERE route_alias='mqtt-message'").fetchone()
    exported = row[0]
    assert "secret/topic/one" not in exported
    assert "secret/topic/inside" not in exported
    assert '"capacityPercent":73' in exported
    status = con.execute(
        """
        SELECT state, battery_soc, capacity_label, current_partition_id, report_time
        FROM mqtt_status_snapshots
        """
    ).fetchone()
    assert status == ("READY", 73, "HIGH", 4, 1782567200)


def test_ingest_binary_mqtt_message_skips_typed_status(tmp_path):
    db = tmp_path / "navimow.sqlite"

    count = live_sync.ingest_mqtt_message(
        db=db,
        topic="secret/topic/one",
        payload=b"\x00\xff\x01",
        observed_at="2026-06-27T12:00:00+00:00",
    )

    assert count == 1
    con = sqlite3.connect(db)
    row = con.execute("SELECT sanitized_json FROM route_snapshot_records WHERE route_alias='mqtt-message'").fetchone()
    exported = row[0]
    assert "binary" in exported
    assert '"messageClasses":["binary"]' in exported
    assert "secret/topic/one" not in exported
    assert con.execute("SELECT COUNT(*) FROM mqtt_status_snapshots").fetchone()[0] == 0


def test_ingest_mqtt_message_classifies_event_and_command_result_without_sensitive_values(tmp_path):
    db = tmp_path / "navimow.sqlite"

    live_sync.ingest_mqtt_message(
        db=db,
        topic="secret/topic/command-result",
        payload=json.dumps(
            {
                "eventType": "TASK_CHANGED",
                "commandNo": "12345",
                "resultCode": 0,
                "clientId": "secret-client",
                "deviceId": "secret-device",
                "token": "secret-token",
            }
        ).encode(),
        observed_at="2026-06-27T12:05:00+00:00",
    )

    con = sqlite3.connect(db)
    row = con.execute("SELECT sanitized_json FROM route_snapshot_records WHERE route_alias='mqtt-message'").fetchone()
    exported = row[0]
    assert '"messageClasses":["event","command_result"]' in exported
    assert "secret/topic" not in exported
    assert "secret-client" not in exported
    assert "secret-device" not in exported
    assert "secret-token" not in exported


def test_ingest_mqtt_message_and_refresh_status_reports_sanitized_refresh_failure(tmp_path, monkeypatch):
    def fake_update_live_status_artifact(**kwargs):
        raise RuntimeError("secret-token signed.example secret/topic")

    monkeypatch.setattr(live_sync, "update_live_status_artifact", fake_update_live_status_artifact)

    result = live_sync.ingest_mqtt_message_and_refresh_status(
        db=tmp_path / "navimow.sqlite",
        topic="secret/topic/failure",
        payload=json.dumps({"vehicleState": "isRunning", "soc": 81, "token": "secret-token"}).encode(),
        update_live_status=True,
        viewer_output=tmp_path / "viewer",
        observed_at="2026-06-27T12:06:00+00:00",
    )

    encoded = json.dumps(result, ensure_ascii=False)
    assert result["route_snapshot_records"] == 1
    assert result["live_status_updated"] is False
    assert result["live_status_error"] == "RuntimeError"
    assert "secret-token" not in encoded
    assert "secret/topic" not in encoded
    assert "signed.example" not in encoded
    assert live_sync.mqtt_ingest_result_summary("mqtt_message=1", result).endswith("live_status=failed (RuntimeError)")


def test_mqtt_replay_smoke_updates_live_status_without_secret_values(tmp_path, capsys):
    import build_navimow_map_viewer as viewer
    from test_navimow_map_viewer import create_fixture_db

    db = create_fixture_db(tmp_path)
    output = tmp_path / "viewer"
    con = live_sync.store.connect(db)
    data = viewer.build_data(con, output, include_satellite=False)
    viewer.write_viewer(output, data)

    code = live_sync.main(
        [
            "mqtt-replay-smoke",
            "--db",
            str(db),
            "--viewer-output",
            str(output),
            "--area-id",
            "3",
            "--mowing-percentage",
            "88",
            "--battery-soc",
            "74",
            "--report-time",
            "1782570000000",
            "--observed-at",
            "2026-06-27T12:50:00+00:00",
            "--update-live-status",
        ]
    )

    assert code == 0
    output_text = capsys.readouterr().out
    assert "mqtt_replay_message=1" in output_text
    assert "live_status=updated" in output_text
    for secret in ["local/mqtt-replay-smoke", "local-secret-redaction-check", "signed.example", "57.0000000"]:
        assert secret not in output_text

    status = json.loads((output / "navimow-live-status.json").read_text(encoding="utf-8"))
    encoded = json.dumps(status, ensure_ascii=False)
    assert status["mower"]["routeInsights"]["mqttStatus"]["batterySoc"] == 74
    assert status["mower"]["routeInsights"]["mqttStatus"]["currentPartitionId"] == 3
    assert status["mower"]["routeInsights"]["mqttStatus"]["mowingPercentage"] == 88
    assert status["mower"]["routeInsights"]["mqttStatus"]["source"] == "mqtt-message"
    assert status["areaStatus"]["3"]["live"]["active"] is True
    assert status["areaStatus"]["3"]["live"]["mowingPercentage"] == 88
    assert status["areaStatus"]["3"]["live"]["source"] == "mqtt-message"
    assert "local/mqtt-replay-smoke" not in encoded
    assert "local-secret-redaction-check" not in encoded
    assert "signed.example" not in encoded
    assert "latitude" not in encoded
    assert "topicHash" not in encoded
    assert "payloadSha256" not in encoded


def test_mqtt_replay_smoke_defaults_to_existing_viewer_area(tmp_path):
    import build_navimow_map_viewer as viewer
    from test_navimow_map_viewer import create_fixture_db

    db = create_fixture_db(tmp_path)
    output = tmp_path / "viewer"
    con = live_sync.store.connect(db)
    data = viewer.build_data(con, output, include_satellite=False)
    viewer.write_viewer(output, data)

    code = live_sync.main(
        [
            "mqtt-replay-smoke",
            "--db",
            str(db),
            "--viewer-output",
            str(output),
            "--mowing-percentage",
            "44",
            "--battery-soc",
            "70",
            "--report-time",
            "1782571000000",
            "--observed-at",
            "2026-06-27T12:55:00+00:00",
            "--update-live-status",
        ]
    )

    assert code == 0
    status = json.loads((output / "navimow-live-status.json").read_text(encoding="utf-8"))
    assert status["mower"]["routeInsights"]["mqttStatus"]["currentPartitionId"] == 1
    assert status["areaStatus"]["1"]["live"]["active"] is True
    assert status["areaStatus"]["1"]["live"]["mowingPercentage"] == 44


def test_mqtt_replay_clear_removes_synthetic_rows_and_refreshes_status(tmp_path, capsys):
    import build_navimow_map_viewer as viewer
    from test_navimow_map_viewer import create_fixture_db

    db = create_fixture_db(tmp_path)
    output = tmp_path / "viewer"
    con = live_sync.store.connect(db)
    data = viewer.build_data(con, output, include_satellite=False)
    viewer.write_viewer(output, data)
    live_sync.main(
        [
            "mqtt-replay-smoke",
            "--db",
            str(db),
            "--viewer-output",
            str(output),
            "--report-time",
            "1782571000000",
            "--observed-at",
            "2026-06-27T12:55:00+00:00",
            "--update-live-status",
        ]
    )
    capsys.readouterr()

    code = live_sync.main(
        [
            "mqtt-replay-clear",
            "--db",
            str(db),
            "--viewer-output",
            str(output),
            "--update-live-status",
        ]
    )

    assert code == 0
    output_text = capsys.readouterr().out
    assert "mqtt_status_snapshots=1" in output_text
    assert "route_snapshot_records=1" in output_text
    assert "live_status=updated" in output_text
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    assert con.execute("SELECT COUNT(*) FROM mqtt_status_snapshots WHERE event_type='LOCAL_MQTT_REPLAY_SMOKE'").fetchone()[0] == 0
    assert (
        con.execute(
            "SELECT COUNT(*) FROM route_snapshot_records WHERE route_alias='mqtt-message' AND sanitized_json LIKE '%LOCAL_MQTT_REPLAY_SMOKE%'"
        ).fetchone()[0]
        == 0
    )
    status = json.loads((output / "navimow-live-status.json").read_text(encoding="utf-8"))
    assert (status["mower"]["routeInsights"].get("mqttStatus") or {}).get("eventType") != "LOCAL_MQTT_REPLAY_SMOKE"


def test_mqtt_sample_report_without_samples_is_redacted(tmp_path, capsys):
    db = tmp_path / "navimow.sqlite"

    report = live_sync.mqtt_sample_report(db, generated_at="2026-06-27T13:00:00+00:00")

    assert report["status"] == "no_samples"
    assert report["messageCount"] == 0
    assert report["sampleCount"] == 0
    assert "no typed MQTT status samples captured yet" in report["sampleGaps"]

    code = live_sync.main(["mqtt-sample-report", "--db", str(db)])

    assert code == 0
    output = capsys.readouterr().out
    assert "mqtt sample report: no_samples" in output
    assert "privacy: Sanitized MQTT status fields only" in output
    assert "topicHash" not in output
    assert "payloadSha256" not in output


def test_mqtt_sample_report_summarizes_samples_without_sensitive_values(tmp_path, capsys):
    db = tmp_path / "navimow.sqlite"
    live_sync.ingest_mqtt_message(
        db=db,
        topic="secret/topic/active",
        payload=json.dumps(
            {
                "vehicleState": "isRunning",
                "workStatus": "MOWING",
                "taskStatus": "secret-token-state",
                "eventType": "TASK_CHANGED",
                "soc": 82,
                "descriptiveCapacityRemaining": "HIGH",
                "currentPartitionId": 7,
                "mowingPercentage": 33,
                "pathId": 4,
                "reportTime": 1782570000000,
                "signedUrl": "https://signed.example/private",
            }
        ).encode(),
        observed_at="2026-06-27T12:50:00+00:00",
    )
    live_sync.ingest_mqtt_message(
        db=db,
        topic="secret/topic/idle",
        payload=json.dumps(
            {
                "vehicleState": "READY",
                "workStatus": "DOCKED",
                "soc": 79,
                "currentPartitionId": 7,
                "mowingPercentage": 100,
                "reportTime": 1782570600000,
            }
        ).encode(),
        observed_at="2026-06-27T13:00:00+00:00",
    )

    report = live_sync.mqtt_sample_report(db, generated_at="2026-06-27T13:01:00+00:00")

    assert report["status"] == "samples_available"
    assert report["messageCount"] == 2
    assert report["sampleCount"] == 2
    assert report["syntheticSampleCount"] == 0
    assert report["fieldCoverage"]["batterySoc"] == 2
    assert report["fieldCoverage"]["currentPartitionId"] == 2
    assert report["fieldCoverage"]["mowingPercentage"] == 2
    assert report["fieldCoverage"]["pathId"] == 1
    assert report["activityClasses"] == {"active": 1, "idle": 1}
    assert {"value": "isRunning", "count": 1, "activityClass": "active"} in report["enumValues"]["state"]
    assert {"value": "READY", "count": 1, "activityClass": "idle"} in report["enumValues"]["state"]
    assert {"value": "<redacted-sensitive-value>", "count": 1, "activityClass": "unknown"} in report["enumValues"]["taskStatus"]
    assert report["sampleGaps"] == []

    code = live_sync.main(["mqtt-sample-report", "--db", str(db), "--json"])

    assert code == 0
    output = capsys.readouterr().out
    exported = json.loads(output)
    assert exported["sampleCount"] == 2
    encoded = json.dumps(exported, ensure_ascii=False)
    for secret in [
        "secret/topic",
        "secret-token-state",
        "signed.example",
        "topicHash",
        "payloadSha256",
    ]:
        assert secret not in encoded


def seed_ready_mqtt_samples(db: Path) -> None:
    live_sync.ingest_mqtt_message(
        db=db,
        topic="secret/topic/active",
        payload=json.dumps(
            {
                "vehicleState": "isRunning",
                "workStatus": "MOWING",
                "soc": 82,
                "descriptiveCapacityRemaining": "HIGH",
                "currentPartitionId": 7,
                "mowingPercentage": 33,
                "reportTime": 1782570000000,
                "signedUrl": "https://signed.example/private",
            }
        ).encode(),
        observed_at="2026-06-27T12:50:00+00:00",
    )
    live_sync.ingest_mqtt_message(
        db=db,
        topic="secret/topic/idle",
        payload=json.dumps(
            {
                "vehicleState": "READY",
                "workStatus": "DOCKED",
                "soc": 79,
                "currentPartitionId": 7,
                "mowingPercentage": 100,
                "reportTime": 1782570600000,
            }
        ).encode(),
        observed_at="2026-06-27T13:00:00+00:00",
    )


def test_mqtt_readiness_without_samples_fails_strict_and_stays_redacted(tmp_path, capsys):
    db = tmp_path / "navimow.sqlite"

    report = live_sync.mqtt_readiness_report(db, generated_at="2026-06-27T13:00:00+00:00")

    assert report["status"] == "needs_real_samples"
    assert report["ready"] is False
    assert report["realSampleCount"] == 0
    assert "no typed MQTT status samples captured yet" in report["blockingGaps"]

    code = live_sync.main(
        [
            "mqtt-readiness",
            "--db",
            str(db),
            "--strict",
            "--json",
            "--now",
            "2026-06-27T14:31:00+00:00",
        ]
    )

    assert code == 1
    output = capsys.readouterr().out
    exported = json.loads(output)
    assert exported["ready"] is False
    assert "topicHash" not in output
    assert "payloadSha256" not in output


def test_mqtt_readiness_rejects_synthetic_only_samples(tmp_path, capsys):
    db = tmp_path / "navimow.sqlite"
    live_sync.main(
        [
            "mqtt-replay-smoke",
            "--db",
            str(db),
            "--area-id",
            "2",
            "--report-time",
            "1782571000000",
            "--observed-at",
            "2026-06-27T12:55:00+00:00",
        ]
    )
    capsys.readouterr()

    code = live_sync.main(["mqtt-readiness", "--db", str(db), "--strict"])

    assert code == 1
    output = capsys.readouterr().out
    assert "mqtt readiness: needs_real_samples" in output
    assert "typed status samples: 1 (real: 0, synthetic: 1)" in output
    assert "no real MQTT status samples captured" in output
    assert "local/mqtt-replay-smoke" not in output


def test_mqtt_readiness_passes_with_real_active_idle_required_fields(tmp_path, capsys):
    db = tmp_path / "navimow.sqlite"
    seed_ready_mqtt_samples(db)

    code = live_sync.main(
        [
            "mqtt-readiness",
            "--db",
            str(db),
            "--strict",
            "--json",
            "--now",
            "2026-06-27T14:31:00+00:00",
        ]
    )

    assert code == 0
    output = capsys.readouterr().out
    payload = json.loads(output)
    assert payload["status"] == "ready"
    assert payload["ready"] is True
    assert payload["realSampleCount"] == 2
    assert payload["syntheticSampleCount"] == 0
    assert payload["blockingGaps"] == []
    assert payload["fieldCoverage"]["batterySoc"] == 2
    assert payload["fieldCoverage"]["currentPartitionId"] == 2
    assert payload["fieldCoverage"]["mowingPercentage"] == 2
    assert payload["activityClasses"] == {"active": 1, "idle": 1}
    assert "activity-aware-cadence" in payload["readySurfaces"]
    encoded = json.dumps(payload, ensure_ascii=False)
    for secret in ["secret/topic", "signed.example", "topicHash", "payloadSha256"]:
        assert secret not in encoded


def test_mqtt_readiness_requires_real_idle_and_required_fields(tmp_path, capsys):
    db = tmp_path / "navimow.sqlite"
    live_sync.ingest_mqtt_message(
        db=db,
        topic="secret/topic/active",
        payload=json.dumps(
            {
                "vehicleState": "isRunning",
                "soc": 82,
                "reportTime": 1782570000000,
            }
        ).encode(),
        observed_at="2026-06-27T14:20:00+00:00",
    )

    code = live_sync.main(
        [
            "mqtt-readiness",
            "--db",
            str(db),
            "--strict",
            "--now",
            "2026-06-27T14:21:00+00:00",
        ]
    )

    assert code == 1
    output = capsys.readouterr().out
    assert "no real idle/docked/ready status sample captured" in output
    assert "no currentPartitionId field observed" in output
    assert "no mowingPercentage field observed" in output
    assert "secret/topic" not in output


def test_mqtt_readiness_rejects_stale_real_samples(tmp_path, capsys):
    db = tmp_path / "navimow.sqlite"
    seed_ready_mqtt_samples(db)

    code = live_sync.main(
        [
            "mqtt-readiness",
            "--db",
            str(db),
            "--strict",
            "--json",
            "--now",
            "2026-06-27T15:00:00+00:00",
        ]
    )

    assert code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ready"] is False
    assert payload["observed"]["stale"] is True
    assert "latest real MQTT sample is stale" in payload["blockingGaps"][-1]


def test_run_mqtt_listener_subscribes_and_stores_sanitized_messages(tmp_path, monkeypatch, capsys):
    class FakeMessage:
        topic = "secret/topic/one"
        payload = json.dumps({"vehicleState": "isRunning", "soc": 91, "token": "secret-token"}).encode()

    class FakeClient:
        created = []

        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs
            self.subscriptions = []
            FakeClient.created.append(self)

        def username_pw_set(self, username, password):
            self.username = username
            self.password = password

        def ws_set_options(self, path):
            self.path = path

        def tls_set_context(self, context):
            self.tls_context = context

        def connect(self, host, port, keepalive):
            self.host = host
            self.port = port
            self.keepalive = keepalive

        def subscribe(self, topic):
            self.subscriptions.append(topic)

        def loop_start(self):
            self.on_connect(self, None, None, 0)
            self.on_message(self, None, FakeMessage())

        def loop_stop(self):
            self.stopped = True

        def disconnect(self):
            self.disconnected = True

    paho_module = types.ModuleType("paho")
    paho_mqtt_module = types.ModuleType("paho.mqtt")
    paho_client_module = types.ModuleType("paho.mqtt.client")
    paho_client_module.Client = FakeClient
    paho_client_module.CallbackAPIVersion = types.SimpleNamespace(VERSION2="callback-v2")
    paho_module.mqtt = paho_mqtt_module
    paho_mqtt_module.client = paho_client_module
    monkeypatch.setitem(sys.modules, "paho", paho_module)
    monkeypatch.setitem(sys.modules, "paho.mqtt", paho_mqtt_module)
    monkeypatch.setitem(sys.modules, "paho.mqtt.client", paho_client_module)

    update_calls = []

    def fake_update_live_status_artifact(**kwargs):
        update_calls.append(kwargs)

    monkeypatch.setattr(live_sync, "update_live_status_artifact", fake_update_live_status_artifact)

    count = live_sync.run_mqtt_listener(
        metadata={
            "host": "mqtt.example",
            "port": 443,
            "transport": "websockets",
            "tls": False,
            "path": "/mqtt/path",
            "username": "secret-user",
            "password": "secret-password",
            "clientId": "secret-client",
            "topics": ["secret/topic/one"],
        },
        db=tmp_path / "navimow.sqlite",
        max_messages=1,
        duration=1,
        update_live_status=True,
        viewer_output=tmp_path / "viewer",
    )

    assert count == 1
    client = FakeClient.created[0]
    assert client.kwargs["callback_api_version"] == "callback-v2"
    assert client.subscriptions == ["secret/topic/one"]
    assert update_calls == [{"db": tmp_path / "navimow.sqlite", "output": tmp_path / "viewer"}]
    output = capsys.readouterr().out
    assert "mqtt connected; subscribed_topics=1" in output
    assert "mqtt_message=1" in output
    assert "secret" not in output
    con = sqlite3.connect(tmp_path / "navimow.sqlite")
    row = con.execute("SELECT sanitized_json FROM route_snapshot_records WHERE route_alias='mqtt-message'").fetchone()
    exported = row[0]
    assert "secret/topic/one" not in exported
    assert "secret-token" not in exported
    assert "vehicleState" in exported
    assert "isRunning" in exported


def test_run_mqtt_listener_refreshes_live_status_artifact_for_ui(tmp_path, monkeypatch, capsys):
    import build_navimow_map_viewer as viewer
    from test_navimow_map_viewer import create_fixture_db

    class FakeMessage:
        topic = "secret/topic/ui"
        payload = json.dumps(
            {
                "vehicleState": "isRunning",
                "workStatus": "MOWING",
                "soc": 69,
                "currentPartitionId": 2,
                "mowingPercentage": 77,
                "reportTime": 1782572000000,
                "token": "secret-token",
                "signedUrl": "https://signed.example/private",
                "latitude": "57.0",
            }
        ).encode()

    class FakeClient:
        def __init__(self, *args, **kwargs):
            self.subscriptions = []

        def username_pw_set(self, username, password):
            self.username = username
            self.password = password

        def connect(self, host, port, keepalive):
            self.host = host
            self.port = port

        def subscribe(self, topic):
            self.subscriptions.append(topic)

        def loop_start(self):
            self.on_connect(self, None, None, 0)
            self.on_message(self, None, FakeMessage())

        def loop_stop(self):
            self.stopped = True

        def disconnect(self):
            self.disconnected = True

    paho_module = types.ModuleType("paho")
    paho_mqtt_module = types.ModuleType("paho.mqtt")
    paho_client_module = types.ModuleType("paho.mqtt.client")
    paho_client_module.Client = FakeClient
    paho_module.mqtt = paho_mqtt_module
    paho_mqtt_module.client = paho_client_module
    monkeypatch.setitem(sys.modules, "paho", paho_module)
    monkeypatch.setitem(sys.modules, "paho.mqtt", paho_mqtt_module)
    monkeypatch.setitem(sys.modules, "paho.mqtt.client", paho_client_module)

    db = create_fixture_db(tmp_path)
    output = tmp_path / "viewer"
    con = live_sync.store.connect(db)
    data = viewer.build_data(con, output, include_satellite=False)
    viewer.write_viewer(output, data)

    count = live_sync.run_mqtt_listener(
        metadata={
            "host": "mqtt.example",
            "port": 1883,
            "transport": "tcp",
            "tls": False,
            "username": "secret-user",
            "password": "secret-password",
            "topics": ["secret/topic/ui"],
        },
        db=db,
        max_messages=1,
        duration=1,
        update_live_status=True,
        viewer_output=output,
    )

    assert count == 1
    status = json.loads((output / "navimow-live-status.json").read_text(encoding="utf-8"))
    encoded = json.dumps(status, ensure_ascii=False)
    assert status["mower"]["routeInsights"]["mqttStatus"]["batterySoc"] == 69
    assert status["mower"]["routeInsights"]["mqttStatus"]["currentPartitionId"] == 2
    assert status["mower"]["routeInsights"]["mqttStatus"]["mowingPercentage"] == 77
    assert status["areaStatus"]["2"]["live"]["active"] is True
    assert status["areaStatus"]["2"]["live"]["mowingPercentage"] == 77
    assert "live_status=updated" in capsys.readouterr().out
    for secret in ["secret/topic/ui", "secret-token", "signed.example", "latitude", "topicHash", "payloadSha256"]:
        assert secret not in encoded


def test_mqtt_ui_report_shows_browser_feed_but_requires_real_samples(tmp_path, capsys):
    import build_navimow_map_viewer as viewer
    from test_navimow_map_viewer import create_fixture_db

    config, responses = write_mqtt_fixture_files(tmp_path)
    db = create_fixture_db(tmp_path)
    output = tmp_path / "viewer"
    con = live_sync.store.connect(db)
    data = viewer.build_data(con, output, include_satellite=False)
    viewer.write_viewer(output, data)
    con.close()

    replay_code = live_sync.main(
        [
            "mqtt-replay-smoke",
            "--db",
            str(db),
            "--viewer-output",
            str(output),
            "--area-id",
            "2",
            "--observed-at",
            "2026-06-27T12:00:00+00:00",
            "--report-time",
            "1782563600000",
            "--battery-soc",
            "68",
            "--mowing-percentage",
            "44",
            "--update-live-status",
        ]
    )
    assert replay_code == 0
    capsys.readouterr()
    generated_at = json.loads((output / "navimow-live-status.json").read_text(encoding="utf-8"))["generatedAt"]

    code = live_sync.main(
        [
            "mqtt-ui-report",
            "--config",
            str(config),
            "--db",
            str(db),
            "--responses-dir",
            str(responses),
            "--viewer-output",
            str(output),
            "--json",
            "--now",
            generated_at,
        ]
    )

    assert code == 0
    output_text = capsys.readouterr().out
    payload = json.loads(output_text)
    assert payload["status"] == "needs_real_samples"
    assert payload["ready"] is False
    assert payload["metadata"]["topicCount"] == 2
    assert payload["listener"]["mode"] == "skipped"
    assert payload["ui"]["browserFeedReady"] is True
    assert payload["ui"]["mqttStatusVisible"] is True
    assert payload["ui"]["mqttMessagesVisible"] is True
    assert payload["ui"]["activeAreaCount"] == 1
    assert payload["ui"]["mqttStatusFields"] == {
        "batterySoc": True,
        "currentPartitionId": True,
        "mowingPercentage": True,
    }
    assert payload["mqttReadiness"]["ready"] is False
    assert payload["mqttReadiness"]["syntheticSampleCount"] >= 1
    assert payload["mqttReadiness"]["realSampleCount"] < payload["mqttReadiness"]["sampleCount"]
    assert "make mqtt-listen MAX_MESSAGES=500 DURATION=600" in payload["nextSteps"]
    for secret in [
        "mqtt.example",
        "mqtt-secret-user",
        "mqtt-secret-password",
        "mqtt-secret-client",
        "secret/topic",
        "topicHash",
        "payloadSha256",
        "signed.example",
        "latitude",
    ]:
        assert secret not in output_text


def test_mqtt_ui_report_strict_fails_zero_message_listener_without_secrets(tmp_path, monkeypatch, capsys):
    import build_navimow_map_viewer as viewer
    from test_navimow_map_viewer import create_fixture_db

    config, responses = write_mqtt_fixture_files(tmp_path)
    db = create_fixture_db(tmp_path)
    output = tmp_path / "viewer"
    con = live_sync.store.connect(db)
    data = viewer.build_data(con, output, include_satellite=False)
    viewer.write_viewer(output, data)
    con.close()

    def fake_run_mqtt_listener(**kwargs):
        return 0

    monkeypatch.setattr(live_sync, "run_mqtt_listener", fake_run_mqtt_listener)

    code = live_sync.main(
        [
            "mqtt-ui-report",
            "--config",
            str(config),
            "--db",
            str(db),
            "--responses-dir",
            str(responses),
            "--viewer-output",
            str(output),
            "--listen",
            "--max-messages",
            "5",
            "--duration",
            "60",
            "--strict",
        ]
    )

    assert code == 1
    output_text = capsys.readouterr().out
    assert "mqtt UI report: waiting_for_messages" in output_text
    assert "listener: listen; messages=0; duration=60.0s; max_messages=5" in output_text
    assert "blocking gaps:" in output_text
    for secret in [
        "mqtt.example",
        "mqtt-secret-user",
        "mqtt-secret-password",
        "mqtt-secret-client",
        "secret/topic",
        "/mqtt/private-path",
    ]:
        assert secret not in output_text


def test_run_mqtt_listener_hides_secret_values_on_connect_failure(tmp_path, monkeypatch):
    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def username_pw_set(self, username, password):
            self.username = username
            self.password = password

        def connect(self, host, port, keepalive):
            raise RuntimeError("mqtt.example secret-password secret/topic")

        def loop_stop(self):
            pass

        def disconnect(self):
            pass

    paho_module = types.ModuleType("paho")
    paho_mqtt_module = types.ModuleType("paho.mqtt")
    paho_client_module = types.ModuleType("paho.mqtt.client")
    paho_client_module.Client = FakeClient
    paho_client_module.CallbackAPIVersion = types.SimpleNamespace(VERSION2="callback-v2")
    paho_module.mqtt = paho_mqtt_module
    paho_mqtt_module.client = paho_client_module
    monkeypatch.setitem(sys.modules, "paho", paho_module)
    monkeypatch.setitem(sys.modules, "paho.mqtt", paho_mqtt_module)
    monkeypatch.setitem(sys.modules, "paho.mqtt.client", paho_client_module)

    message = None
    try:
        live_sync.run_mqtt_listener(
            metadata={
                "host": "mqtt.example",
                "port": 443,
                "transport": "websockets",
                "tls": False,
                "username": "secret-user",
                "password": "secret-password",
                "clientId": "secret-client",
                "topics": ["secret/topic/one"],
            },
            db=tmp_path / "navimow.sqlite",
            max_messages=1,
            duration=1,
            update_live_status=False,
            viewer_output=tmp_path / "viewer",
        )
    except SystemExit as exc:
        message = str(exc)

    assert message == "MQTT listener failed (RuntimeError)"
    assert "mqtt.example" not in message
    assert "secret-password" not in message
    assert "secret/topic" not in message


def test_run_mqtt_listener_fails_on_bad_connack_without_secrets(tmp_path, monkeypatch, capsys):
    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def username_pw_set(self, username, password):
            pass

        def connect(self, host, port, keepalive):
            pass

        def loop_start(self):
            self.on_connect(self, None, None, 5)

        def loop_stop(self):
            pass

        def disconnect(self):
            pass

    paho_module = types.ModuleType("paho")
    paho_mqtt_module = types.ModuleType("paho.mqtt")
    paho_client_module = types.ModuleType("paho.mqtt.client")
    paho_client_module.Client = FakeClient
    paho_client_module.CallbackAPIVersion = types.SimpleNamespace(VERSION2="callback-v2")
    paho_module.mqtt = paho_mqtt_module
    paho_mqtt_module.client = paho_client_module
    monkeypatch.setitem(sys.modules, "paho", paho_module)
    monkeypatch.setitem(sys.modules, "paho.mqtt", paho_mqtt_module)
    monkeypatch.setitem(sys.modules, "paho.mqtt.client", paho_client_module)

    message = None
    try:
        live_sync.run_mqtt_listener(
            metadata={
                "host": "mqtt.example",
                "port": 443,
                "transport": "websockets",
                "tls": False,
                "username": "secret-user",
                "password": "secret-password",
                "clientId": "secret-client",
                "topics": ["secret/topic/one"],
            },
            db=tmp_path / "navimow.sqlite",
            max_messages=1,
            duration=1,
            update_live_status=False,
            viewer_output=tmp_path / "viewer",
        )
    except SystemExit as exc:
        message = str(exc)

    assert message == "MQTT listener failed (ConnectFailed)"
    output = capsys.readouterr()
    encoded = output.out + output.err + message
    assert "mqtt connect failed" in output.err
    assert "mqtt.example" not in encoded
    assert "secret-password" not in encoded
    assert "secret/topic" not in encoded


def test_oauth_login_url_prints_authorization_url(capsys):
    code = live_sync.main(["oauth-login-url"])

    assert code == 0
    output = capsys.readouterr().out
    assert "navimow-h5-fra.willand.com/smartHome/login" in output
    assert "client_id=homeassistant" in output
    assert "redirect_uri=" in output


def test_oauth_exchange_code_writes_token_without_printing_values(tmp_path, monkeypatch, capsys):
    requests = []

    def fake_urlopen(request, timeout, context):
        requests.append(request)
        return FakeHttpResponse(
            {
                "access_token": "secret-access",
                "refresh_token": "secret-refresh",
                "expires_in": 3600,
            }
        )

    monkeypatch.setattr(live_sync.urllib.request, "urlopen", fake_urlopen)
    config = tmp_path / "config.json"
    token_file = tmp_path / "navimow-oauth.local.json"
    write_json(config, {"auth": {"provider": "navimow-oauth", "tokenFile": str(token_file)}})

    code = live_sync.main(
        [
            "oauth-exchange-code",
            "--config",
            str(config),
            "--code",
            "http://localhost:1/callback?code=AUTHCODE123",
        ]
    )

    assert code == 0
    output = capsys.readouterr().out
    assert "secret-access" not in output
    assert "secret-refresh" not in output
    written = json.loads(token_file.read_text(encoding="utf-8"))
    assert written["access_token"] == "secret-access"
    assert written["refresh_token"] == "secret-refresh"
    assert written["expires_at"]
    body = urllib.parse.parse_qs(requests[0].data.decode())
    assert body["grant_type"] == ["authorization_code"]
    assert body["code"] == ["AUTHCODE123"]
    assert body["client_id"] == ["homeassistant"]


def test_oauth_refresh_prepares_authorization_header(tmp_path, monkeypatch):
    def fake_urlopen(request, timeout, context):
        body = urllib.parse.parse_qs(request.data.decode())
        assert body["grant_type"] == ["refresh_token"]
        assert body["refresh_token"] == ["old-refresh"]
        return FakeHttpResponse(
            {
                "access_token": "new-access",
                "expires_in": 3600,
            }
        )

    monkeypatch.setattr(live_sync.urllib.request, "urlopen", fake_urlopen)
    token_file = tmp_path / "navimow-oauth.local.json"
    write_json(
        token_file,
        {
            "access_token": "old-access",
            "refresh_token": "old-refresh",
            "expires_at": "2000-01-01T00:00:00+00:00",
        },
    )
    config = live_sync.load_config(tmp_path / "missing.json")
    config["auth"] = {"provider": "navimow-oauth", "tokenFile": str(token_file)}
    config["headers"].pop("Authorization", None)

    prepared = live_sync.prepare_config_for_network(config, timeout=1)

    assert prepared["headers"]["Authorization"] == "Bearer new-access"
    written = json.loads(token_file.read_text(encoding="utf-8"))
    assert written["access_token"] == "new-access"
    assert written["refresh_token"] == "old-refresh"


def test_oauth_doctor_reports_token_presence_without_values(tmp_path, capsys):
    config = tmp_path / "config.json"
    token_file = tmp_path / "navimow-oauth.local.json"
    write_json(config, {"auth": {"provider": "navimow-oauth", "tokenFile": str(token_file)}})
    write_json(
        token_file,
        {
            "access_token": "secret-access",
            "refresh_token": "secret-refresh",
            "expires_at": "2099-01-01T00:00:00+00:00",
        },
    )

    code = live_sync.main(["oauth-doctor", "--config", str(config)])

    assert code == 0
    output = capsys.readouterr().out
    assert "access token: present" in output
    assert "refresh token: present" in output
    assert "refresh due: False" in output
    assert "secret-access" not in output
    assert "secret-refresh" not in output


def test_openapi_preflight_reports_ready_without_secret_values(tmp_path, capsys):
    config = tmp_path / "config.json"
    token_file = tmp_path / "navimow-oauth.local.json"
    write_json(
        token_file,
        {
            "access_token": "secret-access",
            "refresh_token": "secret-refresh",
            "expires_at": "2099-01-01T00:00:00+00:00",
        },
    )
    write_json(
        config,
        {
            "routes": ["openapi-auth-list", "openapi-vehicle-status", "openapi-mqtt-info"],
            "auth": {"provider": "navimow-oauth", "tokenFile": str(token_file)},
            "requestBodies": {"openapi-vehicle-status": {"devices": [{"id": "secret-device-id"}]}},
        },
    )

    code = live_sync.main(["openapi-preflight", "--config", str(config)])

    assert code == 0
    output = capsys.readouterr().out
    assert "openapi preflight: ok" in output
    assert "openapi status devices: 1" in output
    for secret in ["secret-access", "secret-refresh", "secret-device-id"]:
        assert secret not in output


def test_openapi_preflight_fails_missing_config_with_next_steps(tmp_path, capsys):
    config = tmp_path / "missing.local.json"

    code = live_sync.main(["openapi-preflight", "--config", str(config)])

    assert code == 1
    output = capsys.readouterr().out
    assert f"OpenAPI config is missing: {config}" in output
    assert "init-openapi-config" in output
    assert "oauth-login-url" in output
    assert "openapi preflight: needs attention" in output


def test_openapi_preflight_fails_missing_status_devices(tmp_path, capsys):
    config = tmp_path / "config.json"
    token_file = tmp_path / "navimow-oauth.local.json"
    write_json(
        token_file,
        {
            "access_token": "secret-access",
            "refresh_token": "secret-refresh",
            "expires_at": "2099-01-01T00:00:00+00:00",
        },
    )
    write_json(
        config,
        {
            "routes": ["openapi-auth-list", "openapi-vehicle-status", "openapi-mqtt-info"],
            "auth": {"provider": "navimow-oauth", "tokenFile": str(token_file)},
            "requestBodies": {"openapi-vehicle-status": {"devices": []}},
        },
    )

    code = live_sync.main(["openapi-preflight", "--config", str(config)])

    assert code == 1
    output = capsys.readouterr().out
    assert "OpenAPI vehicle-status request body has no configured devices" in output
    assert "sync-once --config" in output
    assert "configure-openapi-status" in output
    assert "secret-access" not in output
    assert "secret-refresh" not in output


def test_init_openapi_config_writes_oauth_safe_local_template(tmp_path, capsys):
    config = tmp_path / "navimow-live-sync.local.json"

    code = live_sync.main(["init-openapi-config", "--output", str(config)])

    assert code == 0
    output = capsys.readouterr().out
    assert str(config) in output
    written = json.loads(config.read_text(encoding="utf-8"))
    assert written["vehicleSn"] == ""
    assert written["routes"] == ["openapi-auth-list", "openapi-mqtt-info"]
    assert "Authorization" not in written["headers"]
    assert written["auth"]["provider"] == "navimow-oauth"
    assert written["requestBodies"]["openapi-vehicle-status"] == {"devices": []}


def test_init_openapi_config_refuses_to_clobber_without_force(tmp_path, capsys):
    config = tmp_path / "navimow-live-sync.local.json"
    write_json(config, {"keep": "existing"})

    code = live_sync.main(["init-openapi-config", "--output", str(config)])

    assert code == 1
    assert json.loads(config.read_text(encoding="utf-8")) == {"keep": "existing"}
    assert "already exists" in capsys.readouterr().err


def test_configure_openapi_status_uses_sanitized_auth_list_without_printing_ids(tmp_path, capsys):
    db = tmp_path / "navimow.sqlite"
    config = tmp_path / "navimow-live-sync.local.json"
    responses = tmp_path / "responses"
    device_id = "synthetic-device-id"
    write_json(config, {"routes": ["openapi-auth-list"], "auth": {"provider": "navimow-oauth"}})
    write_json(
        responses / "openapi-auth-list.json",
        {"code": 1, "data": [{"id": device_id, "name": "Fixture mower"}]},
    )
    assert (
        live_sync.main(
            [
                "sync-once",
                "--config",
                str(config),
                "--db",
                str(db),
                "--routes",
                "openapi-auth-list",
                "--responses-dir",
                str(responses),
            ]
        )
        == 0
    )
    capsys.readouterr()

    code = live_sync.main(["configure-openapi-status", "--config", str(config), "--db", str(db)])

    assert code == 0
    output = capsys.readouterr().out
    assert "configured 1 OpenAPI device id(s)" in output
    assert device_id not in output
    written = json.loads(config.read_text(encoding="utf-8"))
    assert written["routes"] == ["openapi-auth-list", "openapi-vehicle-status", "openapi-mqtt-info"]
    assert written["requestBodies"]["openapi-vehicle-status"] == {"devices": [{"id": device_id}]}
    assert "Authorization" not in written["headers"]


def test_openapi_sync_promotes_safe_typed_auth_and_status_rows(tmp_path, capsys):
    db = tmp_path / "navimow.sqlite"
    config = tmp_path / "navimow-live-sync.local.json"
    responses = tmp_path / "responses"
    secret_device_id = "synthetic-secret-device-id"
    write_json(
        config,
        {
            "routes": ["openapi-auth-list", "openapi-vehicle-status"],
            "auth": {"provider": "navimow-oauth"},
            "requestBodies": {"openapi-vehicle-status": {"devices": [{"id": secret_device_id}]}},
        },
    )
    write_json(
        responses / "openapi-auth-list.json",
        {
            "code": 1,
            "data": {
                "devices": [
                    {
                        "id": secret_device_id,
                        "name": "Fixture mower",
                        "model": "CM120M1",
                        "firmware": "1.2.3",
                    }
                ]
            },
        },
    )
    write_json(
        responses / "openapi-vehicle-status.json",
        {
            "code": 1,
            "data": {
                "devices": [
                    {
                        "id": secret_device_id,
                        "vehicleState": "READY",
                        "capacityRemaining": [{"rawValue": "64", "unit": "PERCENTAGE"}],
                        "descriptiveCapacityRemaining": "HIGH",
                    }
                ]
            },
        },
    )

    code = live_sync.main(
        [
            "sync-once",
            "--config",
            str(config),
            "--db",
            str(db),
            "--routes",
            "openapi-auth-list,openapi-vehicle-status",
            "--responses-dir",
            str(responses),
            "--observed-at",
            "2026-06-27T12:00:00+00:00",
        ]
    )

    assert code == 0
    output = capsys.readouterr().out
    assert secret_device_id not in output
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    auth = con.execute("SELECT device_count, devices_json FROM openapi_auth_snapshots").fetchone()
    status = con.execute(
        "SELECT device_count, vehicle_state, capacity_percent, capacity_label, statuses_json FROM openapi_status_snapshots"
    ).fetchone()
    assert auth["device_count"] == 1
    assert status["device_count"] == 1
    assert status["vehicle_state"] == "READY"
    assert status["capacity_percent"] == 64
    assert status["capacity_label"] == "HIGH"
    encoded = auth["devices_json"] + status["statuses_json"]
    assert "Fixture mower" in encoded
    assert live_sync.store.short_hash(secret_device_id) in encoded
    assert secret_device_id not in encoded
    assert live_sync.latest_typed_route_summary(con, "openapi-auth-list")["source"] == "openapi_auth_snapshots"
    assert live_sync.latest_typed_route_summary(con, "openapi-vehicle-status")["source"] == "openapi_status_snapshots"


def test_openapi_typed_backfill_promotes_existing_sanitized_snapshots(tmp_path):
    db = tmp_path / "navimow.sqlite"
    secret_device_id = "legacy-secret-device-id"
    con = live_sync.store.connect(db)
    source_id = live_sync.add_live_source(
        con,
        "openapi-fixture",
        {"fixture": True},
        "2026-06-27T12:00:00+00:00",
    )
    live_sync.store.insert_route_snapshot_record(
        con,
        source_id=source_id,
        vehicle_sn=None,
        observed_at="2026-06-27T12:00:00+00:00",
        route_alias="openapi-auth-list",
        data={"payload": {"devices": [{"id": secret_device_id, "name": "Fixture mower"}]}},
    )
    live_sync.store.insert_route_snapshot_record(
        con,
        source_id=source_id,
        vehicle_sn=None,
        observed_at="2026-06-27T12:01:00+00:00",
        route_alias="openapi-vehicle-status",
        data={"payload": {"devices": [{"id": secret_device_id, "vehicleState": "isRunning", "batterySoc": 77}]}},
    )
    con.execute("DELETE FROM openapi_auth_snapshots")
    con.execute("DELETE FROM openapi_status_snapshots")
    con.commit()
    con.close()

    con = live_sync.store.connect(db)

    assert con.execute("SELECT COUNT(*) FROM openapi_auth_snapshots").fetchone()[0] == 1
    assert con.execute("SELECT COUNT(*) FROM openapi_status_snapshots").fetchone()[0] == 1
    status = con.execute("SELECT vehicle_state, capacity_percent FROM openapi_status_snapshots").fetchone()
    assert status["vehicle_state"] == "isRunning"
    assert status["capacity_percent"] == 77
    encoded = "\n".join(
        row[0]
        for row in con.execute(
            "SELECT devices_json FROM openapi_auth_snapshots UNION ALL SELECT statuses_json FROM openapi_status_snapshots"
        ).fetchall()
    )
    assert secret_device_id not in encoded
    assert live_sync.store.short_hash(secret_device_id) in encoded


def test_plan_warns_when_oauth_provider_uses_consumer_routes(tmp_path, capsys):
    config = tmp_path / "config.json"
    write_json(config, {"routes": ["index2"], "auth": {"provider": "navimow-oauth"}})

    code = live_sync.main(["plan", "--config", str(config)])

    assert code == 0
    output = capsys.readouterr().out
    assert "warnings:" in output
    assert "OAuth/OpenAPI auth is configured" in output
    assert "index2" in output


def test_openapi_vehicle_status_requires_configured_devices_before_network(monkeypatch):
    def fake_urlopen(request, timeout, context):
        raise AssertionError("network should not be called")

    monkeypatch.setattr(live_sync.urllib.request, "urlopen", fake_urlopen)
    config = live_sync.openapi_config_from(live_sync.CONFIG_TEMPLATE)
    config["routes"] = ["openapi-vehicle-status"]
    config["requestBodies"]["openapi-vehicle-status"] = {"devices": []}

    message = None
    try:
        live_sync.request_route(config, "openapi-vehicle-status", timeout=1)
    except SystemExit as exc:
        message = str(exc)

    assert "requires configured devices" in message


def test_openapi_route_dry_run_uses_declared_methods(tmp_path, capsys):
    config = tmp_path / "config.json"
    write_json(config, {"routes": ["openapi-auth-list", "openapi-vehicle-status"]})

    code = live_sync.main(["sync-once", "--config", str(config), "--routes", "openapi-auth-list,openapi-vehicle-status", "--dry-run"])

    assert code == 0
    output = capsys.readouterr().out
    assert "would GET /openapi/smarthome/authList" in output
    assert "would POST /openapi/smarthome/getVehicleStatus" in output


def test_openapi_response_commands_is_read_after_command_but_send_is_refused():
    live_sync.assert_read_only_path(live_sync.READ_ROUTES["openapi-response-commands"]["path"])

    code = None
    try:
        live_sync.assert_read_only_path("/openapi/smarthome/sendCommands")
    except SystemExit as exc:
        code = exc.code

    assert code == "Refusing write/command route: /openapi/smarthome/sendCommands"


def make_live_health_fixture(
    tmp_path: Path,
    *,
    routes: list[str] | None = None,
    observed_at: str = "2026-06-27T12:00:00+00:00",
    live_status_generated_at: str = "2026-06-27T12:00:00+00:00",
    token_expires_at: str = "2099-01-01T00:00:00+00:00",
    status_devices: bool = True,
) -> tuple[Path, Path, Path, str]:
    db = tmp_path / "navimow.sqlite"
    config = tmp_path / "navimow-live-sync.local.json"
    token_file = tmp_path / "navimow-oauth.local.json"
    viewer = tmp_path / "viewer"
    secret_device_id = "secret-device-id"
    write_json(
        token_file,
        {
            "access_token": "secret-access-token",
            "refresh_token": "secret-refresh-token",
            "expires_at": token_expires_at,
        },
    )
    write_json(
        config,
        {
            "routes": routes or ["openapi-auth-list", "openapi-vehicle-status", "openapi-mqtt-info"],
            "headers": {},
            "auth": {"provider": "navimow-oauth", "tokenFile": str(token_file)},
            "requestBodies": {
                "openapi-vehicle-status": {"devices": [{"id": secret_device_id}] if status_devices else []}
            },
        },
    )

    con = live_sync.store.connect(db)
    for alias, payload in [
        ("openapi-auth-list", {"payload": {"devices": [{"id": secret_device_id}]}}),
        ("openapi-vehicle-status", {"payload": {"devices": [{"id": secret_device_id, "vehicleState": "isRunning"}]}}),
        ("openapi-mqtt-info", {"configured": True, "topicCount": 2}),
    ]:
        source_id = live_sync.add_live_source(con, alias, payload, observed_at)
        live_sync.store.insert_route_snapshot_record(
            con,
            source_id=source_id,
            vehicle_sn=None,
            observed_at=observed_at,
            route_alias=alias,
            data=payload,
        )
    con.commit()

    viewer.mkdir()
    (viewer / "navimow-map-data.js").write_text("window.NAVIMOW_MAP_DATA = {};", encoding="utf-8")
    write_json(
        viewer / "navimow-live-status.json",
        {
            "generatedAt": live_status_generated_at,
            "layoutVersion": "layout-a",
            "mower": {
                "routeInsights": {
                    "openapiStatus": {"deviceHash": secret_device_id},
                    "mqtt": {"topics": ["secret/topic"]},
                }
            },
        },
    )
    return config, db, viewer, secret_device_id


def test_live_health_reports_readiness_without_secret_values(tmp_path, capsys):
    config, db, viewer, secret_device_id = make_live_health_fixture(tmp_path)

    code = live_sync.main(
        [
            "live-health",
            "--config",
            str(config),
            "--db",
            str(db),
            "--viewer-output",
            str(viewer),
        ]
    )

    assert code == 0
    output = capsys.readouterr().out
    assert "live health: ok" in output
    assert "oauth access token: present" in output
    assert "openapi status devices: 1" in output
    assert "openapi-auth-list: present" in output
    assert "auth-list: missing" in output
    assert "live status insights: mqtt, openapiStatus" in output
    assert "mqtt-message: missing" in output
    for secret in [
        "secret-access-token",
        "secret-refresh-token",
        secret_device_id,
        "secret/topic",
    ]:
        assert secret not in output


def test_live_health_strict_fails_stale_configured_route(tmp_path, capsys):
    config, db, viewer, _secret_device_id = make_live_health_fixture(
        tmp_path,
        observed_at="2026-06-27T12:00:00+00:00",
        live_status_generated_at="2026-06-27T12:10:00+00:00",
    )

    code = live_sync.main(
        [
            "live-health",
            "--config",
            str(config),
            "--db",
            str(db),
            "--viewer-output",
            str(viewer),
            "--strict",
            "--now",
            "2026-06-27T12:10:00+00:00",
        ]
    )

    assert code == 1
    output = capsys.readouterr().out
    assert "openapi-vehicle-status snapshot is stale" in output
    assert "live health: needs attention" in output


def test_live_health_strict_fails_stale_live_status(tmp_path, capsys):
    config, db, viewer, _secret_device_id = make_live_health_fixture(
        tmp_path,
        observed_at="2026-06-27T12:01:00+00:00",
        live_status_generated_at="2026-06-27T11:50:00+00:00",
    )

    code = live_sync.main(
        [
            "live-health",
            "--config",
            str(config),
            "--db",
            str(db),
            "--viewer-output",
            str(viewer),
            "--strict",
            "--now",
            "2026-06-27T12:01:00+00:00",
        ]
    )

    assert code == 1
    output = capsys.readouterr().out
    assert "viewer live status is stale" in output
    assert "live status age: 660s threshold=300s stale" in output


def test_live_health_strict_fails_token_due_for_refresh(tmp_path, capsys):
    config, db, viewer, _secret_device_id = make_live_health_fixture(
        tmp_path,
        observed_at="2026-06-27T12:00:00+00:00",
        live_status_generated_at="2026-06-27T12:00:00+00:00",
        token_expires_at="2026-06-27T12:02:00+00:00",
    )

    code = live_sync.main(
        [
            "live-health",
            "--config",
            str(config),
            "--db",
            str(db),
            "--viewer-output",
            str(viewer),
            "--strict",
            "--now",
            "2026-06-27T12:00:00+00:00",
        ]
    )

    assert code == 1
    output = capsys.readouterr().out
    assert "oauth refresh due: True" in output
    assert "OAuth access token is due for refresh" in output


def test_live_health_strict_uses_selected_typed_route_table_freshness(tmp_path, capsys):
    config, db, viewer, _secret_device_id = make_live_health_fixture(
        tmp_path,
        routes=["get-location"],
        observed_at="2026-06-27T12:10:00+00:00",
        live_status_generated_at="2026-06-27T12:10:00+00:00",
    )
    con = live_sync.store.connect(db)
    source_id = live_sync.add_live_source(
        con,
        "get-location",
        {"posture_x": 10, "posture_y": 20, "mowing_percentage": 55},
        "2026-06-27T12:00:00+00:00",
    )
    live_sync.store.insert_live_location_snapshot(
        con,
        source_id=source_id,
        vehicle_sn=None,
        observed_at="2026-06-27T12:00:00+00:00",
        line_no=1,
        data={"posture_x": 10, "posture_y": 20, "mowing_percentage": 55},
    )
    con.commit()

    code = live_sync.main(
        [
            "live-health",
            "--config",
            str(config),
            "--db",
            str(db),
            "--viewer-output",
            str(viewer),
            "--strict",
            "--now",
            "2026-06-27T12:10:00+00:00",
        ]
    )

    assert code == 1
    output = capsys.readouterr().out
    assert "get-location snapshot is stale" in output
    assert "source" not in output.lower()


def test_live_health_strict_ignores_missing_unselected_route(tmp_path, capsys):
    config, db, viewer, _secret_device_id = make_live_health_fixture(
        tmp_path,
        routes=["openapi-auth-list"],
        observed_at="2026-06-27T12:00:00+00:00",
        live_status_generated_at="2026-06-27T12:00:00+00:00",
    )

    code = live_sync.main(
        [
            "live-health",
            "--config",
            str(config),
            "--db",
            str(db),
            "--viewer-output",
            str(viewer),
            "--strict",
            "--now",
            "2026-06-27T12:01:00+00:00",
        ]
    )

    assert code == 0
    output = capsys.readouterr().out
    assert "get-location: missing" in output
    assert "get-location snapshot is missing" not in output


def test_live_health_strict_fails_missing_viewer_generated_at(tmp_path, capsys):
    config, db, viewer, _secret_device_id = make_live_health_fixture(tmp_path)
    live_status = json.loads((viewer / "navimow-live-status.json").read_text(encoding="utf-8"))
    live_status.pop("generatedAt")
    write_json(viewer / "navimow-live-status.json", live_status)

    code = live_sync.main(
        [
            "live-health",
            "--config",
            str(config),
            "--db",
            str(db),
            "--viewer-output",
            str(viewer),
            "--strict",
            "--now",
            "2026-06-27T12:01:00+00:00",
        ]
    )

    assert code == 1
    output = capsys.readouterr().out
    assert "viewer live status generatedAt is missing or invalid" in output
    assert "live status age: unknown threshold=300s stale" in output


def test_live_health_strict_fails_future_skewed_route_and_viewer(tmp_path, capsys):
    config, db, viewer, _secret_device_id = make_live_health_fixture(
        tmp_path,
        observed_at="2026-06-27T12:10:00+00:00",
        live_status_generated_at="2026-06-27T12:10:00+00:00",
    )

    code = live_sync.main(
        [
            "live-health",
            "--config",
            str(config),
            "--db",
            str(db),
            "--viewer-output",
            str(viewer),
            "--strict",
            "--now",
            "2026-06-27T12:00:00+00:00",
        ]
    )

    assert code == 1
    output = capsys.readouterr().out
    assert "openapi-auth-list snapshot timestamp is in the future: skew=600s" in output
    assert "viewer live status timestamp is in the future: skew=600s" in output
    assert "future-skew" in output


def test_live_health_json_reports_redacted_strict_status(tmp_path, capsys):
    config, db, viewer, secret_device_id = make_live_health_fixture(tmp_path)

    code = live_sync.main(
        [
            "live-health",
            "--config",
            str(config),
            "--db",
            str(db),
            "--viewer-output",
            str(viewer),
            "--strict",
            "--json",
            "--now",
            "2026-06-27T12:01:00+00:00",
        ]
    )

    assert code == 0
    output = capsys.readouterr().out
    payload = json.loads(output)
    assert payload["strict"] is True
    assert payload["ready"] is True
    assert payload["status"] == "ok"
    assert payload["errors"] == []
    assert payload["oauth"]["accessTokenPresent"] is True
    assert payload["openapi"]["statusDevices"] == 1
    assert payload["db"]["routes"]["openapi-vehicle-status"]["ageSeconds"] == 60
    assert payload["viewer"]["liveStatusAgeSeconds"] == 60
    for secret in ["secret-access-token", "secret-refresh-token", secret_device_id, "secret/topic"]:
        assert secret not in output


def test_setup_report_json_combines_preflight_health_catalog_and_gaps_without_secrets(tmp_path, capsys):
    config, db, viewer, secret_device_id = make_live_health_fixture(tmp_path)

    code = live_sync.main(
        [
            "setup-report",
            "--config",
            str(config),
            "--db",
            str(db),
            "--viewer-output",
            str(viewer),
            "--json",
            "--now",
            "2026-06-27T12:01:00+00:00",
        ]
    )

    assert code == 0
    output = capsys.readouterr().out
    payload = json.loads(output)
    assert payload["status"] == "ok"
    assert payload["ready"] is True
    assert payload["openapiPreflight"]["summary"]["accessTokenPresent"] is True
    assert payload["openapiPreflight"]["summary"]["openapiStatusDevices"] == 1
    assert payload["liveHealth"]["status"] == "ok"
    assert payload["liveHealth"]["viewer"]["insights"] == ["mqtt", "openapiStatus"]
    assert payload["routeCatalogSummary"]["readRouteCount"] == len(live_sync.READ_ROUTES)
    assert payload["routeCatalogSummary"]["blockedWriteRouteCount"] == len(live_sync.blocked_write_catalog_rows())
    assert payload["routeCatalogSummary"]["readSurfaces"]["openapi"] >= 1
    assert "openapi-vehicle-status" in payload["routeCatalogSummary"]["openapiReadAliases"]
    assert payload["routeCoverageSummary"]["readRouteCount"] == len(live_sync.READ_ROUTES)
    assert payload["routeCoverageSummary"]["snapshotRouteCount"] >= 1
    assert "mower-state" in payload["routeCoverageSummary"]["promotionCandidates"]
    assert payload["mqttReadiness"]["status"] == "needs_real_samples"
    assert payload["mqttReadiness"]["ready"] is False
    assert payload["trailReplay"]["status"] == "needs_capture"
    assert payload["readinessSummary"]["status"] == "usable_with_gaps"
    assert payload["readinessSummary"]["canOpenConsole"] is True
    assert payload["readinessSummary"]["strictLiveDataReady"] is True
    assert payload["readinessSummary"]["oauthRefreshDue"] is False
    assert payload["readinessSummary"]["openapiRefreshRecommended"] is False
    assert payload["readinessSummary"]["liveStatusFresh"] is True
    assert payload["readinessSummary"]["mqttReady"] is False
    assert payload["readinessSummary"]["consumerSessionReady"] is False
    assert payload["readinessSummary"]["trailReplayReady"] is False
    assert payload["readinessSummary"]["recommendedNextStep"] == "make mqtt-listen MAX_MESSAGES=500 DURATION=600"
    assert payload["consumerSession"]["status"] == "openapi_only"
    assert payload["consumerSession"]["ready"] is False
    assert payload["completionAudit"]["status"] == "incomplete"
    assert payload["completionAudit"]["ready"] is False
    assert "quickstart-guide" not in payload["completionAudit"]["blockingItemIds"]
    assert {
        "mqtt-realtime-ui",
        "consumer-session-live-routes",
        "route-coverage",
        "trail-replay",
        "schedule-write-envelope",
    }.issubset(set(payload["completionAudit"]["blockingItemIds"]))
    assert {gap["id"] for gap in payload["remainingGaps"]} >= {"real-mqtt-samples", "schedule-write-envelope"}
    assert "make mqtt-listen MAX_MESSAGES=500 DURATION=600" in payload["nextSteps"]
    assert "make mqtt-readiness --strict" in payload["nextSteps"]
    assert "make consumer-session-report" in payload["nextSteps"]
    assert "make live-console" in payload["nextSteps"]
    assert "make live-route-catalog" in payload["nextSteps"]
    assert "make live-route-coverage" in payload["nextSteps"]
    assert "make trail-replay-report" in payload["nextSteps"]
    for secret in ["secret-access-token", "secret-refresh-token", secret_device_id, "secret/topic"]:
        assert secret not in output


def test_setup_report_markdown_is_redacted_and_actionable(tmp_path, capsys):
    config, db, viewer, secret_device_id = make_live_health_fixture(tmp_path)

    code = live_sync.main(
        [
            "setup-report",
            "--config",
            str(config),
            "--db",
            str(db),
            "--viewer-output",
            str(viewer),
            "--now",
            "2026-06-27T12:01:00+00:00",
        ]
    )

    assert code == 0
    output = capsys.readouterr().out
    assert "# Navimow Live Setup Report" in output
    assert "Status: ok" in output
    assert "## Readiness Summary" in output
    assert "- Setup status: usable_with_gaps" in output
    assert "- Can open console: True" in output
    assert "- Strict live data ready: True" in output
    assert "- MQTT ready for polling/UI decisions: False" in output
    assert "- Consumer session ready: False" in output
    assert "- Recommended next step: `make mqtt-listen MAX_MESSAGES=500 DURATION=600`" in output
    assert "## Route Coverage" in output
    assert "## Route Storage Coverage" in output
    assert "## MQTT Readiness" in output
    assert "## Consumer Session" in output
    assert "## Trail Replay" in output
    assert "## Completion Audit" in output
    assert "- Ready for full goal completion: False" in output
    assert "schedule-write-envelope" in output
    assert "## Next Steps" in output
    assert "`make live-console`" in output
    assert "`make consumer-session-report`" in output
    assert "real-mqtt-samples" in output
    for secret in ["secret-access-token", "secret-refresh-token", secret_device_id, "secret/topic"]:
        assert secret not in output


def test_completion_report_strict_fails_until_full_goal_ready_without_secrets(tmp_path, capsys):
    config, db, viewer, secret_device_id = make_live_health_fixture(tmp_path)

    code = live_sync.main(
        [
            "completion-report",
            "--config",
            str(config),
            "--db",
            str(db),
            "--viewer-output",
            str(viewer),
            "--json",
            "--strict",
            "--now",
            "2026-06-27T12:01:00+00:00",
        ]
    )

    assert code == 1
    output = capsys.readouterr().out
    payload = json.loads(output)
    assert payload["status"] == "incomplete"
    assert payload["ready"] is False
    assert payload["documentation"]["quickstartPresent"] is True
    assert payload["documentation"]["missingCommandDocs"] == []
    root_has_git = (ROOT / ".git" / "HEAD").exists()
    assert payload["repo"]["gitPresent"] is root_has_git
    if root_has_git:
        assert payload["repo"]["status"] in {"ready", "needs_baseline"}
    else:
        assert payload["repo"]["status"] == "missing_git"
    assert "openapi-live-console" not in payload["blockingItemIds"]
    if payload["repo"]["status"] != "ready":
        assert "repo-baseline" in payload["blockingItemIds"]
    assert "mqtt-realtime-ui" in payload["blockingItemIds"]
    assert "consumer-session-live-routes" in payload["blockingItemIds"]
    assert "schedule-write-envelope" in payload["blockingItemIds"]
    assert "make mqtt-listen MAX_MESSAGES=500 DURATION=600" in payload["nextSteps"]
    assert "make consumer-session-report" in payload["nextSteps"]
    for secret in ["secret-access-token", "secret-refresh-token", secret_device_id, "secret/topic"]:
        assert secret not in output


def seed_trail_replay_inputs(db: Path, *, include_map_context: bool) -> None:
    if include_map_context:
        from test_navimow_map_viewer import create_fixture_db

        fixture_db = create_fixture_db(db.parent)
        if fixture_db != db:
            db.write_bytes(fixture_db.read_bytes())
    con = live_sync.store.connect(db)
    source_id = live_sync.add_live_source(
        con,
        "trail-replay-fixture",
        {"fixture": True},
        "2026-06-27T12:00:00+00:00",
    )
    if not include_map_context:
        live_sync.store.insert_trail_time_snapshot(
            con,
            source_id=source_id,
            vehicle_sn="TESTSN12345",
            observed_at="2026-06-27T12:00:00+00:00",
            line_no=1,
            entries=[
                {
                    "partitionId": 2,
                    "startTime": 1782560000,
                    "endTime": 1782561800,
                    "area": 100.0,
                    "finishedArea": 45.0,
                    "partitionPercentage": 45,
                }
            ],
        )
    live_sync.store.insert_route_snapshot_record(
        con,
        source_id=source_id,
        vehicle_sn="TESTSN12345",
        observed_at="2026-06-27T12:02:00+00:00",
        route_alias="trail-data",
        data=live_sync.trail_data_snapshot_summary(
            {
                "compressedTrail": "secret-compressed-path-points",
                "origin_gps": [57.0, 26.0],
                "count": 42,
            }
        ),
    )
    con.commit()
    con.close()


def test_trail_replay_report_redacts_payload_and_reports_missing_map_context(tmp_path, capsys):
    db = tmp_path / "navimow.sqlite"
    seed_trail_replay_inputs(db, include_map_context=False)

    code = live_sync.main(["trail-replay-report", "--db", str(db), "--json"])

    assert code == 0
    output = capsys.readouterr().out
    payload = json.loads(output)
    assert payload["status"] == "needs_capture"
    assert payload["readyForDecoder"] is False
    assert payload["trailTime"]["entryCount"] == 1
    assert payload["trailData"]["present"] is True
    assert payload["trailData"]["hasPayloadHash"] is True
    assert "map-context" in payload["missing"]
    assert "make live-map-plan" in payload["nextSteps"]
    for secret in ["secret-compressed-path-points", "origin_gps", "57.0", "payloadSha256"]:
        assert secret not in output


def test_trail_replay_report_ready_for_decoder_when_inputs_exist(tmp_path, capsys):
    db = tmp_path / "navimow.sqlite"
    seed_trail_replay_inputs(db, include_map_context=True)

    code = live_sync.main(["trail-replay-report", "--db", str(db)])

    assert code == 0
    output = capsys.readouterr().out
    assert "trail replay readiness: ready_for_decoder" in output
    assert "trail-time: present" in output
    assert "trail-data: present" in output
    assert "map-context: geometry=present; render-calibration=present" in output
    assert "secret-compressed-path-points" not in output
    assert "payloadSha256" not in output


def test_setup_report_strict_json_fails_when_live_health_is_stale(tmp_path, capsys):
    config, db, viewer, secret_device_id = make_live_health_fixture(
        tmp_path,
        observed_at="2026-06-27T12:00:00+00:00",
        live_status_generated_at="2026-06-27T12:00:00+00:00",
    )

    code = live_sync.main(
        [
            "setup-report",
            "--config",
            str(config),
            "--db",
            str(db),
            "--viewer-output",
            str(viewer),
            "--strict",
            "--json",
            "--now",
            "2026-06-27T12:10:00+00:00",
        ]
    )

    assert code == 1
    output = capsys.readouterr().out
    payload = json.loads(output)
    assert payload["status"] == "needs_attention"
    assert payload["ready"] is False
    assert payload["readinessSummary"]["status"] == "blocked"
    assert payload["readinessSummary"]["canOpenConsole"] is False
    assert payload["readinessSummary"]["strictLiveDataReady"] is False
    assert payload["readinessSummary"]["openapiRefreshRecommended"] is True
    assert payload["readinessSummary"]["recommendedNextStep"] == "make openapi-refresh-status"
    assert any("openapi-vehicle-status snapshot is stale" in error for error in payload["liveHealth"]["errors"])
    assert "make openapi-refresh-status" in payload["nextSteps"]
    for secret in ["secret-access-token", "secret-refresh-token", secret_device_id, "secret/topic"]:
        assert secret not in output


def test_poll_runs_multiple_read_only_passes_from_fixtures(tmp_path, capsys):
    db = tmp_path / "navimow.sqlite"
    config, responses = write_fixture_sync_files(tmp_path)

    code = live_sync.main(
        [
            "poll",
            "--config",
            str(config),
            "--db",
            str(db),
            "--responses-dir",
            str(responses),
            "--routes",
            "get-location",
            "--interval",
            "0",
            "--max-iterations",
            "2",
        ]
    )

    assert code == 0
    output = capsys.readouterr().out
    assert "poll 1/2" in output
    assert "poll 2/2" in output
    con = sqlite3.connect(db)
    assert con.execute("SELECT COUNT(*) FROM live_location_snapshots").fetchone()[0] == 2


def test_poll_route_cadence_skips_until_route_is_due(tmp_path, capsys):
    db = tmp_path / "navimow.sqlite"
    config, responses = write_fixture_sync_files(tmp_path)

    code = live_sync.main(
        [
            "poll",
            "--config",
            str(config),
            "--db",
            str(db),
            "--responses-dir",
            str(responses),
            "--routes",
            "get-location",
            "--interval",
            "0",
            "--max-iterations",
            "2",
            "--use-route-cadence",
        ]
    )

    assert code == 0
    output = capsys.readouterr().out
    assert "poll 1/2 routes=get-location" in output
    assert "poll 2/2 routes=none" in output
    con = sqlite3.connect(db)
    assert con.execute("SELECT COUNT(*) FROM live_location_snapshots").fetchone()[0] == 1


def test_poll_activity_aware_cadence_accelerates_location_when_active(tmp_path, monkeypatch, capsys):
    db = tmp_path / "navimow.sqlite"
    config, responses = write_fixture_sync_files(tmp_path)
    write_json(
        responses / "openapi-vehicle-status.json",
        {"data": {"devices": [{"vehicleState": "isRunning", "capacityRemaining": [{"rawValue": 71, "unit": "PERCENTAGE"}]}]}},
    )
    times = iter([100.0, 100.0, 106.0, 106.0])
    monkeypatch.setattr(live_sync.time, "monotonic", lambda: next(times))

    code = live_sync.main(
        [
            "poll",
            "--config",
            str(config),
            "--db",
            str(db),
            "--responses-dir",
            str(responses),
            "--routes",
            "openapi-vehicle-status,get-location",
            "--interval",
            "0",
            "--max-iterations",
            "2",
            "--activity-aware-cadence",
        ]
    )

    assert code == 0
    output = capsys.readouterr().out
    assert "poll 1/2 routes=openapi-vehicle-status,get-location activity=active" in output
    assert "poll 2/2 routes=get-location activity=active" in output
    con = sqlite3.connect(db)
    assert con.execute("SELECT COUNT(*) FROM live_location_snapshots").fetchone()[0] == 2
    assert con.execute("SELECT COUNT(*) FROM route_snapshot_records WHERE route_alias='openapi-vehicle-status'").fetchone()[0] == 1


def test_poll_activity_aware_cadence_slows_location_when_idle(tmp_path, monkeypatch, capsys):
    db = tmp_path / "navimow.sqlite"
    config, responses = write_fixture_sync_files(tmp_path)
    write_json(
        responses / "openapi-vehicle-status.json",
        {"data": {"devices": [{"vehicleState": "READY", "capacityRemaining": [{"rawValue": 71, "unit": "PERCENTAGE"}]}]}},
    )
    times = iter([100.0, 100.0, 106.0])
    monkeypatch.setattr(live_sync.time, "monotonic", lambda: next(times))

    code = live_sync.main(
        [
            "poll",
            "--config",
            str(config),
            "--db",
            str(db),
            "--responses-dir",
            str(responses),
            "--routes",
            "openapi-vehicle-status,get-location",
            "--interval",
            "0",
            "--max-iterations",
            "2",
            "--activity-aware-cadence",
        ]
    )

    assert code == 0
    output = capsys.readouterr().out
    assert "poll 1/2 routes=openapi-vehicle-status,get-location activity=idle" in output
    assert "poll 2/2 routes=none activity=idle" in output
    con = sqlite3.connect(db)
    assert con.execute("SELECT COUNT(*) FROM live_location_snapshots").fetchone()[0] == 1


def test_live_activity_state_ignores_stale_status(tmp_path):
    db = tmp_path / "navimow.sqlite"
    con = live_sync.store.connect(db)
    source_id = live_sync.add_live_source(con, "openapi-vehicle-status", {"vehicleState": "isRunning"}, "2026-06-27T12:00:00+00:00")
    live_sync.store.insert_route_snapshot_record(
        con,
        source_id=source_id,
        vehicle_sn=None,
        observed_at="2026-06-27T12:00:00+00:00",
        route_alias="openapi-vehicle-status",
        data={"vehicleState": "isRunning"},
    )
    con.commit()
    now_epoch = int(live_sync.parse_iso_datetime("2026-06-27T12:20:00+00:00").timestamp())

    activity = live_sync.live_activity_state(db, max_age_seconds=60, now_epoch=now_epoch)

    assert activity["state"] == "unknown"


def test_live_activity_state_uses_typed_openapi_status_when_route_json_is_compacted(tmp_path):
    db = tmp_path / "navimow.sqlite"
    con = live_sync.store.connect(db)
    source_id = live_sync.add_live_source(
        con,
        "openapi-vehicle-status",
        {"payload": {"devices": [{"id": "secret-device-id", "vehicleState": "isRunning"}]}},
        "2026-06-27T12:00:00+00:00",
    )
    live_sync.store.insert_route_snapshot_record(
        con,
        source_id=source_id,
        vehicle_sn=None,
        observed_at="2026-06-27T12:00:00+00:00",
        route_alias="openapi-vehicle-status",
        data={"payload": {"devices": [{"id": "secret-device-id", "vehicleState": "isRunning"}]}},
    )
    con.execute("UPDATE route_snapshot_records SET sanitized_json='{}' WHERE route_alias='openapi-vehicle-status'")
    con.commit()
    now_epoch = int(live_sync.parse_iso_datetime("2026-06-27T12:01:00+00:00").timestamp())

    activity = live_sync.live_activity_state(db, max_age_seconds=300, now_epoch=now_epoch)

    assert activity == {
        "state": "active",
        "source": "openapi-vehicle-status",
        "field": "vehicle_state",
        "observedAt": "2026-06-27T12:00:00+00:00",
    }


def test_live_activity_state_ignores_mqtt_until_readiness_gate_passes(tmp_path):
    db = tmp_path / "navimow.sqlite"
    live_sync.ingest_mqtt_message(
        db=db,
        topic="secret/topic/active",
        payload=json.dumps(
            {
                "vehicleState": "isRunning",
                "workStatus": "MOWING",
                "soc": 82,
                "currentPartitionId": 7,
                "mowingPercentage": 33,
                "reportTime": 1782570000000,
            }
        ).encode(),
        observed_at="2026-06-27T14:20:00+00:00",
    )
    now_epoch = int(live_sync.parse_iso_datetime("2026-06-27T14:21:00+00:00").timestamp())

    activity = live_sync.live_activity_state(db, max_age_seconds=900, now_epoch=now_epoch)

    assert activity["state"] == "unknown"


def test_live_activity_state_uses_mqtt_after_readiness_gate_passes(tmp_path):
    db = tmp_path / "navimow.sqlite"
    seed_ready_mqtt_samples(db)
    now_epoch = int(live_sync.parse_iso_datetime("2026-06-27T14:31:00+00:00").timestamp())

    activity = live_sync.live_activity_state(db, max_age_seconds=900, now_epoch=now_epoch)

    assert activity["state"] == "idle"
    assert activity["source"] == "mqtt-message"


def test_activity_classifier_does_not_treat_not_running_as_active():
    assert live_sync.classify_activity_text("notRunning") == "idle"
    assert live_sync.classify_activity_text("not_running") == "idle"
    assert live_sync.classify_activity_text("isRunning") == "active"
    assert live_sync.classify_activity_text("returning") == "active"
    assert live_sync.classify_activity_text("READY") == "idle"


def test_completion_refresh_routes_only_for_selected_trail_routes():
    assert live_sync.completion_refresh_routes(
        ["get-location", "trail-time", "trail-data"],
        ["get-location"],
        "active",
        "idle",
    ) == ["trail-time", "trail-data"]
    assert live_sync.completion_refresh_routes(["get-location", "trail-time"], ["get-location"], "idle", "idle") == []
    assert live_sync.completion_refresh_routes(["get-location"], ["get-location"], "active", "idle") == []
    assert live_sync.completion_refresh_routes(["get-location", "trail-time"], ["get-location", "trail-time"], "active", "idle") == []


def test_activity_aware_cadence_pulls_routes_forward_when_state_becomes_active(tmp_path, monkeypatch, capsys):
    db = tmp_path / "navimow.sqlite"
    config, responses = write_fixture_sync_files(tmp_path)
    write_json(
        responses / "openapi-vehicle-status.json",
        {"data": {"devices": [{"vehicleState": "READY", "capacityRemaining": [{"rawValue": 71, "unit": "PERCENTAGE"}]}]}},
    )
    times = iter([100.0, 100.0, 106.0, 106.0])
    activities = iter(
        [
            {"state": "idle", "source": "test"},
            {"state": "idle", "source": "test"},
            {"state": "active", "source": "test"},
            {"state": "active", "source": "test"},
        ]
    )
    monkeypatch.setattr(live_sync.time, "monotonic", lambda: next(times))
    monkeypatch.setattr(live_sync, "live_activity_state", lambda db: next(activities))

    code = live_sync.main(
        [
            "poll",
            "--config",
            str(config),
            "--db",
            str(db),
            "--responses-dir",
            str(responses),
            "--routes",
            "openapi-vehicle-status,get-location",
            "--interval",
            "0",
            "--max-iterations",
            "2",
            "--activity-aware-cadence",
        ]
    )

    assert code == 0
    output = capsys.readouterr().out
    assert "poll 1/2 routes=openapi-vehicle-status,get-location activity=idle" in output
    assert "poll 2/2 routes=openapi-vehicle-status,get-location activity=active" in output


def test_completion_refresh_pulls_trail_time_forward_when_activity_becomes_idle(tmp_path, monkeypatch, capsys):
    db = tmp_path / "navimow.sqlite"
    config, responses = write_fixture_sync_files(tmp_path)
    times = iter([100.0, 100.0, 106.0, 106.0])
    activities = iter(
        [
            {"state": "active", "source": "test"},
            {"state": "active", "source": "test"},
            {"state": "idle", "source": "test"},
            {"state": "idle", "source": "test"},
        ]
    )
    monkeypatch.setattr(live_sync.time, "monotonic", lambda: next(times))
    monkeypatch.setattr(live_sync, "live_activity_state", lambda db: next(activities))

    code = live_sync.main(
        [
            "poll",
            "--config",
            str(config),
            "--db",
            str(db),
            "--responses-dir",
            str(responses),
            "--routes",
            "get-location,trail-time",
            "--interval",
            "0",
            "--max-iterations",
            "2",
            "--activity-aware-cadence",
            "--refresh-trails-on-completion",
        ]
    )

    assert code == 0
    output = capsys.readouterr().out
    assert "poll 1/2 routes=get-location,trail-time activity=active" in output
    assert "poll 2/2 routes=get-location,trail-time activity=idle completion_refresh=trail-time" in output
    con = sqlite3.connect(db)
    assert con.execute("SELECT COUNT(*) FROM live_location_snapshots").fetchone()[0] == 2
    assert con.execute("SELECT COUNT(*) FROM trail_time_snapshots").fetchone()[0] == 2


def test_activity_aware_cadence_uses_fixed_cadence_for_non_overridden_routes():
    assert live_sync.route_cadence_seconds("weather", {"state": "active"}, activity_aware=True) == 1200


def test_poll_can_rebuild_viewer_after_sync(tmp_path, monkeypatch):
    db = tmp_path / "navimow.sqlite"
    config, responses = write_fixture_sync_files(tmp_path)
    calls = []

    def fake_rebuild_viewer(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(live_sync, "rebuild_viewer", fake_rebuild_viewer)

    code = live_sync.main(
        [
            "poll",
            "--config",
            str(config),
            "--db",
            str(db),
            "--responses-dir",
            str(responses),
            "--routes",
            "get-location",
            "--interval",
            "0",
            "--max-iterations",
            "1",
            "--rebuild-viewer",
            "--viewer-output",
            str(tmp_path / "viewer"),
            "--no-satellite",
        ]
    )

    assert code == 0
    assert calls == [{"db": db, "output": tmp_path / "viewer", "no_satellite": True}]


def test_poll_rebuild_viewer_skips_cadence_empty_pass(tmp_path, monkeypatch, capsys):
    db = tmp_path / "navimow.sqlite"
    config, responses = write_fixture_sync_files(tmp_path)
    calls = []

    def fake_rebuild_viewer(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(live_sync, "rebuild_viewer", fake_rebuild_viewer)

    code = live_sync.main(
        [
            "poll",
            "--config",
            str(config),
            "--db",
            str(db),
            "--responses-dir",
            str(responses),
            "--routes",
            "get-location",
            "--interval",
            "0",
            "--max-iterations",
            "2",
            "--use-route-cadence",
            "--rebuild-viewer",
            "--viewer-output",
            str(tmp_path / "viewer"),
        ]
    )

    assert code == 0
    assert calls == [{"db": db, "output": tmp_path / "viewer", "no_satellite": False}]
    assert "poll 2/2 routes=none: no rows changed" in capsys.readouterr().out


def test_sync_once_can_update_live_status_after_sync(tmp_path, monkeypatch, capsys):
    db = tmp_path / "navimow.sqlite"
    config, responses = write_fixture_sync_files(tmp_path)
    calls = []

    def fake_update_live_status_artifact(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(live_sync, "update_live_status_artifact", fake_update_live_status_artifact)

    code = live_sync.main(
        [
            "sync-once",
            "--config",
            str(config),
            "--db",
            str(db),
            "--responses-dir",
            str(responses),
            "--routes",
            "get-location",
            "--update-live-status",
            "--viewer-output",
            str(tmp_path / "viewer"),
        ]
    )

    assert code == 0
    assert calls == [{"db": db, "output": tmp_path / "viewer"}]
    assert "live_status=updated" in capsys.readouterr().out


def test_sync_once_dry_run_reports_live_status_without_writing(tmp_path, monkeypatch, capsys):
    config, responses = write_fixture_sync_files(tmp_path)

    def fake_update_live_status_artifact(**kwargs):
        raise AssertionError("dry-run should not write live status")

    monkeypatch.setattr(live_sync, "update_live_status_artifact", fake_update_live_status_artifact)

    code = live_sync.main(
        [
            "sync-once",
            "--config",
            str(config),
            "--db",
            str(tmp_path / "navimow.sqlite"),
            "--responses-dir",
            str(responses),
            "--routes",
            "get-location",
            "--dry-run",
            "--write-live-status",
            "--viewer-output",
            str(tmp_path / "viewer"),
        ]
    )

    assert code == 0
    output = capsys.readouterr().out
    assert "would POST /vehicle/vehicle/get-location" in output
    assert "would write live status" in output


def test_poll_can_update_live_status_without_full_viewer_rebuild(tmp_path, monkeypatch, capsys):
    db = tmp_path / "navimow.sqlite"
    config, responses = write_fixture_sync_files(tmp_path)
    update_calls = []

    def fake_update_live_status_artifact(**kwargs):
        update_calls.append(kwargs)

    def fake_rebuild_viewer(**kwargs):
        raise AssertionError("status-only polling should not rebuild the full viewer")

    monkeypatch.setattr(live_sync, "update_live_status_artifact", fake_update_live_status_artifact)
    monkeypatch.setattr(live_sync, "rebuild_viewer", fake_rebuild_viewer)

    code = live_sync.main(
        [
            "poll",
            "--config",
            str(config),
            "--db",
            str(db),
            "--responses-dir",
            str(responses),
            "--routes",
            "get-location",
            "--interval",
            "0",
            "--max-iterations",
            "1",
            "--update-live-status",
            "--viewer-output",
            str(tmp_path / "viewer"),
        ]
    )

    assert code == 0
    assert update_calls == [{"db": db, "output": tmp_path / "viewer"}]
    output = capsys.readouterr().out
    assert "poll 1/1 routes=get-location" in output
    assert "live_status=updated" in output


def test_poll_live_status_skips_cadence_empty_pass(tmp_path, monkeypatch, capsys):
    db = tmp_path / "navimow.sqlite"
    config, responses = write_fixture_sync_files(tmp_path)
    update_calls = []

    def fake_update_live_status_artifact(**kwargs):
        update_calls.append(kwargs)

    monkeypatch.setattr(live_sync, "update_live_status_artifact", fake_update_live_status_artifact)

    code = live_sync.main(
        [
            "poll",
            "--config",
            str(config),
            "--db",
            str(db),
            "--responses-dir",
            str(responses),
            "--routes",
            "get-location",
            "--interval",
            "0",
            "--max-iterations",
            "2",
            "--use-route-cadence",
            "--write-live-status",
            "--viewer-output",
            str(tmp_path / "viewer"),
        ]
    )

    assert code == 0
    assert update_calls == [{"db": db, "output": tmp_path / "viewer"}]
    output = capsys.readouterr().out
    assert "poll 1/2 routes=get-location" in output
    assert "poll 1/2 routes=get-location: live_location_snapshots=1; live_status=updated" in output
    assert "poll 2/2 routes=none: no rows changed" in output


def test_auto_viewer_refresh_action_classifies_status_and_layout_changes():
    assert live_sync.auto_viewer_refresh_action({"live_location_snapshots": 1}, ["get-location"]) == "live-status"
    assert live_sync.auto_viewer_refresh_action({"route_snapshot_records": 1}, ["openapi-vehicle-status"]) == "live-status"
    assert live_sync.auto_viewer_refresh_action({"area_setting_snapshots": 1}, ["index2"]) == "live-status"
    assert live_sync.auto_viewer_refresh_action({"area_setting_snapshots": 1}, ["set-list"]) == "live-status"
    assert live_sync.auto_viewer_refresh_action({"device_info_snapshots": 1}, ["device-info"]) == "live-status"
    assert live_sync.auto_viewer_refresh_action({"map_resource_events": 1}, ["get-iot-file"]) == "none"
    assert live_sync.auto_viewer_refresh_action({"ignored": 1}, ["map-detail"]) == "none"
    assert live_sync.auto_viewer_refresh_action({"map_detail_snapshots": 1}, ["map-detail"]) == "rebuild"
    assert live_sync.auto_viewer_refresh_action({"map_downloaded": 1}, ["get-iot-file"]) == "rebuild"
    assert live_sync.auto_viewer_refresh_action({"schedule_snapshots": 1}, ["future-schedule-route"]) == "rebuild"
    assert live_sync.auto_viewer_refresh_action({}, ["get-location"]) == "none"


def test_poll_auto_viewer_refresh_updates_status_for_status_only_changes(tmp_path, monkeypatch, capsys):
    db = tmp_path / "navimow.sqlite"
    config, responses = write_fixture_sync_files(tmp_path)
    update_calls = []

    def fake_update_live_status_artifact(**kwargs):
        update_calls.append(kwargs)

    def fake_rebuild_viewer(**kwargs):
        raise AssertionError("status-only change should not rebuild viewer")

    monkeypatch.setattr(live_sync, "update_live_status_artifact", fake_update_live_status_artifact)
    monkeypatch.setattr(live_sync, "rebuild_viewer", fake_rebuild_viewer)

    code = live_sync.main(
        [
            "poll",
            "--config",
            str(config),
            "--db",
            str(db),
            "--responses-dir",
            str(responses),
            "--routes",
            "get-location",
            "--interval",
            "0",
            "--max-iterations",
            "1",
            "--auto-viewer-refresh",
            "--viewer-output",
            str(tmp_path / "viewer"),
        ]
    )

    assert code == 0
    assert update_calls == [{"db": db, "output": tmp_path / "viewer"}]
    output = capsys.readouterr().out
    assert "poll 1/1 routes=get-location" in output
    assert "live_status=updated" in output
    assert "viewer=rebuilt" not in output


def test_poll_auto_viewer_refresh_rebuilds_for_layout_changes(tmp_path, monkeypatch, capsys):
    db = tmp_path / "navimow.sqlite"
    config = tmp_path / "config.json"
    responses = tmp_path / "responses"
    responses.mkdir()
    write_json(config, {"routes": ["map-detail"], "headers": {}, "requestBodies": {}})
    rebuild_calls = []

    def fake_run_sync_pass(**kwargs):
        return {"map_detail_snapshots": 1, "map_detail_areas": 3}

    def fake_rebuild_viewer(**kwargs):
        rebuild_calls.append(kwargs)

    def fake_update_live_status_artifact(**kwargs):
        raise AssertionError("layout change should rebuild viewer")

    monkeypatch.setattr(live_sync, "run_sync_pass", fake_run_sync_pass)
    monkeypatch.setattr(live_sync, "rebuild_viewer", fake_rebuild_viewer)
    monkeypatch.setattr(live_sync, "update_live_status_artifact", fake_update_live_status_artifact)

    code = live_sync.main(
        [
            "poll",
            "--config",
            str(config),
            "--db",
            str(db),
            "--responses-dir",
            str(responses),
            "--routes",
            "map-detail",
            "--interval",
            "0",
            "--max-iterations",
            "1",
            "--auto-viewer-refresh",
            "--viewer-output",
            str(tmp_path / "viewer"),
            "--no-satellite",
        ]
    )

    assert code == 0
    assert rebuild_calls == [{"db": db, "output": tmp_path / "viewer", "no_satellite": True}]
    assert "viewer=rebuilt" in capsys.readouterr().out


def test_plan_uses_only_known_read_routes(tmp_path, capsys):
    config = tmp_path / "config.json"
    write_json(config, {"routes": ["index2", "get-location"]})

    code = live_sync.main(["plan", "--config", str(config)])

    assert code == 0
    output = capsys.readouterr().out
    assert "/vehicle/vehicle/index2" in output
    assert "/vehicle/vehicle/get-location" in output


def test_auth_discover_reports_redacted_header_hints(tmp_path, capsys):
    captures = tmp_path / "captures" / "post"
    captures.mkdir(parents=True)
    (captures / "entry.0").write_text(
        "\n".join(
            [
                "https://navimow-fra.ninebot.com/vehicle/vehicle/index2",
                "POST",
                "HTTP/1.1 200 OK",
                "Authorization: fake-secret-value",
                "traceid: fake-trace",
                "content-type: application/json",
            ]
        ),
        encoding="utf-8",
    )
    (tmp_path / "captures" / "log.txt").write_text("headers include Authorization but no value here", encoding="utf-8")

    code = live_sync.main(["auth-discover", "--path", str(tmp_path / "captures")])

    assert code == 0
    output = capsys.readouterr().out
    assert "/vehicle/vehicle/index2" in output
    assert "Authorization <sensitive-name>" in output
    assert "fake-secret-value" not in output
    assert "fake-trace" not in output


def test_consumer_session_report_blocks_openapi_only_config_without_secrets(tmp_path, capsys):
    config = tmp_path / "config.json"
    db = tmp_path / "navimow.sqlite"
    captures = tmp_path / "captures" / "post"
    captures.mkdir(parents=True)
    write_json(config, {"routes": ["openapi-auth-list"], "headers": {}, "auth": {"provider": "navimow-oauth"}})
    (captures / "entry.0").write_text(
        "\n".join(
            [
                "https://navimow-fra.ninebot.com/vehicle/vehicle/index2",
                "POST",
                "HTTP/1.1 200 OK",
                "Authorization: fake-secret-value",
            ]
        ),
        encoding="utf-8",
    )

    code = live_sync.main(
        [
            "consumer-session-report",
            "--config",
            str(config),
            "--db",
            str(db),
            "--capture-path",
            str(tmp_path / "captures"),
            "--json",
            "--strict",
            "--now",
            "2026-06-27T12:00:00+00:00",
        ]
    )

    assert code == 1
    output = capsys.readouterr().out
    payload = json.loads(output)
    assert payload["status"] == "openapi_only"
    assert payload["ready"] is False
    assert payload["canSyncConsumerRoutes"] is False
    assert payload["captureHints"]["finding"] == "request-auth-values-present"
    assert "make live-android-doctor" in payload["nextSteps"]
    assert "make live-android-capture" in payload["nextSteps"]
    for secret in ["fake-secret-value", "Authorization: fake", "secret-device", "signed.example"]:
        assert secret not in output


def test_consumer_session_report_accepts_env_backed_consumer_auth(tmp_path, capsys, monkeypatch):
    config = tmp_path / "config.json"
    db = tmp_path / "navimow.sqlite"
    captures = tmp_path / "captures"
    write_json(
        config,
        {
            "routes": ["index2", "map-detail"],
            "headers": {"Authorization": "${NAVIMOW_AUTHORIZATION}", "Trace-Id": "local-trace"},
            "auth": {"provider": "manual"},
        },
    )
    monkeypatch.setenv("NAVIMOW_AUTHORIZATION", "secret-auth-value")

    code = live_sync.main(
        [
            "consumer-session-report",
            "--config",
            str(config),
            "--db",
            str(db),
            "--capture-path",
            str(captures),
            "--json",
            "--strict",
            "--now",
            "2026-06-27T12:00:00+00:00",
        ]
    )

    assert code == 0
    output = capsys.readouterr().out
    payload = json.loads(output)
    assert payload["status"] == "ready_for_consumer_sync"
    assert payload["ready"] is True
    assert payload["headers"]["authHeaderStatus"] == "present"
    assert payload["headers"]["envRefs"] == {"NAVIMOW_AUTHORIZATION": "set"}
    assert payload["config"]["selectedConsumerRoutes"] == ["index2", "map-detail"]
    assert "make live-map-delta" in payload["nextSteps"]
    assert "secret-auth-value" not in output
    assert "local-trace" not in output


def test_doctor_reports_missing_env_without_printing_secret(tmp_path, capsys, monkeypatch):
    config = tmp_path / "config.json"
    write_json(config, {"routes": ["index2"], "headers": {"Authorization": "${NAVIMOW_AUTHORIZATION}"}})
    monkeypatch.delenv("NAVIMOW_AUTHORIZATION", raising=False)

    code = live_sync.main(["doctor", "--config", str(config), "--db", str(tmp_path / "navimow.sqlite")])

    assert code == 1
    output = capsys.readouterr().out
    assert "NAVIMOW_AUTHORIZATION: missing" in output
    assert "missing environment variable NAVIMOW_AUTHORIZATION" in output


def test_doctor_passes_with_env_and_valid_shapes(tmp_path, capsys, monkeypatch):
    config = tmp_path / "config.json"
    db = tmp_path / "navimow.sqlite"
    write_json(config, {"routes": ["index2"], "headers": {"Authorization": "${NAVIMOW_AUTHORIZATION}"}})
    monkeypatch.setenv("NAVIMOW_AUTHORIZATION", "secret-value")

    code = live_sync.main(["doctor", "--config", str(config), "--db", str(db)])

    assert code == 0
    output = capsys.readouterr().out
    assert "doctor: ok" in output
    assert "secret-value" not in output


def test_config_validation_rejects_non_object_route_body(tmp_path, capsys):
    config = tmp_path / "config.json"
    write_json(config, {"routes": ["index2"], "requestBodies": {"index2": []}})

    code = live_sync.main(
        [
            "sync-once",
            "--config",
            str(config),
            "--db",
            str(tmp_path / "navimow.sqlite"),
            "--responses-dir",
            str(tmp_path),
            "--routes",
            "index2",
        ]
    )

    assert code == 1
    assert "requestBodies.index2 must be an object" in capsys.readouterr().err
