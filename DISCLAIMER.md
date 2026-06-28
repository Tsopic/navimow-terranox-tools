# Disclaimer

This project is unofficial and community-maintained. It is not affiliated with,
endorsed by, sponsored by, or supported by Navimow, Segway, Ninebot, or any
dealer network.

The tools in this repository are intended for local diagnostics, personal data
inspection, read-only sync experiments, map visualization, and dry-run schedule
planning. They are provided as-is, without warranty. You are responsible for
how you use them and for complying with applicable laws, service terms, safety
rules, and property/privacy expectations.

Robot mowers can cause property damage or injury if controlled incorrectly.
This repository deliberately refuses known schedule/settings write and command
routes unless a future implementation can prove the command envelope, signing,
rollback, and safety behavior. Review every generated schedule or command
payload before using it anywhere outside this local dry-run workflow.

Do not publish private mower data. Local captures, generated viewers, satellite
overlays, map geometry, area names, exact GPS, device IDs, tokens, MQTT
credentials, signed URLs, raw API payloads, APKs, and decompiled app output can
reveal account, property, or location information.
