# Tool Usage

This page is the command index for the local Navimow Terranox console. All
commands are intended for local use. Write/control routes remain disabled unless
explicitly documented otherwise.

## Setup

```bash
make setup
```

Creates a Python virtual environment and installs `requirements.txt`.

## Readiness And Handoff Reports

```bash
make live-setup-report
make completion-report
make completion-report-strict
```

- `live-setup-report` prints a redacted console-readiness summary.
- `completion-report` audits the broader implementation gaps.
- `completion-report-strict` returns non-zero while required full-featured
  blockers remain.

## OAuth/OpenAPI Status Path

```bash
make openapi-init
make oauth-login-url
make oauth-exchange-code OAUTH_CODE='http://localhost:1/callback?code=PASTE_CODE_HERE'
make quickstart-live
```

Use this path for login, mower discovery, OpenAPI status, and MQTT metadata.
Token files stay in ignored local config files. Do not paste OAuth codes,
access tokens, refresh tokens, or MQTT credentials into public places.

## Local Console

```bash
make live-console
make live-console-no-mqtt
```

- `live-console` starts the localhost viewer, live-status polling, strict health
  checks, and MQTT listening with polling fallback.
- `live-console-no-mqtt` starts the same console without MQTT.

## Map, Area, Settings, And History

```bash
make consumer-session-report
make live-map-plan
make live-map-delta
make ingest
make viewer
make live-health
make live-health-strict
```

- `consumer-session-report` checks whether consumer-app route sync has local
  auth/session readiness without printing values.
- `live-map-plan` reports local map/artifact freshness without network calls.
- `live-map-delta` refreshes only needed map-support routes when a consumer
  session config is available.
- `ingest` rebuilds local SQLite state from private captures.
- `viewer` builds the full local map console.
- `live-health-strict` is the stronger readiness gate before relying on the
  live console.

## MQTT Experiments

```bash
make mqtt-doctor
make mqtt-listen MAX_MESSAGES=500 DURATION=600
make mqtt-sample-report
make mqtt-ui-report
make mqtt-readiness
make mqtt-replay-smoke
make mqtt-replay-clear
```

MQTT support is experimental. The listener stores sanitized summaries and
allowlisted status fields only. Use `mqtt-readiness` before reducing polling
based on MQTT evidence. `mqtt-replay-smoke` is deterministic and synthetic; it
proves the browser update path but does not replace real samples.

## Schedule Drafts

```bash
make schedule-export
make schedule-optimize-dry-run OPTIMIZE_DAY=Tuesday
make schedule-optimize-weekly
make schedule-validate
make schedule-payload
```

- `schedule-export` writes `viewer/navimow-map/schedule-draft.json` from the
  latest local SQLite schedule snapshot.
- `schedule-optimize-dry-run` proposes one optimized day.
- `schedule-optimize-weekly` creates a weekly custom-zone rotation with an
  `80` hour default cap and `Autoplats` night-only by default.
- `schedule-payload` prints an app-shaped dry-run `planList` payload.

No schedule command sends data to the mower.

## Android Capture Helpers

```bash
make live-android-doctor
make live-android-capture
python tools/navimow_android_live_setup.py run --duration 60
```

Default Android capture is shape-only. Value-bearing capture requires
`--include-values --i-understand-local-secrets` and must write only ignored
local files.

## Tests

```bash
make test
PYTHONDONTWRITEBYTECODE=1 python3 -B -m pytest -q -p no:cacheprovider tests
```

Run tests before pushing public changes.
