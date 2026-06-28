# Navimow Live Operations Runbook

Use this when running the local Terranox console with live data. Build the full
viewer once, then prefer status-only updates while the console is open: polling
updates SQLite plus `viewer/navimow-map/navimow-live-status.json`, and the
localhost viewer server updates the Mower panel and marker in place. Full viewer
rebuilds are still available for map, layout, schedule, area, or satellite
changes.
The sanitized live feed is centralized in `tools/navimow_live_status.py`; both
polling and MQTT refresh that artifact, and the server only serves or emits the
sanitized result through the live-status endpoint/SSE stream.

## Preflight

For the OpenAPI live-status path, install dependencies and use the redacted
quick-start flow after OAuth has been configured:

```bash
make setup
make openapi-preflight
make live-setup-report
make quickstart-live
make live-route-catalog
make live-route-coverage
```

`openapi-preflight` verifies that the local live-sync config is OpenAPI-shaped,
OAuth token files are present, and OpenAPI status devices are configured when
needed. If not, it prints the next exact local command to run without printing
token or device values. `quickstart-live` syncs OpenAPI discovery/status/MQTT,
builds a status-only console when map captures are absent, and builds the full
viewer when local map data is already present. Use the full map/capture
preflight below when you need terrain, areas, schedules, and map settings.
`live-route-catalog` prints the code-owned route allowlist, cadences,
OpenAPI/consumer split, and refused write/command patterns without reading local
config, DB, network, or secrets.
`live-route-coverage` reads the local SQLite store and reports which read
routes are typed, snapshot-only, viewer-backed, present, or promotion
candidates without exporting raw route data.
`live-setup-report` is the single redacted setup handoff: it combines OpenAPI
preflight, local OAuth readiness, route freshness, viewer live-status state,
route catalog counts, route storage coverage, next commands, and remaining
mapping gaps. Its `Readiness Summary` says whether the console can open now,
whether strict live data is fresh, whether OAuth/OpenAPI refresh is recommended,
and whether MQTT/trail replay evidence is still gated. It does not sync with the
network; run the refresh commands separately.

For the full map, area, schedule, settings, and history console:

```bash
make setup
make ingest
make viewer
make test
```

If `ingest` fails while decoding map data, install the system `zstd` CLI. On
macOS:

```bash
brew install zstd
```

## Choose Auth Mode

Use OAuth/OpenAPI first when you need mower discovery, status cards, and MQTT
metadata:

```bash
make openapi-init
make oauth-login-url
make oauth-exchange-code OAUTH_CODE='http://localhost:1/callback?code=PASTE_CODE_HERE'
make oauth-doctor
make quickstart-live
```

Use the consumer-app session path only for consumer routes such as map detail,
location, settings, trail time, weather, today plan, and maintenance. Keep
value-bearing capture output local:

```bash
make consumer-session-report
make live-android-doctor
make live-android-capture
python tools/navimow_android_live_setup.py run \
  --duration 60 \
  --include-values \
  --i-understand-local-secrets \
  --write-config config/navimow-live-sync.local.json
make live-doctor
```

`make consumer-session-report` checks the current config and local captures
without network calls. It reports whether consumer-app auth headers are present,
whether referenced env vars are set, which consumer read routes already exist
in SQLite, and which Android/session command should be run next. It does not
print header values, tokens, cookies, device IDs, signed URLs, GPS, or raw
payloads.

## Start The Local Console

The default local workflow is a single supervised command. Auto refresh requires
existing viewer data from `make viewer`, `make viewer-status-only`, or
`make quickstart-live`; it writes status-only updates for live status, settings,
capability, trail-time, and MQTT/OpenAPI changes, and rebuilds the viewer only
for structural map/detail, downloaded map artifact, or future schedule changes.
The polling loop also uses completion-triggered trail refresh: when sanitized
MQTT/OpenAPI/consumer activity changes from active to idle, selected
`trail-time` and `trail-data` routes are pulled forward instead of waiting for
their normal cadence.

Start the console:

```bash
make live-console
```

This runs OpenAPI preflight, refreshes OpenAPI discovery/status/MQTT metadata,
checks `live-health --strict`, starts the localhost viewer server, starts the
status polling loop, and starts MQTT with realtime defaults. MQTT is optional in
the supervisor: if the listener exits or receives no events, the polling loop
continues to update the UI. Use polling only with:

```bash
make live-console-no-mqtt
```

When no real MQTT message has arrived yet, prove the browser update path with a
deterministic local replay:

```bash
make mqtt-replay-smoke
```

The replay command injects one synthetic sanitized MQTT status event into the
same SQLite tables used by `mqtt-listen`, refreshes
`navimow-live-status.json`, and lets the open browser receive the update through
full sanitized SSE live-status events with `/__navimow/live-status` as fallback.
It is local-only and does not print or export
topic names, raw payloads, tokens, signed URLs, or GPS. It auto-picks an
existing area from the generated viewer; override with
`make mqtt-replay-smoke REPLAY_AREA_ID=3` when you want a specific zone.
Clean synthetic replay rows afterward with `make mqtt-replay-clear`.

For a stronger browser-level proof, use the optional headless Chromium smoke:

```bash
make live-ui-smoke
```

It serves the generated viewer on localhost, applies a temporary sanitized
live-status patch, waits for the visible Mower panel to update through the SSE
live-status path, then restores the original status file. It does not create screenshots, traces,
or browser recordings.

For debugging, the pieces can still be run in separate terminals. Terminal 1
refreshes live data and writes only the status artifact:

```bash
make live-poll-status
```

The Make polling targets enable activity-aware cadence. The classifier uses
only sanitized local MQTT/OpenAPI/consumer status snapshots; it speeds selected
pose/state routes up when the latest known state looks active and slows them
when it looks idle or docked. Unknown states fall back to the fixed route
cadence. Effective cadence is bounded by the poll `--interval`; the Make
targets use `--interval 5`.

The default polling recipes pass `--refresh-trails-on-completion`; set
`POLL_REALTIME_FLAGS=` if you deliberately want to disable the forced
post-completion trail reads for a run.

Before fetching map artifacts, inspect the local map/detail/artifact state:

```bash
make live-map-plan
```

This is a read-only report. It compares latest mower map version, decoded
map-detail freshness, artifact metadata, and local terrain bundle file presence,
then prints redacted next commands such as `make live-map-artifacts` and
`make viewer` when a map refresh is warranted.

To run that route subset automatically, use:

```bash
make live-map-delta
```

It refreshes `index2`, uses the local map plan to decide whether `map-list`,
`map-detail`, and `get-iot-file` are needed, downloads required map artifacts
only when requested, clears retained signed URLs in cleanup, and rebuilds the
viewer afterward.

For experimental standalone realtime status updates from OpenAPI MQTT metadata,
validate metadata first, then listen alongside or instead of the poll loop:

```bash
make mqtt-doctor
make mqtt-listen
make mqtt-sample-report
make mqtt-ui-report
```

`make live-console` starts MQTT with realtime defaults. The standalone
`make mqtt-listen` target defaults to a bounded smoke run (`MAX_MESSAGES=1`,
`DURATION=60`). For a longer standalone session, pass explicit values such as
`make mqtt-listen MAX_MESSAGES=500 DURATION=600`. MQTT listening stores only
sanitized message summaries, classifies safe payload categories, promotes
allowlisted live status fields into typed status rows when present, and updates
`navimow-live-status.json`. OpenAPI auth/status also promotes into safe typed
rows, so the browser and activity-aware cadence can use mower state, capacity,
and device count even if the generic route snapshot is compacted. The browser
consumes that feed to update the Mower panel, current-zone badge/highlight, area
progress text, last-mow summary, and effective cutting-height text without a
full map bundle rewrite. Mower marker position still comes from sanitized
`get-location` local pose until real MQTT pose fields are mapped. The
live-status feed exposes counts, classes, and area IDs only; it does not
expose topic names, raw payloads, payload hashes, broker data, credentials, area
names, polygons, or GPS arrays. Keep `make live-poll-status` as periodic
reconciliation and fallback; MQTT does not refresh map geometry, layout,
schedule structure, or area definitions.
The MQTT parser/sanitizer/listener code is isolated in
`tools/navimow_mqtt_client.py`; `tools/navimow_live_sync.py` handles storage and
calls the shared viewer artifact refresh helper.

After a listener or replay run, `make mqtt-sample-report` prints a redacted
coverage report from the typed MQTT table: state/task/work/event enum counts,
active/idle/unknown classes, field coverage, and whether rows are real or
synthetic. Use this report before lowering polling cadence; MQTT should only
replace a route after the needed fields and states have real sample coverage.
`make mqtt-ui-report` is the operator-facing companion check. It validates the
MQTT metadata shape, refreshes the sanitized live-status feed, reports whether
the browser feed has MQTT metadata/status/message summaries, and prints the next
command. To combine a bounded listener attempt and the report, run
`make mqtt-ui-report MQTT_UI_FLAGS="--listen --update-live-status"
MAX_MESSAGES=500 DURATION=600`.
`make mqtt-readiness` is the stricter gate for that decision. It fails in
strict mode until real samples cover active and idle states plus battery,
current-area, and progress fields:

```bash
python tools/navimow_live_sync.py mqtt-readiness --db data/navimow.sqlite --strict --json
```

Use the auto viewer refresh loop when structural map or future schedule data
might change but you still want status-only writes for ordinary live state:

```bash
make live-poll-viewer
```

For a lower-network auto refresh run without satellite tile refresh:

```bash
make live-poll-viewer-no-satellite
```

Terminal 2 serves the viewer on localhost only, sends compact change events, and
serves the sanitized live-status feed:

```bash
make serve-live
```

Open the URL printed by `make serve-live`. It prefers
[http://127.0.0.1:8765/](http://127.0.0.1:8765/), but the Make target passes
`--auto-port`, so the live-aware server selects the next free localhost port if
8765 is already occupied. Use `make serve-live PORT=8768` to start from a
specific port, or `make serve-live SERVE_FLAGS=` if you want binding to fail
instead of falling forward. The browser updates the Mower panel in place after
status-only writes, and reloads after layout/full viewer changes. If you prefer
a plain static server, use `make serve-local` and refresh manually.

For a one-shot refresh:

```bash
make live-once-status
```

Use `make live-once-viewer` for a one-shot full sync plus full viewer rebuild;
it requires existing captured/consumer map data.

## Token And Session Refresh

OAuth tokens are stored only in `config/navimow-oauth.local.json`. Check without
printing token values:

```bash
make oauth-doctor
```

Refresh manually when needed:

```bash
make oauth-refresh
```

Network sync also refreshes OAuth automatically when the token is near expiry.
If OAuth works but consumer routes fail, that is usually expected: consumer
routes need consumer-app session headers, not the OpenAPI Bearer token.
`make live-map-delta` is intentionally guarded here: with `auth.provider` set to
`navimow-oauth`, it stops before network access and prints the local
Android/session capture commands instead of trying consumer map routes with an
OpenAPI token. Fixture runs can still use `sync-once --map-delta --responses-dir`.

## Viewer Refresh Model

- `make openapi-preflight` validates local OpenAPI config/token/status-device readiness without network calls.
- `make quickstart-live` runs the OpenAPI preflight, syncs OpenAPI discovery/status/MQTT, builds a status-only console when map captures are absent, rebuilds the full viewer when map data exists, and prints a redacted health report.
- `make openapi-first-sync` is the lower-level discovery/status/MQTT plus viewer-if-map-data primitive.
- `make openapi-refresh-status` refreshes OpenAPI status/MQTT and writes only `navimow-live-status.json`.
- `make openapi-refresh-viewer` refreshes OpenAPI status/MQTT and rebuilds the full viewer when map data exists, otherwise refreshing the status-only console.
- `make live-poll-status` runs selected config routes by activity-aware cadence and updates the live status artifact only when a route actually runs.
- `make live-poll-viewer` runs selected config routes by activity-aware cadence,
  writes live-status for status/settings/capability/trail-time/MQTT/OpenAPI
  changes, and rebuilds when map/detail, downloaded map artifact, or future
  schedule counts change.
- `make live-map-plan` inspects local map/detail/artifact versions and prints
  the next safe redacted sync commands without touching the network or viewer.
- `make live-map-delta` runs the map plan as an explicit sync: state first,
  only needed map-support routes, transient artifact download, then viewer
  rebuild. It requires consumer-app session auth for live network calls, not an
  OpenAPI-only token.
- `make trail-replay-report` checks local readiness for decoded historical
  trail/path replay without exporting compressed trail blobs, payload hashes,
  area names, GPS, device IDs, signed URLs, or raw route data.
- `make mqtt-doctor` validates OpenAPI MQTT metadata without printing broker,
  username, password, client ID, or topic values.
- `make live-console` supervises the localhost viewer server, activity-aware
  status polling, strict readiness check, and optional MQTT listener in one
  local command.
- `make live-console-no-mqtt` runs the same supervised console without MQTT.
- `make mqtt-listen` subscribes to discovered MQTT topics, stores sanitized
  message summaries, exposes safe class/count coverage, promotes allowlisted
  status fields, and can update the shared live status artifact after each
  message.
- `make mqtt-ui-report` combines MQTT metadata shape, optional listener results,
  live-status freshness, browser-feed MQTT visibility, and the readiness next
  command in one redacted report.
- `make mqtt-readiness` gates whether real MQTT samples are sufficient for
  Mower panel/current-zone/activity-aware polling decisions.
- `make mqtt-replay-smoke` injects one synthetic sanitized MQTT status event,
  refreshes the live-status artifact, and is the deterministic local smoke test
  for the MQTT-to-browser path.
- `make mqtt-replay-clear` removes synthetic replay rows and refreshes the
  live-status artifact.
- `make live-ui-smoke` launches a local headless Chromium smoke against the
  generated viewer and proves the visible Mower panel changes after a
  live-status SSE update.
- `make live-health-strict` is the local gate for the realtime console. It
  checks selected routes against compact snapshots or typed tables, verifies the
  OAuth token against the same clock used by the report, detects stale/future
  skewed data, and confirms the browser live-status artifact is readable and
  fresh.
- `python tools/navimow_live_sync.py live-health --strict --json` emits the same
  redacted readiness result with top-level `ready` and `status` fields for
  local scripts.
- `make live-setup-report` emits the broader local-only handoff report with
  preflight, health, a compact readiness summary, route-catalog counts, route
  storage coverage, next commands, and unresolved gaps.
- `make live-route-coverage` prints local typed/snapshot/viewer/promotion
  coverage for all read routes without exporting raw payloads.
- `make serve-live` serves generated files and exposes metadata-only
  `/__navimow/status`, compact `/__navimow/events`, and sanitized
  `/__navimow/live-status` endpoints.
- The browser updates the Mower panel, mower marker, area list/details, and
  current-zone highlight in place when only the live status file changes. It
  reloads the page when viewer assets or the map layout version change.

## Troubleshooting Matrix

| Symptom | Check | Likely fix |
|---|---|---|
| `oauth-doctor` says token file missing | `config/navimow-oauth.local.json` | Run OAuth login and exchange again. |
| `plan` warns OAuth with consumer routes | `make live-plan` | Use `openapi-*` routes for OAuth or capture consumer headers. |
| `openapi-vehicle-status` has no devices | latest auth-list snapshot | Run `make openapi-discover`, then `make openapi-configure-status`. |
| `mqtt-doctor` says metadata is missing | latest OpenAPI MQTT snapshot/token | Run `make openapi-discover` or refresh OAuth, then retry. |
| `mqtt-listen` says `paho-mqtt` is missing | Python environment | Run `make setup` or `python -m pip install -r requirements.txt`. |
| `live-ui-smoke` cannot find Chromium | `CHROMIUM=/path/to/browser python tools/navimow_live_ui_smoke.py --dry-run` | Install Chromium/Chrome/Brave or set `CHROMIUM` to a working browser executable. |
| Missing env vars | `make live-doctor` | Export local consumer session values or switch to OpenAPI config. |
| No map snapshot | `make ingest` | Re-ingest captures and ensure map detail exists. |
| Map decode fails | `zstd --version` | Install `zstd`. |
| Android capture cannot attach | `make live-android-doctor` | Wake phone, authorize debugging, open original signed app. |
| Port 8765 is busy | `lsof -nP -iTCP:8765 -sTCP:LISTEN` | Run `make serve-live` and open the printed URL; it auto-selects the next free localhost port. |
| Browser does not auto-reload | `/__navimow/status` | Use the URL printed by `make serve-live`; `make serve-local` and `python -m http.server` are static-only and return 404 for live endpoints. |
| Mower panel does not update in place | `/__navimow/live-status` and `/__navimow/events` | Run `make live-poll-status`, `make live-poll-viewer`, or `make mqtt-replay-smoke` after `make viewer`, `make viewer-status-only`, or `make quickstart-live`, and serve through `make serve-live` or `make live-console`. |
| Satellite background missing | network/tile availability | Rebuild with `make viewer-no-satellite`. |
| Viewer looks stale | rebuild output timestamp, `/__navimow/status`, and `/__navimow/live-status` | Check that `make live-poll-status` or `make live-poll-viewer` is writing the same `VIEWER` directory that `make serve-live` is serving. |

## Safe Sharing Checklist

Do not share `captures/`, `data/`, `config/*.local.json`, raw logs, generated
viewer bundles, or screenshots without review. Even sanitized local viewers can
show area names, property layout, and satellite imagery that reveal location.

Before sharing any screenshot:

- Turn off satellite background if location privacy matters.
- Check that no token, raw device ID, signed URL, trace/request ID, or exact GPS
  appears.
- Avoid showing area names if they identify the property.

## Shutdown And Cleanup

Stop long-running `make live-poll-status`, `make live-poll-viewer`, and
`make serve-live` processes with
`Ctrl-C`. Generated private outputs stay local and ignored by git. To remove
viewer/test caches:

```bash
make clean-generated
```
