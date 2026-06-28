import json
from pathlib import Path

import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import navimow_android_live_setup as android_setup
import navimow_live_sync as live_sync


def capture_line(record):
    return android_setup.PREFIX + json.dumps(record)


def sample_record():
    return {
        "kind": "navimow-live-sync-request",
        "method": "POST",
        "urlHost": "navimow-fra.ninebot.com",
        "path": "/vehicle/vehicle/index2",
        "headers": {
            "names": ["Authorization", "Content-Type"],
            "values": {
                "Authorization": "secret-auth-value",
                "Content-Type": "application/json",
            },
        },
        "body": {
            "contentType": "application/json",
            "contentLength": 15,
            "text": "{\"foo\":\"bar\"}",
            "json": {"foo": "bar"},
        },
    }


def sample_shape_only_record():
    record = sample_record()
    record["headers"] = {"names": ["Authorization", "Trace-Id", "Content-Type"], "values": {}}
    record["body"] = {
        "contentType": "application/json",
        "contentLength": 104,
        "jsonShape": {
            "vehicleSn": "string",
            "token": "string",
            "origin_gps": {"type": "array", "length": 2, "items": "number"},
            "blobUrl": "string",
            "nested": {"enabled": "boolean", "count": "number"},
        },
    }
    return record


def test_parse_capture_line_and_summary_redacts_values(capsys):
    records = [
        {"kind": "navimow-live-sync-hook-ready", "captureValues": True},
        sample_record(),
    ]

    summary = android_setup.summarize_records(records)
    android_setup.print_summary(summary)

    output = capsys.readouterr().out
    assert summary["routes"]["index2"] == 1
    assert "Authorization <sensitive-name>" in output
    assert "secret-auth-value" not in output
    assert "foo" not in output


def test_shape_only_summary_never_prints_scalar_values(capsys):
    records = [
        {"kind": "navimow-live-sync-hook-ready", "captureValues": False},
        sample_shape_only_record(),
    ]

    summary = android_setup.summarize_records(records)
    android_setup.print_summary(summary)

    output = capsys.readouterr().out
    assert summary["bodyShapes"]["index2"]["hasJsonShape"] is True
    assert "jsonShape=True" in output
    assert "vehicleSn" in output
    assert "secret-auth-value" not in output
    assert "abc123" not in output
    assert "https://" not in output
    assert "origin_gps" in output


def test_parse_command_can_write_local_config_with_values(tmp_path, capsys):
    capture = tmp_path / "frida.log"
    config = tmp_path / "navimow-live-sync.local.json"
    capture.write_text(capture_line(sample_record()) + "\n", encoding="utf-8")

    code = android_setup.main(
        [
            "parse",
            "--input",
            str(capture),
            "--write-config",
            str(config),
            "--include-values",
            "--i-understand-local-secrets",
        ]
    )

    assert code == 0
    output = capsys.readouterr().out
    assert "secret-auth-value" not in output
    written = json.loads(config.read_text(encoding="utf-8"))
    assert written["baseUrl"] == "https://navimow-fra.ninebot.com"
    assert written["headers"]["Authorization"] == "secret-auth-value"
    assert written["requestBodies"]["index2"] == {"foo": "bar"}
    assert written["routes"] == ["index2"]


def test_parse_requires_explicit_local_secret_acknowledgement(tmp_path):
    capture = tmp_path / "frida.log"
    capture.write_text(capture_line(sample_record()) + "\n", encoding="utf-8")

    code = None
    try:
        android_setup.main(["parse", "--input", str(capture), "--include-values"])
    except SystemExit as exc:
        code = exc.code

    assert code == "--include-values requires --i-understand-local-secrets"


def test_include_values_refuses_non_local_config_without_force(tmp_path):
    capture = tmp_path / "frida.log"
    config = tmp_path / "navimow-live-sync.json"
    capture.write_text(capture_line(sample_record()) + "\n", encoding="utf-8")

    code = None
    try:
        android_setup.main(
            [
                "parse",
                "--input",
                str(capture),
                "--write-config",
                str(config),
                "--include-values",
                "--i-understand-local-secrets",
            ]
        )
    except SystemExit as exc:
        code = exc.code

    assert code == "--include-values config output must end with .local.json or use --force-non-local-config"
    assert not config.exists()


def test_run_requires_explicit_local_secret_acknowledgement(tmp_path):
    code = None
    try:
        android_setup.main(["run", "--include-values", "--output-dir", str(tmp_path)])
    except SystemExit as exc:
        code = exc.code

    assert code == "--include-values requires --i-understand-local-secrets"


def test_android_doctor_snapshot_does_not_expose_device_serials(monkeypatch):
    commands = []

    def fake_which(binary):
        return f"/usr/local/bin/{binary}"

    def fake_run(command, check, stdout, stderr, text, timeout):
        commands.append(command)

        class Completed:
            returncode = 0
            stdout = ""

        completed = Completed()
        if command == ["adb", "devices"]:
            completed.stdout = "List of devices attached\nABC123\tdevice\nDEF456\tunauthorized\n"
        elif command[:4] == ["adb", "shell", "pm", "path"]:
            completed.stdout = "package:/data/app/com.segway.mower/base.apk\n"
        elif command[:3] == ["adb", "shell", "pidof"]:
            completed.stdout = "12345\n"
        return completed

    monkeypatch.setattr(android_setup.shutil, "which", fake_which)
    monkeypatch.setattr(android_setup.subprocess, "run", fake_run)

    snapshot = android_setup.android_doctor_snapshot("com.segway.mower")

    assert snapshot["authorizedDevices"] == 1
    assert snapshot["unauthorizedDevices"] == 1
    assert snapshot["packageInstalled"] is True
    assert snapshot["appRunning"] is True
    assert "ABC123" not in json.dumps(snapshot)
    assert commands


def test_frida_read_paths_match_live_sync_read_routes():
    script = android_setup.DEFAULT_SCRIPT.read_text(encoding="utf-8")

    for route in live_sync.READ_ROUTES.values():
        if str(route["path"]).startswith("/openapi/"):
            continue
        assert f'"{route["path"]}": true' in script
    for blocked in live_sync.WRITE_ROUTE_PARTS:
        assert blocked not in script
