#!/usr/bin/env python3
"""Serve the local Navimow viewer with metadata-only live reload events."""

from __future__ import annotations

import argparse
import errno
import json
import mimetypes
import time
import urllib.parse
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import navimow_live_status as live_status


DEFAULT_DIRECTORY = Path("viewer/navimow-map")
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
DEFAULT_EVENT_INTERVAL_SECONDS = 0.25
RELOAD_FILES = (
    "index.html",
    "assets/navimow-map.js",
    "assets/navimow-map.css",
)
LIVE_STATUS_FILE = live_status.LIVE_STATUS_FILE
WATCHED_FILES = RELOAD_FILES + (LIVE_STATUS_FILE,)
PRIVATE_SEGMENTS = {
    ".git",
    "apk",
    "captures",
    "config",
    "data",
    "decompiled",
    "logs",
    "patched",
    "refs",
    "screenshots",
}
PRIVATE_SUFFIXES = (
    ".apk",
    ".apks",
    ".db",
    ".local.json",
    ".log",
    ".mitm",
    ".sqlite",
    ".sqlite3",
)

now_iso = live_status.now_iso
file_record = live_status.file_record
records_version = live_status.records_version
safe_live_status_payload = live_status.safe_live_status_payload
live_status_summary = live_status.live_status_summary


def viewer_status(root: Path) -> dict[str, Any]:
    root = root.resolve()
    reload_files = [file_record(root, item) for item in RELOAD_FILES]
    live_status_file = file_record(root, LIVE_STATUS_FILE)
    live_summary = live_status_summary(root)
    reload_version = records_version(reload_files)
    live_status_version = records_version([live_status_file])
    return {
        "version": reload_version,
        "reloadVersion": reload_version,
        "liveStatusVersion": live_status_version,
        "layoutVersion": live_summary.get("layoutVersion"),
        "observedAt": now_iso(),
        "files": reload_files,
        "liveStatus": {
            **live_status_file,
            **live_summary,
        },
        "privacy": "Metadata only; viewer file contents are not included.",
    }


def sse_event(event: str, data: dict[str, Any]) -> bytes:
    body = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"event: {event}\ndata: {body}\n\n".encode("utf-8")


def reload_event_payload(status: dict[str, Any]) -> dict[str, Any]:
    return {
        "version": status["reloadVersion"],
        "layoutVersion": status.get("layoutVersion"),
        "observedAt": status["observedAt"],
    }


def live_status_event_payload(
    status: dict[str, Any],
    *,
    root: Path | None = None,
    include_status_payload: bool = False,
) -> dict[str, Any]:
    event = {
        "version": status["liveStatusVersion"],
        "layoutVersion": status.get("layoutVersion"),
        "available": (status.get("liveStatus") or {}).get("available", False),
        "generatedAt": (status.get("liveStatus") or {}).get("generatedAt"),
        "observedAt": status["observedAt"],
    }
    if include_status_payload and root is not None:
        payload, available = safe_live_status_payload(root)
        event["available"] = available
        if available:
            event["status"] = payload
    return event


def request_path_parts(path: str) -> list[str] | None:
    decoded = urllib.parse.unquote(urllib.parse.urlparse(path).path)
    parts: list[str] = []
    for part in decoded.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            return None
        parts.append(part)
    return parts


def is_private_request_path(path: str) -> bool:
    parts = request_path_parts(path)
    if parts is None:
        return True
    if not parts:
        return False
    for part in parts:
        if part.startswith(".") or part in PRIVATE_SEGMENTS:
            return True
    name = parts[-1]
    return any(name.endswith(suffix) for suffix in PRIVATE_SUFFIXES)


class NavimowViewerHandler(SimpleHTTPRequestHandler):
    viewer_root: Path = DEFAULT_DIRECTORY

    def __init__(self, *args: Any, directory: str | None = None, **kwargs: Any) -> None:
        super().__init__(*args, directory=str(self.viewer_root), **kwargs)

    def log_message(self, format: str, *args: Any) -> None:
        return

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        super().end_headers()

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/__navimow/status":
            self.write_json(viewer_status(self.viewer_root))
            return
        if parsed.path == "/__navimow/live-status":
            payload, available = safe_live_status_payload(self.viewer_root)
            self.write_json(payload, HTTPStatus.OK if available else HTTPStatus.NOT_FOUND)
            return
        if parsed.path == "/__navimow/events":
            self.write_events(parsed.query)
            return
        if is_private_request_path(self.path):
            self.send_error(HTTPStatus.FORBIDDEN, "private viewer path")
            return
        super().do_GET()

    def do_HEAD(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path.startswith("/__navimow/"):
            self.send_error(HTTPStatus.METHOD_NOT_ALLOWED, "HEAD is not supported for live endpoints")
            return
        if is_private_request_path(self.path):
            self.send_error(HTTPStatus.FORBIDDEN, "private viewer path")
            return
        super().do_HEAD()

    def write_json(self, value: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        payload = json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def write_events(self, query: str) -> None:
        params = urllib.parse.parse_qs(query)
        interval = safe_float(
            first_query_value(params, "interval"),
            default=DEFAULT_EVENT_INTERVAL_SECONDS,
            minimum=0.1,
            maximum=60.0,
        )
        max_events = safe_int(first_query_value(params, "max"), default=0, minimum=0, maximum=1000)
        live_mode = first_query_value(params, "live")
        include_status_payload = live_mode in {"full", "payload", "status"}
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        if not max_events:
            self.send_header("Connection", "keep-alive")
        self.end_headers()

        sent = 0
        previous_reload_version: str | None = None
        previous_live_status_version: str | None = None
        previous_layout_version: str | None = None
        while True:
            status = viewer_status(self.viewer_root)
            reload_changed = (
                status["reloadVersion"] != previous_reload_version
                or status.get("layoutVersion") != previous_layout_version
            )
            if reload_changed:
                previous_reload_version = status["reloadVersion"]
                previous_layout_version = status.get("layoutVersion")
                self.wfile.write(sse_event("viewer-update", reload_event_payload(status)))
                self.wfile.flush()
                sent += 1
                if max_events and sent >= max_events:
                    return
            if status["liveStatusVersion"] != previous_live_status_version:
                previous_live_status_version = status["liveStatusVersion"]
                self.wfile.write(
                    sse_event(
                        "live-status",
                        live_status_event_payload(
                            status,
                            root=self.viewer_root,
                            include_status_payload=include_status_payload,
                        ),
                    )
                )
                self.wfile.flush()
                sent += 1
                if max_events and sent >= max_events:
                    return
            time.sleep(interval)


def first_query_value(params: dict[str, list[str]], key: str) -> str | None:
    values = params.get(key)
    return values[0] if values else None


def safe_float(value: str | None, *, default: float, minimum: float, maximum: float) -> float:
    try:
        parsed = float(value) if value is not None else default
    except ValueError:
        parsed = default
    return min(max(parsed, minimum), maximum)


def safe_int(value: str | None, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value) if value is not None else default
    except ValueError:
        parsed = default
    return min(max(parsed, minimum), maximum)


def make_handler(directory: Path) -> type[NavimowViewerHandler]:
    class Handler(NavimowViewerHandler):
        viewer_root = directory.resolve()

    return Handler


def create_server(
    *,
    directory: Path,
    host: str,
    port: int,
    auto_port: bool = False,
    max_port_attempts: int = 50,
) -> ThreadingHTTPServer:
    handler = make_handler(directory)
    attempts = 1 if not auto_port or port == 0 else max(1, max_port_attempts)
    for offset in range(attempts):
        candidate_port = port + offset if port else 0
        try:
            return ThreadingHTTPServer((host, candidate_port), handler)
        except OSError as exc:
            if not auto_port or exc.errno != errno.EADDRINUSE or offset == attempts - 1:
                raise
    raise RuntimeError("unreachable")


def serve(*, directory: Path, host: str, port: int, auto_port: bool = False) -> None:
    directory = directory.resolve()
    if not directory.exists():
        raise SystemExit(f"viewer directory does not exist: {directory}")
    mimetypes.add_type("text/event-stream", ".event-stream")
    server = create_server(directory=directory, host=host, port=port, auto_port=auto_port)
    actual_host, actual_port = server.server_address[:2]
    display_host = host if host not in {"", "0.0.0.0", "::"} else str(actual_host)
    if auto_port and actual_port != port:
        print(f"Requested port {port} was busy; selected port {actual_port}.")
    print(f"Serving Navimow viewer at http://{display_host}:{actual_port}/")
    print("Live reload endpoint: /__navimow/events")
    print("Live status endpoint: /__navimow/live-status")
    server.serve_forever()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=DEFAULT_DIRECTORY)
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--auto-port", action="store_true", help="If the requested port is busy, bind the next available local port")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    serve(directory=args.directory, host=args.host, port=args.port, auto_port=args.auto_port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
