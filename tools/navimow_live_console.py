#!/usr/bin/env python3
"""Run the local Navimow live console as a supervised localhost workflow."""

from __future__ import annotations

import argparse
import shlex
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path


DEFAULT_CONFIG = Path("config/navimow-live-sync.local.json")
DEFAULT_DB = Path("data/navimow.sqlite")
DEFAULT_VIEWER = Path("viewer/navimow-map")
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
DEFAULT_INTERVAL = 5
DEFAULT_MAX_ITERATIONS = 999999


@dataclass(frozen=True)
class ConsoleCommand:
    name: str
    argv: list[str]
    long_running: bool
    required: bool = True


def tool_path(name: str) -> str:
    return str(Path(__file__).resolve().with_name(name))


def path_arg(value: Path) -> str:
    return str(value)


def build_preflight_commands(args: argparse.Namespace) -> list[ConsoleCommand]:
    commands: list[ConsoleCommand] = []
    if args.openapi_preflight:
        commands.append(
            ConsoleCommand(
                "openapi-preflight",
                [
                    sys.executable,
                    tool_path("navimow_live_sync.py"),
                    "openapi-preflight",
                    "--config",
                    path_arg(args.config),
                ],
                False,
            )
        )
    if args.refresh_openapi:
        commands.append(
            ConsoleCommand(
                "openapi-refresh",
                [
                    sys.executable,
                    tool_path("navimow_live_sync.py"),
                    "sync-once",
                    "--config",
                    path_arg(args.config),
                    "--db",
                    path_arg(args.db),
                    "--routes",
                    "openapi-auth-list,openapi-vehicle-status,openapi-mqtt-info",
                    "--update-live-status",
                    "--viewer-output",
                    path_arg(args.viewer_output),
                ],
                False,
            )
        )
    if args.strict_health:
        commands.append(
            ConsoleCommand(
                "live-health",
                [
                    sys.executable,
                    tool_path("navimow_live_sync.py"),
                    "live-health",
                    "--config",
                    path_arg(args.config),
                    "--db",
                    path_arg(args.db),
                    "--viewer-output",
                    path_arg(args.viewer_output),
                    "--strict",
                ],
                False,
            )
        )
    return commands


def build_runtime_commands(args: argparse.Namespace) -> list[ConsoleCommand]:
    server = [
        sys.executable,
        tool_path("navimow_viewer_server.py"),
        "--host",
        args.host,
        "--port",
        str(args.port),
        "--directory",
        path_arg(args.viewer_output),
    ]
    if args.auto_port:
        server.append("--auto-port")

    poll = [
        sys.executable,
        tool_path("navimow_live_sync.py"),
        "poll",
        "--config",
        path_arg(args.config),
        "--db",
        path_arg(args.db),
        "--interval",
        str(args.interval),
        "--use-route-cadence",
        "--auto-viewer-refresh",
        "--viewer-output",
        path_arg(args.viewer_output),
        "--max-iterations",
        str(args.max_iterations),
    ]
    if args.activity_aware_cadence:
        poll.append("--activity-aware-cadence")
    if args.activity_aware_cadence and args.refresh_trails_on_completion:
        poll.append("--refresh-trails-on-completion")
    if args.routes:
        poll.extend(["--routes", args.routes])

    commands = [
        ConsoleCommand("viewer-server", server, True),
        ConsoleCommand("live-poll", poll, True),
    ]
    if args.with_mqtt:
        mqtt = [
            sys.executable,
            tool_path("navimow_live_sync.py"),
            "mqtt-listen",
            "--config",
            path_arg(args.config),
            "--db",
            path_arg(args.db),
            "--update-live-status",
            "--viewer-output",
            path_arg(args.viewer_output),
            "--max-messages",
            str(args.mqtt_max_messages),
            "--duration",
            str(args.mqtt_duration),
        ]
        commands.append(ConsoleCommand("mqtt-listen", mqtt, True, required=args.require_mqtt))
    return commands


def shell_join(argv: list[str]) -> str:
    return " ".join(shlex.quote(item) for item in argv)


def print_commands(commands: list[ConsoleCommand]) -> None:
    for command in commands:
        print(f"{command.name}: {shell_join(command.argv)}")


def run_preflight(commands: list[ConsoleCommand]) -> int:
    for command in commands:
        print(f"[{command.name}] starting")
        completed = subprocess.run(command.argv, check=False)
        if completed.returncode != 0:
            print(f"[{command.name}] exited with {completed.returncode}")
            return completed.returncode
    return 0


def stream_process_output(name: str, process: subprocess.Popen[str]) -> None:
    assert process.stdout is not None
    for line in process.stdout:
        print(f"[{name}] {line}", end="")


def terminate_processes(processes: list[tuple[ConsoleCommand, subprocess.Popen[str]]]) -> None:
    for _command, process in processes:
        if process.poll() is None:
            process.terminate()
    deadline = time.monotonic() + 5
    for _command, process in processes:
        remaining = max(0.1, deadline - time.monotonic())
        try:
            process.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            process.kill()
    for _command, process in processes:
        if process.poll() is None:
            process.wait(timeout=1)


def run_runtime(commands: list[ConsoleCommand], *, duration: float) -> int:
    processes: list[tuple[ConsoleCommand, subprocess.Popen[str]]] = []
    previous_sigterm = signal.getsignal(signal.SIGTERM)

    def handle_sigterm(_signum, _frame) -> None:
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, handle_sigterm)
    try:
        for command in commands:
            process = subprocess.Popen(
                command.argv,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            processes.append((command, process))
            thread = threading.Thread(target=stream_process_output, args=(command.name, process), daemon=True)
            thread.start()

        deadline = time.monotonic() + duration if duration > 0 else None
        while True:
            running_processes: list[tuple[ConsoleCommand, subprocess.Popen[str]]] = []
            for command, process in processes:
                return_code = process.poll()
                if return_code is not None:
                    if command.required:
                        print(f"[{command.name}] exited with {return_code}; stopping live console")
                        terminate_processes(processes)
                        return return_code
                    print(f"[{command.name}] exited with {return_code}; continuing with polling fallback")
                    continue
                running_processes.append((command, process))
            processes = running_processes
            if deadline is not None and time.monotonic() >= deadline:
                print("live console duration reached; stopping")
                terminate_processes(processes)
                return 0
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("stopping live console")
        terminate_processes(processes)
        return 130
    finally:
        signal.signal(signal.SIGTERM, previous_sigterm)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--viewer-output", type=Path, default=DEFAULT_VIEWER)
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--auto-port", action="store_true")
    parser.add_argument("--interval", type=int, default=DEFAULT_INTERVAL)
    parser.add_argument("--max-iterations", type=int, default=DEFAULT_MAX_ITERATIONS)
    parser.add_argument("--routes", help="Optional comma-separated route aliases for the polling loop")
    parser.add_argument("--no-activity-aware-cadence", dest="activity_aware_cadence", action="store_false")
    parser.add_argument("--no-refresh-trails-on-completion", dest="refresh_trails_on_completion", action="store_false")
    parser.set_defaults(activity_aware_cadence=True)
    parser.set_defaults(refresh_trails_on_completion=True)
    parser.add_argument("--openapi-preflight", action="store_true", help="Run the local OpenAPI readiness check before starting")
    parser.add_argument("--refresh-openapi", action="store_true", help="Refresh OpenAPI auth/status/MQTT snapshots before starting")
    parser.add_argument("--strict-health", action="store_true", help="Require live-health --strict to pass before starting")
    parser.add_argument("--with-mqtt", action="store_true", help="Start the MQTT listener alongside the polling loop")
    parser.add_argument("--require-mqtt", action="store_true", help="Stop the console if the optional MQTT listener exits")
    parser.add_argument("--mqtt-max-messages", type=int, default=0)
    parser.add_argument("--mqtt-duration", type=float, default=0.0)
    parser.add_argument("--duration", type=float, default=0.0, help="Stop the supervisor after this many seconds; 0 runs until interrupted")
    parser.add_argument("--dry-run", action="store_true", help="Print child commands without starting them")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    preflight_commands = build_preflight_commands(args)
    runtime_commands = build_runtime_commands(args)
    if args.dry_run:
        print_commands(preflight_commands + runtime_commands)
        return 0
    preflight_status = run_preflight(preflight_commands)
    if preflight_status != 0:
        return preflight_status
    return run_runtime(runtime_commands, duration=args.duration)


if __name__ == "__main__":
    raise SystemExit(main())
