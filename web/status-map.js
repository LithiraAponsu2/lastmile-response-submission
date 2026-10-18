/* Area status map shared by the incident workspace, the dashboard and the public status page.

   One picture (from the server) drives three map layers over DS-division outlines:
   - warnings: official warning level in force (yellow / amber / red), as published by the issuer;
   - affected: people affected, one blue ramp from light to dark (unknown stays grey);
   - services: status of one service, or the worst of all, as working / being restored / not working / not known.
   Safety centres and reported problem points sit on top. Colour is never the only cue:
   tooltips, the legend and every list use words and symbols too. Untrusted text is always set
   as text nodes. Requires Leaflet (window.L). */

export const SECTOR_ORDER = ["power", "water", "roads", "health", "communications", "shelter", "food"];
export const LEVEL_ORDER = ["disrupted", "restoring", "unknown", "working"];
export const STATUS = {
  working: { colour: "#188038", icon: "✓", tone: "green" },
  restoring: { colour: "#e37400", icon: "◐", tone: "warn" },
  disrupted: { colour: "#c5221f", icon: "✕", tone: "danger" },
  unknown: { colour: "#80868b", icon: "?", tone: "" },
};
export const WARNING = {
  yellow: { colour: "#f6c026", icon: "!", rank: 1 },
  amber: { colour: "#ef7d00", icon: "!!", rank: 2 },
  red: { colour: "#c5221f", icon: "!!!", rank: 3 },
};
const SEVERITY = { working: 0, unknown: 1, restoring: 2, disrupted: 3 };
// People affected: sequential single hue, light → dark. Bins suit DS-division counts.
export const AFFECTED_BINS = [[1, "1–99"], [100, "100–999"], [1000, "1,000–9,999"], [10000, "10,000–49,999"], [50000, "50,000 or more"]];
const AFFECTED_RAMP = ["#d2e3fc", "#a1c4f7", "#669df0", "#2f6fd1", "#123f8c"];
const NO_DATA = "#eef2f7", OUTLINE = "#a8b6c9";

function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) if (v != null && v !== false) k === "class" ? node.className = v : node.setAttribute(k, v === true ? "" : v);
  for (const child of children.flat(Infinity)) if (child != null && child !== false) node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  return node;
}

let cache = null;
export function loadAreas() {
  cache ||= Promise.all(["/static/data/sri-lanka-areas.geojson", "/static/data/sri-lanka-areas.json", "/static/data/sri-lanka-districts.geojson"].map(async url => {
    const r = await fetch(url); if (!r.ok) throw new Error("Map unavailable"); return r.json();
  })).then(([geo, index, districts]) => {
    const byId = new Map([...index.divisions, ...index.districts].map(a => [a.id, a]));
    return { geo, index, districts, byId };
  }).catch(error => { cache = null; throw error; });
  return cache;
}

export function areaName(areas, id) {
  const a = areas.byId.get(id); if (!a) return id;
  return a.district && a.id.startsWith("ds-") ? `${a.name} DS, ${a.district}` : `${a.name} District`;
}
export const empty = { services: {}, warnings: [], figures: null };
export const areaState = (picture, id) => picture.areas[id] || empty;
export function worst(services, sector = "all") {
  if (sector !== "all") return services[sector]?.level || null;
  const levels = Object.values(services).map(s => s.level);
  return levels.length ? levels.reduce((a, b) => SEVERITY[b] > SEVERITY[a] ? b : a) : null;
}
export const topWarning = state => state.warnings[0] || null;
export const affectedBin = n => n == null ? -1 : n < 1 ? -1 : AFFECTED_BINS.reduce((i, [min], j) => n >= min ? j : i, -1);
export function fill(state, layer, sector) {
  if (layer === "warnings") { const w = topWarning(state); return w ? WARNING[w.level].colour : NO_DATA; }
  if (layer === "affected") { const bin = affectedBin(state.figures?.values?.people_affected); return bin < 0 ? NO_DATA : AFFECTED_RAMP[bin]; }
  const level = worst(state.services, sector); return level ? STATUS[level].colour : NO_DATA;
}
export function summary(state, layer, sector, vocab) {
  if (layer === "warnings") { const w = topWarning(state); return w ? `${vocab.warning_levels[w.level]} · ${vocab.hazards[w.hazard]} · ${vocab.issuers[w.issuer]}` : "No warning recorded"; }
  if (layer === "affected") { const n = state.figures?.values?.people_affected; return n == null ? "People affected: not reported" : `${n.toLocaleString()} people affected`; }
  const level = worst(state.services, sector);
  const what = sector === "all" ? "Services" : vocab.sectors[sector];
  return level ? `${what}: ${STATUS[level].icon} ${vocab.levels[level]}` : `${what}: no report`;
}

export function legend(layer, sector, vocab) {
  const key = (colour, text, symbol) => el("span", {}, el("i", { class: "swatch", "data-colour": colour }, symbol || ""), text);
  const items = layer === "warnings" ? ["red", "amber", "yellow"].map(l => key(WARNING[l].colour, vocab.warning_levels[l], WARNING[l].icon))
    : layer === "affected" ? AFFECTED_BINS.map(([, label], i) => key(AFFECTED_RAMP[i], label))
    : LEVEL_ORDER.map(l => key(STATUS[l].colour, vocab.levels[l], STATUS[l].icon));
  const title = layer === "warnings" ? "Official warning in force" : layer === "affected" ? "People affected" : sector === "all" ? "Worst service status" : vocab.sectors[sector];
  const node = el("div", { class: "status-legend", role: "group", "aria-label": `Map key: ${title}` }, el("strong", {}, title), items, key(NO_DATA, layer === "warnings" ? "None recorded (not an all-clear)" : "No report"));
  for (const swatch of node.querySelectorAll(".swatch")) swatch.style.background = swatch.dataset.colour;
  return node;
}

// Point-in-polygon for "use my location".
function inRing(point, ring) {
  let inside = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const [xi, yi] = ring[i], [xj, yj] = ring[j];
    if ((yi > point[1]) !== (yj > point[1]) && point[0] < (xj - xi) * (point[1] - yi) / (yj - yi) + xi) inside = !inside;
  }
  return inside;
}
export function locate(areas, lat, lng) {
  const p = [lng, lat];
  for (const f of areas.geo.features) {
    const polys = f.geometry.type === "Polygon" ? [f.geometry.coordinates] : f.geometry.coordinates;
    if (polys.some(rings => inRing(p, rings[0]) && !rings.slice(1).some(hole => inRing(p, hole)))) return f.properties.id;
  }
  return null;
}

// Views re-render often; a redraw queued just before a canvas map is removed must not draw on the destroyed canvas.
export function guardCanvas() {
  const L = window.L;
  if (!L?.Canvas || L.Canvas.prototype._lastMileGuard) return;
  const redraw = L.Canvas.prototype._redraw;
  L.Canvas.prototype._redraw = function () { if (this._ctx) return redraw.call(this); this._redrawRequest = null; };
  L.Canvas.prototype._lastMileGuard = true;
}

// Removing a canvas map with vector layers still attached can leave one redraw queued on a removed canvas.
export function clearLayers(instance) {
  const L = window.L;
  instance.eachLayer(layer => { if (!(L && layer instanceof L.Renderer)) instance.removeLayer(layer); });
  // Removing paths queues a canvas redraw; cancel it before the canvas itself goes.
  instance.eachLayer(layer => { if (L && layer instanceof L.Renderer && layer._redrawRequest) { L.Util.cancelAnimFrame(layer._redrawRequest); layer._redrawRequest = null; } });
}

// Uploaded map layers (flood extent, road closures, landslide areas) drawn under the status points.
export const LAYER_STYLE = { flood_extent: { color: "#1a5fb4", weight: 1, fillColor: "#3584e4", fillOpacity: .35 }, road_closure: { color: "#c01c28", weight: 4, dashArray: "6 4", fillOpacity: .5 },
  landslide_area: { color: "#865e3c", weight: 1, fillColor: "#986a44", fillOpacity: .45 }, other: { color: "#613583", weight: 2, fillColor: "#9141ac", fillOpacity: .25 } };
const geometryCache = new Map();

/* Draw the map. Options:
   layer, sector, focus (district name or null for the whole country), selected (area id),
   onSelect(id), centres (bool), points (bool), vocab, label (aria-label), zoom (bool: scroll-wheel zoom),
   fetchLayer(meta) -> GeoJSON for picture.layers, shapes (bool: draw them). */
export async function drawStatusMap(container, picture, options) {
  const areas = await loadAreas();
  if (!container.isConnected || !window.L) return null;
  guardCanvas();
  const o = { layer: "warnings", sector: "all", centres: true, points: true, ...options };
  const L = window.L;
  const renderer = L.canvas({ padding: 0.3 });
  // No zoom animation: the views re-render often, and removing a map mid-animation breaks Leaflet.
  const instance = L.map(container, { renderer, scrollWheelZoom: !!o.zoom, attributionControl: true, zoomSnap: .25, zoomDelta: .5, minZoom: 6.5, zoomAnimation: false, fadeAnimation: false, markerZoomAnimation: false });
  instance.attributionControl.setPrefix(false).addAttribution("Boundaries: geoBoundaries (OCHA / Survey Dept., OSM) · reference only");
  container.setAttribute("role", "region"); container.setAttribute("aria-label", o.label || "Area status map");
  let selected = o.selected || null;
  const style = f => {
    const s = areaState(picture, f.properties.id), on = f.properties.id === selected;
    return { renderer, color: on ? "#174ea6" : OUTLINE, weight: on ? 3 : .6, fillColor: fill(s, o.layer, o.sector), fillOpacity: on ? .95 : .8 };
  };
  const divisions = L.geoJSON(areas.geo, { style, onEachFeature: (f, layer) => {
    layer.bindTooltip(() => el("span", {}, el("strong", {}, areaName(areas, f.properties.id)), el("br"), summary(areaState(picture, f.properties.id), o.layer, o.sector, o.vocab)), { sticky: true });
    layer.on("click", () => { selected = f.properties.id; divisions.setStyle(style); o.onSelect?.(selected); });
  } }).addTo(instance);
  L.geoJSON(areas.districts, { interactive: false, style: { renderer, color: "#4d5f78", weight: 1.4, fill: false } }).addTo(instance);
  const shapes = L.layerGroup().addTo(instance);
  let removed = false;
  const drawShapes = async () => {
    shapes.clearLayers();
    if (o.shapes === false || !o.fetchLayer) return;
    for (const meta of picture.layers || []) {
      let geometry = geometryCache.get(meta.id);
      if (!geometry) { try { geometry = await o.fetchLayer(meta); geometryCache.set(meta.id, geometry); } catch { continue; } }
      if (removed || !container.isConnected) return;  // the map went away while the layer loaded
      const style = { renderer, ...(LAYER_STYLE[meta.kind] || LAYER_STYLE.other) };
      L.geoJSON(geometry, { style, interactive: true, pointToLayer: (_, latlng) => L.circleMarker(latlng, { ...style, radius: 6 }),
        onEachFeature: (f, layer) => layer.bindTooltip(`${meta.name} · ${meta.issuer}${f.properties?.label ? ` · ${f.properties.label}` : ""}`, { sticky: true }) }).addTo(shapes);
    }
  };
  const overlay = L.layerGroup().addTo(instance);
  const drawOverlay = () => {
    overlay.clearLayers();
    const drawn = new Set();  // a district-wide record is copied to each division; draw its point once
    if (o.points) for (const state of Object.values(picture.areas)) for (const s of Object.values(state.services)) {
      if (!s.point || drawn.has(s.id)) continue;
      drawn.add(s.id);
      const colour = STATUS[s.level].colour;
      L.marker(s.point, { icon: L.divIcon({ className: "status-point", html: `<span>${STATUS[s.level].icon}</span>`, iconSize: [26, 26] }), keyboard: false,
        title: `${o.vocab.sectors[s.sector]} · ${o.vocab.levels[s.level]}` }).addTo(overlay)
        .bindPopup(el("div", { class: "map-popup" }, el("strong", {}, `${o.vocab.sectors[s.sector]} · ${STATUS[s.level].icon} ${o.vocab.levels[s.level]}`), s.note && el("p", {}, s.note), el("small", {}, areaName(areas, s.area))))
        .getElement()?.style.setProperty("--point", colour);
    }
    if (o.centres) for (const x of picture.centres) {
      const full = x.available === 0, unknown = x.available == null;
      L.circleMarker([x.latitude, x.longitude], { renderer, radius: 8, color: "#fff", weight: 2, fillColor: unknown ? "#5f6368" : full ? "#c5221f" : "#188038", fillOpacity: 1 }).addTo(overlay)
        .bindTooltip(el("span", {}, el("strong", {}, x.name), el("br"), unknown ? "Safety centre · free places not known" : full ? "Safety centre · full" : `Safety centre · ${x.available} free places`))
        .on("click", () => o.onCentre?.(x));
    }
  };
  drawOverlay();
  drawShapes();
  // A map drawn in a hidden pane (the phone's Chat view) has no size: Leaflet cannot fit
  // bounds there. Start from a fixed view and fit once the container gets a size.
  const sized = () => container.clientWidth > 0 && container.clientHeight > 0;
  const fit = () => {
    if (!sized()) return false;
    instance.invalidateSize();
    const target = o.focus ? areas.index.districts.find(d => d.name === o.focus) : null;
    if (target) instance.fitBounds([[target.bounds[0], target.bounds[1]], [target.bounds[2], target.bounds[3]]], { padding: [12, 12], animate: false });
    else instance.fitBounds(divisions.getBounds(), { padding: [8, 8], animate: false });
    return true;
  };
  instance.setView([7.85, 80.7], 7, { animate: false });
  let fitted = fit();
  const watcher = new ResizeObserver(() => { if (!container.isConnected) return; if (!fitted) fitted = fit(); else if (sized()) instance.invalidateSize(); });
  watcher.observe(container);
  return {
    map: instance, areas,
    remove: () => { removed = true; watcher.disconnect(); instance.stop(); clearLayers(instance); instance.remove(); },
    invalidateSize: () => instance.invalidateSize(),
    update(next, changes = {}) { picture = next || picture; Object.assign(o, changes); divisions.setStyle(style); drawOverlay(); drawShapes(); },
    select(id, { zoom = false } = {}) {
      selected = id; divisions.setStyle(style);
      const a = areas.byId.get(id);
      if (a && zoom && sized()) instance.fitBounds([[a.bounds[0], a.bounds[1]], [a.bounds[2], a.bounds[3]]], { padding: [24, 24], maxZoom: 11, animate: false });
    },
  };
}

/* Read-only summary of one area for the national map and the public page.
   `fmt` supplies date formatting, organisation names and an optional incident link builder. */
export function areaCard(areas, picture, id, vocab, fmt = {}) {
  const s = areaState(picture, id), when = fmt.dateTime || (v => new Date(v).toLocaleString()), org = fmt.orgName || (v => v);
  const figureKeys = Object.keys(vocab.figures).filter(k => s.figures?.values?.[k] != null);
  const chip = level => el("span", { class: `status-chip ${level || "none"}` }, el("span", { class: "status-icon", "aria-hidden": "true" }, level ? STATUS[level].icon : "–"), level ? vocab.levels[level] : "No report");
  const services = SECTOR_ORDER.filter(k => s.services[k] || fmt.allSectors);
  return el("div", { class: "stack area-card-body" },
    el("h3", { id: "area-card-title", tabindex: "-1" }, areaName(areas, id)),
    el("section", { "aria-label": fmt.t?.("warnings") || "Warnings in force" }, el("h4", {}, fmt.t?.("warnings") || "Warnings in force"),
      s.warnings.length ? el("ul", { class: "plain-list" }, s.warnings.map(w => el("li", { class: `warning-item ${w.level}` },
        el("span", { class: `warning-chip ${w.level}` }, el("span", { "aria-hidden": "true" }, WARNING[w.level].icon), `${vocab.warning_levels[w.level]} · ${vocab.hazards[w.hazard]}`),
        el("p", {}, w.advice), el("small", { class: "muted" }, `${vocab.issuers[w.issuer]} · ${when(w.issued_at)}${w.valid_until ? ` → ${when(w.valid_until)}` : ""}`))))
        : el("p", { class: "muted" }, fmt.t?.("no_warning") || "No warning recorded. That is not an all-clear.")),
    el("section", { "aria-label": fmt.t?.("services") || "Services" }, el("h4", {}, fmt.t?.("services") || "Services"),
      services.length ? el("ul", { class: "service-list" }, services.map(k => { const r = s.services[k]; return el("li", {},
        el("div", { class: "row between" }, el("strong", {}, fmt.sector?.(k) || vocab.sectors[k]), chip(r?.level)),
        r?.note && el("p", {}, r.note),
        r && el("small", { class: "muted" }, `${org(r.organisation)} · ${when(r.at)}${r.expected_by ? ` · ${fmt.t?.("expected") || "expected back"} ${when(r.expected_by)}` : ""}${r.stale ? ` · ${fmt.t?.("stale") || "may be out of date"}` : ""}`),
        r && fmt.link && fmt.link(r)); })) : el("p", { class: "muted" }, fmt.t?.("no_services") || "No service status reported.")),
    el("section", { "aria-label": fmt.t?.("affected") || "Affected" }, el("h4", {}, fmt.t?.("affected") || "Affected"),
      figureKeys.length ? el("dl", { class: "figure-list" }, figureKeys.map(k => [el("dt", {}, fmt.figure?.(k) || vocab.figures[k]), el("dd", {}, s.figures.values[k].toLocaleString())]))
        : el("p", { class: "muted" }, fmt.t?.("no_figures") || "No figures reported."),
      figureKeys.length && el("small", { class: "muted" }, `${fmt.t?.("as_of") || "As of"} ${when(Object.values(s.figures.as_of).sort().at(-1))}`)));
}
