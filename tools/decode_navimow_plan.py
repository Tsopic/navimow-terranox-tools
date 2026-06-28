#!/usr/bin/env python3
"""Decode Navimow compact mowing schedule hex strings."""

from __future__ import annotations

import argparse


def fmt_tick(tick: int) -> str:
    if tick == 96:
        return "24:00"
    minutes = tick * 15
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def decode(plan_hex: str) -> list[dict[str, object]]:
    data = bytes.fromhex(plan_hex.strip())
    if not data:
        return []

    offset = 0
    day_count = data[offset]
    offset += 1
    days: list[dict[str, object]] = []

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
            start = data[offset]
            end = data[offset + 1]
            offset += 2
            periods.append(
                {
                    "start_tick": start,
                    "end_tick": end,
                    "start": fmt_tick(start),
                    "end": fmt_tick(end),
                }
            )

        days.append({"day": day, "open": open_flag, "periods": periods})

    if offset != len(data):
        raise ValueError(f"unused trailing bytes: {data[offset:].hex()}")

    return days


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("plan_hex", help="compact Navimow plan hex string")
    args = parser.parse_args()

    for day in decode(args.plan_hex):
        periods = day["periods"]
        pretty_periods = ", ".join(
            f"{p['start_tick']}-{p['end_tick']} ({p['start']}-{p['end']})"
            for p in periods
        )
        print(f"day={day['day']} open={day['open']} slots={len(periods)}: {pretty_periods}")


if __name__ == "__main__":
    main()
