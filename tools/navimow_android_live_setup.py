#!/usr/bin/env python3
"""Capture local Android app request hints for Navimow live sync setup."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import navimow_live_sync as live_sync  # noqa: E402


DEFAULT_PACKAGE = "com.segway.mower"
DEFAULT_CONFIG = Path("config/navimow-live-sync.local.json")
DEFAULT_SCRIPT = Path(__file__).resolve().parent / "frida-live-sync-capture.js"
PREFIX = "NAVIMOW_LIVE_SYNC "
LOCAL_SECRET_CONFIG_SUFFIX = ".local.json"


def now_slug() -> str:
    return dt.datetime.now().strftime("%Y%m%d-%H%M%S")


def read_json_lines(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line in path.read_text(errors="replace").splitlines():
        record = parse_capture_line(line)
        if record is not None:
            records.append(record)
    return records


def parse_capture_line(line: str) -> dict[str, Any] | None:
    if PREFIX not in line:
        return None
    raw = line.split(PREFIX, 1)[1].strip()
    try:
        record = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return record if isinstance(record, dict) else None


def alias_for_path(path: str) -> str | None:
    for alias, route in live_sync.READ_ROUTES.items():
        if route["path"] == path:
            return alias
    return None


def merge_capture_records(records: list[dict[str, Any]], *, include_values: bool) -> dict[str, Any]:
    config = live_sync.load_config(Path("__missing_navimow_live_config__.json"))
    routes: list[str] = []
    request_bodies = dict(config.get("requestBodies") or {})
    headers = dict(config.get("headers") or {})
    base_host = None

    for record in records:
        if record.get("kind") != "navimow-live-sync-request":
            continue
        alias = alias_for_path(str(record.get("path") or ""))
        if alias is None:
            continue
        if alias not in routes:
            routes.append(alias)
        if record.get("urlHost") and base_host is None:
            base_host = str(record["urlHost"])

        captured_headers = ((record.get("headers") or {}).get("values") or {})
        if include_values and isinstance(captured_headers, dict):
            for name, value in captured_headers.items():
                if value is not None:
                    headers[str(name)] = str(value)

        body = record.get("body") or {}
        if include_values and isinstance(body, dict):
            if isinstance(body.get("json"), dict):
                request_bodies[alias] = body["json"]
            elif body.get("text"):
                try:
                    parsed = json.loads(str(body["text"]))
                except json.JSONDecodeError:
                    parsed = {}
                request_bodies[alias] = parsed if isinstance(parsed, dict) else {}

    if base_host:
        config["baseUrl"] = f"https://{base_host}"
    config["headers"] = headers
    config["requestBodies"] = request_bodies
    config["routes"] = routes or config.get("routes", [])
    return config


def summarize_records(records: list[dict[str, Any]]) -> dict[str, Any]:
    route_counts: dict[str, int] = {}
    header_names: dict[str, int] = {}
    body_shapes: dict[str, dict[str, Any]] = {}
    hook_ready = 0
    errors = 0

    for record in records:
        kind = record.get("kind")
        if kind == "navimow-live-sync-hook-ready":
            hook_ready += 1
            continue
        if kind and str(kind).endswith("error"):
            errors += 1
            continue
        if kind != "navimow-live-sync-request":
            continue
        alias = alias_for_path(str(record.get("path") or "")) or str(record.get("path") or "unknown")
        route_counts[alias] = route_counts.get(alias, 0) + 1
        names = ((record.get("headers") or {}).get("names") or [])
        for name in names:
            safe = live_sync.safe_header_name(str(name))
            header_names[safe] = header_names.get(safe, 0) + 1
        body = record.get("body") or {}
        if isinstance(body, dict):
            body_shapes[alias] = {
                "contentType": body.get("contentType"),
                "contentLength": body.get("contentLength"),
                "hasJson": isinstance(body.get("json"), dict),
                "hasJsonShape": isinstance(body.get("jsonShape"), (dict, str)),
                "hasText": bool(body.get("text")),
                "jsonShape": body.get("jsonShape") if isinstance(body.get("jsonShape"), (dict, str)) else None,
                "bodyKind": body.get("bodyKind"),
            }

    return {
        "records": len(records),
        "hookReady": hook_ready,
        "errors": errors,
        "routes": dict(sorted(route_counts.items())),
        "headerNames": dict(sorted(header_names.items())),
        "bodyShapes": body_shapes,
    }


def print_summary(summary: dict[str, Any]) -> None:
    print(f"records: {summary['records']}")
    print(f"hook ready: {summary['hookReady']}")
    print(f"errors: {summary['errors']}")
    print("routes:")
    for route, count in summary["routes"].items():
        print(f"  {route}: {count}")
    print("header names:")
    for name, count in summary["headerNames"].items():
        print(f"  {name}: {count}")
    print("body shapes:")
    for route, shape in summary["bodyShapes"].items():
        json_shape = shape.get("jsonShape")
        shape_text = "n/a"
        if json_shape is not None:
            shape_text = json.dumps(json_shape, ensure_ascii=False, sort_keys=True)
        print(
            f"  {route}: contentType={shape.get('contentType') or 'n/a'} "
            f"length={shape.get('contentLength') or 'n/a'} "
            f"json={shape.get('hasJson')} jsonShape={shape.get('hasJsonShape')} "
            f"bodyKind={shape.get('bodyKind') or 'n/a'} shape={shape_text}"
        )


def write_config(path: Path, config: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def is_local_secret_config_path(path: Path) -> bool:
    return path.name.endswith(LOCAL_SECRET_CONFIG_SUFFIX)


def require_local_secret_ack(args: argparse.Namespace) -> None:
    if args.include_values and not getattr(args, "i_understand_local_secrets", False):
        raise SystemExit("--include-values requires --i-understand-local-secrets")
    write_config_path = getattr(args, "write_config", None)
    if (
        args.include_values
        and write_config_path
        and not is_local_secret_config_path(write_config_path)
        and not getattr(args, "force_non_local_config", False)
    ):
        raise SystemExit("--include-values config output must end with .local.json or use --force-non-local-config")


def build_frida_script(*, include_values: bool) -> str:
    script = DEFAULT_SCRIPT.read_text(encoding="utf-8")
    replacement = "var CAPTURE_VALUES = true;" if include_values else "var CAPTURE_VALUES = false;"
    return script.replace("var CAPTURE_VALUES = false;", replacement, 1)


def run_frida_capture(args: argparse.Namespace) -> Path:
    require_local_secret_ack(args)
    if shutil.which("frida") is None:
        raise SystemExit("frida CLI not found. Install frida-tools in your local environment.")

    output_dir = args.output_dir or Path("captures") / f"live-sync-{now_slug()}"
    output_dir.mkdir(parents=True, exist_ok=True)
    raw_log = output_dir / ("raw-sensitive-frida.log" if args.include_values else "redacted-frida.log")
    script_text = build_frida_script(include_values=args.include_values)

    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as script_file:
        script_file.write(script_text)
        script_path = Path(script_file.name)

    command = ["frida", "-U", "-f", args.package, "-l", str(script_path), "--no-pause"]
    try:
        with raw_log.open("w", encoding="utf-8") as output:
            proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            started = time.monotonic()
            while True:
                if proc.stdout is not None:
                    line = proc.stdout.readline()
                    if line:
                        output.write(line)
                        output.flush()
                if proc.poll() is not None:
                    break
                if time.monotonic() - started >= args.duration:
                    proc.terminate()
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                    break
    finally:
        script_path.unlink(missing_ok=True)

    return raw_log


def run_command(command: list[str], *, timeout: float = 10.0) -> tuple[int | None, str]:
    try:
        completed = subprocess.run(
            command,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError:
        return None, "missing"
    except subprocess.TimeoutExpired:
        return None, "timeout"
    return completed.returncode, completed.stdout


def parse_adb_devices(output: str) -> dict[str, int]:
    counts = {"authorized": 0, "unauthorized": 0, "offline": 0}
    for line in output.splitlines()[1:]:
        parts = line.split()
        if len(parts) < 2:
            continue
        state = parts[1].lower()
        if state == "device":
            counts["authorized"] += 1
        elif state == "unauthorized":
            counts["unauthorized"] += 1
        elif state == "offline":
            counts["offline"] += 1
    return counts


def android_doctor_snapshot(package: str = DEFAULT_PACKAGE) -> dict[str, Any]:
    adb_path = shutil.which("adb")
    frida_path = shutil.which("frida")
    snapshot: dict[str, Any] = {
        "adb": adb_path,
        "frida": frida_path,
        "authorizedDevices": None,
        "unauthorizedDevices": None,
        "offlineDevices": None,
        "package": package,
        "packageInstalled": None,
        "appRunning": None,
    }
    if adb_path is None:
        return snapshot

    devices_code, devices_out = run_command(["adb", "devices"], timeout=10)
    if devices_code == 0:
        counts = parse_adb_devices(devices_out)
        snapshot["authorizedDevices"] = counts["authorized"]
        snapshot["unauthorizedDevices"] = counts["unauthorized"]
        snapshot["offlineDevices"] = counts["offline"]
    else:
        snapshot["adbDevicesError"] = devices_out.strip()[:160]

    if snapshot["authorizedDevices"]:
        package_code, package_out = run_command(["adb", "shell", "pm", "path", package], timeout=10)
        snapshot["packageInstalled"] = package_code == 0 and "package:" in package_out
        pid_code, pid_out = run_command(["adb", "shell", "pidof", package], timeout=10)
        snapshot["appRunning"] = pid_code == 0 and bool(pid_out.strip())

    return snapshot


def cmd_doctor(args: argparse.Namespace) -> int:
    snapshot = android_doctor_snapshot(args.package)
    print(f"adb: {snapshot['adb'] or 'missing'}")
    print(f"frida: {snapshot['frida'] or 'missing'}")
    if snapshot["authorizedDevices"] is None:
        print("authorized devices: unknown")
    else:
        print(f"authorized devices: {snapshot['authorizedDevices']}")
        print(f"unauthorized devices: {snapshot['unauthorizedDevices']}")
        print(f"offline devices: {snapshot['offlineDevices']}")
    package_status = "unknown"
    if snapshot["packageInstalled"] is True:
        package_status = "installed"
    elif snapshot["packageInstalled"] is False:
        package_status = "missing"
    running_status = "unknown"
    if snapshot["appRunning"] is True:
        running_status = "running"
    elif snapshot["appRunning"] is False:
        running_status = "not-running"
    print(f"package {snapshot['package']}: {package_status}")
    print(f"app process: {running_status}")
    return 0


def cmd_parse(args: argparse.Namespace) -> int:
    require_local_secret_ack(args)
    records = read_json_lines(args.input)
    summary = summarize_records(records)
    print_summary(summary)
    if args.write_config:
        config = merge_capture_records(records, include_values=args.include_values)
        write_config(args.write_config, config)
        print(f"config written: {args.write_config}")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    raw_log = run_frida_capture(args)
    print(f"capture log: {raw_log}")
    records = read_json_lines(raw_log)
    print_summary(summarize_records(records))
    if args.write_config:
        config = merge_capture_records(records, include_values=args.include_values)
        write_config(args.write_config, config)
        print(f"config written: {args.write_config}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    doctor = subparsers.add_parser("doctor", help="Check adb/frida and Android app readiness")
    doctor.add_argument("--package", default=DEFAULT_PACKAGE)
    doctor.set_defaults(func=cmd_doctor)

    parse = subparsers.add_parser("parse", help="Parse a saved Frida capture log")
    parse.add_argument("--input", type=Path, required=True)
    parse.add_argument("--write-config", type=Path)
    parse.add_argument("--include-values", action="store_true", help="Use captured local values when writing config")
    parse.add_argument("--i-understand-local-secrets", action="store_true")
    parse.add_argument("--force-non-local-config", action="store_true")
    parse.set_defaults(func=cmd_parse)

    run = subparsers.add_parser("run", help="Run local Frida capture against the Android app")
    run.add_argument("--package", default=DEFAULT_PACKAGE)
    run.add_argument("--duration", type=float, default=60.0)
    run.add_argument("--output-dir", type=Path)
    run.add_argument("--write-config", type=Path, default=DEFAULT_CONFIG)
    run.add_argument("--include-values", action="store_true", help="Capture local-only sensitive header/body values")
    run.add_argument("--i-understand-local-secrets", action="store_true")
    run.add_argument("--force-non-local-config", action="store_true")
    run.set_defaults(func=cmd_run)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
