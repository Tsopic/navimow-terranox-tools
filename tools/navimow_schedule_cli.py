#!/usr/bin/env python3
"""Edit and validate local Navimow schedule drafts.

This tool is intentionally dry-run: it generates the app-shaped planList payload,
but it does not send commands to the mower.
"""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import json
import math
import sqlite3
import sys
from pathlib import Path
from typing import Any

from build_navimow_map_viewer import (
    DAY_NAMES,
    extract_areas,
    load_area_last_mow,
    load_map_detail,
    load_mower_display,
    load_render_metadata,
    load_schedule,
    schedule_to_draft,
)


DAY_LOOKUP = {
    "1": 1,
    "sun": 1,
    "sunday": 1,
    "2": 2,
    "mon": 2,
    "monday": 2,
    "3": 3,
    "tue": 3,
    "tuesday": 3,
    "4": 4,
    "wed": 4,
    "wednesday": 4,
    "5": 5,
    "thu": 5,
    "thursday": 5,
    "6": 6,
    "fri": 6,
    "friday": 6,
    "7": 7,
    "sat": 7,
    "saturday": 7,
}

MAX_PERIODS_PER_DAY = 4
DEFAULT_WEEKLY_DAY_WINDOW = "06:00-22:00"
DEFAULT_WEEKLY_NIGHT_WINDOW = "22:00-06:00"


def day_number(value: str | int) -> int:
    key = str(value).strip().lower()
    if key not in DAY_LOOKUP:
        raise argparse.ArgumentTypeError(f"Unknown day: {value}")
    return DAY_LOOKUP[key]


def time_to_tick(value: str) -> int:
    raw = value.strip()
    if raw == "24:00":
        return 96
    parts = raw.split(":")
    if len(parts) != 2:
        raise ValueError(f"Invalid time: {value}")
    hours = int(parts[0])
    minutes = int(parts[1])
    if not (0 <= hours <= 23 and 0 <= minutes <= 59):
        raise ValueError(f"Invalid time: {value}")
    if minutes % 15 != 0:
        raise ValueError(f"Time must align to 15-minute ticks: {value}")
    return (hours * 60 + minutes) // 15


def tick_to_time(tick: int) -> str:
    if tick == 96:
        return "24:00"
    minutes = tick * 15
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def now_iso() -> str:
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat()


def parse_iso_datetime(value: Any) -> dt.datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.UTC)
    return parsed


def parse_preferred_window(value: str) -> tuple[int, int]:
    parts = value.split("-", 1)
    if len(parts) != 2:
        raise ValueError("--preferred-window must look like HH:MM-HH:MM")
    start = time_to_tick(parts[0])
    end = time_to_tick(parts[1])
    if not (0 <= start < end <= 96):
        raise ValueError(f"Invalid preferred window: {value}")
    return start, end


def parse_window(value: str, *, option_name: str, allow_wrap: bool = False) -> tuple[int, int]:
    parts = value.split("-", 1)
    if len(parts) != 2:
        raise ValueError(f"{option_name} must look like HH:MM-HH:MM")
    start = time_to_tick(parts[0])
    end = time_to_tick(parts[1])
    if start == end:
        raise ValueError(f"{option_name} cannot have matching start and end times")
    if start > end and not allow_wrap:
        raise ValueError(f"{option_name} cannot wrap midnight")
    return start, end


def window_segments(value: str) -> list[tuple[int, int]]:
    start, end = parse_window(value, option_name="window", allow_wrap=True)
    if start < end:
        return [(start, end)]
    return [(0, end), (start, 96)]


def largest_window_capacity(segments: list[tuple[int, int]]) -> int:
    return max((end - start for start, end in segments), default=0)


def period_within_segments(period: dict[str, Any], segments: list[tuple[int, int]]) -> bool:
    start = period.get("startTick")
    end = period.get("endTick")
    return any(isinstance(start, int) and isinstance(end, int) and start >= seg_start and end <= seg_end for seg_start, seg_end in segments)


def normalize_area_selector(value: Any) -> str:
    return "".join(ch for ch in str(value).casefold() if ch.isalnum())


def split_area_selectors(values: list[str] | None) -> list[str]:
    selectors: list[str] = []
    for value in values or []:
        for part in str(value).split(","):
            selector = part.strip()
            if selector:
                selectors.append(selector)
    return selectors


def resolve_area_selectors(areas: list[dict[str, Any]], selectors: list[str]) -> set[int]:
    resolved: set[int] = set()
    if not selectors:
        return resolved
    by_id = {str(area.get("id")): int(area["id"]) for area in areas if area.get("id") is not None}
    normalized = [
        (normalize_area_selector(area.get("name") or ""), int(area["id"]), str(area.get("name") or area.get("id")))
        for area in areas
        if area.get("id") is not None
    ]
    for selector in selectors:
        if selector in by_id:
            resolved.add(by_id[selector])
            continue
        needle = normalize_area_selector(selector)
        matches = [area_id for name, area_id, _label in normalized if name == needle]
        if not matches and needle:
            matches = [area_id for name, area_id, _label in normalized if needle in name]
        if not matches:
            labels = ", ".join(label for _name, _area_id, label in normalized)
            raise ValueError(f"night-only area '{selector}' did not match any known area ({labels})")
        if len(set(matches)) > 1:
            raise ValueError(f"night-only area '{selector}' matched multiple areas; use an area id")
        resolved.add(matches[0])
    return resolved


def parse_area_ids(value: str) -> list[int]:
    if not value.strip():
        return []
    ids = []
    for part in value.split(","):
        part = part.strip()
        if not part:
            continue
        ids.append(int(part))
    return ids


def load_draft(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_draft(path: Path, draft: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(draft, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def validate_draft(draft: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    days = draft.get("days")
    if not isinstance(days, list) or len(days) != 7:
        errors.append("draft.days must contain 7 days")
        return errors

    seen_days = set()
    for day in days:
        day_id = day.get("day")
        if day_id in seen_days:
            errors.append(f"duplicate day {day_id}")
        seen_days.add(day_id)
        if day_id not in range(1, 8):
            errors.append(f"invalid day {day_id}")

        periods = day.get("periods", [])
        if isinstance(periods, list) and len(periods) > MAX_PERIODS_PER_DAY:
            errors.append(f"day {day_id}: at most {MAX_PERIODS_PER_DAY} periods are supported")
        previous_end = -1
        for index, period in enumerate(periods):
            start_tick = period.get("startTick")
            end_tick = period.get("endTick")
            if not isinstance(start_tick, int) or not isinstance(end_tick, int):
                errors.append(f"day {day_id} period {index}: ticks must be integers")
                continue
            if not (0 <= start_tick < end_tick <= 96):
                errors.append(f"day {day_id} period {index}: invalid tick range {start_tick}-{end_tick}")
            if start_tick < previous_end:
                errors.append(f"day {day_id} period {index}: overlaps previous period")
            previous_end = max(previous_end, end_tick)

            ids = period.get("partitionIds")
            if not isinstance(ids, list) or not all(isinstance(item, int) for item in ids):
                errors.append(f"day {day_id} period {index}: partitionIds must be integer list")
            mode = period.get("mode")
            if mode not in {"custom", "all_zones"}:
                errors.append(f"day {day_id} period {index}: invalid mode {mode}")
            if mode == "all_zones" and ids:
                errors.append(f"day {day_id} period {index}: all_zones periods must not contain partitionIds")
            if mode == "custom" and not ids:
                errors.append(f"day {day_id} period {index}: custom periods require partitionIds")
    return errors


def make_plan_list(draft: dict[str, Any]) -> dict[str, Any]:
    return {
        "planList": [
            {
                "day": day["day"],
                "open": day.get("open", 1 if day.get("periods") else 0),
                "period": [
                    {
                        "start_time": period["startTick"],
                        "end_time": period["endTick"],
                        "partition_ids": period.get("partitionIds", []),
                    }
                    for period in day.get("periods", [])
                ],
            }
            for day in sorted(draft.get("days", []), key=lambda item: item["day"])
        ]
    }


def load_optimization_context(db: Path) -> dict[str, Any]:
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    schedule = load_schedule(con)
    _detail_row, detail = load_map_detail(con)
    render, _artifact_path, _image_name = load_render_metadata(con)
    areas = extract_areas(detail, render)
    last_mow = load_area_last_mow(con)
    mower = load_mower_display(con)
    global_height = ((mower.get("cutting") or {}).get("heightMm"))
    for area in areas:
        area_id = area["id"]
        area_height = ((area.get("cutting") or {}).get("areaHeightMm"))
        area.setdefault("cutting", {})["effectiveHeightMm"] = area_height or global_height
        area["lastMow"] = last_mow.get(
            area_id,
            {
                "status": "no_mow_in_history",
                "lastAt": None,
                "partitionPercentage": 0,
                "finishedAreaM2": 0.0,
            },
        )
    return {
        "latestScheduleSnapshotId": schedule.get("snapshotId"),
        "latestScheduleObservedAt": schedule.get("observedAt"),
        "areas": areas,
        "mower": mower,
    }


def stale_base_messages(draft: dict[str, Any], context: dict[str, Any]) -> list[str]:
    messages: list[str] = []
    latest_id = context.get("latestScheduleSnapshotId")
    latest_observed = context.get("latestScheduleObservedAt")
    base_id = draft.get("baseSnapshotId")
    base_observed = draft.get("baseObservedAt")
    if latest_id is not None and base_id != latest_id:
        messages.append(f"draft base snapshot {base_id} does not match latest SQLite snapshot {latest_id}")
    if latest_observed and base_observed and base_observed != latest_observed:
        messages.append("draft base observed timestamp does not match latest SQLite schedule")
    return messages


def is_adverse_flag(value: Any) -> bool:
    if value in (None, "", False):
        return False
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value > 0
    text = str(value).strip().lower()
    return text not in {"0", "false", "none", "normal", "clear", "ok", "idle", "unknown"}


def is_active_state(value: Any) -> bool:
    if value in (None, ""):
        return False
    text = str(value).strip().lower()
    passive_terms = ("idle", "dock", "charge", "standby", "park", "stop", "offline")
    active_terms = ("running", "mow", "work", "return", "pause", "moving", "active")
    if any(term in text for term in active_terms):
        return True
    if any(term in text for term in passive_terms):
        return False
    return False


def optimizer_blockers(context: dict[str, Any]) -> list[str]:
    blockers: list[str] = []
    insights = ((context.get("mower") or {}).get("routeInsights") or {})
    weather_flags = ((insights.get("weather") or {}).get("flags") or {})
    adverse = [key for key, value in weather_flags.items() if is_adverse_flag(value)]
    if adverse:
        blockers.append("adverse weather flags: " + ", ".join(sorted(adverse)))

    openapi_state = (insights.get("openapiStatus") or {}).get("vehicleState")
    if is_active_state(openapi_state):
        blockers.append(f"OpenAPI mower state looks active: {openapi_state}")
    consumer_state = (insights.get("consumerLiveState") or {}).get("state")
    if is_active_state(consumer_state):
        blockers.append(f"consumer mower state looks active: {consumer_state}")
    today_status = (insights.get("todayPlan") or {}).get("status")
    if is_active_state(today_status):
        blockers.append(f"today-plan status looks active: {today_status}")
    return blockers


def score_area(area: dict[str, Any], *, now: dt.datetime | None = None) -> tuple[float, list[str]]:
    now = now or dt.datetime.now(dt.UTC)
    last_mow = area.get("lastMow") or {}
    status = str(last_mow.get("status") or "no_mow_in_history")
    percentage = last_mow.get("partitionPercentage")
    try:
        completion = float(percentage)
    except (TypeError, ValueError):
        completion = 0.0
    size_m2 = float(area.get("sizeM2") or 0.0)
    height = ((area.get("cutting") or {}).get("effectiveHeightMm"))
    try:
        height_mm = float(height)
    except (TypeError, ValueError):
        height_mm = 0.0

    score = size_m2 / 25.0
    reasons = [f"area {size_m2:.1f} m2"]
    if status == "no_mow_in_history":
        score += 120
        reasons.append("no mow history")
    elif status == "partial":
        score += 90 + max(0.0, 100.0 - completion) / 2.0
        reasons.append(f"partial completion {completion:.0f}%")
    else:
        last_at = parse_iso_datetime(last_mow.get("lastAt"))
        if last_at is None:
            score += 40
            reasons.append("missing last mow timestamp")
        else:
            age_days = max(0.0, (now - last_at).total_seconds() / 86400.0)
            score += min(age_days, 30.0) * 3.0
            reasons.append(f"{age_days:.1f} days since last mow")
    if height_mm >= 60:
        score += 8
        reasons.append(f"higher cut {height_mm:.0f} mm")
    return round(score, 2), reasons


def estimate_duration_ticks(area: dict[str, Any], *, m2_per_hour: float) -> int:
    size_m2 = max(1.0, float(area.get("sizeM2") or 1.0))
    height = ((area.get("cutting") or {}).get("effectiveHeightMm"))
    try:
        height_mm = float(height)
    except (TypeError, ValueError):
        height_mm = 50.0
    complexity = 1.0 + max(0.0, height_mm - 50.0) / 120.0
    hours = (size_m2 / max(m2_per_hour, 1.0)) * complexity
    return max(1, math.ceil(hours * 4))


def build_optimization_plan(
    context: dict[str, Any],
    *,
    day_id: int,
    preferred_window: str,
    m2_per_hour: float,
    min_days_between: int,
    max_periods_per_day: int,
) -> dict[str, Any]:
    if max_periods_per_day < 1 or max_periods_per_day > MAX_PERIODS_PER_DAY:
        raise ValueError(f"max periods per day must be between 1 and {MAX_PERIODS_PER_DAY}")
    start_tick, end_tick = parse_preferred_window(preferred_window)
    capacity = end_tick - start_tick
    now = dt.datetime.now(dt.UTC)
    candidates = []
    for area in context.get("areas", []):
        score, reasons = score_area(area, now=now)
        last_mow = area.get("lastMow") or {}
        last_at = parse_iso_datetime(last_mow.get("lastAt"))
        if min_days_between > 0 and last_at is not None:
            age_days = (now - last_at).total_seconds() / 86400.0
            if age_days < min_days_between and last_mow.get("status") == "completed":
                continue
        duration = estimate_duration_ticks(area, m2_per_hour=m2_per_hour)
        candidates.append(
            {
                "areaId": area["id"],
                "name": area.get("name"),
                "score": score,
                "durationTicks": duration,
                "durationMinutes": duration * 15,
                "sizeM2": area.get("sizeM2"),
                "lastMowStatus": last_mow.get("status"),
                "lastMowAt": last_mow.get("lastAt"),
                "completionPercent": last_mow.get("partitionPercentage"),
                "effectiveHeightMm": ((area.get("cutting") or {}).get("effectiveHeightMm")),
                "reasons": reasons,
            }
        )
    candidates.sort(key=lambda item: (-item["score"], -float(item.get("sizeM2") or 0.0), item["areaId"]))

    periods = []
    cursor = start_tick
    for candidate in candidates:
        if len(periods) >= max_periods_per_day:
            break
        duration = int(candidate["durationTicks"])
        if cursor + duration > end_tick:
            continue
        periods.append(
            {
                "start": tick_to_time(cursor),
                "end": tick_to_time(cursor + duration),
                "startTick": cursor,
                "endTick": cursor + duration,
                "partitionIds": [int(candidate["areaId"])],
                "mode": "custom",
                "optimizer": {
                    "score": candidate["score"],
                    "durationMinutes": candidate["durationMinutes"],
                    "reasons": candidate["reasons"],
                },
            }
        )
        cursor += duration

    return {
        "day": day_id,
        "preferredWindow": preferred_window,
        "windowCapacityTicks": capacity,
        "m2PerHour": m2_per_hour,
        "minDaysBetween": min_days_between,
        "maxPeriodsPerDay": max_periods_per_day,
        "candidateCount": len(candidates),
        "selectedAreaCount": len(periods),
        "candidates": candidates[:20],
        "periods": periods,
    }


def weekly_visit_count(area: dict[str, Any], *, min_visits: int, priority_visits: int) -> int:
    if min_visits < 1:
        raise ValueError("min visits per week must be at least 1")
    if priority_visits < min_visits:
        raise ValueError("priority visits per week must be greater than or equal to min visits")
    last_mow = area.get("lastMow") or {}
    status = str(last_mow.get("status") or "")
    completion = last_mow.get("partitionPercentage")
    try:
        completion_percent = float(completion)
    except (TypeError, ValueError):
        completion_percent = 0.0
    size_m2 = float(area.get("sizeM2") or 0.0)
    if status in {"no_mow_in_history", "partial"} or completion_percent < 80 or size_m2 >= 300:
        return priority_visits
    return min_visits


def weekly_area_weight(area: dict[str, Any], score: float) -> float:
    size_m2 = max(1.0, float(area.get("sizeM2") or 1.0))
    height = ((area.get("cutting") or {}).get("effectiveHeightMm"))
    try:
        height_mm = float(height)
    except (TypeError, ValueError):
        height_mm = 50.0
    height_factor = 1.0 + max(0.0, height_mm - 50.0) / 150.0
    score_factor = 1.0 + min(max(score, 0.0), 180.0) / 600.0
    return round(size_m2 * height_factor * score_factor, 3)


def build_weekly_area_targets(
    context: dict[str, Any],
    *,
    night_only_ids: set[int],
    min_visits_per_week: int,
    priority_visits_per_week: int,
) -> list[dict[str, Any]]:
    now = dt.datetime.now(dt.UTC)
    targets = []
    for area in context.get("areas", []):
        area_id = int(area["id"])
        score, reasons = score_area(area, now=now)
        visits = weekly_visit_count(
            area,
            min_visits=min_visits_per_week,
            priority_visits=priority_visits_per_week,
        )
        weight = weekly_area_weight(area, score)
        last_mow = area.get("lastMow") or {}
        targets.append(
            {
                "areaId": area_id,
                "name": area.get("name"),
                "sizeM2": area.get("sizeM2"),
                "score": score,
                "reasons": reasons,
                "visitsPerWeek": visits,
                "nightOnly": area_id in night_only_ids,
                "weight": weight,
                "lastMowStatus": last_mow.get("status"),
                "lastMowAt": last_mow.get("lastAt"),
                "completionPercent": last_mow.get("partitionPercentage"),
                "effectiveHeightMm": ((area.get("cutting") or {}).get("effectiveHeightMm")),
            }
        )
    targets.sort(key=lambda item: (-item["score"], -float(item.get("sizeM2") or 0.0), item["areaId"]))
    return targets


def assign_weekly_buckets(
    targets: list[dict[str, Any]],
    *,
    day_numbers: list[int],
    bucket_kind: str,
    capacity_ticks: int,
) -> list[dict[str, Any]]:
    buckets = {
        day: {
            "kind": bucket_kind,
            "day": day,
            "areaIds": [],
            "items": [],
            "weight": 0.0,
            "capacityTicks": capacity_ticks,
        }
        for day in day_numbers
    }
    for target_index, target in enumerate(targets):
        used_days: set[int] = set()
        visits = min(int(target["visitsPerWeek"]), len(day_numbers))
        for visit_index in range(visits):
            preferred_position = round((target_index + visit_index * len(day_numbers) / visits)) % len(day_numbers)
            choices = []
            for day_index, day in enumerate(day_numbers):
                if day in used_days:
                    continue
                distance = min((day_index - preferred_position) % len(day_numbers), (preferred_position - day_index) % len(day_numbers))
                choices.append((buckets[day]["weight"], distance, day_index, day))
            if not choices:
                break
            _weight, _distance, _day_index, chosen_day = min(choices)
            used_days.add(chosen_day)
            bucket = buckets[chosen_day]
            area_id = int(target["areaId"])
            if area_id not in bucket["areaIds"]:
                bucket["areaIds"].append(area_id)
            bucket["items"].append(
                {
                    "areaId": area_id,
                    "name": target.get("name"),
                    "score": target.get("score"),
                    "weight": target.get("weight"),
                    "visit": visit_index + 1,
                    "visitsPerWeek": visits,
                    "nightOnly": target.get("nightOnly"),
                    "reasons": target.get("reasons"),
                }
            )
            bucket["weight"] += float(target["weight"])
    return [bucket for bucket in buckets.values() if bucket["areaIds"]]


def allocate_bucket_ticks(buckets: list[dict[str, Any]], target_ticks: int) -> None:
    if not buckets:
        return
    total_capacity = sum(int(bucket["capacityTicks"]) for bucket in buckets)
    if target_ticks < len(buckets):
        raise ValueError("max weekly hours is too low for one 15-minute period per scheduled bucket")
    target_ticks = min(target_ticks, total_capacity)
    total_weight = sum(max(0.001, float(bucket["weight"])) for bucket in buckets)
    assigned = 0
    for bucket in buckets:
        raw = target_ticks * (max(0.001, float(bucket["weight"])) / total_weight)
        ticks = max(1, min(int(bucket["capacityTicks"]), math.floor(raw)))
        bucket["allocatedRawTicks"] = raw
        bucket["durationTicks"] = ticks
        assigned += ticks

    while assigned > target_ticks:
        candidates = [bucket for bucket in buckets if int(bucket["durationTicks"]) > 1]
        if not candidates:
            break
        bucket = max(candidates, key=lambda item: (int(item["durationTicks"]), float(item["allocatedRawTicks"])))
        bucket["durationTicks"] -= 1
        assigned -= 1

    while assigned < target_ticks:
        candidates = [bucket for bucket in buckets if int(bucket["durationTicks"]) < int(bucket["capacityTicks"])]
        if not candidates:
            break
        bucket = max(
            candidates,
            key=lambda item: (
                float(item["allocatedRawTicks"]) - int(item["durationTicks"]),
                float(item["weight"]),
            ),
        )
        bucket["durationTicks"] += 1
        assigned += 1


def choose_segment_for_duration(segments: list[tuple[int, int]], duration_ticks: int) -> tuple[int, int]:
    candidates = [(start, end) for start, end in segments if end - start >= duration_ticks]
    if candidates:
        return max(candidates, key=lambda item: (item[1] - item[0], -item[0]))
    return max(segments, key=lambda item: item[1] - item[0])


def bucket_to_period(bucket: dict[str, Any], *, segments: list[tuple[int, int]]) -> dict[str, Any]:
    duration = int(bucket["durationTicks"])
    segment_start, segment_end = choose_segment_for_duration(segments, duration)
    if duration > segment_end - segment_start:
        raise ValueError("allocated period does not fit its scheduling window")
    area_ids = sorted(set(int(area_id) for area_id in bucket["areaIds"]))
    return {
        "start": tick_to_time(segment_start),
        "end": tick_to_time(segment_start + duration),
        "startTick": segment_start,
        "endTick": segment_start + duration,
        "partitionIds": area_ids,
        "mode": "custom",
        "optimizer": {
            "kind": bucket["kind"],
            "durationMinutes": duration * 15,
            "weight": round(float(bucket["weight"]), 3),
            "areas": bucket["items"],
        },
    }


def scheduled_weekly_ticks(draft: dict[str, Any]) -> int:
    return sum(
        int(period.get("endTick", 0)) - int(period.get("startTick", 0))
        for day in draft.get("days", [])
        for period in day.get("periods", [])
    )


def weekly_constraint_errors(
    draft: dict[str, Any],
    *,
    max_weekly_hours: float,
    night_only_ids: set[int],
    night_segments: list[tuple[int, int]],
    all_area_ids: set[int],
) -> list[str]:
    errors: list[str] = []
    weekly_hours = scheduled_weekly_ticks(draft) / 4.0
    if weekly_hours > max_weekly_hours + 0.001:
        errors.append(f"weekly schedule is {weekly_hours:.2f}h, above {max_weekly_hours:.2f}h")
    for day in draft.get("days", []):
        for period in day.get("periods", []):
            partition_ids = set(period.get("partitionIds") or [])
            if period.get("mode") != "custom":
                errors.append(f"day {day.get('day')}: weekly optimizer periods must be custom")
            night_ids_in_period = partition_ids & night_only_ids
            if night_ids_in_period and not period_within_segments(period, night_segments):
                ids = ",".join(str(item) for item in sorted(night_ids_in_period))
                errors.append(f"day {day.get('day')}: night-only area(s) {ids} outside night window")
            if all_area_ids and partition_ids == all_area_ids:
                errors.append(f"day {day.get('day')}: period still selects every known area")
    return errors


def build_weekly_optimization_plan(
    context: dict[str, Any],
    *,
    max_weekly_hours: float,
    target_weekly_hours: float | None,
    night_only_selectors: list[str],
    night_window: str,
    day_window: str,
    min_visits_per_week: int,
    priority_visits_per_week: int,
) -> dict[str, Any]:
    areas = list(context.get("areas", []))
    if not areas:
        raise ValueError("weekly optimizer needs at least one known map area")
    if max_weekly_hours <= 0:
        raise ValueError("max weekly hours must be greater than zero")
    target_hours = max_weekly_hours if target_weekly_hours is None else target_weekly_hours
    if target_hours <= 0:
        raise ValueError("target weekly hours must be greater than zero")
    target_hours = min(target_hours, max_weekly_hours)

    night_segments = window_segments(night_window)
    day_segments = [parse_window(day_window, option_name="--day-window", allow_wrap=False)]
    if any(start == end for start, end in night_segments + day_segments):
        raise ValueError("optimizer windows must have duration")

    selectors = split_area_selectors(night_only_selectors)
    night_only_ids = resolve_area_selectors(areas, selectors)
    all_area_ids = {int(area["id"]) for area in areas}
    targets = build_weekly_area_targets(
        context,
        night_only_ids=night_only_ids,
        min_visits_per_week=min_visits_per_week,
        priority_visits_per_week=priority_visits_per_week,
    )

    day_numbers = [1, 2, 3, 4, 5, 6, 7]
    day_capacity = day_segments[0][1] - day_segments[0][0]
    night_capacity = largest_window_capacity(night_segments)
    day_buckets = assign_weekly_buckets(
        [target for target in targets if not target["nightOnly"]],
        day_numbers=day_numbers,
        bucket_kind="day",
        capacity_ticks=day_capacity,
    )
    night_buckets = assign_weekly_buckets(
        [target for target in targets if target["nightOnly"]],
        day_numbers=day_numbers,
        bucket_kind="night",
        capacity_ticks=night_capacity,
    )
    buckets = day_buckets + night_buckets
    target_ticks = int(math.floor(target_hours * 4))
    allocate_bucket_ticks(buckets, target_ticks)

    days = []
    for day_id in day_numbers:
        periods = []
        for bucket in buckets:
            if bucket["day"] != day_id:
                continue
            segments = night_segments if bucket["kind"] == "night" else day_segments
            periods.append(bucket_to_period(bucket, segments=segments))
        periods.sort(key=lambda item: item["startTick"])
        days.append(
            {
                "day": day_id,
                "dayName": DAY_NAMES[day_id],
                "open": 1 if periods else 0,
                "periods": periods,
            }
        )

    draft_like = {"days": days}
    constraint_errors = weekly_constraint_errors(
        draft_like,
        max_weekly_hours=max_weekly_hours,
        night_only_ids=night_only_ids,
        night_segments=night_segments,
        all_area_ids=all_area_ids,
    )
    if constraint_errors:
        raise ValueError("; ".join(constraint_errors))

    scheduled_hours = scheduled_weekly_ticks(draft_like) / 4.0
    return {
        "mode": "weekly",
        "maxWeeklyHours": max_weekly_hours,
        "targetWeeklyHours": target_hours,
        "scheduledWeeklyHours": scheduled_hours,
        "scheduledWeeklyTicks": scheduled_weekly_ticks(draft_like),
        "dayWindow": day_window,
        "nightWindow": night_window,
        "minVisitsPerWeek": min_visits_per_week,
        "priorityVisitsPerWeek": priority_visits_per_week,
        "nightOnlyAreas": [
            {"id": target["areaId"], "name": target.get("name")}
            for target in targets
            if target["nightOnly"]
        ],
        "allAreaCount": len(all_area_ids),
        "areaTargets": targets,
        "dayPlans": days,
    }


def apply_optimization_plan(draft: dict[str, Any], plan: dict[str, Any], *, warnings: list[str], blockers: list[str]) -> dict[str, Any]:
    result = copy.deepcopy(draft)
    day = next((item for item in result["days"] if item["day"] == plan["day"]), None)
    if day is None:
        raise ValueError(f"Day {plan['day']} not found in draft")
    day["open"] = 1 if plan["periods"] else 0
    day["periods"] = plan["periods"]
    result["optimization"] = {
        "generatedAt": now_iso(),
        "status": "proposed",
        "dryRunOnly": True,
        "warnings": warnings,
        "blockersIgnored": blockers,
        "plan": plan,
    }
    return result


def apply_weekly_optimization_plan(draft: dict[str, Any], plan: dict[str, Any], *, warnings: list[str], blockers: list[str]) -> dict[str, Any]:
    result = copy.deepcopy(draft)
    result["days"] = copy.deepcopy(plan["dayPlans"])
    result["optimization"] = {
        "generatedAt": now_iso(),
        "status": "proposed",
        "dryRunOnly": True,
        "warnings": warnings,
        "blockersIgnored": blockers,
        "plan": plan,
    }
    return result


def print_optimization_summary(optimization: dict[str, Any], *, output: Path | None, explain: bool) -> None:
    plan = optimization.get("plan") or {}
    day_name = DAY_NAMES.get(plan.get("day"), str(plan.get("day") or "n/a"))
    print("optimizer dry run")
    print(f"day: {day_name}")
    print(f"areas considered: {plan.get('candidateCount', 0)}")
    print(f"proposed periods: {plan.get('selectedAreaCount', 0)}")
    print(f"preferred window: {plan.get('preferredWindow', 'n/a')}")
    warnings = optimization.get("warnings") or []
    blockers = optimization.get("blockersIgnored") or []
    print("warnings: " + ("; ".join(warnings) if warnings else "none"))
    print("blockers ignored: " + ("; ".join(blockers) if blockers else "none"))
    if output:
        print(f"wrote: {output}")
    if explain:
        for period in plan.get("periods", []):
            meta = period.get("optimizer") or {}
            area = ",".join(str(item) for item in period.get("partitionIds", []))
            reasons = "; ".join(meta.get("reasons") or [])
            print(f"  {period['start']}-{period['end']} areas={area} score={meta.get('score')} {reasons}")
    print("no mower command was sent")


def print_weekly_optimization_summary(optimization: dict[str, Any], *, output: Path | None, explain: bool) -> None:
    plan = optimization.get("plan") or {}
    print("weekly optimizer dry run")
    print(f"scheduled weekly hours: {float(plan.get('scheduledWeeklyHours') or 0):.2f}")
    print(f"max weekly hours: {float(plan.get('maxWeeklyHours') or 0):.2f}")
    print(f"day window: {plan.get('dayWindow', 'n/a')}")
    print(f"night window: {plan.get('nightWindow', 'n/a')}")
    night_only = plan.get("nightOnlyAreas") or []
    print("night-only areas: " + (", ".join(f"{item.get('name') or item.get('id')} ({item.get('id')})" for item in night_only) if night_only else "none"))
    warnings = optimization.get("warnings") or []
    blockers = optimization.get("blockersIgnored") or []
    print("warnings: " + ("; ".join(warnings) if warnings else "none"))
    print("blockers ignored: " + ("; ".join(blockers) if blockers else "none"))
    if output:
        print(f"wrote: {output}")
    if explain:
        for day in plan.get("dayPlans", []):
            for period in day.get("periods", []):
                meta = period.get("optimizer") or {}
                names = ", ".join(str(item.get("name") or item.get("areaId")) for item in meta.get("areas", []))
                print(f"  {day.get('dayName')}: {period['start']}-{period['end']} {meta.get('kind')} areas={names}")
    print("no mower command was sent")


def add_period(draft: dict[str, Any], day_id: int, start: str, end: str, area_ids: list[int], mode: str, replace_day: bool) -> None:
    day = next((item for item in draft["days"] if item["day"] == day_id), None)
    if day is None:
        raise ValueError(f"Day {day_id} not found in draft")
    period = {
        "start": tick_to_time(time_to_tick(start)),
        "end": tick_to_time(time_to_tick(end)),
        "startTick": time_to_tick(start),
        "endTick": time_to_tick(end),
        "partitionIds": [] if mode == "all_zones" else area_ids,
        "mode": mode,
    }
    if replace_day:
        day["periods"] = []
    day["open"] = 1
    day["periods"].append(period)
    day["periods"].sort(key=lambda item: item["startTick"])


def cmd_export(args: argparse.Namespace) -> int:
    con = sqlite3.connect(args.db)
    con.row_factory = sqlite3.Row
    draft = schedule_to_draft(load_schedule(con))
    write_draft(args.output, draft)
    print(args.output)
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    draft = load_draft(args.schedule)
    errors = validate_draft(draft)
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1
    print("schedule draft is valid")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    draft = load_draft(args.schedule)
    for day in draft["days"]:
        print(f"{day['day']} {day.get('dayName') or DAY_NAMES.get(day['day'])}:")
        if not day.get("periods"):
            print("  closed")
        for period in day.get("periods", []):
            areas = ",".join(str(item) for item in period.get("partitionIds", [])) or "all"
            print(f"  {period['start']}-{period['end']} {period['mode']} areas={areas}")
    return 0


def cmd_add_period(args: argparse.Namespace) -> int:
    draft = load_draft(args.schedule)
    add_period(
        draft,
        args.day,
        args.start,
        args.end,
        parse_area_ids(args.areas),
        args.mode,
        args.replace_day,
    )
    errors = validate_draft(draft)
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1
    write_draft(args.output or args.schedule, draft)
    return 0


def cmd_payload(args: argparse.Namespace) -> int:
    draft = load_draft(args.schedule)
    errors = validate_draft(draft)
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1
    print(json.dumps(make_plan_list(draft), ensure_ascii=False, indent=2))
    return 0


def cmd_optimize(args: argparse.Namespace) -> int:
    draft = load_draft(args.schedule)
    errors = validate_draft(draft)
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1

    try:
        context = load_optimization_context(args.db)
        stale_messages = stale_base_messages(draft, context)
        blockers = optimizer_blockers(context)
        if stale_messages and args.stale_policy == "fail":
            for message in stale_messages:
                print(f"stale draft: {message}", file=sys.stderr)
            return 1
        if blockers and not args.ignore_blockers:
            for blocker in blockers:
                print(f"optimizer blocked: {blocker}", file=sys.stderr)
            return 1
        plan = build_optimization_plan(
            context,
            day_id=args.day,
            preferred_window=args.preferred_window,
            m2_per_hour=args.m2_per_hour,
            min_days_between=args.min_days_between,
            max_periods_per_day=args.max_periods_per_day,
        )
        optimized = apply_optimization_plan(
            draft,
            plan,
            warnings=stale_messages,
            blockers=blockers if args.ignore_blockers else [],
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    errors = validate_draft(optimized)
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1

    if args.output:
        write_draft(args.output, optimized)
        print_optimization_summary(optimized["optimization"], output=args.output, explain=args.explain)
    else:
        print(json.dumps(optimized, ensure_ascii=False, indent=2))
    return 0


def cmd_optimize_weekly(args: argparse.Namespace) -> int:
    draft = load_draft(args.schedule)
    errors = validate_draft(draft)
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1

    try:
        context = load_optimization_context(args.db)
        stale_messages = stale_base_messages(draft, context)
        blockers = optimizer_blockers(context)
        if stale_messages and args.stale_policy == "fail":
            for message in stale_messages:
                print(f"stale draft: {message}", file=sys.stderr)
            return 1
        if blockers and not args.ignore_blockers:
            for blocker in blockers:
                print(f"optimizer blocked: {blocker}", file=sys.stderr)
            return 1
        plan = build_weekly_optimization_plan(
            context,
            max_weekly_hours=args.max_weekly_hours,
            target_weekly_hours=args.target_weekly_hours,
            night_only_selectors=args.night_only_area,
            night_window=args.night_window,
            day_window=args.day_window,
            min_visits_per_week=args.min_visits_per_week,
            priority_visits_per_week=args.priority_visits_per_week,
        )
        optimized = apply_weekly_optimization_plan(
            draft,
            plan,
            warnings=stale_messages,
            blockers=blockers if args.ignore_blockers else [],
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    errors = validate_draft(optimized)
    if not errors:
        night_ids = {int(item["id"]) for item in (optimized.get("optimization", {}).get("plan", {}).get("nightOnlyAreas") or [])}
        all_ids = {int(item["areaId"]) for item in (optimized.get("optimization", {}).get("plan", {}).get("areaTargets") or [])}
        errors = weekly_constraint_errors(
            optimized,
            max_weekly_hours=args.max_weekly_hours,
            night_only_ids=night_ids,
            night_segments=window_segments(args.night_window),
            all_area_ids=all_ids,
        )
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1

    if args.output:
        write_draft(args.output, optimized)
        print_weekly_optimization_summary(optimized["optimization"], output=args.output, explain=args.explain)
    else:
        print(json.dumps(optimized, ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    export = subparsers.add_parser("export", help="Export the latest SQLite schedule as a draft")
    export.add_argument("--db", type=Path, default=Path("data/navimow.sqlite"))
    export.add_argument("--output", type=Path, default=Path("viewer/navimow-map/schedule-draft.json"))
    export.set_defaults(func=cmd_export)

    validate = subparsers.add_parser("validate", help="Validate a schedule draft")
    validate.add_argument("--schedule", type=Path, required=True)
    validate.set_defaults(func=cmd_validate)

    list_cmd = subparsers.add_parser("list", help="List a schedule draft")
    list_cmd.add_argument("--schedule", type=Path, required=True)
    list_cmd.set_defaults(func=cmd_list)

    add = subparsers.add_parser("add-period", help="Add or replace a day period")
    add.add_argument("--schedule", type=Path, required=True)
    add.add_argument("--output", type=Path)
    add.add_argument("--day", type=day_number, required=True)
    add.add_argument("--start", required=True)
    add.add_argument("--end", required=True)
    add.add_argument("--mode", choices=["custom", "all_zones"], default="custom")
    add.add_argument("--areas", default="", help="Comma-separated area IDs for custom mode")
    add.add_argument("--replace-day", action="store_true")
    add.set_defaults(func=cmd_add_period)

    payload = subparsers.add_parser("payload", help="Print app-shaped planList payload")
    payload.add_argument("--schedule", type=Path, required=True)
    payload.set_defaults(func=cmd_payload)

    optimize = subparsers.add_parser("optimize", help="Create a dry-run optimized schedule draft for one day")
    optimize.add_argument("--db", type=Path, default=Path("data/navimow.sqlite"))
    optimize.add_argument("--schedule", type=Path, required=True)
    optimize.add_argument("--output", type=Path)
    optimize.add_argument("--day", type=day_number, required=True)
    optimize.add_argument("--preferred-window", default="04:00-22:00")
    optimize.add_argument("--m2-per-hour", type=float, default=250.0)
    optimize.add_argument("--min-days-between", type=int, default=2)
    optimize.add_argument("--max-periods-per-day", type=int, default=4)
    optimize.add_argument("--stale-policy", choices=["fail", "warn"], default="fail")
    optimize.add_argument("--ignore-blockers", action="store_true", help="Proceed despite weather/status blockers; metadata records what was ignored")
    optimize.add_argument("--explain", action="store_true", help="Print optimization metadata after writing --output")
    optimize.set_defaults(func=cmd_optimize)

    weekly = subparsers.add_parser("optimize-weekly", help="Create a dry-run optimized weekly draft with custom-zone rotation")
    weekly.add_argument("--db", type=Path, default=Path("data/navimow.sqlite"))
    weekly.add_argument("--schedule", type=Path, required=True)
    weekly.add_argument("--output", type=Path)
    weekly.add_argument("--max-weekly-hours", type=float, default=80.0)
    weekly.add_argument("--target-weekly-hours", type=float, help="Optional lower target; capped by --max-weekly-hours")
    weekly.add_argument("--night-only-area", action="append", default=[], help="Area name or id that must only appear in --night-window; repeat or comma-separate")
    weekly.add_argument("--night-window", default=DEFAULT_WEEKLY_NIGHT_WINDOW)
    weekly.add_argument("--day-window", default=DEFAULT_WEEKLY_DAY_WINDOW)
    weekly.add_argument("--min-visits-per-week", type=int, default=2)
    weekly.add_argument("--priority-visits-per-week", type=int, default=3)
    weekly.add_argument("--stale-policy", choices=["fail", "warn"], default="fail")
    weekly.add_argument("--ignore-blockers", action="store_true", help="Proceed despite weather/status blockers; metadata records what was ignored")
    weekly.add_argument("--explain", action="store_true", help="Print weekly optimization metadata after writing --output")
    weekly.set_defaults(func=cmd_optimize_weekly)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
