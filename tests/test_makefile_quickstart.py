import json
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def test_makefile_exposes_live_quickstart_targets():
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")

    assert "PYTHON ?= $(shell [ -x .venv/bin/python ]" in makefile
    assert "RESPONSES_DIR ?=" in makefile
    assert "SYNC_RESPONSE_FLAGS = $(if $(RESPONSES_DIR),--responses-dir $(RESPONSES_DIR),)" in makefile
    assert "quickstart-live: openapi-preflight oauth-doctor openapi-discover openapi-configure-status openapi-sync-status" in makefile
    assert "viewer-status-only:" in makefile
    assert "viewer-if-map-data:" in makefile
    assert "map_detail_snapshots" in makefile
    assert "map_render_metadata" in makefile
    assert "--status-only" in makefile
    assert "OpenAPI live data is synced. Building a status-only viewer" in makefile
    assert "openapi-first-sync: openapi-discover openapi-configure-status openapi-sync-status viewer-if-map-data" in makefile
    assert "openapi-first-sync: openapi-discover openapi-configure-status openapi-sync-status viewer\n" not in makefile
    assert "live-poll-status:" in makefile
    assert "--interval 5 --use-route-cadence --activity-aware-cadence --update-live-status" in makefile
    assert "POLL_REALTIME_FLAGS ?= --refresh-trails-on-completion" in makefile
    assert "$(POLL_REALTIME_FLAGS)" in makefile
    assert "live-poll-viewer:" in makefile
    assert "--auto-viewer-refresh" in makefile
    assert "live-route-catalog:" in makefile
    assert "tools/navimow_live_sync.py route-catalog" in makefile
    assert "live-route-coverage:" in makefile
    assert "tools/navimow_live_sync.py route-coverage" in makefile
    assert "live-setup-report:" in makefile
    assert "tools/navimow_live_sync.py setup-report" in makefile
    assert "completion-report:" in makefile
    assert "tools/navimow_live_sync.py completion-report" in makefile
    assert "completion-report-strict:" in makefile
    assert "--strict" in makefile
    assert "consumer-session-report:" in makefile
    assert "tools/navimow_live_sync.py consumer-session-report" in makefile
    assert "live-map-plan:" in makefile
    assert "tools/navimow_live_sync.py map-sync-plan" in makefile
    assert "live-map-delta:" in makefile
    assert "--map-delta --download-map-artifacts --map-dir $(MAP_DIR)" in makefile
    assert "live-map-artifacts:" in makefile
    assert "--routes get-iot-file --download-map-artifacts --map-dir $(MAP_DIR)" in makefile
    assert "trail-replay-report:" in makefile
    assert "tools/navimow_live_sync.py trail-replay-report" in makefile
    assert "SERVE_FLAGS ?= --auto-port" in makefile
    assert "serve-live:" in makefile
    assert "$(SERVE_FLAGS)" in makefile
    assert "live-console:" in makefile
    assert "tools/navimow_live_console.py" in makefile
    assert "--with-mqtt" in makefile
    assert "live-console-no-mqtt:" in makefile
    assert "live-ui-smoke:" in makefile
    assert "tools/navimow_live_ui_smoke.py" in makefile
    assert "mqtt-sample-report:" in makefile
    assert "tools/navimow_live_sync.py mqtt-sample-report" in makefile
    assert "mqtt-readiness:" in makefile
    assert "tools/navimow_live_sync.py mqtt-readiness" in makefile
    assert "mqtt-ui-report:" in makefile
    assert "tools/navimow_live_sync.py mqtt-ui-report" in makefile
    assert "MQTT_UI_FLAGS ?= --update-live-status" in makefile
    assert "mqtt-replay-smoke:" in makefile
    assert "tools/navimow_live_sync.py mqtt-replay-smoke" in makefile
    assert "mqtt-replay-http-check:" in makefile
    assert "mqtt-replay-clear:" in makefile
    assert "tools/navimow_live_sync.py mqtt-replay-clear" in makefile
    assert "openapi-preflight:" in makefile
    assert "tools/navimow_live_sync.py openapi-preflight" in makefile
    assert "$(SYNC_RESPONSE_FLAGS) --routes openapi-auth-list,openapi-mqtt-info" in makefile
    assert "$(SYNC_RESPONSE_FLAGS) --routes openapi-vehicle-status,openapi-mqtt-info" in makefile
    assert "oauth-exchange-code:" in makefile
    assert "OAUTH_CODE" in makefile
    assert "tools/navimow_live_sync.py live-health" in makefile
    assert "live-health-strict:" in makefile
    assert "--strict" in makefile


def test_quickstart_docs_reference_redacted_health_flow():
    quickstart = (ROOT / "QUICKSTART.md").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "make quickstart-live" in quickstart
    assert "make live-console" in quickstart
    assert "make live-console-no-mqtt" in quickstart
    assert "make live-ui-smoke" in quickstart
    assert "make live-route-catalog" in quickstart
    assert "make live-route-coverage" in quickstart
    assert "make live-setup-report" in quickstart
    assert "make completion-report" in quickstart
    assert "make completion-report-strict" in quickstart
    assert "completion-report --strict --json" in quickstart
    assert "make consumer-session-report" in quickstart
    assert "make live-map-plan" in quickstart
    assert "make live-map-delta" in quickstart
    assert "make trail-replay-report" in quickstart
    assert "make mqtt-replay-smoke" in quickstart
    assert "make mqtt-sample-report" in quickstart
    assert "make mqtt-readiness" in quickstart
    assert "make mqtt-ui-report" in quickstart
    assert "redacted" in quickstart
    assert "live-health report" in quickstart
    assert "make live-health-strict" in quickstart
    assert "live-health --strict --json" in quickstart
    assert "setup-report --strict --json" in quickstart
    assert "Readiness Summary" in quickstart
    assert "whether the console can be opened now" in quickstart
    assert "ready" in quickstart
    assert "status-only console when map captures are absent" in quickstart
    assert "make openapi-preflight" in quickstart
    assert "prints the next exact local command" in quickstart
    assert "automatically selects the next free localhost port" in quickstart
    assert "make quickstart-live" in readme
    assert "make live-console" in readme
    assert "make live-ui-smoke" in readme
    assert "make live-route-catalog" in readme
    assert "make live-route-coverage" in readme
    assert "make live-setup-report" in readme
    assert "make completion-report" in readme
    assert "make completion-report-strict" in readme
    assert "make consumer-session-report" in readme
    assert "make live-map-plan" in readme
    assert "make live-map-delta" in readme
    assert "make trail-replay-report" in readme
    assert "make mqtt-replay-smoke" in readme
    assert "make mqtt-sample-report" in readme
    assert "make mqtt-readiness" in readme
    assert "make mqtt-ui-report" in readme
    assert "make openapi-preflight" in readme
    assert "make live-health" in readme
    assert "make live-health-strict" in readme
    assert "live-health" in readme and "--json" in readme
    assert "setup-report --strict --json" in readme
    assert "completion-report --strict --json" in readme
    assert "Capability Matrix" in readme
    assert "Consumer-session config" in readme
    assert "Readiness Summary" in readme
    assert "console can be opened now" in readme
    assert "automatically chooses the" in readme
    assert "--status-only" in readme


def test_quickstart_live_make_target_runs_from_openapi_fixtures(tmp_path):
    config = tmp_path / "navimow-live-sync.local.json"
    token = tmp_path / "navimow-oauth.local.json"
    db = tmp_path / "navimow.sqlite"
    viewer = tmp_path / "viewer"
    responses = tmp_path / "responses"
    secret_device_id = "fixture-secret-device-id"
    write_json(
        token,
        {
            "access_token": "fixture-secret-access-token",
            "refresh_token": "fixture-secret-refresh-token",
            "expires_at": "2099-01-01T00:00:00+00:00",
        },
    )
    write_json(
        config,
        {
            "routes": ["openapi-auth-list", "openapi-vehicle-status", "openapi-mqtt-info"],
            "headers": {},
            "auth": {"provider": "navimow-oauth", "tokenFile": str(token)},
            "requestBodies": {"openapi-vehicle-status": {"devices": [{"id": secret_device_id}]}},
        },
    )
    write_json(
        responses / "openapi-auth-list.json",
        {"payload": {"devices": [{"id": secret_device_id, "name": "Fixture mower", "model": "CM120M1"}]}},
    )
    write_json(
        responses / "openapi-vehicle-status.json",
        {
            "payload": {
                "devices": [
                    {
                        "id": secret_device_id,
                        "vehicleState": "READY",
                        "capacityRemaining": [{"rawValue": 88, "unit": "PERCENTAGE"}],
                        "descriptiveCapacityRemaining": "HIGH",
                    }
                ]
            }
        },
    )
    write_json(responses / "openapi-mqtt-info.json", {"configured": True, "topicCount": 0, "credentialStatus": "present"})

    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    result = subprocess.run(
        [
            "make",
            "quickstart-live",
            f"PYTHON={sys.executable}",
            f"LIVE_CONFIG={config}",
            f"DB={db}",
            f"VIEWER={viewer}",
            f"RESPONSES_DIR={responses}",
        ],
        cwd=ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
    )

    combined = result.stdout + result.stderr
    assert result.returncode == 0, combined
    assert (viewer / "navimow-live-status.json").exists()
    assert (viewer / "navimow-map-data.js").exists()
    assert "OpenAPI live data is synced. Building a status-only viewer" in combined
    assert "Start the live console with: make live-console" in combined
    for secret in [secret_device_id, "fixture-secret-access-token", "fixture-secret-refresh-token"]:
        assert secret not in combined
