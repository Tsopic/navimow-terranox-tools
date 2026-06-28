import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import build_navimow_map_viewer as viewer
import navimow_live_ui_smoke as ui_smoke
from test_navimow_map_viewer import create_fixture_db


def build_fixture_viewer(tmp_path: Path) -> Path:
    db = create_fixture_db(tmp_path)
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    output = tmp_path / "viewer"
    data = viewer.build_data(con, output, include_satellite=False)
    viewer.write_viewer(output, data)
    return output


def test_smoke_status_payload_updates_only_sanitized_live_fields(tmp_path):
    output = build_fixture_viewer(tmp_path)
    original = ui_smoke.load_live_status(output)

    patched = ui_smoke.smoke_status_payload(original, battery_soc=42, mowing_percentage=87)
    encoded = json.dumps(patched)
    area_id = ui_smoke.pick_area_id(original)

    assert patched["layoutVersion"] == original["layoutVersion"]
    assert patched["mower"]["battery"]["soc"] == 42
    assert patched["mower"]["routeInsights"]["mqttStatus"]["batterySoc"] == 42
    assert patched["mower"]["routeInsights"]["mqttStatus"]["mowingPercentage"] == 87
    assert patched["areaStatus"][str(area_id)]["live"]["active"] is True
    assert patched["areaStatus"][str(area_id)]["live"]["mowingPercentage"] == 87
    assert "topicHash" not in encoded
    assert "payloadSha256" not in encoded
    assert "latitude" not in encoded
    assert "signedUrl" not in encoded
    assert "secret-token" not in encoded


def test_live_ui_smoke_dry_run_validates_generated_viewer(tmp_path, capsys):
    output = build_fixture_viewer(tmp_path)

    code = ui_smoke.main(["--viewer-output", str(output), "--dry-run"])

    assert code == 0
    text = capsys.readouterr().out
    assert "ui_smoke=dry-run" in text
    assert "viewer=ready" in text


def test_smoke_expression_checks_mqtt_live_status_text():
    expression = ui_smoke.smoke_expression(battery_soc=42, mowing_percentage=87)

    assert "MQTT live status" in expression
    assert "battery 42%" in expression
    assert "87%" in expression
