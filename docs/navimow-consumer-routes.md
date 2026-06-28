# Navimow Terranox Consumer Route Catalog

This catalog is based on local Android app captures, app cache, decoded logs, and `data/navimow.sqlite`. It intentionally omits raw serials, tokens, signed URLs, exact GPS arrays, trace IDs, and raw command payloads.

For the current code-owned read allowlist, methods, cadences, surfaces, and
refused write/command patterns, run:

```bash
make live-route-catalog
```

The command is redacted and does not read local config, DB, network, tokens,
MQTT credentials, device IDs, signed URLs, or payloads.

## Read/Sync Routes

| Route | Method | Purpose | Local evidence | Notes |
|---|---:|---|---|---|
| `/vehicle/vehicle/index2` | POST | Device state/dashboard snapshot, map version sync, partition metadata | `http_cache_entries`, `device_state_snapshots` | Normalized enough for portal battery, health, state code, network, map version, and update-time display. Poll about every 30-60 seconds while the portal is open. |
| `/vehicle/vehicle/get-device-info` | POST | Static mower capabilities and limits | `http_cache_entries`, `device_info_snapshots` | Normalized enough for model/name, limits, firmware, speed lists, mowing height lists, and screen support. Refresh hourly or when `vehicle_info_update_time` changes. Redact account/commercial fields. |
| `/vehicle/vehicle/auth-list` | POST | Authorized mower list and mower-card state | `http_cache_entries`, app logs, `route_snapshot_records` | Useful for top-level mower selection/state. Compact snapshots suppress serials/account identifiers and expose only safe route freshness/card fields. |
| `/map/index/map-list` | POST | Map list for mower | app logs, `route_snapshot_records` | Compact route snapshot support exists. Use `make live-map-plan` to inspect local map/detail/artifact version state before running refresh commands; use `make live-map-delta` for the explicit state-first map refresh flow. |
| `/map/index/map-detail-compress` | POST | Full map and area detail | `map_detail_snapshots`, `map_detail_areas` | Response is base64-wrapped Zstandard JSON. Live `map-detail` sync decodes through the existing map-detail normalizer. Contains `sub_maps`, obstacles, local-coordinate polygons, and exact GPS anchors. Do not export GPS. |
| `/mowerbot/vehicle/common/get-iot-file` | POST | Short-lived map artifact URL for a map version | `map_resource_events`, `route_snapshot_records` | Compact snapshots redact URL-like values. Use signed URLs only for explicit local artifact download, then discard them. `map-sync-plan` reports whether a downloaded artifact for the latest metadata is missing; `--map-delta --download-map-artifacts` retains signed URLs only for the download window and clears them in cleanup. |
| Signed blob URL from `get-iot-file` | GET | Download terrain/map resource bundle | `map_artifacts`, `map_artifact_files`, `map_render_metadata` | Bundle contains terrain WebP and calibration JSON. |
| `/vehicle/vehicle/get-location` | POST | Live mower pose/progress | app logs, `live_location_snapshots` | Normalized into sanitized local pose/progress fields and rendered as a mower marker. Exact GPS fields are suppressed. Poll only while the mower is active or the portal map is open. |
| `/mowerbot/vehicle/vehicle/state` | POST | Richer mower live state/status semantics | app logs, `route_snapshot_records` | Compact snapshot support exists; state enum names, active task semantics, and current-area mapping need live classification. |
| `/vehicle/vehicle/get-vehicle-weather` | POST | Weather flags | app logs, `route_snapshot_records` | Contains rain/frost/snow/storm/high-temperature flags. Compact snapshot support exists; enums still need mapping. |
| `/vehicle/vehicle/get-today-plan` | POST | Current-day task/plan refresh | app logs, `route_snapshot_records` | Used around schedule saves. It is not the weekly schedule source of truth. Compact snapshot support exists. |
| `/vehicle/vehicle/set-list` | POST | Full settings snapshot | app logs, `area_setting_snapshots` | Partially normalized. Current global cutting height is visible as `height`; `cutterHeight` is also observed. Refresh every few minutes or when `vehicleSettingUpdateTime` changes. |
| `/vehicle/trail/get-path-info-time` | POST | Historical/path time index | app logs, `trail_time_snapshots`, `trail_time_entries` | Normalized enough for last observed mow time, completion percentage, and finished area per partition. |
| `/vehicle/trail/get-path-info-data-compress` | POST | Compressed path/trail data | app logs, `trail-data` route snapshot alias | Live sync stores only a sanitized shape/size/hash snapshot for change detection. Needed for exact mower trail display and stronger per-area attribution, but decode format is not mapped yet. |
| `/vehicle/firmware/get-new-firmware` | POST | Firmware update check | `http_cache_entries`, app logs, `route_snapshot_records` | Compact snapshot support exists; observed empty update list in captured sample. |
| `/vehicle/vehicle/get-component-maintenance` | POST | Component maintenance counters | app logs, `route_snapshot_records` | Compact snapshot support exists; units and reset behavior need mapping. |

## OAuth/OpenAPI Routes

These routes are exposed by the public Navimow SDK / Home Assistant / ioBroker
consumer integrations. They help with login, status, and MQTT, but they do not
expose weekly schedule CRUD.

| Route | Method | Purpose | Local support | Notes |
|---|---:|---|---|---|
| `/openapi/oauth/getAccessToken` | POST form | OAuth authorization-code exchange and refresh | Implemented in `tools/navimow_live_sync.py` as `oauth-exchange-code` and `oauth-refresh` | Token values are written only to ignored local token files and are never printed. |
| `/openapi/smarthome/authList` | GET | OpenAPI mower/device discovery | `openapi-auth-list` route snapshot alias | Uses Bearer token auth. |
| `/openapi/smarthome/getVehicleStatus` | POST | OpenAPI mower status cards | `openapi-vehicle-status` route snapshot alias | Requires a `devices` body in local config. |
| `/openapi/mqtt/userInfo/get/v2` | GET | MQTT host/path/credential discovery | `openapi-mqtt-info` route snapshot alias | MQTT credential and URL-like values are sanitized from snapshots. |
| `/openapi/smarthome/responseCommands` | POST | OpenAPI command-result polling | `openapi-response-commands` route snapshot alias | Read-after-command only; useful if control routes are deliberately added later. |
| `/openapi/smarthome/sendCommands` | POST | OpenAPI basic mower commands | Refused | Write/control route. Not used for schedules and not enabled by this read-only client. |

## Command Lifecycle Routes

| Route | Method | Purpose | Local evidence | Status |
|---|---:|---|---|---|
| `/mowerbot/vehicle/set/send` | POST | Generic IoT command send for schedule/settings writes | app logs, `command_envelopes` | Mapped as dry-run only. Exact signed/encrypted request body and headers remain the blocker. |
| `/vehicle/set/send` | POST | Alternate send route observed in logs | app logs | Relationship to `/mowerbot/vehicle/set/send` needs confirmation. |
| `/vehicle/set/response` | POST | Poll command result | app logs | Observed status polling until success. Full enum unknown. |
| `/vehicle/set/save-set-data` | POST | Persist setting after command success | app logs | Required field order and coverage for all settings unknown. |
| `/vehicle/set/index` | POST | Command/status bookkeeping | app logs | Exact role unclear. |
| `/vehicle/set/status` | POST | Command/status bookkeeping | app logs | Exact role unclear. |

## Schedule Notes

Terranox schedule source of truth is `workPlanV2` / app `planList`, not the older compact `plan` field.

- Days use app numbers `1..7`.
- Period times are 15-minute ticks from local midnight.
- `96` means `24:00`.
- Empty `partition_ids` means all-zone mowing.
- Non-empty `partition_ids` means customized zones.

Observed write flow:

1. App computes modified days only.
2. `setWorkPlanTotal` creates a partial `planList` JSON body.
3. `updateDeviceSetting` wraps the body as a string field.
4. App sends a command via `/mowerbot/vehicle/set/send`.
5. App polls `/vehicle/set/response`.
6. App calls `/vehicle/set/save-set-data`.
7. App saves/enables `mowingCycle=1`.

Until command signing/encryption is mapped, local tools should generate dry-run `planList` payloads and schedule drafts only.

The checked public OpenAPI/MQTT repos do not expose `workPlanV2`, `planList`, or
schedule CRUD; schedule work therefore remains on the consumer-app route path.

## Portal Feature Coverage

| Feature | Current local support | Missing work |
|---|---|---|
| Area polygons and names | Implemented from `map_detail_snapshots` / `map_detail_areas`. Live `map-detail` sync can decode `/map/index/map-detail-compress` into the existing tables. | Add version-driven delta sync so map detail refreshes automatically when map/update metadata changes. |
| Area schedule draft | Implemented as local draft/export from `workPlanV2`. | Direct live writes need command signing/encryption for the command lifecycle routes. |
| Battery and mower state | Implemented from normalized `/vehicle/vehicle/index2` snapshots. `tools/navimow_live_sync.py` can import read-only route responses or call whitelisted read routes from a local config. Compact route snapshots cover richer mower-state, auth-list, weather, firmware, and maintenance routes. | Capture/confirm current consumer auth headers for unattended live polling. Map route-specific enums and promote stable fields into typed tables. |
| Live mower position/progress | Implemented from sanitized `/vehicle/vehicle/get-location` rows, rendered as a local-position mower marker, and refreshable through the `poll --use-route-cadence --update-live-status` loop. MQTT listener output now includes safe class/count coverage for state, progress, battery, event, and command-result style payloads. | Map exact status enums/current-area semantics from live samples before reducing polling. |
| Cutting height | Current global height and supported height list are displayed; per-area map `height_set` is exported. | Decode whether `height_set=256` means inherit/default on this model; map any setting write command for height changes. |
| Last mow per area | Implemented from normalized `/vehicle/trail/get-path-info-time` entries; viewer shows completed, partial, or no mow in captured history. | Decode compressed trail data for exact path replay and stronger completion confidence. |
| Historical trails | Time index is normalized, and compressed trail responses can be captured as sanitized `trail-data` snapshots without storing the blob. | Decode compressed path format and add trail segment/point tables. |
| Maintenance counters | Route observed. | Normalize units and reset behavior. |

For the broader implementation backlog and next patch order, see
[`docs/implementation-gap-map.md`](implementation-gap-map.md).
