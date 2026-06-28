#!/usr/bin/env python3
"""Extract and compare Navimow schedule evidence from app logs."""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


DAY_RE = re.compile(r"MowerDailyPlanBeanV2@[0-9a-fA-F]+\{")
PERIOD_RE = re.compile(
    r"PlanPeriodBeanV2\{startTime=(\d+), endTime=(\d+), partitionIds=\[(.*?)\]\}"
)
PLAN_RE = re.compile(r"\bplan=([0-9a-fA-F]+)")
SET_WORK_PLAN_RE = re.compile(r"setWorkPlanTotal: fileContent = (\{.*\})")
UPDATE_DEVICE_RE = re.compile(r"updateDeviceSetting: .*?strData = (\{.*\}), operation_type = ([^\s,]+)")
CHANGE_HTTP_RE = re.compile(r"changeDeviceSettingNew http strData = (\{.*\})")
URL_RE = re.compile(r"request = url (https?://[^ ,]+).*?data -> (.*)$")
MODIFIED_RE = re.compile(r"getModifiedPlan: bSame = (true|false), newPlan = ")


@dataclass
class Evidence:
    line: int
    text: str
    value: Any


def fmt_tick(tick: int) -> str:
    if tick == 96:
        return "24:00"
    minutes = tick * 15
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def parse_jsonish(raw: str) -> Any:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def normalize_partition_ids(raw: str) -> list[str]:
    raw = raw.strip()
    if not raw:
        return []
    return [part.strip().strip("'\"") for part in raw.split(",") if part.strip()]


def find_matching_brace(text: str, start: int) -> int | None:
    depth = 0
    in_string = False
    escape = False

    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index

    return None


def parse_day_blocks(text: str) -> list[dict[str, Any]]:
    days: list[dict[str, Any]] = []

    for match in DAY_RE.finditer(text):
        start = text.find("{", match.start())
        end = find_matching_brace(text, start)
        if end is None:
            continue

        block = text[start + 1 : end]
        day_match = re.search(r"\bday=(\d+)", block)
        open_match = re.search(r"\bopen=(\d+)", block)
        if not day_match or not open_match:
            continue

        periods = []
        for period in PERIOD_RE.finditer(block):
            start_tick = int(period.group(1))
            end_tick = int(period.group(2))
            periods.append(
                {
                    "start_tick": start_tick,
                    "end_tick": end_tick,
                    "start": fmt_tick(start_tick),
                    "end": fmt_tick(end_tick),
                    "partition_ids": normalize_partition_ids(period.group(3)),
                }
            )

        days.append(
            {
                "day": int(day_match.group(1)),
                "open": int(open_match.group(1)),
                "periods": periods,
            }
        )

    return sorted(days, key=lambda item: item["day"])


def decode_legacy_plan(plan_hex: str) -> list[dict[str, Any]]:
    data = bytes.fromhex(plan_hex.strip())
    if not data:
        return []

    offset = 0
    day_count = data[offset]
    offset += 1
    days: list[dict[str, Any]] = []

    for _ in range(day_count):
        if offset + 3 > len(data):
            raise ValueError("truncated day header")

        day = data[offset]
        open_flag = data[offset + 1]
        period_count = data[offset + 2]
        offset += 3

        periods = []
        for _ in range(period_count):
            if offset + 2 > len(data):
                raise ValueError("truncated period")
            start_tick = data[offset]
            end_tick = data[offset + 1]
            offset += 2
            periods.append(
                {
                    "start_tick": start_tick,
                    "end_tick": end_tick,
                    "start": fmt_tick(start_tick),
                    "end": fmt_tick(end_tick),
                    "partition_ids": [],
                }
            )

        days.append({"day": day, "open": open_flag, "periods": periods})

    if offset != len(data):
        raise ValueError(f"unused trailing bytes: {data[offset:].hex()}")

    return days


def decode_single_day_work_plan(hex_value: str) -> dict[str, Any] | None:
    data = bytes.fromhex(hex_value.strip())
    if len(data) < 4 or data[0] != 1:
        return None

    day = data[1]
    open_flag = data[2]
    period_count = data[3]
    offset = 4
    periods = []

    for _ in range(period_count):
        if offset + 3 > len(data):
            return None
        start_tick = data[offset]
        end_tick = data[offset + 1]
        partition_count = data[offset + 2]
        offset += 3

        partition_ids = []
        for _ in range(partition_count):
            if offset >= len(data):
                return None
            partition_ids.append(str(data[offset]))
            offset += 1

        periods.append(
            {
                "start_tick": start_tick,
                "end_tick": end_tick,
                "start": fmt_tick(start_tick),
                "end": fmt_tick(end_tick),
                "partition_ids": partition_ids,
            }
        )

    if offset != len(data):
        return None

    return {"day": day, "open": open_flag, "periods": periods}


def schedule_key(schedule: list[dict[str, Any]]) -> str:
    return json.dumps(schedule, sort_keys=True, separators=(",", ":"))


def sanitize_value(value: Any) -> Any:
    if isinstance(value, dict):
        sanitized = {}
        for key, item in value.items():
            lowered = str(key).lower()
            if lowered in {"vehicle_sn", "sn", "serial", "auth_uid", "uid", "token", "accesstoken", "refreshtoken"}:
                sanitized[key] = "<redacted>"
            elif lowered == "cmd_num":
                sanitized[key] = "<redacted>"
            elif lowered == "url" and isinstance(item, str):
                sanitized[key] = "<signed-url redacted>" if "blob.core.windows.net" in item else sanitize_text(item)
            elif lowered == "file_path" and isinstance(item, str):
                sanitized[key] = "<signed-url redacted>" if "blob.core.windows.net" in item else sanitize_text(item)
            elif lowered == "file_name" and isinstance(item, str):
                sanitized[key] = sanitize_text(item)
            else:
                sanitized[key] = sanitize_value(item)
        return sanitized
    if isinstance(value, list):
        return [sanitize_value(item) for item in value]
    if isinstance(value, str):
        return sanitize_text(value)
    return value


def sanitize_text(text: str) -> str:
    text = re.sub(r"[A-Z0-9]{14,}", "<id>", text)
    text = re.sub(r"(?<=[_/])\d{7,}(?=[_.-])", "<uid>", text)
    text = re.sub(r"([?&]sig=)[^&\s]+", r"\1<redacted>", text)
    text = re.sub(
        r"((?:vehicleSn|vehicle_sn|sn|serial|Authorization|authorization|Bearer|token|accessToken|refreshToken|auth_uid|uid|cmd_num)\s*[=:\"']+\s*)[^,& }\"']+",
        r"\1<redacted>",
        text,
        flags=re.IGNORECASE,
    )
    return text


def endpoint_summary(url: str, data: str) -> dict[str, Any]:
    parsed = urlparse(url)
    value = parse_jsonish(data.strip())
    return {
        "host": parsed.netloc,
        "path": parsed.path,
        "data": sanitize_value(value),
    }


def extract(path: Path) -> dict[str, Any]:
    legacy_plans: list[Evidence] = []
    legacy_decoded: dict[str, Any] = {}
    v2_snapshots: list[Evidence] = []
    modified_days: list[Evidence] = []
    set_work_plan_payloads: list[Evidence] = []
    update_device_settings: list[Evidence] = []
    http_str_data: list[Evidence] = []
    endpoints: list[Evidence] = []
    single_day_hex: list[Evidence] = []

    for line_no, line in enumerate(path.read_text(errors="replace").splitlines(), start=1):
        for plan_match in PLAN_RE.finditer(line):
            plan_hex = plan_match.group(1)
            legacy_plans.append(Evidence(line_no, "legacy plan", plan_hex))
            if plan_hex not in legacy_decoded:
                try:
                    legacy_decoded[plan_hex] = decode_legacy_plan(plan_hex)
                except ValueError as exc:
                    legacy_decoded[plan_hex] = {"error": str(exc)}

        if "workPlanV2=[" in line:
            days = parse_day_blocks(line)
            if days:
                v2_snapshots.append(Evidence(line_no, "workPlanV2", days))

        modified_match = MODIFIED_RE.search(line)
        if modified_match:
            days = parse_day_blocks(line)
            if days:
                modified_days.append(
                    Evidence(line_no, f"getModifiedPlan bSame={modified_match.group(1)}", days[0])
                )

        set_match = SET_WORK_PLAN_RE.search(line)
        if set_match:
            set_work_plan_payloads.append(
                Evidence(line_no, "setWorkPlanTotal", sanitize_value(parse_jsonish(set_match.group(1))))
            )

        update_match = UPDATE_DEVICE_RE.search(line)
        if update_match:
            update_device_settings.append(
                Evidence(
                    line_no,
                    f"updateDeviceSetting operation_type={update_match.group(2)}",
                    sanitize_value(parse_jsonish(update_match.group(1))),
                )
            )

        change_match = CHANGE_HTTP_RE.search(line)
        if change_match:
            http_str_data.append(
                Evidence(line_no, "changeDeviceSettingNew http strData", sanitize_value(parse_jsonish(change_match.group(1))))
            )

        url_match = URL_RE.search(line)
        if url_match:
            endpoints.append(Evidence(line_no, "http", endpoint_summary(url_match.group(1), url_match.group(2))))

        if "getWorkPlan: strWorkPlan =" in line:
            hex_value = line.rsplit("=", 1)[-1].strip()
            decoded = decode_single_day_work_plan(hex_value)
            if decoded:
                single_day_hex.append(Evidence(line_no, hex_value, decoded))

    unique_legacy_plans = []
    seen_plans = set()
    for item in legacy_plans:
        if item.value in seen_plans:
            continue
        seen_plans.add(item.value)
        unique_legacy_plans.append({"line": item.line, "plan_hex": item.value, "decoded": legacy_decoded[item.value]})

    unique_snapshots = []
    seen_snapshots = set()
    for item in v2_snapshots:
        key = schedule_key(item.value)
        if key in seen_snapshots:
            continue
        seen_snapshots.add(key)
        unique_snapshots.append({"line": item.line, "schedule": item.value})

    return {
        "file": str(path),
        "legacy_plans": unique_legacy_plans,
        "latest_legacy_plan": unique_legacy_plans[-1] if unique_legacy_plans else None,
        "v2_snapshots": unique_snapshots,
        "latest_v2_schedule": unique_snapshots[-1] if unique_snapshots else None,
        "modified_days": [item.__dict__ for item in modified_days],
        "set_work_plan_payloads": [item.__dict__ for item in set_work_plan_payloads],
        "update_device_settings": [item.__dict__ for item in update_device_settings],
        "change_http_str_data": [item.__dict__ for item in http_str_data],
        "endpoints": [item.__dict__ for item in endpoints],
        "single_day_work_plan_hex": [item.__dict__ for item in single_day_hex],
    }


def day_map(schedule: list[dict[str, Any]] | None) -> dict[int, dict[str, Any]]:
    return {int(day["day"]): day for day in schedule or []}


def diff_schedules(before: list[dict[str, Any]] | None, after: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    before_days = day_map(before)
    after_days = day_map(after)
    changes = []

    for day in sorted(set(before_days) | set(after_days)):
        old = before_days.get(day)
        new = after_days.get(day)
        if old != new:
            changes.append({"day": day, "before": old, "after": new})

    return changes


def evidence_to_lines(items: list[dict[str, Any]], title: str) -> list[str]:
    lines = [title]
    if not items:
        lines.append("  none")
        return lines

    for item in items:
        lines.append(f"  line {item['line']}: {json.dumps(item['value'], ensure_ascii=False, sort_keys=True)}")
    return lines


def format_periods(day: dict[str, Any] | None) -> str:
    if day is None:
        return "<missing>"
    if not day["periods"]:
        return "closed"
    return ", ".join(
        f"{period['start_tick']}-{period['end_tick']} ({period['start']}-{period['end']})"
        for period in day["periods"]
    )


def print_extract(result: dict[str, Any]) -> None:
    print(f"file: {result['file']}")

    latest = result["latest_v2_schedule"]
    if latest:
        print(f"latest workPlanV2 schedule: line {latest['line']}")
        for day in latest["schedule"]:
            print(f"  day {day['day']} open={day['open']}: {format_periods(day)}")
    else:
        print("latest workPlanV2 schedule: none")

    legacy = result["latest_legacy_plan"]
    if legacy:
        print(f"latest legacy plan field: line {legacy['line']} {legacy['plan_hex']}")
    else:
        print("latest legacy plan field: none")

    for line in evidence_to_lines(result["set_work_plan_payloads"], "setWorkPlanTotal payloads:"):
        print(line)
    for line in evidence_to_lines(result["update_device_settings"], "updateDeviceSetting payloads:"):
        print(line)

    print("HTTP endpoints:")
    if not result["endpoints"]:
        print("  none")
    else:
        seen = set()
        for item in result["endpoints"]:
            endpoint = item["value"]
            key = (endpoint["path"], json.dumps(endpoint["data"], sort_keys=True))
            if key in seen:
                continue
            seen.add(key)
            print(f"  line {item['line']}: {endpoint['path']} -> {json.dumps(endpoint['data'], ensure_ascii=False, sort_keys=True)}")


def compare(before_path: Path, after_path: Path) -> dict[str, Any]:
    before = extract(before_path)
    after = extract(after_path)

    before_v2 = before["latest_v2_schedule"]["schedule"] if before["latest_v2_schedule"] else None
    after_v2 = after["latest_v2_schedule"]["schedule"] if after["latest_v2_schedule"] else None

    before_legacy = before["latest_legacy_plan"]["decoded"] if before["latest_legacy_plan"] else None
    after_legacy = after["latest_legacy_plan"]["decoded"] if after["latest_legacy_plan"] else None

    return {
        "before_file": str(before_path),
        "after_file": str(after_path),
        "work_plan_v2_changes": diff_schedules(before_v2, after_v2),
        "legacy_plan_changes": diff_schedules(before_legacy, after_legacy),
        "after_set_work_plan_payloads": after["set_work_plan_payloads"],
        "after_update_device_settings": after["update_device_settings"],
    }


def parse_period_arg(raw: str) -> dict[str, Any]:
    # Format: start-end or start-end:partition1,partition2
    time_part, _, partition_part = raw.partition(":")
    start_text, sep, end_text = time_part.partition("-")
    if not sep:
        raise argparse.ArgumentTypeError(f"period must be start-end, got {raw!r}")

    try:
        start_tick = int(start_text)
        end_tick = int(end_text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"period ticks must be integers, got {raw!r}") from exc

    if start_tick < 0 or end_tick > 96 or start_tick >= end_tick:
        raise argparse.ArgumentTypeError("period ticks must satisfy 0 <= start < end <= 96")

    partition_ids = []
    if partition_part:
        partition_ids = [part.strip() for part in partition_part.split(",") if part.strip()]

    return {
        "end_time": end_tick,
        "partition_ids": partition_ids,
        "start_time": start_tick,
    }


def build_payload(day: int, open_flag: int, periods: list[dict[str, Any]]) -> dict[str, Any]:
    plan = {
        "day": day,
        "open": open_flag,
        "period": periods,
    }
    set_work_plan_total = {"planList": [plan]}
    return {
        "setWorkPlanTotal": set_work_plan_total,
        "updateDeviceSetting": {"planList": json.dumps(set_work_plan_total, separators=(",", ":"))},
    }


def print_compare(result: dict[str, Any]) -> None:
    print(f"before: {result['before_file']}")
    print(f"after:  {result['after_file']}")
    print("workPlanV2 changes:")
    if not result["work_plan_v2_changes"]:
        print("  none")
    else:
        for change in result["work_plan_v2_changes"]:
            print(f"  day {change['day']}:")
            print(f"    before: {format_periods(change['before'])}")
            print(f"    after:  {format_periods(change['after'])}")

    print("legacy plan field changes:")
    if not result["legacy_plan_changes"]:
        print("  none")
    else:
        for change in result["legacy_plan_changes"]:
            print(f"  day {change['day']}:")
            print(f"    before: {format_periods(change['before'])}")
            print(f"    after:  {format_periods(change['after'])}")

    for line in evidence_to_lines(result["after_set_work_plan_payloads"], "after setWorkPlanTotal payloads:"):
        print(line)
    for line in evidence_to_lines(result["after_update_device_settings"], "after updateDeviceSetting payloads:"):
        print(line)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    extract_parser = subparsers.add_parser("extract")
    extract_parser.add_argument("logfile", type=Path)
    extract_parser.add_argument("--json", action="store_true")

    diff_parser = subparsers.add_parser("diff")
    diff_parser.add_argument("before", type=Path)
    diff_parser.add_argument("after", type=Path)
    diff_parser.add_argument("--json", action="store_true")

    payload_parser = subparsers.add_parser("payload")
    payload_parser.add_argument("--day", type=int, required=True, choices=range(1, 8))
    payload_parser.add_argument("--open", type=int, default=1, choices=(0, 1))
    payload_parser.add_argument(
        "--period",
        type=parse_period_arg,
        action="append",
        default=[],
        help="Period as start-end ticks, optionally start-end:partition1,partition2. Repeat for multiple periods.",
    )
    payload_parser.add_argument("--json", action="store_true")

    args = parser.parse_args()

    if args.command == "extract":
        result = extract(args.logfile)
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        else:
            print_extract(result)
    elif args.command == "diff":
        result = compare(args.before, args.after)
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        else:
            print_compare(result)
    elif args.command == "payload":
        result = build_payload(args.day, args.open, args.period)
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        else:
            print("setWorkPlanTotal:")
            print(json.dumps(result["setWorkPlanTotal"], ensure_ascii=False, separators=(",", ":")))
            print("updateDeviceSetting:")
            print(json.dumps(result["updateDeviceSetting"], ensure_ascii=False, separators=(",", ":")))


if __name__ == "__main__":
    main()
