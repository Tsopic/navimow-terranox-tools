# Navimow Schedule Optimization Plan

This plan keeps schedule changes local and dry-run only. The CLI writes
`viewer/navimow-map/schedule-optimized.json` and can print the app-shaped
`planList` payload, but it does not send commands to the mower.

## Constraints

- Fit the weekly schedule into `80` hours or less.
- Use customized zones instead of all-zone periods so each area can be rotated.
- Do not schedule every known area on the same day.
- Keep `Autoplats` inside the night window only.
- Default night window is `22:00-06:00`.
- Default daytime window is `06:00-22:00`.
- Keep one schedule period per daytime rotation bucket and one period per
  night-only bucket so the mower stays under the four-periods-per-day model.

## Optimizer Method

1. Load the latest local schedule draft and local area metadata from SQLite.
2. Score each area using area size, last mow status, completion percentage, and
   effective cutting height.
3. Assign a default weekly visit target:
   - normal completed areas get two visits per week,
   - large, partial, or missing-history areas get three visits per week.
4. Resolve night-only areas by id or case-insensitive area name.
5. Spread visits across the seven days by current bucket load, avoiding duplicate
   visits for the same area on the same day.
6. Allocate the requested weekly time budget across the buckets by weighted area
   demand.
7. Validate the generated draft:
   - weekly duration is at or below the cap,
   - every generated period is custom-zone mode,
   - night-only areas appear only inside the night window,
   - no generated period selects every known area.

## Local Run

```bash
make schedule-export
make schedule-optimize-weekly
make schedule-validate
make schedule-payload
```

Equivalent direct command:

```bash
python tools/navimow_schedule_cli.py optimize-weekly \
  --db data/navimow.sqlite \
  --schedule viewer/navimow-map/schedule-draft.json \
  --output viewer/navimow-map/schedule-optimized.json \
  --max-weekly-hours 80 \
  --night-only-area Autoplats \
  --night-window 22:00-06:00 \
  --day-window 06:00-22:00 \
  --stale-policy warn \
  --explain
```

The current local dry run schedules exactly `80.00` hours. `Autoplats` is placed
only in night slots, and all daytime periods are customized zone rotations.

## Current Draft Shape

- Sunday: Autoplats at night, then a daytime rotation.
- Monday through Wednesday: daytime rotations only.
- Thursday: Autoplats at night, then a daytime rotation.
- Friday and Saturday: daytime rotations only.

This is intentionally a starting point. After more trail-history and compressed
path replay evidence is available, tune visit counts and weights from actual
finish times instead of the current size/status heuristic.
