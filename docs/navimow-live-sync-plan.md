# Navimow Terranox Live Sync Plan

This plan extends the local Navimow Terranox portal from captured snapshots into
a cautious live-sync loop. It is based on the existing route catalog, schedule
notes, captured Android logs/cache, map-detail captures, and `data/navimow.sqlite`.

Do not export raw serials, tokens, trace IDs, signed blob URLs, exact GPS
coordinates, raw command payloads, or raw API envelopes. Live sync code should
normalize only the fields the portal needs and keep credentials outside the repo.

## Status Legend

- Implemented locally: already available from captures, SQLite, generated viewer,
  or schedule tooling.
- Read-only direct-server-client work: safe next implementation path using the
  observed consumer routes, without sending commands.
- Unknown/needs capture: requires another app capture or command mapping before
  being trusted.

## Sync Surfaces

| Surface | Routes | Implemented locally | Read-only direct-server-client work and cadence | Unknown/needs capture |
|---|---|---|---|---|
| Battery and live state | `POST /vehicle/vehicle/index2`, `POST /mowerbot/vehicle/vehicle/state`, `POST /vehicle/vehicle/auth-list`, `POST /vehicle/vehicle/get-vehicle-weather`, `POST /vehicle/firmware/get-new-firmware`, `POST /vehicle/vehicle/get-component-maintenance` | `device_state_snapshots` and `device_info_snapshots` are ingested. Compact `route_snapshot_records` cover mower-state, auth-list, weather, firmware, and maintenance. The viewer mower panel shows state code, battery, network, firmware, limits, capabilities, and route snapshot freshness from sanitized data. `tools/navimow_live_sync.py` can run a read-only one-shot sync from local config or response fixtures. `poll --activity-aware-cadence` classifies recent sanitized MQTT/OpenAPI/consumer state snapshots and adjusts selected route cadences, with `notRunning`-style values treated as idle; MQTT-derived cadence is gated by `mqtt-readiness` real-sample coverage. | Implemented fixed cadence defaults include `index2=45s`, `mower-state=15s`, weather=1200s, firmware/maintenance=daily. Activity-aware overrides include faster `index2`, `mower-state`, `get-location`, `today-plan`, and OpenAPI status while active, and slower polling while idle. Effective cadence is bounded by `--interval`, and stale/unknown states fall back to fixed route cadence. Future event-driven policy should refresh immediately after detected setting/map updates. | Confirm current consumer auth/session headers for unattended direct polling; map state enum names and use `mqtt-readiness --strict` before letting MQTT/push replace frequent polling. |
| OAuth/OpenAPI status | `POST /openapi/oauth/getAccessToken`, `GET /openapi/smarthome/authList`, `POST /openapi/smarthome/getVehicleStatus`, `GET /openapi/mqtt/userInfo/get/v2`, `POST /openapi/smarthome/responseCommands` | Optional OAuth token-file flow is implemented. Read aliases exist for OpenAPI auth-list, vehicle status, MQTT info, response command polling, and an experimental MQTT listener. OpenAPI auth/status responses are kept as compact route snapshots and promoted into safe typed `openapi_auth_snapshots` and `openapi_status_snapshots`; token, raw device id, and MQTT values are not printed or exported. MQTT messages are stored as topic/payload hashes plus allowlisted status-like fields, classed into safe coverage buckets, summarized in the viewer/live-status feed without exporting topic or payload hashes, promoted into typed `mqtt_status_snapshots` when possible, merged into a sanitized per-area `areaStatus` delta for current-zone progress, summarized by `mqtt-sample-report` for redacted enum/field coverage, and gated by `mqtt-readiness` before MQTT evidence can drive activity-aware cadence. MQTT parsing, sanitization, and the paho loop now live in `tools/navimow_mqtt_client.py`; storage stays in `tools/navimow_live_sync.py`; browser-feed sanitization and status artifact refresh live in `tools/navimow_live_status.py`. | Use OAuth when consumer-app session headers are unavailable or when OpenAPI status/MQTT is enough for mower display. Configure `auth.provider=navimow-oauth`, exchange the login code once, and let live sync refresh the token. Use `mqtt-doctor` before `mqtt-listen`, run `mqtt-sample-report` after captures, and require `mqtt-readiness --strict` before reducing polling reliance. | OpenAPI routes do not expose weekly schedule CRUD. Live MQTT enum semantics still need real sample mapping before MQTT can replace polling. `sendCommands` remains refused. |
| Live mower pose and progress | `POST /vehicle/vehicle/get-location`, `POST /mowerbot/vehicle/vehicle/state`, `POST /vehicle/trail/get-path-info-time`, `POST /vehicle/trail/get-path-info-data-compress` | Map calibration, local coordinate transforms, sanitized `live_location_snapshots`, trail-time indexes, a viewer mower marker, and status-only live refresh are implemented. Fixed cadence uses `get-location=10s`; activity-aware cadence uses `5s` while active and `60s` while idle for selected polling loops, bounded by the poll interval. `poll --refresh-trails-on-completion` forces already-selected `trail-time`/`trail-data` reads when sanitized activity transitions from active to idle. | Use only local map coordinates or sanitized relative pose in the browser. During active mowing, update provisional area progress every 30-60 seconds if `get-location` exposes enough local pose/progress. After completion, use the forced trail reads to refresh summary indexes immediately, then decode compressed trail routes for stronger attribution. | Current partition/area indicators, status enum names, richer path semantics, and compressed trail decoding. Capture docked, mowing, paused, returning, and completed states. |
| Cutting height and settings | `POST /vehicle/vehicle/get-device-info`, `POST /vehicle/vehicle/set-list`, `POST /vehicle/vehicle/index2`, write lifecycle routes listed below | Device capabilities include cutting-height lists. Area settings are exported to the viewer with keys such as `heightSet`, edge settings, boundary type, direction, and obstacle counts. | Refresh `get-device-info` on startup and daily. Refresh `set-list` on startup, when the Mower/Area panels open, every 5 minutes while active, and every 30 minutes when idle. Re-read `index2` after settings update timestamps change. | Full setting enum mapping, which settings are global vs per-area, and safe write envelopes for cutting height/settings. Writes stay disabled until the command body and signing/encryption behavior are mapped. |
| Map and area sync | `POST /map/index/map-list`, `POST /map/index/map-detail-compress`, `POST /mowerbot/vehicle/common/get-iot-file`, returned blob download | The local store has map detail, 12 named area records, obstacles, a terrain artifact, render metadata, and sanitized viewer output. Live aliases now cover `map-list`, `map-detail`, and `get-iot-file`; `map-detail` decodes through the existing area normalizer, while signed URL-bearing map artifact responses stay compact/sanitized. `map-sync-plan` / `make live-map-plan` compares latest local map version, map-detail freshness, artifact metadata, and downloaded terrain bundle presence without network calls. `sync-once --map-delta` / `make live-map-delta` refreshes `index2`, runs only needed map-support routes from that plan, downloads required artifacts with signed URLs retained only for the download window, and rebuilds the viewer. The viewer can render terrain/satellite backgrounds with local-coordinate overlays and schedule membership. | Use `index2` map/update timestamps as the cheap change detector every 1-2 minutes while open. Call `map-sync-plan` before artifact work to see the next safe commands. Use `make live-map-delta` for explicit map refresh: call `map-list` and `map-detail-compress` only when map-detail is missing/stale, call `get-iot-file` only when artifact metadata/download is missing/stale, download once, then discard the signed URL. | Relationship between sparse partition metadata and full map-detail areas, app flows for area edits, richer area fields, and live validation of route/body selection. Exact GPS anchors remain local-only and must not enter browser data exports. |
| Schedule draft vs live writes | `POST /vehicle/vehicle/set-list`, `POST /vehicle/vehicle/get-today-plan`, `POST /mowerbot/vehicle/set/send`, `POST /vehicle/set/response`, `POST /vehicle/set/save-set-data`, observed alternate `POST /vehicle/set/send` | `workPlanV2`/`planList` schedule snapshots are ingested. The local schedule CLI and viewer planner edit `schedule-draft.json` and produce app-shaped dry-run payloads. The CLI can also produce dry-run optimized one-day drafts from area size, last mow status, completion, cutting height, weather flags, and mower status. `get-today-plan` is available as a compact read-only snapshot. No local tool sends commands to the mower. | Read weekly schedule/settings from `set-list` on startup and every 10-30 minutes while open. Use `get-today-plan` every 1-5 minutes during active schedule windows and immediately after any future write attempt. Keep live writes behind an explicit disabled gate. Use optimizer output as a local draft only. | Exact outgoing command envelope and headers for `set/send`, alternate send route purpose, response status enum, save-set-data coverage, and conflict behavior when the app changes the schedule concurrently. If writes become safe, send only modified days, poll `response` about once per second with a short timeout, save set data, then refresh `set-list`, `index2`, and `get-today-plan`. |
| Per-area last mow time | `POST /vehicle/trail/get-path-info-time`, `POST /vehicle/trail/get-path-info-data-compress`, `POST /vehicle/vehicle/get-today-plan`, `POST /vehicle/vehicle/get-location` | Area polygons, schedule membership, trail-time snapshots, viewer/export last-mow fields, and completion-triggered selected trail refresh exist locally. Current values are derived from `partitionId`, `endTime`, `finishedArea`, and `partitionPercentage`. | Backfill trail time indexes on startup and daily. After a mowing task completes, force selected trail reads immediately with `--refresh-trails-on-completion`; then, once compressed path decoding is implemented, fetch the relevant compressed path data, intersect path samples with area polygons, and update confidence/path details. During active mowing, update provisional area progress every 30-60 seconds if `get-location` exposes enough local pose/progress. | Compressed path format, time index pagination, how to distinguish mowing from transit, and whether the app exposes authoritative per-area completion timestamps elsewhere. |

## Direct Client Shape

1. Credential/session provider: read session material from a local keychain or
   ignored config, never from committed files or browser data.
2. Route client: call only read routes by default, with per-route backoff and a
   global "portal open" vs "idle" cadence.
3. Sanitizer: strip identifiers, signed URLs, trace fields, GPS anchors, raw
   payloads, and command envelopes before data reaches SQLite-derived browser
   exports.
4. Normalizer: write small, typed snapshots for OpenAPI auth/status, MQTT status,
   live state, pose samples, settings, map versions, map areas, schedules, and
   per-area last mow time. Trail segment tables remain future work until
   compressed trail decoding is mapped. Keep raw capture material local-only.
5. Portal feed: the local viewer writes `navimow-live-status.json` and serves a
   sanitized `/__navimow/live-status` endpoint. SSE emits full sanitized
   `live-status` payloads for in-place Mower panel/marker updates and compact
   `viewer-update` metadata for asset or layout changes that require a reload.

## Implementation Order

1. Use the current read-only polling runner for `index2`, `get-device-info`,
   `set-list`, `get-location`, trail time, auth-list, mower-state, today-plan,
   weather, firmware, maintenance, OpenAPI status/MQTT, and map-support routes.
   Promote compact snapshots into typed fields as fresh captures prove response
   shapes.
2. Live-validate `sync-once --map-delta` against fresh map-support responses,
   especially route body shapes for `map-list`, `map-detail`, and
   `get-iot-file`; keep signed URLs transient.
3. Map `mowerbot/vehicle/vehicle/state` and live status enums into the existing
   sanitized local pose/progress model.
4. Decode compressed trail routes enough to render historical paths and improve
   per-area completion confidence beyond the normalized trail-time index.
5. Keep schedule/settings writes as dry-run until a fresh controlled capture
   proves the command lifecycle, request signing/encryption, response statuses,
   save step, and rollback behavior.

## Capture Checklist

- Home/dashboard capture while docked, mowing, paused, returning, and after task
  completion, focused on `index2`, `mowerbot/vehicle/vehicle/state`, and
  `get-location`.
- Small controlled mowing run to correlate `get-location`, trail time indexes,
  compressed trail data, and visible app progress.
- Read-only settings navigation to refresh `set-list` and identify cutting
  height/global/per-area fields.
- Controlled app change for one reversible setting only after approval, followed
  by immediate revert, to map the write lifecycle without publishing raw command
  bodies.
- Map/area edit or area-detail refresh capture if the app exposes area changes,
  to reconcile map-detail areas with partition metadata.

## Current Local Commands

```bash
python tools/navimow_live_sync.py auth-discover --path captures
python tools/navimow_live_sync.py consumer-session-report --config config/navimow-live-sync.local.json --db data/navimow.sqlite --capture-path captures
python tools/navimow_android_live_setup.py doctor
python tools/navimow_android_live_setup.py run --duration 60
python tools/navimow_android_live_setup.py parse --input captures/live-sync-*/redacted-frida.log
python tools/navimow_live_sync.py route-catalog
python tools/navimow_live_sync.py init-openapi-config --output config/navimow-live-sync.local.json
python tools/navimow_live_sync.py oauth-login-url
python tools/navimow_live_sync.py oauth-doctor --config config/navimow-live-sync.local.json
python tools/navimow_live_sync.py sync-once --config config/navimow-live-sync.local.json --db data/navimow.sqlite --routes openapi-auth-list,openapi-mqtt-info
python tools/navimow_live_sync.py configure-openapi-status --config config/navimow-live-sync.local.json --db data/navimow.sqlite
python tools/navimow_live_sync.py sync-once --config config/navimow-live-sync.local.json --db data/navimow.sqlite --routes openapi-vehicle-status,openapi-mqtt-info
python tools/navimow_live_sync.py plan --config config/navimow-live-sync.local.json
python tools/navimow_live_sync.py doctor --config config/navimow-live-sync.local.json --db data/navimow.sqlite
python tools/navimow_live_sync.py setup-report --config config/navimow-live-sync.local.json --db data/navimow.sqlite --viewer-output viewer/navimow-map
python tools/navimow_live_sync.py live-health --config config/navimow-live-sync.local.json --db data/navimow.sqlite --viewer-output viewer/navimow-map
python tools/navimow_live_sync.py map-sync-plan --db data/navimow.sqlite
python tools/navimow_live_sync.py sync-once --config config/navimow-live-sync.local.json --db data/navimow.sqlite --map-delta --download-map-artifacts --map-dir data/maps
python tools/navimow_live_sync.py sync-once --config config/navimow-live-sync.local.json --db data/navimow.sqlite
python tools/navimow_live_sync.py sync-once --config config/navimow-live-sync.local.json --db data/navimow.sqlite --update-live-status --viewer-output viewer/navimow-map
python tools/navimow_live_sync.py poll --config config/navimow-live-sync.local.json --db data/navimow.sqlite --interval 10 --use-route-cadence --activity-aware-cadence --refresh-trails-on-completion --max-iterations 12 --update-live-status --viewer-output viewer/navimow-map
python tools/navimow_live_sync.py poll --config config/navimow-live-sync.local.json --db data/navimow.sqlite --interval 10 --use-route-cadence --activity-aware-cadence --refresh-trails-on-completion --max-iterations 12 --auto-viewer-refresh --viewer-output viewer/navimow-map
python tools/navimow_live_sync.py mqtt-doctor --config config/navimow-live-sync.local.json
python tools/navimow_live_sync.py mqtt-listen --config config/navimow-live-sync.local.json --db data/navimow.sqlite --duration 600 --max-messages 500 --update-live-status --viewer-output viewer/navimow-map
python tools/navimow_live_sync.py mqtt-sample-report --db data/navimow.sqlite
python tools/navimow_live_sync.py mqtt-ui-report --config config/navimow-live-sync.local.json --db data/navimow.sqlite --viewer-output viewer/navimow-map --update-live-status
python tools/navimow_live_console.py --config config/navimow-live-sync.local.json --db data/navimow.sqlite --viewer-output viewer/navimow-map --openapi-preflight --refresh-openapi --strict-health --auto-port --with-mqtt
```

For OpenAPI first setup, `make quickstart-live` wraps token readiness, discovery,
status body configuration, status sync, status-only console generation when map
captures are absent, full viewer rebuild when map data exists, and a redacted
live-health report. For continuous local use after viewer data exists,
`make live-console` is the default supervised local console: it starts the
browser server, status polling, strict readiness check, and optional MQTT
listener in one process group. `make live-console-no-mqtt` keeps the same
polling/server workflow without MQTT. `make live-poll-status` still updates only
`navimow-live-status.json` when routes are due and is useful for debugging. Use
`make live-poll-viewer` for automatic live-status writes plus full rebuilds when
map/detail, downloaded map artifact, or future schedule counts change.
The default Make polling recipes pass `--refresh-trails-on-completion`, so
already-selected trail index/path routes are pulled forward when sanitized state
changes from active to idle.
Use `make live-route-coverage` after syncs to see which read routes are typed,
snapshot-only, viewer-backed, present, or promotion candidates before adding new
tables.
Use `make serve-live` when you deliberately want the browser server separate; it
serves generated files locally, sends compact reload metadata, sends full
sanitized live-status events, and serves the same sanitized live-status feed for
field-level mower/status updates. Use
`make serve-local` only when manual refresh is acceptable.
Use `make live-ui-smoke` when you want an optional headless Chromium proof that
the visible Mower panel changes after a sanitized live-status SSE update.

MQTT live listening is optional and experimental. It uses OpenAPI MQTT metadata,
prints only present/missing fields and topic counts, stores compact sanitized
message summaries, and should run alongside `make serve-live`. Run
`make mqtt-sample-report` after listener sessions to inspect the redacted typed
sample coverage, use `make mqtt-ui-report` to check MQTT-to-browser freshness,
then `mqtt-readiness --strict` before changing polling policy. When MQTT exposes
current partition/progress fields, `navimow-live-status.json` updates the Mower
panel battery/progress, current-zone badge, zone progress text, and area
highlight in place. It does not
replace full viewer rebuilds for map geometry/layout/schedule/area definition
changes or consumer route polling for fields not present in real MQTT samples.

## Auth Discovery Status

`auth-discover` intentionally reports only header names, route paths, and counts.
It must not print header values, tokens, cookies, trace IDs, raw payloads, or
exact location fields.

The current captured post-cache metadata exposes response headers and route
paths, but it does not contain reusable request Authorization/Cookie values.
For true unattended live polling, use `tools/navimow_android_live_setup.py` to
capture the live app request shape. The default Android capture is shape-only:
it records route aliases, header names, content type, content length, and JSON
key/type shape without scalar values or body previews. The doctor command checks
adb/frida, authorized device count, package install status, and app process
status without printing device serials.

The `--include-values --i-understand-local-secrets` mode can write a usable
ignored local config, but its output must stay on this machine and out of chat,
git, and browser exports. Parsing an existing value-bearing capture requires the
same acknowledgement, and value-bearing config outputs should end with
`.local.json`. Validate with `doctor` before running network sync.

For the current route and feature backlog, see
[`docs/implementation-gap-map.md`](implementation-gap-map.md).
