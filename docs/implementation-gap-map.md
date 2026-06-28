# Navimow Terranox Implementation Gap Map

This is the working map for turning the local captured-data console into a
full-featured local Terranox portal. It intentionally avoids raw serials,
tokens, signed URLs, exact GPS, trace IDs, and raw API payloads.

## Implemented

| Area | Current support |
|---|---|
| Local state store | SQLite ingest for app cache/log evidence, map detail, area geometry, map artifacts, settings, schedule snapshots, device state, device info, sanitized live location, and trail-time indexes. |
| Map portal | Static browser console with terrain/satellite toggle, zoom/pan, readable area labels, mower marker, area settings, schedule membership, cutting height, and last mow status. |
| Mower display | Viewer shows battery, health/state code, network, firmware/capabilities, current cutting height, supported cutting heights, sanitized live-pose source, typed OpenAPI auth/status insights, and route-derived MQTT/weather/today-plan insights when present. |
| Schedule tooling | CLI and viewer draft flow can export, edit, validate, optimize, and print app-shaped dry-run `planList` payloads. Browser and CLI optimizers only update local drafts. No tool sends schedule writes to the mower. |
| Read-only live sync | `tools/navimow_live_sync.py` supports `index2`, `get-device-info`, `get-location`, `set-list`, `trail-time`, `trail-data`, `auth-list`, `mower-state`, `weather`, `today-plan`, `firmware`, `maintenance`, `map-list`, `map-detail`, `get-iot-file`, OpenAPI auth/status/MQTT read routes, and OpenAPI `responseCommands` as read-after-command polling from fixtures or a local ignored config. `make live-route-coverage` reports typed/snapshot/viewer/promotion coverage from local SQLite. Write routes are refused. |
| OAuth/OpenAPI auth | `tools/navimow_live_sync.py` can print the Navimow OAuth login URL, exchange a copied localhost redirect/code into an ignored token file, refresh tokens, and inject Bearer auth for read-only OpenAPI routes. |
| Android live setup | `tools/navimow_android_live_setup.py` can run or parse a Frida capture wrapper. Default output is shape-only; value-bearing mode requires explicit local-secret acknowledgement and writes only local ignored config/log files. |
| Live viewer serving | `tools/navimow_viewer_server.py` serves the generated viewer on localhost, exposes metadata-only `/__navimow/status`, emits compact SSE metadata by default, emits full sanitized live-status SSE payloads when the viewer requests them, and serves sanitized `/__navimow/live-status` as fallback for in-place Mower panel/marker updates. `tools/navimow_live_console.py` supervises the viewer server, strict readiness gate, polling loop, and optional MQTT listener in one local command. |
| Safety boundaries | Privacy docs, `.gitignore`, route allowlists, and tests prevent obvious leakage of tokens, signed URLs, command envelopes, exact GPS, raw captures, and generated private artifacts. |

## Partially Implemented

| Area | What exists | Still needed |
|---|---|---|
| Auth/session refresh | Capture tools can discover route shapes and, with explicit acknowledgement, write a local config from live app requests. OAuth/OpenAPI token refresh is implemented through an ignored token file. `consumer-session-report` checks consumer auth header readiness, env refs, capture hints, and consumer route coverage without printing values. | Keychain-backed token storage and a durable consumer-app session refresh path, if direct consumer routes still need app-specific headers beyond OpenAPI Bearer auth. |
| OpenAPI/MQTT status | OAuth token flow, OpenAPI auth-list/status/MQTT-info route snapshots, typed `openapi_auth_snapshots` and `openapi_status_snapshots`, sanitized status/capacity/device-count/MQTT-topic insights, experimental `mqtt-doctor`/`mqtt-listen`, sanitized MQTT message summaries, safe MQTT payload class/count coverage, typed `mqtt_status_snapshots`, redacted `mqtt-sample-report` enum/field coverage, combined `mqtt-ui-report` operator readiness, `mqtt-readiness` strict real-sample gate, nested OpenAPI-style capacity normalization, viewer rows, deterministic local `mqtt-replay-smoke` testing, a dedicated `tools/navimow_mqtt_client.py` parser/listener module, and shared `tools/navimow_live_status.py` browser-feed sanitization exist. | Capture enough real MQTT samples for `mqtt-readiness --strict` to pass before lowering polling reliance. |
| Direct server polling | Core read routes plus compact route snapshots for `auth-list`, `mowerbot/vehicle/vehicle/state`, weather, today-plan, firmware, maintenance, and map-support routes are implemented and tested. OpenAPI auth/status now promote high-value fields into safe typed tables, the viewer derives safe operational insight fields from known snapshots, and `live-route-coverage` lists which routes are typed, viewer-backed, or promotion candidates. | Map route-specific enums and promote additional high-value fields into typed tables as captures prove their shapes. |
| Live portal updates | Polling can refresh SQLite, apply activity-aware cadence from typed OpenAPI status plus sanitized MQTT/consumer status snapshots, avoid false active classification for `notRunning`-style values, force selected `trail-time`/`trail-data` reads when state moves from active to idle, auto-refresh the viewer by writing only `navimow-live-status.json` through the shared live-status helper for status/settings/capability/trail-time/MQTT/OpenAPI changes and rebuilding for structural map/detail, downloaded map artifact, or future schedule count changes, notify the browser to reload on layout changes, update mower/status fields in place through full sanitized SSE live-status payloads, and show a compact header freshness strip for live-status/OpenAPI/MQTT age. Experimental MQTT listening can store sanitized message summaries, expose safe class/count coverage, promote allowlisted status fields, update the same status artifact, report typed sample coverage with `mqtt-sample-report`, summarize MQTT-to-browser readiness with `mqtt-ui-report`, and gate real-sample confidence with `mqtt-readiness`. The browser replaces live MQTT/pose subtrees from each new feed to avoid stale merged state and promotes MQTT battery/progress into the visible operational stats when mapped fields exist. `make live-console` starts server, polling, strict health, and optional MQTT with polling fallback. `make mqtt-replay-smoke` proves the MQTT-to-live-status path without waiting for a real mower event. `make live-ui-smoke` adds an optional headless Chromium proof that the visible Mower panel updates through the SSE live-status path. | Use live MQTT evidence to map exact status/location payload fields, then reduce polling only where `mqtt-readiness --strict` is green. Add CI-friendly browser automation if this repo later adopts a standard browser runner. |
| Map and area sync | Captured map detail and map artifact parsing are normalized. Live aliases exist for `map-list`, `map-detail-compress`, and `get-iot-file`; `map-detail` decodes through the existing map-detail normalizer. `map-sync-plan` / `make live-map-plan` reports whether local map-detail and terrain artifact state are current before any fetch. `sync-once --map-delta` / `make live-map-delta` refreshes `index2`, runs only needed map-support routes, downloads required terrain artifacts, clears retained signed URLs even on download failure, rebuilds the viewer, and now refuses live network map-delta when the active config is OAuth/OpenAPI-only instead of consumer-session authenticated. | Live-validate route body shapes and version/update semantics for `map-list`, `map-detail-compress`, and `get-iot-file` against fresh app/server responses. |
| Last mow per area | Trail-time indexes produce per-area latest status, completion percentage, and finished area. Selected trail reads can now be forced immediately after active-to-idle completion detection. | Decode `get-path-info-data-compress`, store route samples, and intersect local path points with area polygons for stronger attribution. |
| Cutting height/settings | Viewer displays current global height, supported height list, and area height settings where present. | Map global vs per-area setting semantics and safe write envelopes. Settings writes remain disabled. |
| Schedule optimization | Schedules can be edited locally per area, app-shaped dry-run payloads are generated, the browser can propose one-day dry-run schedules, and the CLI can generate weekly custom-zone rotations with a max-hours cap plus night-only area constraints such as `Autoplats`. | Surface the weekly optimizer in the browser planner and tune heuristics from more real mowing history and compressed trail replay. |

## Missing Or Blocked

| Area | Blocker |
|---|---|
| Schedule writes | Exact outgoing command envelope, signing/encryption, response enum, save step, concurrency behavior, and rollback path are not yet trusted. |
| Settings writes | Same command lifecycle blocker as schedules, plus incomplete global/per-area enum mapping. |
| Command lifecycle routes | `/mowerbot/vehicle/set/send`, `/vehicle/set/response`, `/vehicle/set/save-set-data`, `/vehicle/set/index`, `/vehicle/set/status`, and alternate `/vehicle/set/send` are documented but must stay dry-run only. |
| Full live mower state | `mowerbot/vehicle/vehicle/state` and experimental MQTT messages are captured as compact sanitized snapshots. MQTT messages are now classed into safe coverage buckets such as state, progress, battery, event, and command-result, with `mqtt-sample-report` available to summarize typed enum/field coverage and `mqtt-readiness` available to fail until real active/idle samples and required UI fields are fresh. State names, current partition, active task, pause/return/dock semantics, and typed MQTT/push replacement need real sample mapping. |
| Historical trail replay | `trail-data` can be captured as a sanitized compact route snapshot without storing the compressed path blob, and `make trail-replay-report` checks whether trail-time history, sanitized trail-data, decoded map geometry, and render calibration are present. The compressed trail route format is not decoded yet. |
| Area edit sync | App flows and routes for area edits or richer area metadata are not fully mapped. |
| Safe share/export mode | Current viewer is local-only. A shareable export would need stronger redaction, satellite warning, and likely no area names. |
| OpenAPI schedules | Public SDK/HA/ioBroker OpenAPI surfaces do not expose weekly schedule read/write routes. |

## Recommended Next Patches

1. Run one fresh Android shape-only capture while the app is open on dashboard,
   map, mower status, settings, and history screens. Parse it and confirm the
   expected read routes appear.
2. Run the explicit local-secret capture only on this machine to create
   `config/navimow-live-sync.local.json`, then validate with live-sync `doctor`.
3. Use `oauth-login-url` / `oauth-exchange-code` if OpenAPI status/MQTT reads
   are enough for live display or when app-session headers are stale.
4. Run `make live-route-coverage`, then promote derived route insight fields
   into typed SQLite tables for richer state, today-plan, weather, firmware,
   and maintenance once fresh captures prove the response shape.
5. Run `make mqtt-sample-report` after real listener sessions, then
   `python tools/navimow_live_sync.py mqtt-readiness --db data/navimow.sqlite
   --strict --json`; capture docked, mowing, paused, returning, and completed
   MQTT state coverage before reducing polling.
6. Live-validate `sync-once --map-delta` against fresh map-support responses,
   especially request body shapes and map-version semantics.
7. Decode compressed trail fixtures and add route-sample tables plus map replay.
8. Tune the weekly dry-run optimizer scoring and visit counts after compressed
   trail replay gives stronger per-area completion evidence.
9. Only after a controlled reversible app write capture, add a disabled-by-
   default command lifecycle simulator for schedules/settings. Keep actual sends
   behind a separate explicit approval gate.

## What Is Needed From The Owner

- Android phone awake with Wi-Fi or USB debugging authorized.
- Original signed Navimow app, not a patched APK.
- One dashboard/status capture while docked or idle, and ideally one capture
  while mowing, paused, returning, and after completion.
- Permission before any controlled reversible setting change capture.
- Local-only handling for `captures/`, `data/`, `config/*.local.json`, and
  generated viewers/screenshots.

For long-running local operation, see
[`docs/live-operations-runbook.md`](live-operations-runbook.md).
