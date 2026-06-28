.PHONY: setup test ingest viewer viewer-no-satellite viewer-status-only viewer-if-map-data serve serve-local serve-live live-console live-console-no-mqtt live-ui-smoke quickstart-live live-plan live-route-catalog live-route-coverage live-setup-report completion-report completion-report-strict live-auth-discover consumer-session-report live-android-doctor live-android-capture live-doctor live-sync-dry-run live-map-plan live-map-delta live-map-artifacts trail-replay-report live-once-status live-once-viewer live-poll live-poll-status live-poll-viewer live-poll-viewer-no-satellite openapi-init openapi-preflight oauth-login-url oauth-exchange-code oauth-doctor oauth-refresh openapi-discover openapi-configure-status openapi-sync-status openapi-first-sync openapi-refresh-status openapi-refresh-viewer mqtt-doctor mqtt-listen mqtt-sample-report mqtt-readiness mqtt-ui-report mqtt-replay-smoke mqtt-replay-http-check mqtt-replay-clear schedule-export schedule-optimize-dry-run schedule-optimize-weekly schedule-validate schedule-payload live-health live-health-strict clean-generated

PYTHON ?= $(shell [ -x .venv/bin/python ] && printf .venv/bin/python || printf python3)
DB ?= data/navimow.sqlite
MAP_DIR ?= data/maps
VIEWER ?= viewer/navimow-map
PORT ?= 8765
LIVE_CONFIG ?= config/navimow-live-sync.local.json
RESPONSES_DIR ?=
OAUTH_CODE ?=
SERVE_FLAGS ?= --auto-port
SCHEDULE ?= viewer/navimow-map/schedule-draft.json
OPTIMIZED_SCHEDULE ?= viewer/navimow-map/schedule-optimized.json
OPTIMIZE_DAY ?= Tuesday
OPTIMIZE_FLAGS ?=
MAX_WEEKLY_HOURS ?= 80
NIGHT_ONLY_AREAS ?=
NIGHT_WINDOW ?= 22:00-06:00
DAY_WINDOW ?= 06:00-22:00
MAX_MESSAGES ?= 1
DURATION ?= 60
LIVE_CONSOLE_FLAGS ?= --openapi-preflight --refresh-openapi --strict-health --auto-port --with-mqtt
POLL_REALTIME_FLAGS ?= --refresh-trails-on-completion
MQTT_UI_FLAGS ?= --update-live-status
REPLAY_AREA_ID ?=
REPLAY_MOWING_PERCENTAGE ?= 55
REPLAY_BATTERY_SOC ?= 66
SYNC_RESPONSE_FLAGS = $(if $(RESPONSES_DIR),--responses-dir $(RESPONSES_DIR),)

setup:
	$(PYTHON) -m venv .venv
	. .venv/bin/activate && python -m pip install -r requirements.txt

test:
	PYTHONDONTWRITEBYTECODE=1 $(PYTHON) -B -m pytest -q -p no:cacheprovider tests

ingest:
	$(PYTHON) tools/navimow_state_store.py --db $(DB) ingest captures --download-maps
	$(PYTHON) tools/navimow_state_store.py --db $(DB) summary

viewer:
	$(PYTHON) tools/build_navimow_map_viewer.py --db $(DB) --output $(VIEWER)

viewer-no-satellite:
	$(PYTHON) tools/build_navimow_map_viewer.py --db $(DB) --output $(VIEWER) --no-satellite

viewer-status-only:
	$(PYTHON) tools/build_navimow_map_viewer.py --db $(DB) --output $(VIEWER) --status-only

viewer-if-map-data:
	@if $(PYTHON) -c "import pathlib, sqlite3, sys; p=pathlib.Path('$(DB)'); con=sqlite3.connect(p) if p.exists() else None; tables=set(row[0] for row in con.execute(\"SELECT name FROM sqlite_master WHERE type='table'\").fetchall()) if con else set(); detail=con.execute(\"SELECT COUNT(*) FROM map_detail_snapshots\").fetchone()[0] if 'map_detail_snapshots' in tables else 0; render=con.execute(\"SELECT COUNT(*) FROM map_render_metadata\").fetchone()[0] if 'map_render_metadata' in tables else 0; sys.exit(0 if detail and render else 1)"; then \
		$(PYTHON) tools/build_navimow_map_viewer.py --db $(DB) --output $(VIEWER); \
	else \
		printf 'OpenAPI live data is synced. Building a status-only viewer; full map viewer needs local map captures.\n'; \
		$(PYTHON) tools/build_navimow_map_viewer.py --db $(DB) --output $(VIEWER) --status-only; \
	fi

serve:
	$(PYTHON) -m http.server $(PORT) --directory $(VIEWER)

serve-local:
	$(PYTHON) -m http.server $(PORT) --bind 127.0.0.1 --directory $(VIEWER)

serve-live:
	$(PYTHON) tools/navimow_viewer_server.py --host 127.0.0.1 --port $(PORT) --directory $(VIEWER) $(SERVE_FLAGS)

live-console:
	$(PYTHON) tools/navimow_live_console.py --config $(LIVE_CONFIG) --db $(DB) --viewer-output $(VIEWER) --host 127.0.0.1 --port $(PORT) $(LIVE_CONSOLE_FLAGS)

live-console-no-mqtt:
	$(PYTHON) tools/navimow_live_console.py --config $(LIVE_CONFIG) --db $(DB) --viewer-output $(VIEWER) --host 127.0.0.1 --port $(PORT) --openapi-preflight --refresh-openapi --strict-health --auto-port

live-ui-smoke:
	$(PYTHON) tools/navimow_live_ui_smoke.py --viewer-output $(VIEWER)

quickstart-live: openapi-preflight oauth-doctor openapi-discover openapi-configure-status openapi-sync-status viewer-if-map-data
	$(PYTHON) tools/navimow_live_sync.py live-health --config $(LIVE_CONFIG) --db $(DB) --viewer-output $(VIEWER)
	@printf '\nStart the live console with: make live-console\n'
	@printf 'Use polling only with: make live-console-no-mqtt\n'
	@printf 'Open http://127.0.0.1:$(PORT)/\n'

live-plan:
	$(PYTHON) tools/navimow_live_sync.py plan --config $(LIVE_CONFIG)

live-route-catalog:
	$(PYTHON) tools/navimow_live_sync.py route-catalog

live-route-coverage:
	$(PYTHON) tools/navimow_live_sync.py route-coverage --db $(DB)

live-setup-report:
	$(PYTHON) tools/navimow_live_sync.py setup-report --config $(LIVE_CONFIG) --db $(DB) --viewer-output $(VIEWER)

completion-report:
	$(PYTHON) tools/navimow_live_sync.py completion-report --config $(LIVE_CONFIG) --db $(DB) --viewer-output $(VIEWER)

completion-report-strict:
	$(PYTHON) tools/navimow_live_sync.py completion-report --config $(LIVE_CONFIG) --db $(DB) --viewer-output $(VIEWER) --strict

live-auth-discover:
	$(PYTHON) tools/navimow_live_sync.py auth-discover --path captures

consumer-session-report:
	$(PYTHON) tools/navimow_live_sync.py consumer-session-report --config $(LIVE_CONFIG) --db $(DB) --capture-path captures

live-android-doctor:
	$(PYTHON) tools/navimow_android_live_setup.py doctor

live-android-capture:
	$(PYTHON) tools/navimow_android_live_setup.py run --duration 60

live-doctor:
	$(PYTHON) tools/navimow_live_sync.py doctor --config $(LIVE_CONFIG) --db $(DB)

live-sync-dry-run:
	$(PYTHON) tools/navimow_live_sync.py sync-once --config $(LIVE_CONFIG) --db $(DB) $(SYNC_RESPONSE_FLAGS) --dry-run

live-map-plan:
	$(PYTHON) tools/navimow_live_sync.py map-sync-plan --db $(DB)

live-map-delta:
	$(PYTHON) tools/navimow_live_sync.py sync-once --config $(LIVE_CONFIG) --db $(DB) $(SYNC_RESPONSE_FLAGS) --map-delta --download-map-artifacts --map-dir $(MAP_DIR)
	$(PYTHON) tools/build_navimow_map_viewer.py --db $(DB) --output $(VIEWER)

live-map-artifacts:
	$(PYTHON) tools/navimow_live_sync.py sync-once --config $(LIVE_CONFIG) --db $(DB) $(SYNC_RESPONSE_FLAGS) --routes get-iot-file --download-map-artifacts --map-dir $(MAP_DIR)
	$(PYTHON) tools/build_navimow_map_viewer.py --db $(DB) --output $(VIEWER)

trail-replay-report:
	$(PYTHON) tools/navimow_live_sync.py trail-replay-report --db $(DB)

live-once-viewer:
	$(PYTHON) tools/navimow_live_sync.py sync-once --config $(LIVE_CONFIG) --db $(DB) $(SYNC_RESPONSE_FLAGS)
	$(PYTHON) tools/build_navimow_map_viewer.py --db $(DB) --output $(VIEWER)

live-once-status:
	$(PYTHON) tools/navimow_live_sync.py sync-once --config $(LIVE_CONFIG) --db $(DB) $(SYNC_RESPONSE_FLAGS) --update-live-status --viewer-output $(VIEWER)

live-poll:
	$(PYTHON) tools/navimow_live_sync.py poll --config $(LIVE_CONFIG) --db $(DB) --interval 5 --use-route-cadence --activity-aware-cadence $(POLL_REALTIME_FLAGS) --max-iterations 999999

live-poll-status:
	$(PYTHON) tools/navimow_live_sync.py poll --config $(LIVE_CONFIG) --db $(DB) --interval 5 --use-route-cadence --activity-aware-cadence --update-live-status --viewer-output $(VIEWER) $(POLL_REALTIME_FLAGS) --max-iterations 999999

live-poll-viewer:
	$(PYTHON) tools/navimow_live_sync.py poll --config $(LIVE_CONFIG) --db $(DB) --interval 5 --use-route-cadence --activity-aware-cadence --auto-viewer-refresh --viewer-output $(VIEWER) $(POLL_REALTIME_FLAGS) --max-iterations 999999

live-poll-viewer-no-satellite:
	$(PYTHON) tools/navimow_live_sync.py poll --config $(LIVE_CONFIG) --db $(DB) --interval 5 --use-route-cadence --activity-aware-cadence --auto-viewer-refresh --viewer-output $(VIEWER) --no-satellite $(POLL_REALTIME_FLAGS) --max-iterations 999999

openapi-init:
	$(PYTHON) tools/navimow_live_sync.py init-openapi-config --output $(LIVE_CONFIG)

openapi-preflight:
	$(PYTHON) tools/navimow_live_sync.py openapi-preflight --config $(LIVE_CONFIG)

oauth-login-url:
	$(PYTHON) tools/navimow_live_sync.py oauth-login-url

oauth-exchange-code:
	@test -n "$(OAUTH_CODE)" || (printf 'Set OAUTH_CODE to the full localhost redirect URL or code.\\n' >&2; exit 2)
	$(PYTHON) tools/navimow_live_sync.py oauth-exchange-code --config $(LIVE_CONFIG) --code '$(OAUTH_CODE)'

oauth-doctor:
	$(PYTHON) tools/navimow_live_sync.py oauth-doctor --config $(LIVE_CONFIG)

oauth-refresh:
	$(PYTHON) tools/navimow_live_sync.py oauth-refresh --config $(LIVE_CONFIG)

openapi-discover:
	$(PYTHON) tools/navimow_live_sync.py sync-once --config $(LIVE_CONFIG) --db $(DB) $(SYNC_RESPONSE_FLAGS) --routes openapi-auth-list,openapi-mqtt-info

openapi-configure-status:
	$(PYTHON) tools/navimow_live_sync.py configure-openapi-status --config $(LIVE_CONFIG) --db $(DB)

openapi-sync-status:
	$(PYTHON) tools/navimow_live_sync.py sync-once --config $(LIVE_CONFIG) --db $(DB) $(SYNC_RESPONSE_FLAGS) --routes openapi-vehicle-status,openapi-mqtt-info

openapi-first-sync: openapi-discover openapi-configure-status openapi-sync-status viewer-if-map-data

openapi-refresh-status:
	$(PYTHON) tools/navimow_live_sync.py sync-once --config $(LIVE_CONFIG) --db $(DB) $(SYNC_RESPONSE_FLAGS) --routes openapi-vehicle-status,openapi-mqtt-info --update-live-status --viewer-output $(VIEWER)

openapi-refresh-viewer: openapi-sync-status viewer-if-map-data

mqtt-doctor: oauth-doctor
	$(PYTHON) tools/navimow_live_sync.py mqtt-doctor --config $(LIVE_CONFIG)

mqtt-listen:
	$(PYTHON) tools/navimow_live_sync.py mqtt-listen --config $(LIVE_CONFIG) --db $(DB) --update-live-status --viewer-output $(VIEWER) --max-messages $(MAX_MESSAGES) --duration $(DURATION)

mqtt-sample-report:
	$(PYTHON) tools/navimow_live_sync.py mqtt-sample-report --db $(DB)

mqtt-readiness:
	$(PYTHON) tools/navimow_live_sync.py mqtt-readiness --db $(DB)

mqtt-ui-report: oauth-doctor
	$(PYTHON) tools/navimow_live_sync.py mqtt-ui-report --config $(LIVE_CONFIG) --db $(DB) $(SYNC_RESPONSE_FLAGS) --viewer-output $(VIEWER) --max-messages $(MAX_MESSAGES) --duration $(DURATION) $(MQTT_UI_FLAGS)

mqtt-replay-smoke:
	$(PYTHON) tools/navimow_live_sync.py mqtt-replay-smoke --db $(DB) --viewer-output $(VIEWER) $(if $(REPLAY_AREA_ID),--area-id $(REPLAY_AREA_ID),) --mowing-percentage $(REPLAY_MOWING_PERCENTAGE) --battery-soc $(REPLAY_BATTERY_SOC) --update-live-status

mqtt-replay-http-check:
	$(PYTHON) tools/navimow_live_sync.py mqtt-replay-smoke --db $(DB) --viewer-output $(VIEWER) $(if $(REPLAY_AREA_ID),--area-id $(REPLAY_AREA_ID),) --mowing-percentage $(REPLAY_MOWING_PERCENTAGE) --battery-soc $(REPLAY_BATTERY_SOC) --update-live-status
	@printf 'Live-status endpoint should now include MQTT status. Check: http://127.0.0.1:$(PORT)/__navimow/live-status\n'

mqtt-replay-clear:
	$(PYTHON) tools/navimow_live_sync.py mqtt-replay-clear --db $(DB) --viewer-output $(VIEWER) --update-live-status

schedule-export:
	$(PYTHON) tools/navimow_schedule_cli.py export --db $(DB) --output $(SCHEDULE)

schedule-optimize-dry-run:
	$(PYTHON) tools/navimow_schedule_cli.py optimize --db $(DB) --schedule $(SCHEDULE) --output $(OPTIMIZED_SCHEDULE) --day $(OPTIMIZE_DAY) --stale-policy warn --explain $(OPTIMIZE_FLAGS)

schedule-optimize-weekly:
	$(PYTHON) tools/navimow_schedule_cli.py optimize-weekly --db $(DB) --schedule $(SCHEDULE) --output $(OPTIMIZED_SCHEDULE) --max-weekly-hours $(MAX_WEEKLY_HOURS) --night-only-area "$(NIGHT_ONLY_AREAS)" --night-window $(NIGHT_WINDOW) --day-window $(DAY_WINDOW) --stale-policy warn --explain $(OPTIMIZE_FLAGS)

schedule-validate:
	$(PYTHON) tools/navimow_schedule_cli.py validate --schedule $(OPTIMIZED_SCHEDULE)

schedule-payload:
	$(PYTHON) tools/navimow_schedule_cli.py payload --schedule $(OPTIMIZED_SCHEDULE)

live-health:
	$(PYTHON) tools/navimow_live_sync.py live-health --config $(LIVE_CONFIG) --db $(DB) --viewer-output $(VIEWER)

live-health-strict:
	$(PYTHON) tools/navimow_live_sync.py live-health --config $(LIVE_CONFIG) --db $(DB) --viewer-output $(VIEWER) --strict

clean-generated:
	rm -rf .pytest_cache tests/__pycache__ tools/__pycache__ viewer
