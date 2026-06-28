# Navimow Terranox Quick Start

This is the shortest safe path for the local Terranox console. It keeps OAuth
tokens, MQTT credentials, mower IDs, signed URLs, raw route payloads, and exact
GPS out of terminal output and browser exports.

## 1. Install Local Dependencies

```bash
git clone https://github.com/Tsopic/navimow-terranox-tools.git
cd navimow-terranox-tools
make setup
```

If map ingest fails on compressed data, install the system `zstd` CLI:

```bash
brew install zstd
```

Docker is also supported:

```bash
make docker-build
make docker-serve PORT=8765
```

The Docker image starts a status-only console when no private SQLite/map data is
mounted. With local `data/`, `viewer/`, and `config/` bind mounts, it can serve
your local generated console without copying private files into the image.

## 2. Use OAuth/OpenAPI For Live Status

If `config/navimow-oauth.local.json` already exists, run the read-only OpenAPI
preflight first:

```bash
make openapi-preflight
```

It checks that `config/navimow-live-sync.local.json` is OpenAPI-shaped, the auth
provider is `navimow-oauth`, the local token file exists, and status device IDs
are configured when the status route is enabled. If anything is missing, it
prints the next exact local command to run without printing token or device
values. When it reports `openapi preflight: ok`, run the setup:

For a single local handoff/debug summary before or after setup:

```bash
make live-setup-report
```

It combines OpenAPI preflight, OAuth token presence/freshness, SQLite route
freshness, viewer live-status state, route coverage counts, next commands, and
remaining mapping gaps. The top `Readiness Summary` separates the practical
states: whether the console can be opened now, whether strict live data is fresh,
whether OAuth/OpenAPI refresh is recommended, whether MQTT has enough real
samples to drive UI/polling decisions, and whether trail replay is ready. It does
not perform network sync and does not print token values, device IDs, MQTT
credentials/topics, raw payloads, signed URLs, or GPS.

To audit the broader repo goal, including what still blocks calling the system
full-featured, run:

```bash
make completion-report
```

Use the strict gate or machine-readable form when handing off unfinished work:

```bash
make completion-report-strict
python tools/navimow_live_sync.py completion-report --strict --json
```

This report intentionally distinguishes a runnable OpenAPI console from full
completion. It remains incomplete until real MQTT samples, consumer-session
routes, route coverage, trail replay, a tracked source baseline, and the native
schedule/settings write envelope have enough local evidence.

```bash
make quickstart-live
```

That validates the token, syncs mower discovery, configures the local status
request body without printing device IDs, syncs status/MQTT metadata, builds a
status-only console when map captures are absent, rebuilds the full viewer when
local map data already exists, and prints a redacted live-health report.

Inspect the redacted route allowlist and blocked write/command patterns without
network, DB, config, or secrets:

```bash
make live-route-catalog
```

Inspect local route storage/promotion coverage from SQLite without exporting
payloads or secrets:

```bash
make live-route-coverage
```

If OAuth is not set up yet:

```bash
make openapi-init
make oauth-login-url
```

Open the printed URL, sign in, and copy the failed `localhost:1` redirect URL
from the browser address bar. Exchange it locally:

```bash
make oauth-exchange-code OAUTH_CODE='http://localhost:1/callback?code=PASTE_CODE_HERE'
make quickstart-live
```

Do not paste OAuth codes, redirect URLs, access tokens, refresh tokens, or MQTT
credentials into chat or shared docs.

## 3. Open The Console

Start the supervised localhost-only live console after `quickstart-live` builds
viewer data:

```bash
make live-console
```

This runs OpenAPI preflight, refreshes OpenAPI discovery/status/MQTT metadata,
requires `live-health --strict` to pass, starts the live-aware viewer server,
keeps `navimow-live-status.json` fresh for status/settings/capability changes,
rebuilds the viewer for structural map or future schedule changes, and starts
the MQTT listener with realtime defaults. MQTT and polling both write through
the same sanitized live-status helper, so the browser receives full sanitized
live-status SSE payloads with fetch fallback and stale MQTT panel fields are
replaced instead of merged forever. It
also forces already-selected trail read routes after sanitized activity changes
from active to idle, so last-mow data catches up after a task finishes. If MQTT
exits or no event arrives, the polling loop continues as the fallback. Open the
URL printed by the server. It prefers
[http://127.0.0.1:8765/](http://127.0.0.1:8765/), but the console automatically selects the next free localhost port when 8765 is already busy.
If you want a specific port:

```bash
make live-console PORT=8767
```

If you want the same live console without MQTT:

```bash
make live-console-no-mqtt
```

For full map/terrain sync decisions, inspect the local map state first:

```bash
make live-map-plan
```

This does not call the network. It compares the latest known mower map version,
decoded map-detail snapshot, artifact metadata, and downloaded terrain bundle,
then prints the next safe local commands when the map or terrain needs refresh.

When the plan says map data is missing or stale, run:

```bash
make live-map-delta
```

That command refreshes the cheap map state first, runs only the needed map
routes, downloads any required terrain artifact with transient signed URLs, and
rebuilds the local viewer.
Map delta uses consumer-app routes, so OAuth/OpenAPI-only config is not enough
for live network calls. If your config is OpenAPI-only, the command stops before
network access and prints the local Android/session capture commands to create an
ignored consumer-session config. You can still use `--responses-dir` fixtures for
local testing.

Before running live consumer routes, check the local session boundary:

```bash
make consumer-session-report
```

It reports whether the current config has consumer-app auth headers, whether any
referenced local env vars are set, which consumer routes are already present in
SQLite, and the next Android capture commands. It never prints header values,
tokens, cookies, signed URLs, device IDs, GPS, or raw payloads.

For trail/path replay readiness without exporting compressed paths, area names,
GPS, payload hashes, or raw route data:

```bash
make trail-replay-report
```

It checks whether trail-time history, a sanitized trail-data snapshot, decoded
map geometry, and render calibration are present before any decoder work starts.

To prove the MQTT-to-browser path without waiting for a real mower event, run
this in another terminal while the console is open:

```bash
make mqtt-replay-smoke
```

It injects one synthetic sanitized MQTT status event, refreshes
`navimow-live-status.json`, and lets the browser update through the same
`/__navimow/live-status` and SSE path used by real MQTT messages. It auto-picks
an existing area from the generated viewer; override with
`make mqtt-replay-smoke REPLAY_AREA_ID=3` when you want a specific zone.
Clear synthetic replay rows afterward with:

```bash
make mqtt-replay-clear
```

## 4. Keep Status Fresh

For OpenAPI status/MQTT metadata refreshes:

```bash
make openapi-refresh-status
```

For a long-running status loop:

```bash
make live-poll-status
```

The Make polling loops use activity-aware cadence plus
`--refresh-trails-on-completion`, so selected `trail-time`/`trail-data` reads run
immediately when the latest sanitized state moves from active to idle.

For experimental MQTT status events:

```bash
make mqtt-doctor
make mqtt-listen
make mqtt-sample-report
make mqtt-ui-report
```

`make live-console` starts MQTT with realtime defaults. The standalone
`make mqtt-listen` target remains a bounded smoke run. Use explicit values for a
longer standalone MQTT session, for example `make mqtt-listen MAX_MESSAGES=500
DURATION=600`. The listener stores sanitized message summaries, classifies safe
payload categories, promotes allowlisted status-like fields, and refreshes the
local live-status feed without exporting MQTT credentials, topic names, raw
payloads, payload hashes, device IDs, or exact GPS.
MQTT can update the Mower panel battery/progress fields and current-zone
badge/progress when real payloads contain mapped fields. The mower marker still depends on sanitized
local pose from `get-location` until real MQTT pose fields are mapped.

Run `make mqtt-sample-report` after `mqtt-listen` or `mqtt-replay-smoke` to see
which sanitized enum values and UI fields have actually been observed. Real
samples are needed before MQTT can safely replace any polling route; synthetic
rows prove the path but are labelled separately.

Run `make mqtt-ui-report` when you want the operator view in one place. It
validates MQTT metadata shape, refreshes the sanitized live-status feed, reports
whether the browser feed contains MQTT metadata/status/message summaries, and
prints the next command without exposing broker details, topics, credentials,
payload hashes, raw payloads, device IDs, or GPS.

Use `make mqtt-readiness` for the stricter gate. It passes only after real
MQTT samples include active and idle states plus the required battery, current
area, and progress fields needed for UI/polling decisions. Add `--strict`
through the CLI when automation should fail until that evidence exists:

```bash
python tools/navimow_live_sync.py mqtt-readiness --db data/navimow.sqlite --strict --json
```

Use `make mqtt-replay-smoke` as a local deterministic UI smoke test when no live
MQTT message has arrived yet. Use `make mqtt-replay-clear` to remove synthetic
replay rows and refresh the live-status feed.

To prove the same update reaches the visible browser UI, run the optional
headless Chromium smoke after a viewer has been built:

```bash
make live-ui-smoke
```

It serves the generated viewer on localhost, applies a temporary sanitized
live-status patch, waits for the Mower panel text to change through the full
SSE payload path, then restores the original live-status file. It does not create screenshots,
traces, or browser recordings. Set `CHROMIUM=/path/to/chromium` if Chromium is
not on `PATH`.

## 5. Add Map, Area, Settings, And History Data

OAuth/OpenAPI is enough for login, mower discovery, status cards, and MQTT
metadata. It does not expose weekly schedule CRUD or the full consumer-app map
and settings routes, so the full map console needs local map captures at least
once.

To refresh local map, area, schedule, settings, cutting height, trail-time, and
last-mow evidence, use local consumer-app captures:

```bash
make ingest
make viewer
make live-health
```

Before leaving the browser open as a realtime console, run the stricter gate:

```bash
make live-health-strict
```

It checks the selected read routes against typed SQLite tables where available,
verifies OAuth token freshness, and fails stale or unreadable
`navimow-live-status.json` output. For scripts, use
`python tools/navimow_live_sync.py live-health --strict --json`; the JSON output
contains `ready` and `status` fields and does not include tokens, MQTT
credentials, device IDs, topic names, raw payloads, or GPS.

For a broader script-friendly setup handoff, use
`python tools/navimow_live_sync.py setup-report --strict --json`. It includes the
same readiness signal plus a `readinessSummary`, route-catalog counts, route
storage coverage, and the remaining work list.

For fresh consumer-app request shapes:

```bash
make live-android-doctor
make live-android-capture
```

Only use value-bearing Android capture mode for local ignored files, and keep
schedule/settings writes dry-run until the command envelope is fully mapped.
