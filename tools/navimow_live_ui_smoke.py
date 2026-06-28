#!/usr/bin/env python3
"""Browser smoke test for the local Navimow live-status update path."""

from __future__ import annotations

import argparse
import base64
import contextlib
import datetime as dt
import json
import os
import shutil
import socket
import struct
import subprocess
import tempfile
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

import navimow_viewer_server as viewer_server


DEFAULT_VIEWER = Path("viewer/navimow-map")
DEFAULT_HOST = "127.0.0.1"
DEFAULT_TIMEOUT = 15.0
LIVE_STATUS_FILE = "navimow-live-status.json"


class DevToolsWebSocket:
    def __init__(self, url: str, *, timeout: float) -> None:
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme != "ws" or not parsed.hostname or not parsed.port:
            raise RuntimeError("unsupported DevTools websocket URL")
        self.host = parsed.hostname
        self.port = parsed.port
        self.path = parsed.path + (f"?{parsed.query}" if parsed.query else "")
        self.timeout = timeout
        self.sock: socket.socket | None = None
        self.next_id = 0

    def __enter__(self) -> "DevToolsWebSocket":
        self.connect()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
        self.close()

    def connect(self) -> None:
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        request = (
            f"GET {self.path} HTTP/1.1\r\n"
            f"Host: {self.host}:{self.port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "\r\n"
        )
        sock.sendall(request.encode("ascii"))
        response = b""
        while b"\r\n\r\n" not in response:
            chunk = sock.recv(4096)
            if not chunk:
                raise RuntimeError("DevTools websocket handshake failed")
            response += chunk
        if b" 101 " not in response.split(b"\r\n", 1)[0]:
            raise RuntimeError("DevTools websocket handshake was not accepted")
        self.sock = sock

    def close(self) -> None:
        if self.sock is None:
            return
        with contextlib.suppress(Exception):
            self._send_frame(b"", opcode=0x8)
        with contextlib.suppress(Exception):
            self.sock.close()
        self.sock = None

    def call(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self.next_id += 1
        message_id = self.next_id
        self._send_json({"id": message_id, "method": method, "params": params or {}})
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            message = self._recv_json()
            if message.get("id") == message_id:
                if "error" in message:
                    raise RuntimeError(f"DevTools command failed: {message['error'].get('message', 'unknown')}")
                return message
        raise TimeoutError(f"DevTools command timed out: {method}")

    def evaluate(self, expression: str) -> Any:
        response = self.call(
            "Runtime.evaluate",
            {
                "expression": expression,
                "returnByValue": True,
                "awaitPromise": True,
            },
        )
        result = ((response.get("result") or {}).get("result") or {})
        if "exceptionDetails" in (response.get("result") or {}):
            raise RuntimeError("browser expression raised an exception")
        return result.get("value")

    def _send_json(self, value: dict[str, Any]) -> None:
        self._send_frame(json.dumps(value, separators=(",", ":")).encode("utf-8"), opcode=0x1)

    def _send_frame(self, payload: bytes, *, opcode: int) -> None:
        if self.sock is None:
            raise RuntimeError("DevTools websocket is not connected")
        header = bytearray([0x80 | opcode])
        length = len(payload)
        if length < 126:
            header.append(0x80 | length)
        elif length < 65536:
            header.append(0x80 | 126)
            header.extend(struct.pack("!H", length))
        else:
            header.append(0x80 | 127)
            header.extend(struct.pack("!Q", length))
        mask = os.urandom(4)
        masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
        self.sock.sendall(bytes(header) + mask + masked)

    def _recv_json(self) -> dict[str, Any]:
        while True:
            opcode, payload = self._recv_frame()
            if opcode == 0x1:
                return json.loads(payload.decode("utf-8"))
            if opcode == 0x8:
                raise RuntimeError("DevTools websocket closed")
            if opcode == 0x9:
                self._send_frame(payload, opcode=0xA)

    def _recv_frame(self) -> tuple[int, bytes]:
        if self.sock is None:
            raise RuntimeError("DevTools websocket is not connected")
        header = self._read_exact(2)
        opcode = header[0] & 0x0F
        masked = bool(header[1] & 0x80)
        length = header[1] & 0x7F
        if length == 126:
            length = struct.unpack("!H", self._read_exact(2))[0]
        elif length == 127:
            length = struct.unpack("!Q", self._read_exact(8))[0]
        mask = self._read_exact(4) if masked else b""
        payload = self._read_exact(length)
        if masked:
            payload = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
        return opcode, payload

    def _read_exact(self, length: int) -> bytes:
        if self.sock is None:
            raise RuntimeError("DevTools websocket is not connected")
        chunks = []
        remaining = length
        while remaining:
            chunk = self.sock.recv(remaining)
            if not chunk:
                raise RuntimeError("DevTools websocket ended unexpectedly")
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)


def now_iso() -> str:
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat()


def reserve_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def find_chromium(explicit: str | None = None) -> str | None:
    candidates = [
        explicit,
        os.environ.get("CHROMIUM"),
        shutil.which("chromium"),
        shutil.which("google-chrome"),
        shutil.which("chrome"),
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
        "/opt/homebrew/bin/chromium",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists() and chromium_executable_works(str(candidate)):
            return str(candidate)
    return None


def chromium_executable_works(path: str) -> bool:
    try:
        completed = subprocess.run([path, "--version"], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
    except Exception:
        return False
    return completed.returncode == 0


def load_live_status(viewer_output: Path) -> dict[str, Any]:
    path = viewer_output / LIVE_STATUS_FILE
    if not path.exists():
        raise SystemExit(f"Missing {path}; build the viewer or run make quickstart-live first")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise SystemExit(f"{path} must contain a JSON object")
    return payload


def validate_viewer_output(viewer_output: Path) -> None:
    missing = [
        path
        for path in (
            viewer_output / "index.html",
            viewer_output / "navimow-map-data.js",
            viewer_output / LIVE_STATUS_FILE,
            viewer_output / "assets" / "navimow-map.js",
        )
        if not path.exists()
    ]
    if missing:
        joined = ", ".join(str(path) for path in missing)
        raise SystemExit(f"Viewer output is missing required files: {joined}")


def pick_area_id(status: dict[str, Any]) -> int | None:
    area_status = status.get("areaStatus")
    if not isinstance(area_status, dict):
        return None
    ids = []
    for key, value in area_status.items():
        candidate = value.get("id") if isinstance(value, dict) else key
        try:
            ids.append(int(candidate))
        except (TypeError, ValueError):
            continue
    return min(ids) if ids else None


def smoke_status_payload(original: dict[str, Any], *, battery_soc: int, mowing_percentage: int) -> dict[str, Any]:
    status = json.loads(json.dumps(original))
    observed_at = now_iso()
    area_id = pick_area_id(status)
    mower = status.setdefault("mower", {})
    mower.setdefault("battery", {})["soc"] = battery_soc
    insights = mower.setdefault("routeInsights", {})
    insights["mqttStatus"] = {
        "observedAt": observed_at,
        "reportAt": observed_at,
        "state": "isRunning",
        "workStatus": "MOWING",
        "batterySoc": battery_soc,
        "mowingPercentage": mowing_percentage,
        "eventType": "LOCAL_UI_SMOKE",
        "source": "ui-smoke",
    }
    if area_id is not None:
        insights["mqttStatus"]["currentPartitionId"] = area_id
        area_status = status.setdefault("areaStatus", {})
        area_entry = area_status.setdefault(str(area_id), {"id": area_id})
        area_entry["live"] = {
            "active": True,
            "observedAt": observed_at,
            "reportAt": observed_at,
            "state": "isRunning",
            "workStatus": "MOWING",
            "currentPartitionId": area_id,
            "mowingPercentage": mowing_percentage,
            "eventType": "LOCAL_UI_SMOKE",
            "source": "ui-smoke",
        }
    status["generatedAt"] = observed_at
    return status


def write_live_status(viewer_output: Path, payload: dict[str, Any]) -> None:
    (viewer_output / LIVE_STATUS_FILE).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def wait_for_debugger(port: int, *, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    url = f"http://127.0.0.1:{port}/json/version"
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=0.5) as response:
                json.loads(response.read().decode())
                return
        except Exception:
            time.sleep(0.1)
    raise TimeoutError("Chromium DevTools endpoint did not become ready")


def create_devtools_target(port: int, url: str) -> str:
    endpoint = f"http://127.0.0.1:{port}/json/new?{urllib.parse.quote(url, safe=':/?&=')}"
    for method in ("PUT", "GET"):
        try:
            request = urllib.request.Request(endpoint, method=method)
            with urllib.request.urlopen(request, timeout=5) as response:
                payload = json.loads(response.read().decode())
            websocket = payload.get("webSocketDebuggerUrl")
            if websocket:
                return str(websocket)
        except urllib.error.HTTPError as exc:
            if exc.code not in (405, 404):
                raise
    raise RuntimeError("Could not create a Chromium DevTools target")


def wait_for_expression(cdp: DevToolsWebSocket, expression: str, *, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cdp.evaluate(expression):
            return
        time.sleep(0.2)
    raise TimeoutError("Browser smoke assertion timed out")


def smoke_expression(*, battery_soc: int, mowing_percentage: int) -> str:
    return (
        "(() => {"
        "const text = document.getElementById('mowerDisplay')?.innerText || '';"
        f"return text.includes('MQTT live status') && text.includes('battery {battery_soc}%') "
        f"&& text.includes('{mowing_percentage}%');"
        "})()"
    )


def run_smoke(args: argparse.Namespace) -> int:
    viewer_output = args.viewer_output
    validate_viewer_output(viewer_output)
    original = load_live_status(viewer_output)
    chromium = find_chromium(args.chromium)
    if args.dry_run:
        print(f"ui_smoke=dry-run; viewer=ready; chromium={'present' if chromium else 'missing'}")
        return 0
    if not chromium:
        raise SystemExit("Chromium is required for live UI smoke; set CHROMIUM=/path/to/chromium")

    current_soc = ((original.get("mower") or {}).get("battery") or {}).get("soc")
    battery_soc = args.battery_soc
    if current_soc == battery_soc:
        battery_soc = battery_soc + 1 if battery_soc < 99 else battery_soc - 1
    patched = smoke_status_payload(original, battery_soc=battery_soc, mowing_percentage=args.mowing_percentage)

    server = viewer_server.create_server(directory=viewer_output, host=args.host, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    server_port = int(server.server_address[1])
    debug_port = reserve_port()
    user_data_dir = tempfile.TemporaryDirectory(prefix="navimow-ui-smoke-")
    chromium_process: subprocess.Popen[str] | None = None
    try:
        chromium_process = subprocess.Popen(
            [
                chromium,
                "--headless=new",
                "--disable-gpu",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-background-networking",
                f"--remote-debugging-port={debug_port}",
                f"--user-data-dir={user_data_dir.name}",
                "about:blank",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        wait_for_debugger(debug_port, timeout=args.timeout)
        url = f"http://{args.host}:{server_port}/"
        websocket_url = create_devtools_target(debug_port, url)
        with DevToolsWebSocket(websocket_url, timeout=args.timeout) as cdp:
            wait_for_expression(
                cdp,
                "document.readyState === 'complete' && !!document.getElementById('mowerDisplay')",
                timeout=args.timeout,
            )
            write_live_status(viewer_output, patched)
            wait_for_expression(cdp, smoke_expression(battery_soc=battery_soc, mowing_percentage=args.mowing_percentage), timeout=args.timeout)
        print(f"ui_smoke=ok; browser=chromium; live_status=updated; battery={battery_soc}; progress={args.mowing_percentage}")
        return 0
    finally:
        if not args.keep_smoke_status:
            with contextlib.suppress(Exception):
                write_live_status(viewer_output, original)
        if chromium_process is not None:
            with contextlib.suppress(Exception):
                chromium_process.terminate()
                chromium_process.wait(timeout=5)
            if chromium_process.poll() is None:
                with contextlib.suppress(Exception):
                    chromium_process.kill()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        user_data_dir.cleanup()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--viewer-output", type=Path, default=DEFAULT_VIEWER)
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--chromium", help="Chromium/Chrome executable path; defaults to CHROMIUM or PATH discovery")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    parser.add_argument("--battery-soc", type=int, default=42)
    parser.add_argument("--mowing-percentage", type=int, default=87)
    parser.add_argument("--keep-smoke-status", action="store_true", help="Leave the synthetic live-status patch in place")
    parser.add_argument("--dry-run", action="store_true", help="Validate viewer files and browser availability without launching Chromium")
    return parser


def main(argv: list[str] | None = None) -> int:
    return run_smoke(build_parser().parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
