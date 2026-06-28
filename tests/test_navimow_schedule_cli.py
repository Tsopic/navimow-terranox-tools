from pathlib import Path

import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import navimow_schedule_cli as cli


def sample_draft():
    return {
        "version": 1,
        "baseSnapshotId": 1,
        "baseObservedAt": "2026-06-27T12:00:00+00:00",
        "days": [
            {"day": i, "dayName": cli.DAY_NAMES[i], "open": 0, "periods": []}
            for i in range(1, 8)
        ],
    }


def optimizer_context(**overrides):
    context = {
        "latestScheduleSnapshotId": 1,
        "latestScheduleObservedAt": "2026-06-27T12:00:00+00:00",
        "areas": [
            {
                "id": 1,
                "name": "Front",
                "sizeM2": 120.0,
                "cutting": {"effectiveHeightMm": 60},
                "lastMow": {"status": "completed", "lastAt": "2026-06-20T12:00:00+00:00", "partitionPercentage": 100},
            },
            {
                "id": 2,
                "name": "Back",
                "sizeM2": 80.0,
                "cutting": {"effectiveHeightMm": 50},
                "lastMow": {"status": "partial", "lastAt": "2026-06-26T12:00:00+00:00", "partitionPercentage": 45},
            },
            {
                "id": 3,
                "name": "Orchard",
                "sizeM2": 60.0,
                "cutting": {"effectiveHeightMm": 50},
                "lastMow": {"status": "no_mow_in_history", "lastAt": None, "partitionPercentage": 0},
            },
        ],
        "mower": {"routeInsights": {}},
    }
    context.update(overrides)
    return context


def weekly_optimizer_context():
    return optimizer_context(
        areas=[
            {
                "id": 1,
                "name": "Front",
                "sizeM2": 180.0,
                "cutting": {"effectiveHeightMm": 60},
                "lastMow": {"status": "completed", "lastAt": "2026-06-20T12:00:00+00:00", "partitionPercentage": 100},
            },
            {
                "id": 2,
                "name": "Back",
                "sizeM2": 160.0,
                "cutting": {"effectiveHeightMm": 50},
                "lastMow": {"status": "partial", "lastAt": "2026-06-26T12:00:00+00:00", "partitionPercentage": 45},
            },
            {
                "id": 3,
                "name": "Meadow",
                "sizeM2": 360.0,
                "cutting": {"effectiveHeightMm": 70},
                "lastMow": {"status": "no_mow_in_history", "lastAt": None, "partitionPercentage": 0},
            },
            {
                "id": 4,
                "name": "Orchard",
                "sizeM2": 280.0,
                "cutting": {"effectiveHeightMm": 60},
                "lastMow": {"status": "completed", "lastAt": "2026-06-24T12:00:00+00:00", "partitionPercentage": 100},
            },
            {
                "id": 5,
                "name": "Road edge",
                "sizeM2": 210.0,
                "cutting": {"effectiveHeightMm": 60},
                "lastMow": {"status": "completed", "lastAt": "2026-06-23T12:00:00+00:00", "partitionPercentage": 100},
            },
            {
                "id": 7,
                "name": "Autoplats",
                "sizeM2": 220.0,
                "cutting": {"effectiveHeightMm": 70},
                "lastMow": {"status": "completed", "lastAt": "2026-06-27T12:00:00+00:00", "partitionPercentage": 100},
            },
        ]
    )


def test_time_ticks_round_trip():
    assert cli.time_to_tick("00:00") == 0
    assert cli.time_to_tick("04:00") == 16
    assert cli.time_to_tick("22:00") == 88
    assert cli.time_to_tick("24:00") == 96
    assert cli.tick_to_time(96) == "24:00"


def test_add_period_validate_and_payload():
    draft = sample_draft()
    cli.add_period(draft, 3, "04:00", "22:00", [1, 3, 5], "custom", replace_day=True)

    assert cli.validate_draft(draft) == []
    payload = cli.make_plan_list(draft)
    tuesday = payload["planList"][2]
    assert tuesday == {
        "day": 3,
        "open": 1,
        "period": [
            {
                "start_time": 16,
                "end_time": 88,
                "partition_ids": [1, 3, 5],
            }
        ],
    }


def test_validate_rejects_overlapping_periods():
    draft = sample_draft()
    cli.add_period(draft, 3, "04:00", "10:00", [1], "custom", replace_day=False)
    cli.add_period(draft, 3, "09:00", "12:00", [3], "custom", replace_day=False)

    errors = cli.validate_draft(draft)

    assert any("overlaps" in error for error in errors)


def test_validate_rejects_more_than_four_periods_per_day():
    draft = sample_draft()
    for index in range(5):
        start = f"{index:02d}:00"
        end = f"{index:02d}:15"
        cli.add_period(draft, 3, start, end, [index + 1], "custom", replace_day=False)

    errors = cli.validate_draft(draft)

    assert any("at most 4 periods" in error for error in errors)


def test_cli_add_period_writes_output(tmp_path):
    schedule = tmp_path / "schedule.json"
    output = tmp_path / "out.json"
    cli.write_draft(schedule, sample_draft())

    code = cli.main(
        [
            "add-period",
            "--schedule",
            str(schedule),
            "--output",
            str(output),
            "--day",
            "Tuesday",
            "--start",
            "04:00",
            "--end",
            "22:00",
            "--areas",
            "1,3,5",
            "--replace-day",
        ]
    )

    assert code == 0
    draft = cli.load_draft(output)
    assert draft["days"][2]["periods"][0]["partitionIds"] == [1, 3, 5]


def test_optimizer_prioritizes_no_history_and_partial_then_builds_draft():
    draft = sample_draft()
    context = optimizer_context()

    plan = cli.build_optimization_plan(
        context,
        day_id=3,
        preferred_window="04:00-08:00",
        m2_per_hour=240,
        min_days_between=0,
        max_periods_per_day=3,
    )
    optimized = cli.apply_optimization_plan(draft, plan, warnings=[], blockers=[])

    selected = [period["partitionIds"][0] for period in optimized["days"][2]["periods"]]
    assert selected[:2] == [3, 2]
    assert optimized["optimization"]["dryRunOnly"] is True
    assert cli.validate_draft(optimized) == []


def test_optimizer_rejects_more_than_four_periods_per_day():
    context = optimizer_context()

    try:
        cli.build_optimization_plan(
            context,
            day_id=3,
            preferred_window="04:00-08:00",
            m2_per_hour=240,
            min_days_between=0,
            max_periods_per_day=5,
        )
    except ValueError as exc:
        message = str(exc)
    else:
        message = ""

    assert "between 1 and 4" in message


def test_optimizer_stale_base_fails_by_default(tmp_path, monkeypatch, capsys):
    schedule = tmp_path / "schedule.json"
    cli.write_draft(schedule, sample_draft() | {"baseSnapshotId": 99})
    monkeypatch.setattr(cli, "load_optimization_context", lambda db: optimizer_context())

    code = cli.main(["optimize", "--db", str(tmp_path / "db.sqlite"), "--schedule", str(schedule), "--day", "Tuesday"])

    assert code == 1
    assert "stale draft" in capsys.readouterr().err


def test_optimizer_stale_base_can_warn_and_write_output(tmp_path, monkeypatch):
    schedule = tmp_path / "schedule.json"
    output = tmp_path / "optimized.json"
    cli.write_draft(schedule, sample_draft() | {"baseSnapshotId": 99})
    monkeypatch.setattr(cli, "load_optimization_context", lambda db: optimizer_context())

    code = cli.main(
        [
            "optimize",
            "--db",
            str(tmp_path / "db.sqlite"),
            "--schedule",
            str(schedule),
            "--output",
            str(output),
            "--day",
            "Tuesday",
            "--stale-policy",
            "warn",
        ]
    )

    assert code == 0
    optimized = cli.load_draft(output)
    assert optimized["optimization"]["warnings"]


def test_optimizer_blocks_on_weather_or_active_status_unless_ignored(tmp_path, monkeypatch, capsys):
    schedule = tmp_path / "schedule.json"
    output = tmp_path / "optimized.json"
    cli.write_draft(schedule, sample_draft())
    blocked_context = optimizer_context(
        mower={
            "routeInsights": {
                "weather": {"flags": {"rainState": 1}},
                "openapiStatus": {"vehicleState": "isRunning"},
            }
        }
    )
    monkeypatch.setattr(cli, "load_optimization_context", lambda db: blocked_context)

    code = cli.main(["optimize", "--db", str(tmp_path / "db.sqlite"), "--schedule", str(schedule), "--day", "Tuesday"])
    assert code == 1
    assert "optimizer blocked" in capsys.readouterr().err

    code = cli.main(
        [
            "optimize",
            "--db",
            str(tmp_path / "db.sqlite"),
            "--schedule",
            str(schedule),
            "--output",
            str(output),
            "--day",
            "Tuesday",
            "--ignore-blockers",
        ]
    )
    assert code == 0
    assert cli.load_draft(output)["optimization"]["blockersIgnored"]


def test_optimizer_metadata_is_ignored_by_plan_payload():
    draft = sample_draft()
    context = optimizer_context()
    plan = cli.build_optimization_plan(
        context,
        day_id=3,
        preferred_window="04:00-08:00",
        m2_per_hour=240,
        min_days_between=0,
        max_periods_per_day=2,
    )
    optimized = cli.apply_optimization_plan(draft, plan, warnings=["warning"], blockers=["blocker"])

    payload = cli.make_plan_list(optimized)

    assert "optimization" not in payload
    assert len(payload["planList"][2]["period"]) == len(optimized["days"][2]["periods"])


def test_weekly_optimizer_caps_hours_and_rotates_custom_areas():
    draft = sample_draft()
    context = weekly_optimizer_context()

    plan = cli.build_weekly_optimization_plan(
        context,
        max_weekly_hours=80,
        target_weekly_hours=None,
        night_only_selectors=["autoplats"],
        night_window="22:00-06:00",
        day_window="06:00-22:00",
        min_visits_per_week=2,
        priority_visits_per_week=3,
    )
    optimized = cli.apply_weekly_optimization_plan(draft, plan, warnings=[], blockers=[])

    assert plan["scheduledWeeklyHours"] <= 80
    assert cli.validate_draft(optimized) == []
    assert all(period["mode"] == "custom" for day in optimized["days"] for period in day["periods"])
    all_ids = {area["id"] for area in context["areas"]}
    for day in optimized["days"]:
        day_ids = {area_id for period in day["periods"] for area_id in period["partitionIds"]}
        assert day_ids != all_ids


def test_weekly_optimizer_keeps_autoplats_inside_night_window():
    context = weekly_optimizer_context()
    plan = cli.build_weekly_optimization_plan(
        context,
        max_weekly_hours=80,
        target_weekly_hours=None,
        night_only_selectors=["Autoplats"],
        night_window="22:00-06:00",
        day_window="06:00-22:00",
        min_visits_per_week=2,
        priority_visits_per_week=3,
    )

    night_segments = cli.window_segments("22:00-06:00")
    autoplats_periods = [
        period
        for day in plan["dayPlans"]
        for period in day["periods"]
        if 7 in period["partitionIds"]
    ]

    assert autoplats_periods
    assert all(cli.period_within_segments(period, night_segments) for period in autoplats_periods)


def test_weekly_optimizer_unknown_night_area_fails():
    try:
        cli.build_weekly_optimization_plan(
            weekly_optimizer_context(),
            max_weekly_hours=80,
            target_weekly_hours=None,
            night_only_selectors=["missing area"],
            night_window="22:00-06:00",
            day_window="06:00-22:00",
            min_visits_per_week=2,
            priority_visits_per_week=3,
        )
    except ValueError as exc:
        message = str(exc)
    else:
        message = ""

    assert "did not match" in message


def test_weekly_optimizer_cli_writes_output(tmp_path, monkeypatch):
    schedule = tmp_path / "schedule.json"
    output = tmp_path / "weekly.json"
    cli.write_draft(schedule, sample_draft())
    monkeypatch.setattr(cli, "load_optimization_context", lambda db: weekly_optimizer_context())

    code = cli.main(
        [
            "optimize-weekly",
            "--db",
            str(tmp_path / "db.sqlite"),
            "--schedule",
            str(schedule),
            "--output",
            str(output),
            "--max-weekly-hours",
            "12",
            "--target-weekly-hours",
            "12",
            "--night-only-area",
            "autoplats",
            "--stale-policy",
            "warn",
        ]
    )

    assert code == 0
    optimized = cli.load_draft(output)
    assert optimized["optimization"]["plan"]["scheduledWeeklyHours"] <= 12
