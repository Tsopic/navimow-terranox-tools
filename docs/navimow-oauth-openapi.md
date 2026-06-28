# Navimow OAuth And OpenAPI Notes

This documents the optional OAuth/OpenAPI path used by public Navimow
integrations such as ioBroker. It complements the consumer-app route mapping;
it does not expose weekly schedule CRUD.

## Source Evidence

- `segwaynavimow/navimow-sdk` commit `6596aa0`,
  `segwaynavimow/NavimowHA` commit `2331841`, and
  `TA2k/ioBroker.navimow` commit `dc0cf90` were checked on 2026-06-27.
- `TA2k/ioBroker.navimow` and `NavimowHA` implement OAuth code exchange and
  refresh against `/openapi/oauth/getAccessToken`.
- The ioBroker admin config uses the Navimow H5 login page with the
  `homeassistant` client and a localhost callback URL.
- The SDK and adapter use Bearer auth for OpenAPI read routes:
  `/openapi/smarthome/authList`,
  `/openapi/smarthome/getVehicleStatus`,
  `/openapi/smarthome/responseCommands`, and
  `/openapi/mqtt/userInfo/get/v2`.
- The same adapter exposes OpenAPI commands, but this repo does not implement
  those write routes.

Useful upstream pointers:

- [NavimowHA constants](https://github.com/segwaynavimow/NavimowHA/blob/2331841f1fbb5b28440228426469d2ceab0cbb28/custom_components/navimow/const.py#L10-L25)
- [NavimowHA OAuth code](https://github.com/segwaynavimow/NavimowHA/blob/2331841f1fbb5b28440228426469d2ceab0cbb28/custom_components/navimow/auth.py#L40-L66)
- [ioBroker login config](https://github.com/TA2k/ioBroker.navimow/blob/dc0cf901f941d4630d7e34d2499ce81354842381/admin/jsonConfig.json#L23-L25)
- [ioBroker token exchange/refresh](https://github.com/TA2k/ioBroker.navimow/blob/dc0cf901f941d4630d7e34d2499ce81354842381/main.js#L732-L772)
- [Navimow SDK API routes](https://github.com/segwaynavimow/navimow-sdk/blob/6596aa0a65dcf05ed248da87c36975f2ea236ab8/mower_sdk/api.py#L96-L128)

## Local Flow

Create an ignored local OAuth/OpenAPI config:

```bash
python tools/navimow_live_sync.py init-openapi-config \
  --output config/navimow-live-sync.local.json
```

Print the login URL:

```bash
python tools/navimow_live_sync.py oauth-login-url
```

Open it in a browser, sign in with the Navimow account, then copy the full
failed localhost redirect URL containing `?code=...`.

Exchange that code into an ignored local token file:

```bash
make oauth-exchange-code OAUTH_CODE='http://localhost:1/callback?code=PASTE_CODE_HERE'
```

The underlying CLI is `python tools/navimow_live_sync.py oauth-exchange-code
--config config/navimow-live-sync.local.json --code ...` if you prefer not to
use Make.

Use OAuth in the local live-sync config:

```json
{
  "auth": {
    "provider": "navimow-oauth",
    "tokenFile": "config/navimow-oauth.local.json"
  }
}
```

`init-openapi-config` writes this provider setting and removes the manual
`Authorization` header placeholder so OpenAPI sync does not require consumer-app
session headers.

Check the token without printing values:

```bash
python tools/navimow_live_sync.py oauth-doctor --config config/navimow-live-sync.local.json
```

The sync client refreshes the token automatically when it is close to expiry.
Manual refresh is also available:

```bash
python tools/navimow_live_sync.py oauth-refresh --config config/navimow-live-sync.local.json
```

Run discovery, configure the status request body from the sanitized auth-list
snapshot without printing device IDs, then sync status:

```bash
python tools/navimow_live_sync.py sync-once \
  --config config/navimow-live-sync.local.json \
  --db data/navimow.sqlite \
  --routes openapi-auth-list,openapi-mqtt-info

python tools/navimow_live_sync.py configure-openapi-status \
  --config config/navimow-live-sync.local.json \
  --db data/navimow.sqlite

python tools/navimow_live_sync.py sync-once \
  --config config/navimow-live-sync.local.json \
  --db data/navimow.sqlite \
  --routes openapi-vehicle-status,openapi-mqtt-info
```

After token setup, `make quickstart-live` wraps token readiness, discovery,
status configuration, status sync, status-only console generation when map
captures are absent, full viewer rebuild when map data exists, and a redacted
live-health report.

## Supported Read Routes

| Alias | Route | Method | Notes |
|---|---|---:|---|
| `openapi-auth-list` | `/openapi/smarthome/authList` | GET | Mower/device list. |
| `openapi-vehicle-status` | `/openapi/smarthome/getVehicleStatus` | POST | Requires a `devices` body in local config. |
| `openapi-mqtt-info` | `/openapi/mqtt/userInfo/get/v2` | GET | Returns MQTT metadata; credentials and URL-like values are sanitized from route snapshots. `mqtt-doctor` validates the metadata shape without printing values, and `mqtt-listen` can subscribe experimentally while storing only sanitized message summaries. |
| `openapi-response-commands` | `/openapi/smarthome/responseCommands` | POST | Command-result polling only; useful later if control routes are explicitly implemented. |

These routes are stored as compact `route_snapshot_records`. Auth-list and
vehicle-status are also promoted into safe typed `openapi_auth_snapshots` and
`openapi_status_snapshots` rows so the viewer, route coverage, and
activity-aware cadence can use device counts, hashed device identity, mower
state, and capacity without depending on generic route JSON. Raw OpenAPI device
IDs are not stored in those typed rows.

MQTT message handling is intentionally conservative. Stored `mqtt-message`
snapshots contain topic hashes, payload hashes, payload size/shape, sanitized
top-level keys, and allowlisted scalar status fields only. Known status fields
such as mower state, task/work state, current partition, mowing percentage,
path ID, event type, report time, capacity label, and battery percentage are
promoted into `mqtt_status_snapshots`. OpenAPI-style
`capacityRemaining: [{rawValue, unit: "PERCENTAGE"}]` payloads are normalized
to `capacityPercent` for the live Mower panel. Raw broker URLs, usernames,
passwords, client IDs, topic names, raw payloads, device IDs, and exact GPS
fields are not exported.

After `mqtt-listen` or `mqtt-replay-smoke`, run:

```bash
make mqtt-sample-report
make mqtt-readiness
```

The report uses only `mqtt_status_snapshots` and prints redacted enum counts,
active/idle/unknown classification, field coverage, and whether rows are real
or synthetic. `mqtt-readiness` is the stricter gate: it ignores synthetic replay
rows and requires fresh real active/idle samples plus battery, current-area, and
progress fields before MQTT evidence can drive polling decisions.

When no real MQTT message has arrived yet, use the deterministic local smoke
path:

```bash
make mqtt-replay-smoke
```

It injects one synthetic sanitized MQTT status event, refreshes the local
`navimow-live-status.json` feed, and exercises the same browser full-SSE
live-status path as live MQTT messages.
Clean synthetic replay rows afterward:

```bash
make mqtt-replay-clear
```

## Boundaries

- Token files are local-only and ignored by git.
- Do not paste access tokens, refresh tokens, MQTT passwords, or copied redirect
  URLs into chat.
- OpenAPI command routes remain out of scope until they are explicitly needed
  and reviewed separately from schedule/settings writes.
- `/openapi/smarthome/sendCommands` is deliberately refused by the read-only
  client.
- OpenAPI status/MQTT can improve live display, but it does not replace the
  consumer-app route work needed for area schedules, map detail, and schedule
  writes.
- MQTT listening is optional and experimental. It does not expose schedule CRUD,
  and it must not print or persist raw broker URLs, usernames, passwords, client
  IDs, topic names, raw payloads, device IDs, or exact GPS.
- The checked public consumer repos do not expose schedule read/write routes.
