# Navimow Terranox Schedule CLI Plan

## Current Findings

- Official native schedule CRUD exists through NavimowFleet Open API, but it requires Fleet/dealer-provisioned organization credentials.
- The public consumer SDK and ioBroker/Home Assistant style integrations expose status, MQTT updates, and start/pause/resume/dock, not built-in schedule edits.
- The Android app exposes the schedule model in logs after opening the schedule screen:
  - `startPlan` is the global schedule enable flag.
  - `mowingCycle` is the mowing-cycle toggle.
  - `workPlanV2` is a weekly list of day plans.
  - Each day has `day`, `open`, and `periodList`.
  - Each period has `startTime`, `endTime`, and `partitionIds`.
  - Times are 15-minute ticks from local midnight; `96` means `24:00`.
- The compact `plan` field is a byte serialization:

```text
day_count
repeated per day:
  day_number
  open_flag
  period_count
  start_tick, end_tick
  ...
```
- After a controlled Android app edit, `workPlanV2` changed but the legacy `plan`
  field did not. Treat `workPlanV2` plus the app's `planList` write payload as
  the schedule source of truth for Terranox CLI work.
- The controlled edit changed day 3 from tick `90` (`22:30`) to tick `88`
  (`22:00`). The app generated this partial write payload:

```json
{"planList":[{"day":3,"open":1,"period":[{"end_time":88,"partition_ids":[],"start_time":16}]}]}
```

## Route Capture Status

- Simple mitmproxy failed for Navimow even after installing the user CA, because the app does not trust the proxy certificate path.
- The phone is not rooted and the app is not debuggable, so Frida attach and `am dumpheap` are blocked.
- Static decompilation confirms the APK is NetEase-wrapped: resources are visible, but app code such as `MowerSettingManager` and `WorkPlanFragment2` is unpacked only at runtime.
- Local cache shows the consumer API base host `https://navimow-fra.ninebot.com`.
- The app's own logger shows the schedule write flow:
  1. `WorkPlanFragment2` calculates modified days only.
  2. `MowerSettingManager.setWorkPlanTotal` creates a partial `planList` JSON body.
  3. `DeviceApiSource.updateDeviceSetting` wraps that body as:

```json
{"planList":"{\"planList\":[{\"day\":3,\"open\":1,\"period\":[{\"end_time\":88,\"partition_ids\":[],\"start_time\":16}]}]}"}
```

  4. The app sends a generic IoT command through `/mowerbot/vehicle/set/send`.
  5. It polls `/vehicle/set/response` until `status=3` and receives
     `respData={"count":1,"body":"01"}`.
  6. It calls `/vehicle/set/save-set-data` and then saves/enables
     `mowingCycle=1` through the same generic command flow.
- The logger also shows `/vehicle/vehicle/get-today-plan` checks around saving,
  but this appears to be task-state validation/refresh rather than the schedule
  write itself.
- Remaining unknown: the exact outgoing request body and headers for
  `/mowerbot/vehicle/set/send`. Log output currently shows only the response
  (`cmd_num`, status), not the sent encrypted/signed command payload.
- A patched/re-signed split APK archive was built locally at `patched/navimow-original-patched.apks`.
- The patched APK was installed and launched on 2026-06-27, but the app showed a
  tamper warning and exited before schedule navigation. The original APK set was
  reinstalled afterward, proxy was reset to `:0`, and the restored original app
  launched normally. Evidence is in `captures/patched-mitm-20260627-165700/`.

## Local Tools

- `tools/decode_navimow_plan.py` decodes the legacy compact `plan` hex field.
- `tools/navimow_schedule_capture.py` extracts and compares `workPlanV2`,
  `setWorkPlanTotal` payloads, `updateDeviceSetting` payloads, and HTTP endpoint
  events from captured app logs. It can also generate a non-sending schedule
  payload for a modified day:

```bash
tools/navimow_schedule_capture.py payload --day 3 --period 16-88
```
- Verified diff command:

```bash
tools/navimow_schedule_capture.py diff captures/navimow-logcat-schedule.txt captures/after-change-20260627-164524/logcat.txt
```

  Result: day 3 changed `16-90` (`04:00-22:30`) to `16-88`
  (`04:00-22:00`); legacy `plan` field had no change.
- `tools/navimow_state_store.py` builds the local SQLite state store for repeatable
  schedule/map/area sync:

```bash
python3 tools/navimow_state_store.py --db data/navimow.sqlite ingest captures --download-maps
python3 tools/navimow_state_store.py --db data/navimow.sqlite summary
```

- `tools/build_navimow_map_viewer.py` builds a sanitized local browser map from
  SQLite and the downloaded terrain artifact. It exports area polygons, names,
  settings, obstacles, and schedule membership without serials, signed URLs, raw
  payloads, or GPS arrays. It can also build a local Esri World Imagery satellite
  mosaic for a terrain/satellite toggle:

```bash
python3 tools/build_navimow_map_viewer.py --db data/navimow.sqlite --output viewer/navimow-map --satellite-zoom 20
python3 -m http.server 8765 --directory viewer/navimow-map
```

- `tools/navimow_schedule_cli.py` edits and validates local `schedule-draft.json`
  files and emits app-shaped dry-run `planList` payloads. It does not send
  commands to the mower:

```bash
python3 tools/navimow_schedule_cli.py validate --schedule viewer/navimow-map/schedule-draft.json
python3 tools/navimow_schedule_cli.py list --schedule viewer/navimow-map/schedule-draft.json
python3 tools/navimow_schedule_cli.py payload --schedule viewer/navimow-map/schedule-draft.json
python3 tools/navimow_schedule_cli.py add-period --schedule viewer/navimow-map/schedule-draft.json --day Tuesday --start 04:00 --end 22:00 --areas 1,3,5 --replace-day
```

- `docs/navimow-consumer-routes.md` documents the observed read/sync routes,
  command lifecycle routes, local evidence, known response shapes, and unknowns.

  Current DB output stores:
  - device state/info snapshots from app cache,
  - map resource polling events keyed by remote version and blob path,
  - downloaded terrain artifact metadata under `data/maps/`,
  - map bundle member rows for the WebP render and JSON calibration file,
  - render calibration (`width`, `height`, bounds, `pixel_per_meter`),
  - area-setting snapshots from `index2` and `MowerSettingBean`,
  - schedule snapshots and encrypted command envelopes from logs.
- Stable sync keys are `mapVersion`, `vehicleSettingUpdateTime`,
  `vehicle_info_update_time`, `partitionLength`, normalized `partitionIdList`,
  terrain `remote_version`/`local_version`, blob host/path, artifact sha256/size,
  and per-snapshot hashes. Signed blob URLs are used only to download the artifact
  and are cleared from SQLite after success.
- The current captures show `partitionLength=0`, empty `partitionIdList`, and no
  `mowingZone`/`mowingZoneList` content. That means there are no configured
  sub-areas/zones to parse yet, or the app did not fetch them in these captures.
- The downloaded terrain bundle for remote version `1782481675` is a zip with a
  WebP map render and a JSON calibration file, not full editable zone geometry.
  The parser is ready for richer JSON records such as `sub_maps`, `obstacles`,
  `tunnels`, and `vision_off_areas` if future captures/artifacts include them.
- Re-running the ingest over the same `captures/` tree is idempotent: the second
  run inserted zero new rows.

## Practical Next Paths

1. Safest official path: get a NavimowFleet dealer invite and API credentials, then implement a CLI around the documented Fleet schedule endpoints.
2. Low-disruption app mapping: continue controlled, reversible schedule edits in
   the current installed app to map payload variants:
   - add/remove a second period on one day,
   - toggle a day closed/open,
   - edit a zone-specific period if the UI exposes zones.
3. Higher-yield app mapping with patched APK is blocked on this phone by
   tamper detection. Do not repeat this route on the main phone unless we first
   have a specific anti-tamper bypass.
4. Clean lab path: use a rooted spare Android device or emulator that can place the mitmproxy CA in the system trust store and run Frida/dexdump without modifying the APK signature.
5. Runtime instrumentation path: use root/Magisk/LSPosed/Frida server on a lab
   device to hook the original signed app, avoiding the re-signing that triggers
   tamper detection.

## What I Need From You

- If using path 2: permission to make and immediately revert a small schedule change, ideally while the mower is docked and no scheduled task is active.
- If using path 3: permission to replace the installed Navimow app temporarily, plus confidence that you can log back into the Navimow account.
- If available: a rooted spare Android device would avoid touching the main phone's installed app.
- For the current controlled edit: please revert day 3 back from `22:00` to
  `22:30` if that was only a probe and you want the prior schedule restored.
