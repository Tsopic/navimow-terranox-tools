let data = window.NAVIMOW_MAP_DATA;
data.map = data.map || {};
data.map.width = Number(data.map.width || 960);
data.map.height = Number(data.map.height || 540);
data.areas = Array.isArray(data.areas) ? data.areas : [];
data.obstacles = Array.isArray(data.obstacles) ? data.obstacles : [];
const svgNS = "http://www.w3.org/2000/svg";
const MAX_PERIODS_PER_DAY = 4;
const LIVE_EVENT_INTERVAL_SECONDS = 0.25;
const LIVE_REPLACE_INSIGHT_KEYS = [
  "openapiAuth",
  "openapiStatus",
  "mqtt",
  "mqttStatus",
  "mqttMessages",
  "consumerLiveState",
];
const LIVE_REPLACE_MOWER_KEYS = ["liveLocation"];

const state = {
  activeAreaId: data.areas[0]?.id ?? null,
  background: "terrain",
  zoom: 1,
  draft: structuredClone(data.scheduleDraft),
  optimizerPreview: null,
  liveConnection: "static",
};

const svg = document.getElementById("mapView");
const viewport = document.getElementById("mapViewport");
const details = document.getElementById("details");
const areaList = document.getElementById("areaList");
const planner = document.getElementById("planner");
const mowerDisplay = document.getElementById("mowerDisplay");
const routes = document.getElementById("routes");
const liveStatusStrip = document.getElementById("liveStatusStrip");

function el(name, attrs = {}, parent = null) {
  const node = document.createElementNS(svgNS, name);
  for (const [key, value] of Object.entries(attrs)) {
    if (key === "href") {
      node.setAttributeNS("http://www.w3.org/1999/xlink", "href", value);
    } else {
      node.setAttribute(key, value);
    }
  }
  if (parent) parent.appendChild(node);
  return node;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function deepMerge(target, source) {
  if (!source || typeof source !== "object" || Array.isArray(source)) return source;
  const output = { ...(target && typeof target === "object" && !Array.isArray(target) ? target : {}) };
  for (const [key, value] of Object.entries(source)) {
    if (value && typeof value === "object" && !Array.isArray(value)) {
      output[key] = deepMerge(output[key], value);
    } else if (value !== undefined) {
      output[key] = value;
    }
  }
  return output;
}

function pathFromPoints(points, close = true) {
  if (!points.length) return "";
  const body = points.map((point, index) => `${index === 0 ? "M" : "L"}${point[0]} ${point[1]}`).join(" ");
  return close ? `${body} Z` : body;
}

function areaById(id) {
  return data.areas.find((area) => area.id === id);
}

function hasMapGeometry() {
  return !data.map.statusOnly && data.areas.length > 0 && Boolean(data.map.backgrounds?.terrain || data.map.background);
}

function settingText(values) {
  if (!values || values.length === 0) return "n/a";
  return values.join(", ");
}

function formatMm(value) {
  return value === null || value === undefined ? "n/a" : `${value} mm`;
}

function latestMqttStatus(mower = data.mower || {}) {
  const status = mower.routeInsights?.mqttStatus;
  return status && typeof status === "object" && !Array.isArray(status) ? status : {};
}

function displayBatterySoc(mower = data.mower || {}) {
  const mqttStatus = latestMqttStatus(mower);
  return mqttStatus.batterySoc ?? mower.battery?.soc ?? "n/a";
}

function displayProgress(mower = data.mower || {}) {
  const mqttStatus = latestMqttStatus(mower);
  return mqttStatus.mowingPercentage ?? mower.liveLocation?.mowingPercentage ?? null;
}

function cuttingText(cutting) {
  if (!cutting) return "n/a";
  const height = formatMm(cutting.effectiveHeightMm ?? cutting.heightMm);
  if (height === "n/a") return "Not synced";
  if (cutting.areaHeightMm) return `${height} area`;
  return `${height} global`;
}

function lastMowText(lastMow) {
  if (!lastMow) return "Not synced yet";
  const percent = lastMow.partitionPercentage ?? null;
  const status = lastMow.status === "completed"
    ? "Completed"
    : lastMow.status === "partial"
      ? `Partial${percent !== null ? ` ${percent}%` : ""}`
      : lastMow.status === "no_mow_in_history"
        ? "No mow in captured history"
        : lastMow.status || "Unknown";
  if (lastMow.lastAt) {
    const parsed = new Date(lastMow.lastAt);
    if (!Number.isNaN(parsed.getTime())) {
      return `${status} · ${parsed.toLocaleString()}`;
    }
    return `${status} · ${lastMow.lastAt}`;
  }
  if (lastMow.status === "not_synced") return "Not synced yet";
  return status;
}

function coverageText(lastMow) {
  if (!lastMow || lastMow.partitionPercentage === null || lastMow.partitionPercentage === undefined) return "n/a";
  const finished = lastMow.finishedAreaM2 === null || lastMow.finishedAreaM2 === undefined ? null : `${Number(lastMow.finishedAreaM2).toFixed(1)} m2`;
  const area = lastMow.areaM2 === null || lastMow.areaM2 === undefined ? null : `${Number(lastMow.areaM2).toFixed(1)} m2`;
  return `${lastMow.partitionPercentage}%${finished && area ? ` · ${finished}/${area}` : ""}`;
}

function observedText(value) {
  if (!value) return "n/a";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleString();
}

function ageSeconds(value) {
  if (!value) return null;
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return null;
  return Math.max(0, Math.round((Date.now() - parsed.getTime()) / 1000));
}

function ageText(value) {
  const age = ageSeconds(value);
  if (age === null) return "age n/a";
  if (age < 90) return `${age}s old`;
  const minutes = Math.round(age / 60);
  if (minutes < 90) return `${minutes}m old`;
  return `${Math.round(minutes / 60)}h old`;
}

function statusClass(kind) {
  return kind === "ok" ? "is-ok" : kind === "warn" ? "is-warn" : kind === "bad" ? "is-bad" : "is-muted";
}

function statusPill(label, kind = "muted") {
  return `<span class="live-pill ${statusClass(kind)}">${escapeHtml(label)}</span>`;
}

function percentText(value) {
  return value === null || value === undefined ? "n/a" : `${value}%`;
}

function countListText(counts) {
  if (!counts || typeof counts !== "object" || Array.isArray(counts)) return "n/a";
  const entries = Object.entries(counts)
    .filter(([, value]) => Number(value) > 0)
    .sort(([left], [right]) => left.localeCompare(right));
  if (!entries.length) return "n/a";
  return entries.map(([key, value]) => `${key}: ${value}`).join(" · ");
}

function latestMqttText(summary) {
  const latest = summary?.latest || {};
  const classes = Array.isArray(latest.messageClasses) && latest.messageClasses.length
    ? latest.messageClasses.join(", ")
    : "n/a";
  const keys = Array.isArray(latest.payloadKeys) && latest.payloadKeys.length
    ? latest.payloadKeys.slice(0, 6).join(", ")
    : latest.payloadShape || "n/a";
  return `${classes} · ${keys} · ${observedText(latest.observedAt || summary?.observedAt)}`;
}

function formatActivityState(value) {
  if (value === null || value === undefined || value === "") return "n/a";
  const raw = String(value).trim();
  const normalized = raw
    .replace(/^is(?=[A-Z])/, "")
    .replace(/[_-]+/g, " ")
    .replace(/([a-z0-9])([A-Z])/g, "$1 $2")
    .toLowerCase()
    .trim();
  if (!normalized) return "n/a";
  return normalized.charAt(0).toUpperCase() + normalized.slice(1);
}

function liveAreaText(live) {
  if (!live?.active) return "";
  const stateText = formatActivityState(live.state || live.workStatus || live.taskStatus || "active");
  const progress = live.mowingPercentage === null || live.mowingPercentage === undefined
    ? "progress n/a"
    : `${live.mowingPercentage}%`;
  return `${stateText} · ${progress} · ${observedText(live.reportAt || live.observedAt)}`;
}

function applyAreaStatusSnapshot(areaStatus) {
  if (!areaStatus || typeof areaStatus !== "object" || Array.isArray(areaStatus)) return false;
  let changed = false;
  for (const area of data.areas) {
    const patch = areaStatus[String(area.id)];
    if (!patch || typeof patch !== "object" || Array.isArray(patch)) continue;
    if (patch.lastMow && typeof patch.lastMow === "object" && !Array.isArray(patch.lastMow)) {
      area.lastMow = deepMerge(area.lastMow || {}, patch.lastMow);
      changed = true;
    }
    if (patch.cutting && typeof patch.cutting === "object" && !Array.isArray(patch.cutting)) {
      area.cutting = deepMerge(area.cutting || {}, patch.cutting);
      changed = true;
    }
    if (patch.live && typeof patch.live === "object" && !Array.isArray(patch.live)) {
      area.live = structuredClone(patch.live);
      changed = true;
    }
  }
  return changed;
}

function mergeLiveAreaStatus(areaStatus) {
  return applyAreaStatusSnapshot(areaStatus);
}

function mergeLiveMowerStatus(current, patch) {
  if (!patch || typeof patch !== "object" || Array.isArray(patch)) return current || {};
  const output = deepMerge(current || {}, patch);
  if (patch.routeInsights && typeof patch.routeInsights === "object" && !Array.isArray(patch.routeInsights)) {
    output.routeInsights = {
      ...((current && current.routeInsights) || {}),
      ...patch.routeInsights,
    };
    for (const key of LIVE_REPLACE_INSIGHT_KEYS) {
      if (Object.prototype.hasOwnProperty.call(patch.routeInsights, key)) {
        output.routeInsights[key] = patch.routeInsights[key];
      } else {
        delete output.routeInsights[key];
      }
    }
  }
  for (const key of LIVE_REPLACE_MOWER_KEYS) {
    if (Object.prototype.hasOwnProperty.call(patch, key)) {
      output[key] = patch[key];
    }
  }
  return output;
}

function snapshotText(snapshot) {
  if (!snapshot) return "not synced";
  const count = snapshot.itemCount === null || snapshot.itemCount === undefined ? "n/a" : snapshot.itemCount;
  const keys = Array.isArray(snapshot.keys) && snapshot.keys.length
    ? snapshot.keys.slice(0, 5).join(", ")
    : snapshot.shape || "snapshot";
  return `${observedText(snapshot.observedAt)} · ${count} item${count === 1 ? "" : "s"} · ${keys}`;
}

function dayName(day) {
  return data.scheduleDraft.days.find((item) => item.day === day)?.dayName ?? `Day ${day}`;
}

function renderDayOptions(selectedDay = null) {
  return state.draft.days.map((day) => `
    <option value="${day.day}" ${day.day === selectedDay ? "selected" : ""}>${escapeHtml(day.dayName)}</option>
  `).join("");
}

function periodText(period) {
  return `${dayName(period.day)} ${period.start}-${period.end}`;
}

function computeDraftAreaSchedule(areaId) {
  const customPeriods = [];
  let allZonePeriodCount = 0;
  for (const day of state.draft.days) {
    for (const period of day.periods) {
      const normalized = { ...period, day: day.day, dayName: day.dayName };
      if (period.partitionIds.length === 0 || period.mode === "all_zones") {
        allZonePeriodCount += 1;
      } else if (period.partitionIds.includes(areaId)) {
        customPeriods.push(normalized);
      }
    }
  }
  return {
    customSelected: customPeriods.length > 0,
    customPeriods,
    allZonePeriodCount,
  };
}

function makePlanList(draft = state.draft) {
  return {
    planList: draft.days.map((day) => ({
      day: day.day,
      open: day.open,
      period: day.periods.map((period) => ({
        start_time: period.startTick,
        end_time: period.endTick,
        partition_ids: period.partitionIds,
      })),
    })),
  };
}

function renderHeader() {
  document.getElementById("mapMeta").textContent =
    `${data.map.name} · generated ${data.generatedAt.replace("T", " ").replace("+00:00", " UTC")}`;

  const metrics = [
    [data.map.areaCount, "Areas"],
    [`${data.map.totalAreaM2} m2`, "Mapped area"],
    [`${data.map.customSelectedAreaM2} m2`, "Custom zones"],
    [`${displayBatterySoc()}%`, "Battery"],
  ];

  document.getElementById("metricStrip").innerHTML = metrics
    .map(([value, label]) => `<div class="metric"><strong>${escapeHtml(value)}</strong><span>${escapeHtml(label)}</span></div>`)
    .join("");
  renderLiveStatusStrip();
}

function renderLiveStatusStrip() {
  if (!liveStatusStrip) return;
  const liveGeneratedAt = data.liveStatus?.statusGeneratedAt || data.generatedAt;
  const liveAge = ageSeconds(liveGeneratedAt);
  const feedKind = state.liveConnection === "connected"
    ? "ok"
    : state.liveConnection === "connecting" || state.liveConnection === "reconnecting"
      ? "warn"
      : state.liveConnection === "unavailable"
        ? "bad"
        : "muted";
  const freshKind = liveAge === null ? "warn" : liveAge <= 300 ? "ok" : liveAge <= 900 ? "warn" : "bad";
  const insights = data.mower?.routeInsights || {};
  const mqttStatus = insights.mqttStatus || {};
  const mqttMessages = insights.mqttMessages || {};
  const mqttCount = mqttMessages.totalMessages ?? 0;
  const pills = [
    statusPill(`Feed ${formatActivityState(state.liveConnection)}`, feedKind),
    statusPill(`Live ${ageText(liveGeneratedAt)}`, freshKind),
    statusPill(`OpenAPI ${insights.openapiStatus?.observedAt ? ageText(insights.openapiStatus.observedAt) : "not synced"}`, insights.openapiStatus?.observedAt ? "ok" : "warn"),
    statusPill(`MQTT ${mqttStatus.observedAt || mqttStatus.reportAt ? ageText(mqttStatus.reportAt || mqttStatus.observedAt) : mqttCount ? `${mqttCount} messages` : "waiting"}`, mqttStatus.observedAt || mqttStatus.reportAt ? "ok" : mqttCount ? "warn" : "muted"),
  ];
  liveStatusStrip.innerHTML = pills.join("");
}

function renderMap() {
  svg.setAttribute("viewBox", `0 0 ${data.map.width} ${data.map.height}`);
  svg.setAttribute("aria-label", `${data.map.name}, ${data.map.areaCount} mowing areas`);
  el("rect", {
    class: "map-empty-bg",
    x: 0,
    y: 0,
    width: data.map.width,
    height: data.map.height,
  }, svg);

  const terrainHref = data.map.backgrounds?.terrain || data.map.background;
  if (terrainHref) {
    el("image", {
      class: "terrain-image",
      href: terrainHref,
      x: 0,
      y: 0,
      width: data.map.width,
      height: data.map.height,
      preserveAspectRatio: "xMidYMid meet",
    }, svg);
  }

  if (data.map.backgrounds?.satellite) {
    el("image", {
      class: "satellite-image",
      href: data.map.backgrounds.satellite,
      x: 0,
      y: 0,
      width: data.map.width,
      height: data.map.height,
      preserveAspectRatio: "xMidYMid meet",
    }, svg);
  }

  if (!hasMapGeometry()) {
    const empty = el("g", { class: "map-empty-state" }, svg);
    const cx = data.map.width / 2;
    const cy = data.map.height / 2;
    el("circle", { class: "map-empty-ring", cx, cy: cy - 26, r: 32 }, empty);
    const title = el("text", {
      class: "map-empty-title",
      x: cx,
      y: cy + 28,
      "text-anchor": "middle",
    }, empty);
    title.textContent = data.map.statusOnly ? "Live status" : "No mapped areas";
    const subtitle = el("text", {
      class: "map-empty-subtitle",
      x: cx,
      y: cy + 56,
      "text-anchor": "middle",
    }, empty);
    subtitle.textContent = data.map.statusOnly ? "OpenAPI and MQTT snapshots are available" : "Map captures are not loaded";
    renderMowerMarker();
    return;
  }

  const obstacleLayer = el("g", { class: "obstacle-layer" }, svg);
  for (const obstacle of data.obstacles) {
    el("path", {
      class: "obstacle-shape",
      d: pathFromPoints(obstacle.points),
    }, obstacleLayer);
  }

  const areaLayer = el("g", { class: "area-layer" }, svg);
  for (const area of data.areas) {
    const schedule = computeDraftAreaSchedule(area.id);
    for (const element of area.elements) {
      const shape = el("path", {
        class: `area-shape ${schedule.customSelected ? "" : "is-not-custom"} ${area.live?.active ? "is-live" : ""}`,
        d: pathFromPoints(element.points),
        fill: area.color,
        "data-area-id": area.id,
      }, areaLayer);
      shape.addEventListener("click", () => selectArea(area.id));
      shape.addEventListener("mouseenter", () => highlightArea(area.id));
      shape.addEventListener("mouseleave", () => highlightArea(state.activeAreaId));
    }
    for (const tunnel of area.tunnels) {
      el("path", {
        class: "tunnel-line",
        d: pathFromPoints(tunnel.points, false),
      }, areaLayer);
    }
  }

  const labelLayer = el("g", { class: "label-layer" }, svg);
  for (const area of data.areas) {
    if (!area.label) continue;
    const group = el("g", { class: "label-badge", "data-area-id": area.id }, labelLayer);
    const text = el("text", {
      class: "area-label",
      x: area.label[0],
      y: area.label[1],
      "text-anchor": "middle",
    }, group);
    const line1 = el("tspan", { x: area.label[0], dy: 0 }, text);
    line1.textContent = area.name;
    const line2 = el("tspan", { class: "size", x: area.label[0], dy: 26 }, text);
    line2.textContent = `${area.sizeM2} m2`;
    const box = text.getBBox();
    const rect = el("rect", {
      class: "label-bg",
      x: box.x - 14,
      y: box.y - 9,
      width: box.width + 28,
      height: box.height + 18,
      rx: 6,
      ry: 6,
    });
    group.insertBefore(rect, text);
    const accent = el("line", {
      class: "label-accent",
      x1: box.x - 5,
      y1: box.y - 1,
      x2: box.x - 5,
      y2: box.y + box.height + 1,
      stroke: area.color,
    });
    group.insertBefore(accent, text);
  }

  renderMowerMarker();
}

function renderMowerMarker() {
  svg.querySelectorAll(".mower-marker").forEach((node) => node.remove());
  const location = data.mower?.liveLocation;
  if (!location?.positionPixel) return;
  const [x, y] = location.positionPixel;
  const headingDeg = location.postureTheta === null || location.postureTheta === undefined
    ? 0
    : (location.postureTheta * 180) / Math.PI;
  const marker = el("g", {
    class: "mower-marker",
    transform: `translate(${x} ${y}) rotate(${headingDeg})`,
    "aria-label": "Latest mower position",
  }, svg);
  el("circle", { class: "mower-marker-ring", r: 16 }, marker);
  el("path", { class: "mower-marker-arrow", d: "M 0 -18 L 9 10 L 0 5 L -9 10 Z" }, marker);
  el("circle", { class: "mower-marker-core", r: 5 }, marker);
}

function setZoom(nextZoom, focusX = viewport.clientWidth / 2, focusY = viewport.clientHeight / 2) {
  const previousZoom = state.zoom;
  const zoom = Math.max(0.16, Math.min(5, nextZoom));
  const localX = (viewport.scrollLeft + focusX) / previousZoom;
  const localY = (viewport.scrollTop + focusY) / previousZoom;
  state.zoom = zoom;
  svg.style.width = `${Math.round(data.map.width * zoom)}px`;
  svg.style.height = `${Math.round(data.map.height * zoom)}px`;
  viewport.scrollLeft = Math.max(0, localX * zoom - focusX);
  viewport.scrollTop = Math.max(0, localY * zoom - focusY);
}

function fitWidth() {
  const available = Math.max(320, viewport.clientWidth - 34);
  setZoom(available / data.map.width, 0, 0);
}

function bindZoom() {
  document.getElementById("zoomIn").addEventListener("click", () => setZoom(state.zoom * 1.25));
  document.getElementById("zoomOut").addEventListener("click", () => setZoom(state.zoom / 1.25));
  document.getElementById("zoomFit").addEventListener("click", fitWidth);
  document.getElementById("zoomReset").addEventListener("click", () => setZoom(1));
  viewport.addEventListener("wheel", (event) => {
    if (!event.ctrlKey && !event.metaKey && Math.abs(event.deltaY) < Math.abs(event.deltaX)) return;
    event.preventDefault();
    const rect = viewport.getBoundingClientRect();
    const focusX = event.clientX - rect.left;
    const focusY = event.clientY - rect.top;
    setZoom(state.zoom * (event.deltaY > 0 ? 0.9 : 1.1), focusX, focusY);
  }, { passive: false });

  let drag = null;
  viewport.addEventListener("pointerdown", (event) => {
    if (event.button !== 0) return;
    drag = { x: event.clientX, y: event.clientY, left: viewport.scrollLeft, top: viewport.scrollTop };
    viewport.classList.add("is-dragging");
    viewport.setPointerCapture(event.pointerId);
  });
  viewport.addEventListener("pointermove", (event) => {
    if (!drag) return;
    viewport.scrollLeft = drag.left - (event.clientX - drag.x);
    viewport.scrollTop = drag.top - (event.clientY - drag.y);
  });
  viewport.addEventListener("pointerup", () => {
    drag = null;
    viewport.classList.remove("is-dragging");
  });
}

function renderDetails(area) {
  if (!area) {
    details.innerHTML = `
      <h2>Area</h2>
      <p class="muted">${data.map.statusOnly ? "No captured map geometry in this database." : "No area selected."}</p>
    `;
    return;
  }
  const schedule = computeDraftAreaSchedule(area.id);
  const customPeriods = schedule.customPeriods || [];
  const allZoneCount = schedule.allZonePeriodCount || 0;
  const periodHtml = [
    ...customPeriods.map((period) => `
      <div class="period">
        <span>Custom zone</span>
        ${escapeHtml(periodText(period))}
      </div>
    `),
    ...(allZoneCount ? [`
      <div class="period all-zone-line">
        <span>All-zone schedule</span>
        ${allZoneCount} period${allZoneCount === 1 ? "" : "s"} include this area when all-zone mowing runs
      </div>
    `] : []),
  ].join("");

  details.innerHTML = `
    <h2>${escapeHtml(area.name)}</h2>
    <p class="muted">Area ${area.id} · ${escapeHtml(area.type)}${schedule.customSelected ? " · custom selected" : ""}</p>
    <div class="details-grid">
      <div class="detail-item live-detail"><span>Live status</span><strong>${escapeHtml(liveAreaText(area.live) || "Not active")}</strong></div>
      <div class="detail-item"><span>Size</span><strong>${area.sizeM2} m2</strong></div>
      <div class="detail-item"><span>Cutting height</span><strong>${escapeHtml(cuttingText(area.cutting))}</strong></div>
      <div class="detail-item"><span>Last mow</span><strong>${escapeHtml(lastMowText(area.lastMow))}</strong></div>
      <div class="detail-item"><span>Trail coverage</span><strong>${escapeHtml(coverageText(area.lastMow))}</strong></div>
      <div class="detail-item"><span>Map height set</span><strong>${escapeHtml(settingText(area.settings.heightSet))}</strong></div>
      <div class="detail-item"><span>Mow edge</span><strong>${escapeHtml(settingText(area.settings.mowEdge))}</strong></div>
      <div class="detail-item"><span>Obstacle edge</span><strong>${escapeHtml(settingText(area.settings.obstacleMowEdge))}</strong></div>
      <div class="detail-item"><span>Boundary type</span><strong>${escapeHtml(settingText(area.settings.boundaryType))}</strong></div>
      <div class="detail-item"><span>Rec angle</span><strong>${escapeHtml(settingText(area.settings.recBaseAngle))}</strong></div>
      <div class="detail-item"><span>Obstacles inside</span><strong>${area.settings.containedObstacles}</strong></div>
      <div class="detail-item"><span>Points</span><strong>${area.settings.pointCount}</strong></div>
    </div>
    <div class="period-list">${periodHtml || `<p class="muted">No schedule periods found for this area.</p>`}</div>
  `;
}

function renderAreaList() {
  if (!data.areas.length) {
    areaList.innerHTML = `
      <h2>Areas</h2>
      <p class="muted">No captured map areas.</p>
    `;
    renderDetails(null);
    return;
  }
  const sorted = [...data.areas].sort((a, b) => b.sizeM2 - a.sizeM2);
  areaList.innerHTML = `<h2>Areas</h2>` + sorted.map((area) => {
    const schedule = computeDraftAreaSchedule(area.id);
    const liveText = liveAreaText(area.live);
    return `
      <button class="area-button" type="button" data-area-id="${area.id}">
        <span class="swatch" style="background:${area.color}"></span>
        <span>
          <span class="area-name">${escapeHtml(area.name)}</span>
          <span class="area-meta">${area.sizeM2} m2 · ${escapeHtml(cuttingText(area.cutting))} · ${liveText ? escapeHtml(liveText) : escapeHtml(lastMowText(area.lastMow))}</span>
        </span>
        ${area.live?.active ? `<span class="badge live-badge">Now</span>` : schedule.customSelected ? `<span class="badge">Custom</span>` : `<span class="area-meta">All</span>`}
      </button>
    `;
  }).join("");

  for (const button of areaList.querySelectorAll(".area-button")) {
    button.addEventListener("click", () => selectArea(Number(button.dataset.areaId)));
    button.addEventListener("mouseenter", () => highlightArea(Number(button.dataset.areaId)));
    button.addEventListener("mouseleave", () => highlightArea(state.activeAreaId));
  }
}

function highlightArea(id) {
  for (const shape of svg.querySelectorAll(".area-shape")) {
    shape.classList.toggle("is-active", Number(shape.dataset.areaId) === id);
  }
  for (const button of areaList.querySelectorAll(".area-button")) {
    button.classList.toggle("is-active", Number(button.dataset.areaId) === id);
  }
}

function refreshAreaShapeClasses() {
  for (const shape of svg.querySelectorAll(".area-shape[data-area-id]")) {
    const areaId = Number(shape.dataset.areaId);
    const schedule = computeDraftAreaSchedule(areaId);
    const area = areaById(areaId);
    shape.classList.toggle("is-not-custom", !schedule.customSelected);
    shape.classList.toggle("is-live", Boolean(area?.live?.active));
  }
}

function renderAreaLiveState() {
  renderAreaList();
  renderDetails(areaById(state.activeAreaId));
  refreshAreaShapeClasses();
  highlightArea(state.activeAreaId);
}

function selectArea(id) {
  state.activeAreaId = id;
  const area = areaById(id);
  if (!area) return;
  renderDetails(area);
  highlightArea(id);
}

function renderMowerDisplay() {
  const mower = data.mower || {};
  const battery = mower.battery || {};
  const network = mower.network || {};
  const capabilities = mower.capabilities || {};
  const firmware = mower.firmware || {};
  const cutting = mower.cutting || {};
  const observed = mower.observed || {};
  const sync = mower.sync || {};
  const liveLocation = mower.liveLocation || {};
  const snapshots = mower.routeSnapshots || {};
  const insights = mower.routeInsights || {};
  const openapiAuth = insights.openapiAuth || {};
  const openapiStatus = insights.openapiStatus || {};
  const mqtt = insights.mqtt || {};
  const mqttStatus = insights.mqttStatus || {};
  const mqttMessages = insights.mqttMessages || {};
  const weather = insights.weather || {};
  const todayPlan = insights.todayPlan || {};
  const consumerLiveState = insights.consumerLiveState || {};
  const weatherFlags = weather.flags || {};
  const snapshotRows = [
    ["mower-state", "Live state"],
    ["today-plan", "Today plan"],
    ["weather", "Weather"],
    ["maintenance", "Maintenance"],
    ["firmware", "Firmware updates"],
    ["trail-data", "Trail replay data"],
    ["auth-list", "Mower list"],
    ["openapi-auth-list", "OpenAPI mowers"],
    ["openapi-vehicle-status", "OpenAPI status"],
    ["openapi-mqtt-info", "OpenAPI MQTT"],
    ["mqtt-message", "MQTT messages"],
    ["openapi-response-commands", "Command results"],
    ["map-list", "Map list"],
    ["get-iot-file", "Map artifact"],
  ];
  mowerDisplay.innerHTML = `
    <h2>${escapeHtml(mower.name || "Mower")}</h2>
    <p class="muted">${escapeHtml(mower.model || "Unknown model")} · state ${escapeHtml(mower.stateCode || "n/a")}</p>
    <div class="stats-grid">
      <div class="stat"><span>Battery</span><strong>${displayBatterySoc(mower)}%</strong></div>
      <div class="stat"><span>Health</span><strong>${battery.soh ?? "n/a"}%</strong></div>
      <div class="stat"><span>Network</span><strong>${network.signal ?? "n/a"}</strong></div>
      <div class="stat"><span>Cut height</span><strong>${formatMm(cutting.heightMm)}</strong></div>
      <div class="stat"><span>Progress</span><strong>${percentText(displayProgress(mower))}</strong></div>
      <div class="stat"><span>Plan max</span><strong>${capabilities.planMaxTimeHours ?? "n/a"} h</strong></div>
    </div>
    <div class="section-stack">
      <div class="period"><span>State sync</span>${escapeHtml(sync.batteryAndState?.route || "n/a")} · ${escapeHtml(observedText(observed.stateObservedAt))}</div>
      <div class="period"><span>Live pose</span>${escapeHtml(liveLocation.source || sync.liveLocation?.route || "n/a")} · ${escapeHtml(observedText(liveLocation.reportAt || liveLocation.observedAt))}</div>
      <div class="period"><span>OpenAPI status</span>${escapeHtml(openapiStatus.vehicleState || "n/a")} · ${percentText(openapiStatus.capacityPercent)} · ${escapeHtml(openapiStatus.capacityLabel || "no label")} · ${escapeHtml(observedText(openapiStatus.observedAt))}</div>
      <div class="period"><span>OpenAPI devices</span>${openapiAuth.deviceCount ?? "n/a"} configured · ${escapeHtml(observedText(openapiAuth.observedAt))}</div>
      <div class="period"><span>MQTT metadata</span>${mqtt.configured ? "Configured" : "Not synced"} · ${mqtt.topicCount ?? 0} topics · ${escapeHtml(observedText(mqtt.observedAt))}</div>
      <div class="period"><span>MQTT live status</span>${escapeHtml(formatActivityState(mqttStatus.state || mqttStatus.workStatus || mqttStatus.taskStatus))} · battery ${mqttStatus.batterySoc ?? "n/a"}% · area ${mqttStatus.currentPartitionId ?? "n/a"} · ${percentText(mqttStatus.mowingPercentage)} · ${escapeHtml(observedText(mqttStatus.reportAt || mqttStatus.observedAt))}</div>
      <div class="period"><span>MQTT message classes</span>${escapeHtml(countListText(mqttMessages.messageClasses))} · ${mqttMessages.totalMessages ?? 0} messages · ${mqttMessages.observedTopicCount ?? 0} routes</div>
      <div class="period"><span>Latest MQTT message</span>${escapeHtml(latestMqttText(mqttMessages))}</div>
      <div class="period"><span>Weather flags</span>${escapeHtml(Object.entries(weatherFlags).map(([key, value]) => `${key}: ${value}`).join(" · ") || "n/a")} · ${escapeHtml(observedText(weather.observedAt))}</div>
      <div class="period"><span>Today plan</span>${escapeHtml(todayPlan.status || "n/a")} · ${todayPlan.partitionCount ?? 0} areas · ${escapeHtml(observedText(todayPlan.observedAt))}</div>
      <div class="period"><span>Consumer live state</span>${escapeHtml(consumerLiveState.state || "n/a")} · battery ${consumerLiveState.batterySoc ?? "n/a"}% · area ${consumerLiveState.currentPartitionId ?? "n/a"}</div>
      <div class="period"><span>Cutting setting</span>${escapeHtml(cutting.sourceRoute || "n/a")} · current ${formatMm(cutting.heightMm)} · code ${cutting.cutterHeightCode ?? "n/a"}</div>
      <div class="period"><span>Firmware</span>ECU ${escapeHtml(firmware.ECU || "n/a")} · SW ${escapeHtml(firmware.SW || "n/a")} · VCU ${escapeHtml(firmware.VCU || "n/a")}</div>
      <div class="period"><span>Supported heights</span>${escapeHtml((cutting.supportedMm || capabilities.mowingHeightList || []).join(", ") || "n/a")}</div>
      <div class="period"><span>Line speeds</span>${escapeHtml((capabilities.lineSpeedList || []).join(", ") || "n/a")}</div>
      <div class="period"><span>Last mow source</span>${escapeHtml((sync.lastMowPerArea?.routes || []).join(" + ") || "n/a")} · ${escapeHtml(sync.lastMowPerArea?.status || "unknown")}</div>
      ${snapshotRows.map(([alias, label]) => `
        <div class="period"><span>${escapeHtml(label)}</span>${escapeHtml(snapshotText(snapshots[alias]))}</div>
      `).join("")}
    </div>
  `;
}

function renderRoutes() {
  routes.innerHTML = `
    <h2>Observed routes</h2>
    <p class="muted">Read routes are suitable for sync experiments. Write routes remain mapped as dry-run only until command signing is understood.</p>
    <div class="route-list">
      ${data.routeCatalog.map((route) => `
        <div class="route-item" data-access="${escapeHtml(route.access)}">
          <h3>${escapeHtml(route.method)} ${escapeHtml(route.path)}</h3>
          <p>${escapeHtml(route.purpose)}</p>
          <p class="route-meta">${escapeHtml(route.access)} · ${escapeHtml(route.evidence)}</p>
          <p class="muted">${escapeHtml(route.shape)}</p>
          <p class="muted">Unknown: ${escapeHtml(route.unknowns)}</p>
        </div>
      `).join("")}
    </div>
  `;
}

function renderPlanner() {
  const optimizerDefaults = data.scheduleOptimizer || {};
  const dayOptions = renderDayOptions();
  const optimizerDayOptions = renderDayOptions(Number(state.optimizerPreview?.day || optimizerDefaults.defaultDay || 3));
  const hasAreas = data.areas.length > 0;
  const areaChecks = hasAreas
    ? data.areas.map((area) => `
      <label><input type="checkbox" value="${area.id}" ${area.id === state.activeAreaId ? "checked" : ""}> ${escapeHtml(area.name)}</label>
    `).join("")
    : `<p class="muted">No captured area IDs.</p>`;
  const planList = JSON.stringify(makePlanList(), null, 2);
  const draftJson = JSON.stringify(state.draft, null, 2);

  planner.innerHTML = `
    <h2>Schedule planner</h2>
    <p class="muted">Draft changes stay local until exported.</p>
    <div class="form-grid">
      <label class="form-field"><span>Day</span><select id="plannerDay">${dayOptions}</select></label>
      <label class="form-field"><span>Mode</span><select id="plannerMode"><option value="custom" ${hasAreas ? "" : "disabled"}>Custom zones</option><option value="all_zones" ${hasAreas ? "" : "selected"}>All zones</option></select></label>
      <label class="form-field"><span>Start</span><input id="plannerStart" type="time" value="04:00" step="900"></label>
      <label class="form-field"><span>End</span><input id="plannerEnd" type="time" value="22:00" step="900"></label>
    </div>
    <div class="area-checkboxes" id="plannerAreas">${areaChecks}</div>
    <div class="button-row">
      <button type="button" id="addPeriod">Add period</button>
      <button type="button" id="replaceDay" class="secondary">Replace day</button>
      <button type="button" id="downloadDraft" class="secondary">Download draft</button>
      <button type="button" id="copyPlanList" class="secondary">Copy planList</button>
    </div>
    <section class="optimizer-panel">
      <h3>Optimizer dry run</h3>
      <p class="muted">Local preview only. It updates this draft; it does not contact the mower.</p>
      <div class="form-grid">
        <label class="form-field"><span>Day</span><select id="optimizerDay">${optimizerDayOptions}</select></label>
        <label class="form-field"><span>Start</span><input id="optimizerStart" type="time" value="${escapeHtml(optimizerDefaults.defaultStart || "04:00")}" step="900"></label>
        <label class="form-field"><span>End</span><input id="optimizerEnd" type="time" value="${escapeHtml(optimizerDefaults.defaultEnd || "22:00")}" step="900"></label>
        <label class="form-field"><span>m2/hour</span><input id="optimizerRate" type="number" min="1" step="10" value="${escapeHtml(optimizerDefaults.defaultM2PerHour || 250)}"></label>
        <label class="form-field"><span>Max periods</span><input id="optimizerMaxPeriods" type="number" min="1" max="${MAX_PERIODS_PER_DAY}" step="1" value="${escapeHtml(optimizerDefaults.defaultMaxPeriodsPerDay || MAX_PERIODS_PER_DAY)}"></label>
        <label class="form-field"><span>Min days</span><input id="optimizerMinDaysBetween" type="number" min="0" max="30" step="1" value="${escapeHtml(optimizerDefaults.defaultMinDaysBetween || 2)}"></label>
        <label class="form-field checkbox-field"><span>Override</span><label><input id="optimizerIgnoreBlockers" type="checkbox"> Ignore blockers</label></label>
      </div>
      <div class="button-row">
        <button type="button" id="previewOptimization">Preview optimizer</button>
        <button type="button" id="applyOptimization" class="secondary" ${state.optimizerPreview?.status === "proposed" ? "" : "disabled"}>Apply to draft</button>
        <button type="button" id="clearOptimization" class="secondary" ${state.optimizerPreview ? "" : "disabled"}>Clear preview</button>
      </div>
      <div class="optimizer-preview">${renderOptimizerPreview()}</div>
    </section>
    <h3>Draft periods</h3>
    <div class="period-editor-list">${renderDraftPeriods()}</div>
    <div class="planner-output">
      <span>planList dry-run payload</span>
      <textarea id="plannerOutput" name="plannerOutput" spellcheck="false">${escapeHtml(planList)}</textarea>
    </div>
    <div class="planner-output">
      <span>schedule-draft.json</span>
      <textarea id="draftOutput" name="draftOutput" spellcheck="false">${escapeHtml(draftJson)}</textarea>
    </div>
  `;

  document.getElementById("addPeriod").addEventListener("click", () => addPlannerPeriod(false));
  document.getElementById("replaceDay").addEventListener("click", () => addPlannerPeriod(true));
  document.getElementById("previewOptimization").addEventListener("click", () => {
    state.optimizerPreview = buildOptimizerPreview();
    renderPlanner();
  });
  document.getElementById("applyOptimization").addEventListener("click", applyOptimizerPreview);
  document.getElementById("clearOptimization").addEventListener("click", () => {
    state.optimizerPreview = null;
    renderPlanner();
  });
  document.getElementById("downloadDraft").addEventListener("click", downloadDraft);
  document.getElementById("copyPlanList").addEventListener("click", async () => {
    await navigator.clipboard?.writeText(JSON.stringify(makePlanList(), null, 2));
  });
  for (const button of planner.querySelectorAll("[data-delete-period]")) {
    button.addEventListener("click", () => deleteDraftPeriod(Number(button.dataset.day), Number(button.dataset.index)));
  }
}

function renderDraftPeriods() {
  const rows = [];
  for (const day of state.draft.days) {
    day.periods.forEach((period, index) => {
      const names = period.partitionIds.length
        ? period.partitionIds.map((id) => areaById(id)?.name || `Area ${id}`).join(", ")
        : "All zones";
      rows.push(`
        <div class="period">
          <span>${escapeHtml(day.dayName)} · ${escapeHtml(period.mode)}</span>
          ${escapeHtml(period.start)}-${escapeHtml(period.end)} · ${escapeHtml(names)}
          <div class="button-row"><button type="button" class="secondary" data-delete-period data-day="${day.day}" data-index="${index}">Delete</button></div>
        </div>
      `);
    });
  }
  return rows.join("") || `<p class="muted">No periods in draft.</p>`;
}

function timeToTick(value) {
  if (value === "24:00") return 96;
  const parts = String(value || "").split(":").map(Number);
  if (parts.length !== 2) return null;
  const [hours, minutes] = parts;
  if (!Number.isInteger(hours) || !Number.isInteger(minutes)) return null;
  if (hours < 0 || hours > 23 || minutes < 0 || minutes > 59 || minutes % 15 !== 0) return null;
  return (hours * 60 + minutes) / 15;
}

function tickToTime(tick) {
  if (tick === 96) return "24:00";
  const minutes = tick * 15;
  return `${String(Math.floor(minutes / 60)).padStart(2, "0")}:${String(minutes % 60).padStart(2, "0")}`;
}

function isAdverseFlag(value) {
  if (value === null || value === undefined || value === "" || value === false) return false;
  if (typeof value === "number") return value > 0;
  const text = String(value).trim().toLowerCase();
  return !["0", "false", "none", "normal", "clear", "ok", "idle", "unknown"].includes(text);
}

function isActiveState(value) {
  if (!value) return false;
  const raw = String(value).trim();
  const normalized = raw.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "");
  const camelTokens = raw.replace(/(?!^)(?=[A-Z])/g, "_").toLowerCase();
  const tokens = new Set([
    ...camelTokens.split(/[^a-z0-9]+/).filter(Boolean),
    ...normalized.split("_").filter(Boolean),
  ]);
  if (["notrunning", "idle", "docked", "dock", "charging", "charge", "standby", "parked", "park", "stopped", "stop", "offline", "ready", "complete", "completed"].includes(normalized)) return false;
  if (tokens.has("not") && (tokens.has("running") || tokens.has("mowing") || tokens.has("working"))) return false;
  if ([...tokens].some((token) => ["idle", "docked", "dock", "charging", "charge", "standby", "parked", "park", "stopped", "stop", "offline", "ready", "complete", "completed"].includes(token))) return false;
  if (["isrunning", "running", "mowing", "mow", "working", "work", "returning", "return", "returnhome", "paused", "pause", "moving", "active", "tasking"].includes(normalized)) return true;
  if ([...tokens].some((token) => ["running", "mowing", "mow", "working", "work", "returning", "return", "paused", "pause", "moving", "active", "tasking"].includes(token))) return true;
  return false;
}

function optimizerBlockers() {
  const insights = data.mower?.routeInsights || {};
  const flags = insights.weather?.flags || {};
  const blockers = Object.entries(flags)
    .filter(([, value]) => isAdverseFlag(value))
    .map(([key]) => `weather ${key}`);
  if (isActiveState(insights.openapiStatus?.vehicleState)) blockers.push(`OpenAPI state ${insights.openapiStatus.vehicleState}`);
  if (isActiveState(insights.consumerLiveState?.state)) blockers.push(`consumer state ${insights.consumerLiveState.state}`);
  if (isActiveState(insights.todayPlan?.status)) blockers.push(`today plan ${insights.todayPlan.status}`);
  return blockers;
}

function validateDraft(draft = state.draft) {
  const errors = [];
  if (!Array.isArray(draft.days) || draft.days.length !== 7) return ["draft must contain 7 days"];
  const seen = new Set();
  for (const day of draft.days) {
    if (seen.has(day.day)) errors.push(`duplicate day ${day.day}`);
    seen.add(day.day);
    if (!Number.isInteger(day.day) || day.day < 1 || day.day > 7) errors.push(`invalid day ${day.day}`);
    if (Array.isArray(day.periods) && day.periods.length > MAX_PERIODS_PER_DAY) {
      errors.push(`${day.dayName || day.day} supports at most ${MAX_PERIODS_PER_DAY} periods`);
    }
    let previousEnd = -1;
    for (const [index, period] of (day.periods || []).entries()) {
      if (!Number.isInteger(period.startTick) || !Number.isInteger(period.endTick)) {
        errors.push(`${day.dayName || day.day} period ${index + 1} has invalid ticks`);
        continue;
      }
      if (period.startTick < 0 || period.startTick >= period.endTick || period.endTick > 96) {
        errors.push(`${day.dayName || day.day} period ${index + 1} has invalid time range`);
      }
      if (period.startTick < previousEnd) errors.push(`${day.dayName || day.day} period ${index + 1} overlaps previous period`);
      previousEnd = Math.max(previousEnd, period.endTick);
      if (!Array.isArray(period.partitionIds) || period.partitionIds.some((item) => !Number.isInteger(item))) {
        errors.push(`${day.dayName || day.day} period ${index + 1} has invalid area IDs`);
      }
      if (!["custom", "all_zones"].includes(period.mode)) errors.push(`${day.dayName || day.day} period ${index + 1} has invalid mode`);
      if (period.mode === "all_zones" && period.partitionIds.length) errors.push(`${day.dayName || day.day} all-zone period has area IDs`);
      if (period.mode === "custom" && !period.partitionIds.length) errors.push(`${day.dayName || day.day} custom period has no area IDs`);
    }
  }
  return errors;
}

function scoreAreaForOptimization(area) {
  const lastMow = area.lastMow || {};
  const size = Number(area.sizeM2 || 0);
  const completion = Number(lastMow.partitionPercentage || 0);
  const height = Number(area.cutting?.effectiveHeightMm || 0);
  let score = size / 25;
  const reasons = [`area ${size.toFixed(1)} m2`];
  if (lastMow.status === "no_mow_in_history" || !lastMow.lastAt) {
    score += 120;
    reasons.push("no mow history");
  } else if (lastMow.status === "partial") {
    score += 90 + Math.max(0, 100 - completion) / 2;
    reasons.push(`partial completion ${Math.round(completion)}%`);
  } else {
    const parsed = new Date(lastMow.lastAt);
    if (Number.isNaN(parsed.getTime())) {
      score += 40;
      reasons.push("missing last mow timestamp");
    } else {
      const ageDays = Math.max(0, (Date.now() - parsed.getTime()) / 86400000);
      score += Math.min(ageDays, 30) * 3;
      reasons.push(`${ageDays.toFixed(1)} days since last mow`);
    }
  }
  if (height >= 60) {
    score += 8;
    reasons.push(`higher cut ${Math.round(height)} mm`);
  }
  return { score: Math.round(score * 100) / 100, reasons };
}

function estimateOptimizerDurationTicks(area, m2PerHour) {
  const size = Math.max(1, Number(area.sizeM2 || 1));
  const height = Number(area.cutting?.effectiveHeightMm || 50);
  const complexity = 1 + Math.max(0, height - 50) / 120;
  return Math.max(1, Math.ceil((size / Math.max(m2PerHour, 1)) * complexity * 4));
}

function buildOptimizerPreview() {
  const draftErrors = validateDraft();
  if (draftErrors.length) {
    return { status: "blocked", blockers: draftErrors.map((error) => `draft ${error}`), periods: [], candidates: [] };
  }
  const day = Number(document.getElementById("optimizerDay").value);
  const startTick = timeToTick(document.getElementById("optimizerStart").value);
  const endTick = timeToTick(document.getElementById("optimizerEnd").value);
  const m2PerHour = Number(document.getElementById("optimizerRate").value || 250);
  const maxPeriods = Math.max(1, Math.min(MAX_PERIODS_PER_DAY, Number(document.getElementById("optimizerMaxPeriods").value || MAX_PERIODS_PER_DAY)));
  const minDaysBetween = Number(document.getElementById("optimizerMinDaysBetween").value || 0);
  const ignoreBlockers = document.getElementById("optimizerIgnoreBlockers").checked;
  if (startTick === null || endTick === null || startTick >= endTick) {
    return { status: "blocked", day, startTick, endTick, blockers: ["optimizer window must use valid 15-minute start and end times"], periods: [], candidates: [] };
  }
  const blockers = optimizerBlockers();
  if (blockers.length && !ignoreBlockers) {
    return { status: "blocked", day, startTick, endTick, blockers, periods: [], candidates: [] };
  }
  const candidates = data.areas.map((area) => {
    const scored = scoreAreaForOptimization(area);
    const durationTicks = estimateOptimizerDurationTicks(area, m2PerHour);
    return {
      areaId: area.id,
      name: area.name,
      sizeM2: area.sizeM2,
      durationTicks,
      durationMinutes: durationTicks * 15,
      lastMowStatus: area.lastMow?.status || "unknown",
      completionPercent: area.lastMow?.partitionPercentage ?? null,
      effectiveHeightMm: area.cutting?.effectiveHeightMm ?? null,
      ...scored,
    };
  }).filter((candidate) => {
    if (!minDaysBetween || candidate.lastMowStatus !== "completed") return true;
    const area = areaById(candidate.areaId);
    const parsed = new Date(area?.lastMow?.lastAt || "");
    if (Number.isNaN(parsed.getTime())) return true;
    return (Date.now() - parsed.getTime()) / 86400000 >= minDaysBetween;
  }).sort((a, b) => b.score - a.score || b.sizeM2 - a.sizeM2 || a.areaId - b.areaId);

  const periods = [];
  let cursor = startTick;
  for (const candidate of candidates) {
    if (periods.length >= maxPeriods) break;
    if (cursor + candidate.durationTicks > endTick) continue;
    periods.push({
      start: tickToTime(cursor),
      end: tickToTime(cursor + candidate.durationTicks),
      startTick: cursor,
      endTick: cursor + candidate.durationTicks,
      partitionIds: [candidate.areaId],
      mode: "custom",
      optimizer: {
        score: candidate.score,
        durationMinutes: candidate.durationMinutes,
        reasons: candidate.reasons,
      },
    });
    cursor += candidate.durationTicks;
  }

  return {
    status: "proposed",
    day,
    generatedAt: new Date().toISOString(),
    preferredWindow: `${tickToTime(startTick)}-${tickToTime(endTick)}`,
    minDaysBetween,
    blockersIgnored: ignoreBlockers ? blockers : [],
    candidates,
    periods,
  };
}

function applyOptimizerPreview() {
  if (!state.optimizerPreview || state.optimizerPreview.status !== "proposed") return;
  const day = state.draft.days.find((item) => item.day === state.optimizerPreview.day);
  day.open = state.optimizerPreview.periods.length ? 1 : 0;
  day.periods = structuredClone(state.optimizerPreview.periods);
  state.draft.optimization = {
    generatedAt: state.optimizerPreview.generatedAt,
    status: "proposed",
    dryRunOnly: true,
    blockersIgnored: state.optimizerPreview.blockersIgnored,
    plan: {
      day: state.optimizerPreview.day,
      preferredWindow: state.optimizerPreview.preferredWindow,
      candidateCount: state.optimizerPreview.candidates.length,
      selectedAreaCount: state.optimizerPreview.periods.length,
    },
  };
  renderPlanner();
  renderAreaList();
  renderDetails(areaById(state.activeAreaId));
  refreshAreaShapeClasses();
}

function renderOptimizerPreview() {
  const preview = state.optimizerPreview;
  if (!preview) return `<p class="muted">No optimizer preview yet.</p>`;
  if (preview.status === "blocked") {
    return `
      <div class="period optimizer-warning">
        <span>Blocked</span>
        ${escapeHtml(preview.blockers.join(" · ") || "status unavailable")}
      </div>
    `;
  }
  const rows = preview.periods.map((period) => {
    const area = areaById(period.partitionIds[0]);
    const meta = period.optimizer || {};
    return `
      <div class="period">
        <span>${escapeHtml(area?.name || `Area ${period.partitionIds[0]}`)} · score ${escapeHtml(meta.score ?? "n/a")}</span>
        ${escapeHtml(period.start)}-${escapeHtml(period.end)} · ${escapeHtml((meta.reasons || []).join(" · "))}
      </div>
    `;
  }).join("");
  return `
    <div class="optimizer-summary">
      <div class="stat"><span>Areas considered</span><strong>${preview.candidates.length}</strong></div>
      <div class="stat"><span>Proposed periods</span><strong>${preview.periods.length}</strong></div>
    </div>
    ${preview.blockersIgnored?.length ? `<div class="period optimizer-warning"><span>Ignored blockers</span>${escapeHtml(preview.blockersIgnored.join(" · "))}</div>` : ""}
    ${rows || `<p class="muted">No periods fit this window.</p>`}
  `;
}

function addPlannerPeriod(replaceDay) {
  const day = state.draft.days.find((item) => item.day === Number(document.getElementById("plannerDay").value));
  const mode = document.getElementById("plannerMode").value;
  const start = document.getElementById("plannerStart").value;
  const end = document.getElementById("plannerEnd").value;
  const startTick = timeToTick(start);
  const endTick = timeToTick(end);
  const partitionIds = mode === "all_zones"
    ? []
    : [...document.querySelectorAll("#plannerAreas input:checked")].map((item) => Number(item.value));
  if (startTick === null || endTick === null || startTick >= endTick || (mode === "custom" && !partitionIds.length)) {
    state.optimizerPreview = {
      status: "blocked",
      blockers: ["manual period needs valid 15-minute times and at least one custom area"],
      periods: [],
      candidates: [],
    };
    renderPlanner();
    return;
  }
  if (!replaceDay && day.periods.length >= MAX_PERIODS_PER_DAY) {
    state.optimizerPreview = {
      status: "blocked",
      blockers: [`${day.dayName || day.day} already has ${MAX_PERIODS_PER_DAY} periods`],
      periods: [],
      candidates: [],
    };
    renderPlanner();
    return;
  }
  const period = {
    start,
    end,
    startTick,
    endTick,
    partitionIds,
    mode,
  };
  if (replaceDay) day.periods = [];
  day.open = 1;
  day.periods.push(period);
  day.periods.sort((a, b) => a.startTick - b.startTick);
  state.optimizerPreview = null;
  renderPlanner();
  renderAreaList();
  renderDetails(areaById(state.activeAreaId));
  refreshAreaShapeClasses();
}

function deleteDraftPeriod(dayNumber, index) {
  const day = state.draft.days.find((item) => item.day === dayNumber);
  day.periods.splice(index, 1);
  day.open = day.periods.length ? 1 : 0;
  state.optimizerPreview = null;
  renderPlanner();
  renderAreaList();
  renderDetails(areaById(state.activeAreaId));
  refreshAreaShapeClasses();
}

function downloadDraft() {
  const blob = new Blob([JSON.stringify(state.draft, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = "schedule-draft.json";
  link.click();
  URL.revokeObjectURL(url);
}

function bindControls() {
  const satelliteButton = document.getElementById("satelliteMode");
  if (!data.map.backgrounds?.satellite) {
    satelliteButton.disabled = true;
  }
  document.getElementById("terrainMode").addEventListener("click", () => setBackground("terrain"));
  satelliteButton.addEventListener("click", () => setBackground("satellite"));
  document.getElementById("toggleLabels").addEventListener("change", (event) => {
    document.body.classList.toggle("hide-labels", !event.target.checked);
  });
  document.getElementById("toggleObstacles").addEventListener("change", (event) => {
    document.body.classList.toggle("hide-obstacles", !event.target.checked);
  });
  document.getElementById("toggleAllZones").addEventListener("change", (event) => {
    document.body.classList.toggle("hide-allzones", !event.target.checked);
  });
  document.getElementById("overlayOpacity").addEventListener("input", (event) => {
    document.documentElement.style.setProperty("--overlay-opacity", String(Number(event.target.value) / 100));
  });
  for (const tab of document.querySelectorAll(".tab")) {
    tab.addEventListener("click", () => selectTab(tab.dataset.tab));
  }
}

function setBackground(background) {
  if (background === "satellite" && !data.map.backgrounds?.satellite) return;
  state.background = background;
  document.body.classList.toggle("bg-satellite", background === "satellite");
  document.getElementById("terrainMode").classList.toggle("is-active", background === "terrain");
  document.getElementById("satelliteMode").classList.toggle("is-active", background === "satellite");
  const attribution = background === "satellite" ? data.map.satellite?.attribution : "";
  document.getElementById("mapAttribution").textContent = attribution || "";
}

function selectTab(tabName) {
  for (const tab of document.querySelectorAll(".tab")) {
    tab.classList.toggle("is-active", tab.dataset.tab === tabName);
  }
  for (const panel of document.querySelectorAll(".tab-panel")) {
    panel.classList.toggle("is-active", panel.id === `${tabName}Panel`);
  }
}

function isLocalViewerHost() {
  const host = window.location.hostname;
  return host === "localhost" || host === "127.0.0.1" || host === "::1";
}

function bindLiveReload() {
  if (!("EventSource" in window) || window.location.protocol === "file:" || !isLocalViewerHost()) {
    state.liveConnection = "static";
    renderLiveStatusStrip();
    return;
  }
  let baselineVersion = null;
  let baselineStatusVersion = null;
  let source = null;
  state.liveConnection = "connecting";
  renderLiveStatusStrip();
  try {
    source = new EventSource(`/__navimow/events?interval=${LIVE_EVENT_INTERVAL_SECONDS}&live=full`);
  } catch {
    state.liveConnection = "unavailable";
    renderLiveStatusStrip();
    return;
  }
  source.addEventListener("open", () => {
    state.liveConnection = "connected";
    renderLiveStatusStrip();
  });
  source.addEventListener("viewer-update", (event) => {
    state.liveConnection = "connected";
    renderLiveStatusStrip();
    let payload = null;
    try {
      payload = JSON.parse(event.data);
    } catch {
      return;
    }
    if (!payload?.version) return;
    if (baselineVersion === null) {
      baselineVersion = payload.version;
      return;
    }
    if (payload.version !== baselineVersion) {
      source.close();
      window.setTimeout(() => window.location.reload(), 750);
    }
  });
  source.addEventListener("live-status", (event) => {
    state.liveConnection = "connected";
    let payload = null;
    try {
      payload = JSON.parse(event.data);
    } catch {
      return;
    }
    if (!payload?.version || payload.available === false) return;
    if (payload.status) {
      baselineStatusVersion = payload.version;
      applyLiveStatus(payload.status);
      return;
    }
    if (baselineStatusVersion === null) {
      baselineStatusVersion = payload.version;
      return;
    }
    if (payload.version !== baselineStatusVersion) {
      baselineStatusVersion = payload.version;
      fetchLiveStatus();
    }
  });
  source.addEventListener("error", () => {
    state.liveConnection = "reconnecting";
    renderLiveStatusStrip();
    fetchLiveStatus();
  });
  fetchLiveStatus();
}

function fetchLiveStatus() {
  fetch("/__navimow/live-status", { cache: "no-store" })
    .then((response) => (response.ok ? response.json() : null))
    .then((status) => {
      if (status) applyLiveStatus(status);
    })
    .catch(() => {});
}

function applyLiveStatus(status) {
  if (!status || typeof status !== "object") return;
  const patch = mergeLiveStatusData(status);
  if (patch.requiresReload) {
    window.setTimeout(() => window.location.reload(), 250);
    return;
  }
  renderLiveStatusPatch(patch);
}

function mergeLiveStatusData(status) {
  const currentLayout = data.liveStatus?.layoutVersion;
  if (status.layoutVersion && currentLayout && status.layoutVersion !== currentLayout) {
    return { requiresReload: true, areaStatusChanged: false };
  }
  const nextMower = mergeLiveMowerStatus(data.mower || {}, status.mower || {});
  const areaStatusChanged = mergeLiveAreaStatus(status.areaStatus);
  data = deepMerge(data, {
    generatedAt: status.generatedAt || data.generatedAt,
    map: status.map || {},
    areaStatus: status.areaStatus || data.areaStatus || {},
    mower: nextMower,
    liveStatus: {
      ...(data.liveStatus || {}),
      layoutVersion: status.layoutVersion || currentLayout,
      statusGeneratedAt: status.generatedAt,
    },
  });
  data.mower = nextMower;
  return { requiresReload: false, areaStatusChanged };
}

function renderLiveStatusPatch(patch) {
  renderHeader();
  renderMowerMarker();
  renderMowerDisplay();
  if (patch.areaStatusChanged) {
    renderAreaLiveState();
  }
  if (document.getElementById("plannerPanel")?.classList.contains("is-active")) {
    renderPlanner();
  }
}

mergeLiveAreaStatus(data.areaStatus);
renderHeader();
renderMap();
renderAreaList();
renderPlanner();
renderMowerDisplay();
renderRoutes();
bindControls();
bindZoom();
bindLiveReload();
selectArea(state.activeAreaId);
setBackground("terrain");
requestAnimationFrame(fitWidth);
