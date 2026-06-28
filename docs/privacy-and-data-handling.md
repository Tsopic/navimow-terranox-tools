# Privacy And Data Handling

This workspace contains mower/property data and app-capture material. Treat it as local-only unless explicitly sanitized.

Never commit or share:

- raw vehicle serials, auth IDs, tokens, refresh tokens, or session headers
- copied OAuth redirect URLs/codes and `config/navimow-oauth.local.json`
- trace IDs, signed URLs, blob paths, or URL signatures
- exact GPS coordinates or GPS anchor arrays
- raw API/cache/command payloads and command envelopes
- APK/APKS files, decompiled app output, MITM captures, logcat dumps, or Frida dumps
- `data/navimow.sqlite`, downloaded map bundles, generated viewers, or screenshots intended for sharing
- `captures/live-sync-*` logs and `config/navimow-live-sync.local.json` when Android capture is run with `--include-values`
- MQTT broker URLs, usernames, passwords, client IDs, topic names, raw payloads,
  and topic/payload examples copied from live logs

Android capture defaults:

- Use `tools/navimow_android_live_setup.py` instead of calling Frida directly.
- Default capture/parse mode is shape-only and should contain route names,
  header names, content type, content length, and JSON key/type shape only.
- `--include-values` is local-secret mode. It requires
  `--i-understand-local-secrets`; keep the resulting logs and config files on
  this machine.
- Value-bearing config files should end with `.local.json` and must remain
  ignored/private.

Allowed in local-only generated viewer output:

- area names and sizes
- local map-coordinate polygons
- schedule partition IDs
- sanitized mower state, battery, local pose, and completion percentages

Allowed in redacted CLI reports such as `map-sync-plan`:

- hashed device identifiers
- map/artifact version numbers
- snapshot freshness timestamps
- boolean local artifact/file presence
- next local command names

Not allowed in those reports:

- raw serials, map names, area names, or area geometry
- signed URLs, blob hosts, blob paths, signatures, or request bodies
- exact GPS anchors, latitude/longitude, RTK arrays, or raw map-detail payloads

`sync-once --map-delta` / `make live-map-delta` may retain signed map URLs only
inside the local download window. Cleanup must discard them after success,
skip, validation failure, network failure, or artifact parsing failure.

Live viewer server rules:

- `tools/navimow_viewer_server.py` binds to `127.0.0.1` by default.
- `/__navimow/events` may include only compact change metadata such as
  `version`, `layoutVersion`, `generatedAt`, and `observedAt`; it must not
  include file contents, tokens, route payloads, exact GPS, signed URLs, or
  paths outside the viewer root.
- `/__navimow/status` is for local diagnostics only and must remain metadata
  about generated viewer files, not raw data content.
- `/__navimow/live-status` may include only selected display fields from the
  generated `navimow-live-status.json`: status, battery, network signal,
  cutting height, local-pixel pose/progress, route freshness summaries, OpenAPI
  status/capacity, device count, MQTT configured/topic count, MQTT live state
  fields from the allowlist, sanitized area IDs with last-mow/current-progress
  and effective cutting-height deltas, today-plan, and weather summaries. It
  must not include area names, area geometry, schedules, raw route payloads,
  serials, device hashes, MQTT usernames, MQTT topics, signed URLs, or exact GPS.

MQTT listener rules:

- `mqtt-doctor` may print only present/missing fields, transport type, TLS flag,
  and topic count.
- `mqtt-listen` must not print or store raw topic names, broker URLs, usernames,
  passwords, client IDs, raw payloads, device IDs, exact GPS, or URL-like values.
- MQTT messages are local-only and experimental. Store topic hashes, payload
  hashes/sizes, safe key names, and allowlisted status-like scalar fields only
  until live payload shapes are classified.
- The typed `mqtt_status_snapshots` projection may expose only state/task/work
  status, battery percent/label, current partition id, mowing percentage, path
  id, event type, and normalized timestamps.

Before sharing screenshots or generated HTML, remember that satellite imagery, area names, and property layout can reveal the real property location even when GPS fields are removed.
