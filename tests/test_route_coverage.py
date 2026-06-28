import sys
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import build_navimow_map_viewer as viewer
import navimow_live_sync as live_sync


def test_viewer_route_catalog_covers_read_routes():
    catalog_paths = {route["path"] for route in viewer.ROUTE_CATALOG}
    missing = {
        alias: route["path"]
        for alias, route in live_sync.READ_ROUTES.items()
        if route["path"] not in catalog_paths
    }
    assert missing == {}


def test_consumer_route_docs_cover_read_routes_and_current_cadence():
    docs = (ROOT / "docs" / "navimow-consumer-routes.md").read_text(encoding="utf-8")
    for route in live_sync.READ_ROUTES.values():
        assert route["path"] in docs

    live_plan = (ROOT / "docs" / "navimow-live-sync-plan.md").read_text(encoding="utf-8")
    assert "weather=1200s" in live_plan
    assert "weather=900s" not in live_plan
    assert "--activity-aware-cadence" in live_plan
    assert "bounded by the poll interval" in live_plan
    assert "Trail segment tables remain future work" in live_plan


def test_route_catalog_json_matches_read_routes_and_blocked_writes(capsys):
    code = live_sync.main(["route-catalog", "--format", "json"])

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    rows = {row["alias"]: row for row in payload["readRoutes"]}
    assert set(rows) == set(live_sync.READ_ROUTES)
    for alias, route in live_sync.READ_ROUTES.items():
        row = rows[alias]
        assert row["method"] == route.get("method", "POST").upper()
        assert row["path"] == route["path"]
        assert row["kind"] == route["kind"]
        assert row["cadenceSeconds"] == route["cadenceSeconds"]
        assert row.get("activeCadenceSeconds") == route.get("activeCadenceSeconds")
        assert row.get("idleCadenceSeconds") == route.get("idleCadenceSeconds")
        assert row["readOnly"] is True
        assert row["surface"] == live_sync.route_surface(route["path"])
        live_sync.assert_read_only_path(route["path"])

    blocked_patterns = {row["pathPattern"] for row in payload["blockedWriteRoutes"] if row.get("pathPattern")}
    blocked_paths = {row["path"] for row in payload["blockedWriteRoutes"] if row.get("path")}
    assert set(live_sync.WRITE_ROUTE_PARTS) <= blocked_patterns
    assert set(live_sync.DOCUMENTED_WRITE_ROUTES) <= blocked_paths
    assert "/openapi/smarthome/sendCommands" in blocked_paths
    assert "/mowerbot/vehicle/set/send" in blocked_paths
    assert "/vehicle/set/response" in blocked_paths
    assert "/vehicle/set/save-set-data" in blocked_paths
    assert all(pattern not in {route["path"] for route in live_sync.READ_ROUTES.values()} for pattern in blocked_patterns)
    assert all(path not in {route["path"] for route in live_sync.READ_ROUTES.values()} for path in blocked_paths)


def test_route_catalog_summary_matches_catalog_rows():
    payload = live_sync.route_catalog_payload()
    summary = live_sync.route_catalog_summary(payload)
    read_routes = payload["readRoutes"]
    blocked_routes = payload["blockedWriteRoutes"]

    assert summary["readRouteCount"] == len(read_routes) == len(live_sync.READ_ROUTES)
    assert summary["blockedWriteRouteCount"] == len(blocked_routes)
    assert sum(summary["readSurfaces"].values()) == len(read_routes)
    assert sum(summary["readKinds"].values()) == len(read_routes)
    assert sum(summary["blockedWriteSurfaces"].values()) == len(blocked_routes)
    assert set(summary["openapiReadAliases"]) == {alias for alias in live_sync.READ_ROUTES if alias.startswith("openapi-")}
    assert set(summary["consumerReadAliases"]) == {alias for alias in live_sync.READ_ROUTES if not alias.startswith("openapi-")}


def test_route_catalog_preserves_drift_prone_cadences_and_surfaces(capsys):
    live_sync.main(["route-catalog", "--format", "json"])
    payload = json.loads(capsys.readouterr().out)
    rows = {row["alias"]: row for row in payload["readRoutes"]}

    assert rows["index2"]["cadenceSeconds"] == 45
    assert rows["index2"]["activeCadenceSeconds"] == 30
    assert rows["index2"]["idleCadenceSeconds"] == 90
    assert rows["get-location"]["cadenceSeconds"] == 10
    assert rows["get-location"]["activeCadenceSeconds"] == 5
    assert rows["get-location"]["idleCadenceSeconds"] == 60
    assert rows["mower-state"]["cadenceSeconds"] == 15
    assert rows["today-plan"]["cadenceSeconds"] == 120
    assert rows["weather"]["cadenceSeconds"] == 1200
    assert rows["openapi-vehicle-status"]["method"] == "POST"
    assert rows["openapi-vehicle-status"]["surface"] == "openapi"
    assert rows["openapi-vehicle-status"]["cadenceSeconds"] == 45
    assert rows["openapi-auth-list"]["method"] == "GET"
    assert rows["openapi-mqtt-info"]["method"] == "GET"
    assert rows["openapi-response-commands"]["surface"] == "openapi"
    assert rows["openapi-response-commands"]["cadenceSeconds"] == 5
    assert all(row["surface"] == "openapi" for alias, row in rows.items() if alias.startswith("openapi-"))
    assert all(row["surface"] == "consumer" for alias, row in rows.items() if not alias.startswith("openapi-"))


def test_route_catalog_markdown_is_redacted_and_runnable_without_config(capsys):
    code = live_sync.main(["route-catalog"])

    assert code == 0
    output = capsys.readouterr().out
    assert "# Navimow Live Sync Route Catalog" in output
    assert "| openapi-mqtt-info | openapi | GET | `/openapi/mqtt/userInfo/get/v2`" in output
    assert "| index2 | consumer | POST | `/vehicle/vehicle/index2`" in output
    assert "`/openapi/smarthome/sendCommands`" in output
    for forbidden in [
        "Authorization",
        "Cookie",
        "access_token",
        "refresh_token",
        "vehicle_sn",
        "signedUrl",
        "topicHash",
        "payloadSha256",
        "latitude",
    ]:
        assert forbidden not in output
