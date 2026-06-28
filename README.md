# Navimow Terranox Local Console

Local-first tooling for Navimow Terranox mower owners and researchers:
consumer-app data ingest, normalized SQLite state, a browser map/operations
console, schedule draft tooling, and a read-only live-sync path.

This repo deliberately treats schedule/settings writes as dry-run only. Direct mower writes stay disabled until the app command signing/encryption and rollback behavior are fully mapped.

## Disclaimer

This is an unofficial community project. It is not affiliated with, endorsed by,
or supported by Navimow, Segway, Ninebot, or any dealer network. Use it at your
own risk and keep physical safety first around mower automation. The tooling is
designed to be local-first and conservative: public source code is safe to
share, but captures, tokens, generated viewers, map imagery, GPS, device IDs,
and account data must stay private.

See [DISCLAIMER.md](DISCLAIMER.md), [SECURITY.md](SECURITY.md), and
[docs/privacy-and-data-handling.md](docs/privacy-and-data-handling.md) before
publishing screenshots, logs, captures, or generated HTML.

Command reference: [docs/tool-usage.md](docs/tool-usage.md).
Public release checklist: [docs/public-release-checklist.md](docs/public-release-checklist.md).

## Quick Start

For the shortest read-only live-status path, see [QUICKSTART.md](QUICKSTART.md).
If OAuth is already configured locally and the map viewer has been built from
local captures before, this is the fast path:

```bash
make setup
make openapi-preflight
make live-setup-report
make completion-report
make completion-report-strict
make quickstart-live
make live-route-catalog
make live-route-coverage
make live-console
```

Open the URL printed by `make live-console`. It prefers
[http://127.0.0.1:8765/](http://127.0.0.1:8765/) and automatically chooses the
next free localhost port when 8765 is already busy. This supervised command
starts the live-aware viewer server, the status polling loop, and MQTT listening
with realtime defaults. If the MQTT listener exits, the polling loop continues
to refresh the UI. MQTT and polling update the same sanitized live-status file,
and the browser consumes full sanitized SSE payloads for in-place updates with
the live-status HTTP endpoint as fallback.

`make live-route-catalog` prints the code-owned read route allowlist and refused
write/command patterns without reading local config, DB, network, tokens, MQTT
credentials, device IDs, signed URLs, or payloads.

`make live-route-coverage` reads the local SQLite store and reports which read
routes are typed, snapshot-only, viewer-backed, present, or promotion
candidates. It exports metadata only, not route payloads.

`make live-setup-report` prints a broader redacted handoff summary without
network sync: OpenAPI preflight, OAuth readiness, route freshness, viewer
live-status state, route catalog counts, route storage coverage, next commands,
and the remaining mapping gaps. Its `Readiness Summary` calls out whether the
console can be opened now, whether strict live data is fresh, whether
OAuth/OpenAPI refresh is recommended, and whether MQTT/trail replay evidence is
still gated.

`make completion-report` is the broader goal audit. It uses the same redacted
local evidence, but keeps the overall status incomplete until the repo has real
MQTT samples, consumer-session route sync, full local route coverage, trail
replay inputs, a tracked source baseline, and a trusted schedule/settings write-envelope story. Use
`make completion-report-strict` when you want the command to return non-zero
until those blockers are gone.

Install Python dependencies:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
```

Install the system `zstd` CLI if it is missing. On macOS:

```bash
brew install zstd
```

Restore or collect private app captures into `captures/`, then build the local state and viewer:

```bash
python tools/navimow_state_store.py --db data/navimow.sqlite ingest captures --download-maps
python tools/navimow_state_store.py --db data/navimow.sqlite summary
python tools/build_navimow_map_viewer.py --db data/navimow.sqlite --output viewer/navimow-map
python tools/navimow_viewer_server.py --directory viewer/navimow-map --auto-port
```

Open the printed URL.

The same flow is available as Make targets:

```bash
make setup
make ingest
make viewer
make serve-live
```

## What Works

- Real map/area rendering from decoded map detail.
- Terrain and local satellite background toggle.
- Area names, sizes, settings, schedules, cutting height, and last mow status.
- Mower panel with battery, health, network, current cutting height, live-pose source, supported height list, and sanitized OpenAPI/MQTT/weather/today-plan route insights when synced.
- Local schedule planner and CLI that generate app-shaped dry-run `planList` payloads.
- Read-only live sync for core mower state plus compact snapshots for mower list, richer state, today plan, weather, firmware, maintenance, map-support routes, and optional Navimow OAuth/OpenAPI status routes.
- Local viewer server with metadata-only reload for layout changes and in-place
  mower/status updates from full sanitized SSE payloads backed by
  `navimow-live-status.json`.

## Capability Matrix

| Mode | Best for | Not enough for |
|---|---|---|
| OAuth/OpenAPI | Login, mower discovery, status cards, battery/capacity, MQTT metadata, local live-status feed | Map detail, terrain artifacts, consumer settings, trail history, weekly schedule CRUD |
| OpenAPI + MQTT | Event/status samples when the broker emits them, Mower panel/current-zone updates after real sample mapping | Replacing polling before `mqtt-readiness --strict`, mower marker pose until pose fields are mapped |
| Consumer-session config | Map/settings/trail/weather/today-plan/maintenance read routes, full local viewer refresh | Direct schedule/settings writes until command signing/envelope and rollback are mapped |
| Fleet/dealer API | Official schedule CRUD when org credentials are available | Consumer-account access without a dealer/Fleet invite |

## Live Data Setup

Create a local config template:

```bash
python tools/navimow_live_sync.py init-config --output config/navimow-live-sync.local.json
```

Or start from the tracked example:

```bash
cp config/navimow-live-sync.example.json config/navimow-live-sync.local.json
```

Edit the config locally and provide credentials through environment variables, not committed files. Check the route plan:

```bash
python tools/navimow_live_sync.py auth-discover --path captures
make consumer-session-report
python tools/navimow_android_live_setup.py doctor
export NAVIMOW_AUTHORIZATION='...local app/session value...'
export NAVIMOW_VEHICLE_SN='...local mower id...'
python tools/navimow_live_sync.py plan --config config/navimow-live-sync.local.json
python tools/navimow_live_sync.py doctor --config config/navimow-live-sync.local.json --db data/navimow.sqlite
```

If the cache does not contain reusable request headers, capture the live app
request shape from Android. `doctor` reports adb/frida availability, authorized
device count, package install status, and whether the app process is running
without printing device serials. The default capture is shape-only and safe for
summaries:

```bash
python tools/navimow_android_live_setup.py run --duration 60
```

Parse a saved capture without exposing values:

```bash
python tools/navimow_android_live_setup.py parse --input captures/live-sync-*/redacted-frida.log
```

To create a usable local config from live app headers/bodies, run the sensitive
local-only mode. This writes ignored files under `captures/` and
`config/navimow-live-sync.local.json`; do not share the output:

```bash
python tools/navimow_android_live_setup.py run \
  --duration 60 \
  --include-values \
  --i-understand-local-secrets \
  --write-config config/navimow-live-sync.local.json
```

If you already have a value-bearing local capture log, parsing it with
`--include-values` requires the same `--i-understand-local-secrets` flag.
Value-bearing config outputs should end with `.local.json`.

Use `make consumer-session-report` any time map/settings/trail sync is blocked.
It is a redacted readiness report for the consumer-app path: auth header
presence, env var readiness, capture hints, consumer route coverage, and the
next local Android/session commands without printing header values, tokens,
cookies, device IDs, signed URLs, GPS, or raw payloads.

### Optional OAuth/OpenAPI

Public Navimow integrations expose OAuth and OpenAPI status/MQTT routes. This
is useful for login, device discovery, mower status, and MQTT credentials; it
does not expose weekly schedule CRUD.

Create the ignored local OAuth/OpenAPI config:

```bash
python tools/navimow_live_sync.py init-openapi-config --output config/navimow-live-sync.local.json
```

Print the login URL:

```bash
python tools/navimow_live_sync.py oauth-login-url
```

Open it, sign in, then copy the failed localhost redirect URL containing
`?code=...`. Exchange it into the ignored local token file:

```bash
make oauth-exchange-code OAUTH_CODE='http://localhost:1/callback?code=PASTE_CODE_HERE'
```

The local config created above already sets `auth.provider` to `navimow-oauth`.
The live sync client will inject `Authorization: Bearer ...` and refresh the
token when needed without printing token values. Check readiness:

```bash
python tools/navimow_live_sync.py oauth-doctor --config config/navimow-live-sync.local.json
```

Run the first read-only discovery pass:

```bash
python tools/navimow_live_sync.py sync-once \
  --config config/navimow-live-sync.local.json \
  --db data/navimow.sqlite \
  --routes openapi-auth-list,openapi-mqtt-info
```

Populate the OpenAPI status request body from the sanitized auth-list snapshot
without printing device IDs, then sync status and MQTT metadata:

```bash
python tools/navimow_live_sync.py configure-openapi-status \
  --config config/navimow-live-sync.local.json \
  --db data/navimow.sqlite

python tools/navimow_live_sync.py sync-once \
  --config config/navimow-live-sync.local.json \
  --db data/navimow.sqlite \
  --routes openapi-vehicle-status,openapi-mqtt-info
```

At this point OAuth/OpenAPI status is synced. OAuth does not create map detail
or terrain resources. Build a status-only local console immediately, or build
the full map viewer after local map captures have populated the state DB:

```bash
python tools/build_navimow_map_viewer.py --db data/navimow.sqlite --output viewer/navimow-map --status-only
make ingest
make viewer
```

The Makefile wraps the same first-sync sequence after the token exists:

```bash
make oauth-doctor
make quickstart-live
```

For full map/terrain upkeep, inspect local version state before fetching any
new map artifact:

```bash
make live-map-plan
```

The plan compares the latest mower map version, decoded map-detail snapshot,
map artifact metadata, and downloaded local terrain bundle. It prints only
redacted versions/timestamps and the next local commands, such as
`make live-map-artifacts` followed by `make viewer` when a terrain bundle is
missing or stale.

To perform the same safe subset automatically, run:

```bash
make live-map-delta
```

This refreshes `index2`, runs only the needed `map-list`, `map-detail`, and
`get-iot-file` routes, downloads any newly required terrain bundle with signed
URLs retained only for the download window, then rebuilds the viewer.
Those are consumer-app routes. With an OAuth/OpenAPI-only config, live map-delta
now stops before network access and prints the local Android/session capture
commands; use `--responses-dir` fixtures for local tests or capture an ignored
consumer-session config for live map refresh.

After viewer data exists, use the auto refresh mode for the normal open-console
loop. It refreshes SQLite plus `navimow-live-status.json` for status, settings,
capability, trail-time, and MQTT/OpenAPI changes, and rebuilds the viewer only
when structural map/detail, downloaded map artifact, or future schedule counts
change:

```bash
python tools/navimow_live_sync.py poll \
  --config config/navimow-live-sync.local.json \
  --db data/navimow.sqlite \
  --interval 10 \
  --max-iterations 12 \
  --use-route-cadence \
  --activity-aware-cadence \
  --refresh-trails-on-completion \
  --auto-viewer-refresh \
  --viewer-output viewer/navimow-map
```

For a long-running local console, use:

```bash
make live-console
```

Use `make live-console-no-mqtt` for the same supervised console with polling
only.

To prove the MQTT-to-browser update path without waiting for a real mower event,
run a deterministic local replay while the console is open:

```bash
make mqtt-replay-smoke
```

It injects one synthetic sanitized MQTT event through the same sanitizer and
typed status promotion path as `mqtt-listen`, refreshes
`navimow-live-status.json`, and lets the browser consume it through the normal
full-SSE live-status path. It auto-picks an existing area from the generated
viewer; override with `make mqtt-replay-smoke REPLAY_AREA_ID=3` when needed. It
does not print or export topic names, raw payloads, tokens, signed URLs, or GPS.
Clean synthetic replay rows with `make mqtt-replay-clear`.

The Make polling targets use activity-aware cadence. They classify only
sanitized local status values from MQTT/OpenAPI/consumer snapshots, polling
pose/state routes faster while the mower looks active and slower when it looks
idle. Effective cadence is bounded by the poll `--interval`; the Make targets
use `--interval 5`. Unknown or stale activity state falls back to each route's
fixed cadence. The default Make polling targets also use
`--refresh-trails-on-completion`, which forces already-selected `trail-time` and
`trail-data` read routes when sanitized MQTT/OpenAPI/consumer state changes from
active to idle, so per-area last-mow data can catch up immediately after a task.

Use `make live-poll-status` when you deliberately want status-only writes, and
`make live-poll-viewer` when structural map or future schedule changes should
rebuild the viewer automatically.

Optional MQTT live listening can reduce high-frequency status polling after
OpenAPI discovery has synced MQTT metadata. The doctor prints only
present/missing fields and topic counts:

```bash
make mqtt-doctor
make mqtt-listen
make mqtt-sample-report
make mqtt-ui-report
```

`make live-console` starts MQTT with realtime defaults. The standalone
`make mqtt-listen` target defaults to a bounded smoke run (`MAX_MESSAGES=1`,
`DURATION=60`). For longer standalone sessions, pass explicit values, for
example `make mqtt-listen MAX_MESSAGES=500 DURATION=600`. The listener stores
sanitized message summaries, classifies payloads into safe categories such as
state, progress, battery, event, and command-result, promotes allowlisted
status-like fields into typed status rows for the Mower panel, and updates the
same `navimow-live-status.json` feed through `tools/navimow_live_status.py`.
OpenAPI auth/status is also promoted into safe typed rows, so the viewer can
show OpenAPI device count, mower state, and capacity without raw device IDs.
That feed also carries a small per-area `areaStatus` delta so the browser can
refresh current-zone badges, MQTT progress, last-mow summaries, and effective
cutting-height text without rewriting the map bundle. The browser replaces live
MQTT subtrees from each new feed so stale progress/state does not linger when a
newer snapshot omits typed MQTT fields. It does not print or export MQTT
usernames, passwords, client IDs, topic names, broker URLs, raw payloads,
payload hashes, device IDs, area names, polygons, or exact GPS. Keep the polling
loop as periodic reconciliation and use full viewer rebuilds for map geometry,
layout, schedule structure, area definitions, satellite changes, and mower
marker pose until real MQTT pose fields are mapped.
MQTT metadata parsing, message sanitization/classification, and the paho loop
live in `tools/navimow_mqtt_client.py`; `tools/navimow_live_sync.py` keeps the
SQLite writes and calls the shared live-status refresh helper.

After a listener or replay run, use `make mqtt-sample-report` to summarize the
sanitized typed MQTT samples. It reports state/task/work/event enum counts,
active/idle/unknown classification, field coverage for battery, current area,
progress, path, and report time, and whether the rows are real or synthetic.
This is the safe checklist for deciding which MQTT fields can drive the UI and
which polling routes still need to stay on.

Use `make mqtt-ui-report` for the combined operator check. It validates MQTT
metadata shape, can run a bounded listener when passed
`MQTT_UI_FLAGS="--listen --update-live-status"`, refreshes the sanitized
live-status feed by default, and reports whether the localhost browser feed has
MQTT metadata/status/message summaries. It stays redacted: no broker values,
topics, credentials, payload hashes, raw payloads, device IDs, area geometry, or
GPS are printed.

Use `make mqtt-readiness` as the stricter operational gate. It is based only on
real MQTT samples, not synthetic replay rows, and becomes ready after active and
idle states plus battery, current-area, and progress fields are covered. For
automation, run `python tools/navimow_live_sync.py mqtt-readiness --db
data/navimow.sqlite --strict --json`.

`make mqtt-replay-smoke` is a deterministic local UI smoke test for the same
MQTT-to-live-status path when no live mower event is available. Use
`make mqtt-replay-clear` after the smoke test to remove synthetic rows and
refresh the live-status feed.

For browser-level proof, run:

```bash
make live-ui-smoke
```

This optional smoke launches headless Chromium against the localhost viewer,
applies a temporary sanitized live-status patch, waits until the visible Mower
panel reflects the update through the SSE live-status path, and restores the original status
file. It does not create screenshots, traces, or recordings. Set
`CHROMIUM=/path/to/chromium` if needed.

To serve the viewer with automatic browser refresh support without the
supervisor:

```bash
make serve-live
```

Status-only updates require an existing `viewer/navimow-map/navimow-map-data.js`
from `make viewer`, `make viewer-status-only`, or `make quickstart-live`. They
update the Mower panel, marker, area list/details, and current-zone highlight in
place when map geometry exists. Map/layout changes still trigger a full browser
reload.

See [docs/live-operations-runbook.md](docs/live-operations-runbook.md) for the
two-terminal live console flow, token/session refresh, troubleshooting, and
safe sharing checklist.

Use `make live-health` any time you want a redacted readiness report for config,
OAuth token presence, typed route snapshot freshness, SQLite status, and the
generated viewer live-status feed. Use `make live-health-strict` before relying
on the realtime console; strict mode fails when a selected route, MQTT-backed
status feed, OAuth token, or viewer live-status file is missing, stale, skewed,
or unreadable. Automation can read the same check without secrets:

```bash
python tools/navimow_live_sync.py live-health \
  --config config/navimow-live-sync.local.json \
  --db data/navimow.sqlite \
  --viewer-output viewer/navimow-map \
  --strict \
  --json
```

Use `make live-setup-report` or
`python tools/navimow_live_sync.py setup-report --strict --json` when handing the
repo to another run. That report adds OpenAPI preflight, route-catalog counts,
route storage coverage, a compact readiness summary, next local commands, and
unresolved mapping gaps while staying local-only and redacted. Use
`make completion-report`, `make completion-report-strict`, or
`python tools/navimow_live_sync.py completion-report --strict --json` when you
need the stricter full-goal audit rather than a console-readiness report.

Use `make trail-replay-report` to check local readiness for decoded historical
trail/path replay. It reports only redacted readiness: trail-time coverage,
sanitized trail-data presence, decoded map geometry, and render calibration.
The decoder and point/segment tables are still future work, but this command
shows exactly what local evidence is missing before starting that work.

The sync runner only allows known read routes such as `/vehicle/vehicle/index2`, `/vehicle/vehicle/get-location`, `/vehicle/vehicle/set-list`, `/vehicle/vehicle/get-device-info`, `/vehicle/trail/get-path-info-time`, `/vehicle/trail/get-path-info-data-compress`, `/vehicle/vehicle/auth-list`, `/mowerbot/vehicle/vehicle/state`, `/vehicle/vehicle/get-today-plan`, weather, firmware, maintenance, map-support routes, and OpenAPI auth/status/MQTT reads. `openapi-response-commands` is available only as read-after-command status polling for future explicitly mapped control flows. Command/write routes are refused.

## Schedule CLI

Export the latest schedule draft:

```bash
python tools/navimow_schedule_cli.py export --db data/navimow.sqlite --output viewer/navimow-map/schedule-draft.json
```

Edit locally:

```bash
python tools/navimow_schedule_cli.py add-period \
  --schedule viewer/navimow-map/schedule-draft.json \
  --day Tuesday \
  --start 04:00 \
  --end 22:00 \
  --areas 1,3,5 \
  --replace-day
```

Validate and print the dry-run payload:

```bash
python tools/navimow_schedule_cli.py validate --schedule viewer/navimow-map/schedule-draft.json
python tools/navimow_schedule_cli.py payload --schedule viewer/navimow-map/schedule-draft.json
```

Create a dry-run optimized draft for one day using area size, last mow status,
completion, cutting height, weather flags, and mower status:

```bash
python tools/navimow_schedule_cli.py optimize \
  --db data/navimow.sqlite \
  --schedule viewer/navimow-map/schedule-draft.json \
  --output viewer/navimow-map/schedule-optimized.json \
  --day Tuesday \
  --preferred-window 04:00-22:00 \
  --stale-policy warn \
  --explain
```

If synced weather or mower status looks unsafe or active, the optimizer refuses
to propose changes unless you explicitly add `--ignore-blockers`. That override
is still dry-run only and records the ignored blockers in the draft metadata.

Create a weekly dry-run optimized draft that rotates customized zones, keeps
selected areas in the night window only, and caps the plan at 80 hours:

```bash
python tools/navimow_schedule_cli.py optimize-weekly \
  --db data/navimow.sqlite \
  --schedule viewer/navimow-map/schedule-draft.json \
  --output viewer/navimow-map/schedule-optimized.json \
  --max-weekly-hours 80 \
  --night-only-area 'AREA_NAME_OR_ID' \
  --night-window 22:00-06:00 \
  --day-window 06:00-22:00 \
  --stale-policy warn \
  --explain
```

The weekly optimizer replaces all-zone windows with custom-zone rotations so a
night-only area cannot be included by accident. See
`docs/navimow-schedule-optimization.md` for the current rule set.

The same preview is available through Make:

```bash
make schedule-export
make schedule-optimize-dry-run OPTIMIZE_DAY=Tuesday
make schedule-optimize-weekly NIGHT_ONLY_AREAS='AREA_NAME_OR_ID'
make schedule-validate
make schedule-payload
```

If the target refuses because the mower appears active or weather/state data is
unsafe, keep the refusal as the normal guardrail. For a local preview anyway,
rerun with `OPTIMIZE_FLAGS=--ignore-blockers`; the output metadata records what
was ignored.

The Planner tab in the local viewer exposes the same optimizer as a browser
dry run. Preview ranks areas from the generated local data, and Apply updates
only the in-browser draft/`planList` textareas and area badges. It never sends a
mower command.

## Tests

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -B -m pytest -q -p no:cacheprovider tests
```

## Privacy

Do not commit captures, APKs, decoded app output, SQLite databases, generated viewers, screenshots, tokens, signed URLs, exact GPS coordinates, trace IDs, raw API payloads, or command envelopes. See [docs/privacy-and-data-handling.md](docs/privacy-and-data-handling.md).

## Route And Sync Notes

- [docs/tool-usage.md](docs/tool-usage.md)
- [docs/public-release-checklist.md](docs/public-release-checklist.md)
- [docs/navimow-consumer-routes.md](docs/navimow-consumer-routes.md)
- [docs/navimow-live-sync-plan.md](docs/navimow-live-sync-plan.md)
- [docs/navimow-oauth-openapi.md](docs/navimow-oauth-openapi.md)
- [docs/live-operations-runbook.md](docs/live-operations-runbook.md)
- [docs/implementation-gap-map.md](docs/implementation-gap-map.md)
- [notes/navimow-schedule-cli-plan.md](notes/navimow-schedule-cli-plan.md)
- [QUICKSTART.md](QUICKSTART.md)
