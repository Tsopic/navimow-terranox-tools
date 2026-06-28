import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import navimow_live_console as console


def test_live_console_dry_run_includes_realtime_mqtt_defaults(tmp_path, capsys):
    config = tmp_path / "config.json"
    db = tmp_path / "navimow.sqlite"
    viewer = tmp_path / "viewer"

    code = console.main(
        [
            "--config",
            str(config),
            "--db",
            str(db),
            "--viewer-output",
            str(viewer),
            "--openapi-preflight",
            "--refresh-openapi",
            "--strict-health",
            "--with-mqtt",
            "--dry-run",
        ]
    )

    assert code == 0
    output = capsys.readouterr().out
    assert "openapi-preflight:" in output
    assert "openapi-refresh:" in output
    assert "live-health:" in output
    assert "viewer-server:" in output
    assert "live-poll:" in output
    assert "mqtt-listen:" in output
    assert "--auto-viewer-refresh" in output
    assert "--update-live-status" in output
    assert "--use-route-cadence" in output
    assert "--activity-aware-cadence" in output
    assert "--refresh-trails-on-completion" in output
    assert "--max-messages 0" in output
    assert "--duration 0.0" in output
    assert "--max-messages 1" not in output
    assert "--duration 60" not in output


def test_live_console_optional_mqtt_exit_keeps_poll_fallback(capsys):
    required = console.ConsoleCommand(
        "poll",
        [sys.executable, "-c", "import time; print('poll ready', flush=True); time.sleep(2)"],
        True,
        True,
    )
    optional = console.ConsoleCommand(
        "mqtt",
        [sys.executable, "-c", "print('mqtt unavailable', flush=True); raise SystemExit(3)"],
        True,
        False,
    )

    code = console.run_runtime([required, optional], duration=0.4)

    assert code == 0
    output = capsys.readouterr().out
    assert "[mqtt] exited with 3; continuing with polling fallback" in output
    assert "poll ready" in output
    assert "live console duration reached; stopping" in output


def test_live_console_required_process_exit_stops_supervisor(capsys):
    required = console.ConsoleCommand(
        "server",
        [sys.executable, "-c", "print('server failed', flush=True); raise SystemExit(4)"],
        True,
        True,
    )

    code = console.run_runtime([required], duration=2)

    assert code == 4
    output = capsys.readouterr().out
    assert "[server] exited with 4; stopping live console" in output
