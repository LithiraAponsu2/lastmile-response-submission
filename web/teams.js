import { createDataTabs, createViewMenu } from '/static/vendor/components/lastmile-ui.js';
import { STAFF_LANGUAGES, translator } from '/static/staff-i18n.js';
import { LAYER_STYLE, LEVEL_ORDER, clearLayers, guardCanvas, SECTOR_ORDER, STATUS, WARNING, areaCard, areaName, areaState, drawStatusMap, legend, loadAreas } from '/static/status-map.js';

/* Organisation exercise UI. All untrusted content becomes text nodes. */
(() => {
  "use strict";
  const $ = s => document.querySelector(s);
  // The chosen role is this tab's; a new tab starts with the role last chosen in this browser.
  function savedRole() {
    try {
      const token = sessionStorage.getItem("lastmile-demo-session") || localStorage.getItem("lastmile-demo-role") || "";
      if (token) sessionStorage.setItem("lastmile-demo-session", token);
      return token;
    } catch { return ""; }
  }
  // Interface language for fixed labels; records stay as written.
  function savedLanguage() { try { const v = localStorage.getItem("lastmile-staff-lang"); return STAFF_LANGUAGES[v] ? v : "en"; } catch { return "en"; } }
  const forgetRole = () => { try { sessionStorage.removeItem("lastmile-demo-session"); localStorage.removeItem("lastmile-demo-role"); } catch { /* Storage is optional. */ } };
  const state = { me: null, doc: null, tab: "overview", askView: "all", collapsed: false, mobileView: "chat", busy: false, chatDraft: "", reportQuery: "", reportStatus: "all", reportOrg: "all", reportTime: "all", reportGroup: "org", reportMine: false, mapLayer: "warnings", mapSector: "all", mapArea: null, syncedAt: null, syncError: false, catalogue: null, demoToken: savedRole(), lang: savedLanguage() };
  let tr = translator(state.lang);
  document.documentElement.lang = state.lang;
  const drafts = new Map();
  const activityViews = new Map();
  const storyCache = new Map();
  let storyFocus = "", storyGroup = "all", storyLimit = 20;
  let savedEntries = [];
  try { const values = JSON.parse(sessionStorage.getItem("lastmile-entered-incidents") || "[]"); if (Array.isArray(values)) savedEntries = values.filter(v => typeof v === "string" && /^incident_[a-f0-9]+$/.test(v)); } catch { /* A reset browser store starts a fresh entry flow. */ }
  const enteredIncidents = new Set(savedEntries);
  const rememberEntry = value => { enteredIncidents.add(value); sessionStorage.setItem("lastmile-entered-incidents", JSON.stringify([...enteredIncidents])); };
  state.entryReady = false; state.demoMode = false;
  const mobileNavigation = matchMedia("(max-width: 760px)");
  let navCollapsed = false, mobileNavOpen = false;
  try { navCollapsed = localStorage.getItem("lastmile-sidebar-collapsed") === "true"; } catch { /* Storage is optional. */ }
  const toolNames = { whowhat: "Who does what where", requests: "Requests", sources: "Reports", resources: "Places & stock", map: "Status map", tasks: "Needs & assignments", communications: "Communications", activity: "Activity" };
  const draftKey = () => state.doc && state.me ? `${state.me.id}:${state.doc.id}` : "";
  let map = null, mapCtl = null, toastTimer, pollTimer, routeEpoch = 0, lastPoll = 0, pollInFlight = false;
  const id = () => crypto.randomUUID();
  const now = () => new Date().toISOString();
  const has = (...roles) => !!state.me && roles.some(r => state.me.roles.includes(r));
  const canAct = (...roles) => !!state.doc && state.doc.status !== "closed" && has(...roles);
  const c = () => state.doc.coordination;
  const orgName = value => state.catalogue?.organisations.find(o => o.id === value)?.name || value;
  const activeOrgs = () => (state.catalogue?.organisations || []).filter(o => o.active);
  // The job someone chose; its permissions live server-side in me.roles.
  const jobOf = (me = state.me) => state.catalogue?.organisations.find(o => o.id === me?.organisation)?.roles?.find(r => r.id === me?.job_role) || { title: me?.job_title || me?.demo_role?.replaceAll("_", " ") || "Custom role", summary: "", tasks: [], idle: "" };
  const dateTime = value => new Intl.DateTimeFormat("en-GB", { timeZone: "Asia/Colombo", dateStyle: "medium", timeStyle: "short" }).format(new Date(value));
  const money = value => Number(value).toLocaleString("en-GB", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const sourceName = id => state.doc?.sources?.find(s => s.id === id)?.title || "linked source";
  // Remove legacy seed decorators from headings, never from original evidence.
  const titleText = text => String(text).replace(/\s*·\s*fictional\b/gi, "").replace(/^Fictional\s+(.)/i, (_, first) => first.toUpperCase());
  // Model replies may cite internal source IDs; show the report title instead.
  const readable = text => String(text).replace(/\[?\b(source_[0-9a-f]{8,})\b\]?/g, (_, id) => "[" + sourceName(id) + "]");
  function focusSnapshot() {
    const el = document.activeElement;
    if (!el || el === document.body || el.closest("#modal")) return null;
    return { id: el.id, tag: el.tagName, text: el.getAttribute("aria-label") || el.textContent, record: el.closest(".record")?.id, start: el.selectionStart, end: el.selectionEnd };
  }
  function restoreFocus(snapshot, fallback = false) {
    if (!snapshot || $("#modal").open) return;
    const scope = snapshot.record && document.getElementById(snapshot.record) || $("#app");
    let el = snapshot.id && document.getElementById(snapshot.id);
    if (!el) el = [...scope.querySelectorAll(snapshot.tag)].find(n => (n.getAttribute("aria-label") || n.textContent) === snapshot.text);
    if ((!el || el.disabled) && fallback) el = $("#chat-message") || $("#main");
    if (el && !el.disabled) { el.focus({ preventScroll: true }); if (snapshot.start != null && el.setSelectionRange) el.setSelectionRange(snapshot.start, snapshot.end); }
  }
  function rememberView() {
    if (!state.doc || new URLSearchParams(location.hash.slice(1)).get("incident") !== state.doc.id) return;
    const params = new URLSearchParams({ incident: state.doc.id, tab: state.tab });
    if (state.collapsed) params.set("chat", "collapsed");
    if (state.mobileView === "data") params.set("pane", "data");
    history.replaceState(null, "", "#" + params);
  }
  function syncLabel() { return state.syncError ? "Updates paused · retry refresh" : state.syncedAt ? "Updated " + new Intl.DateTimeFormat("en-GB", { timeZone: "Asia/Colombo", hour: "2-digit", minute: "2-digit" }).format(new Date(state.syncedAt)) : "Loading updates…"; }
  function updateSync() {
    const el = $("#sync-status");
    if (el) { el.textContent = syncLabel(); el.classList.toggle("offline", state.syncError); }
  }
  function h(tag, props = {}, ...children) {
    const el = document.createElement(tag);
    for (const [key, value] of Object.entries(props)) {
      if (value == null) continue;
      if (key.startsWith("on")) el.addEventListener(key.slice(2).toLowerCase(), value);
      else if (["value", "checked", "disabled", "hidden", "open"].includes(key)) el[key] = value;
      else el.setAttribute(key, ["aria-label", "placeholder", "title"].includes(key) ? tr(value) : value);
    }
    for (const child of children.flat(Infinity)) if (child != null && child !== false) el.append(child instanceof Node ? child : document.createTextNode(tr(["h1", "h2", "h3", "strong"].includes(tag) ? titleText(child) : String(child))));
    return el;
  }
  const btn = (text, fn, cls = "", props = {}) => h("button", { type: "button", class: cls, onclick: e => perform(e.currentTarget, fn), ...props }, text);
  const iconButton = (label, icon, fn, props = {}) => h("lm-icon-button", { label, icon, onclick: e => perform(e.currentTarget, fn), ...props });
  const pill = (text, cls = "") => h("span", { class: "pill " + cls }, text);
  const empty = text => h("div", { class: "empty" }, text);
  function notice(text) { return h("div", { class: "notice" }, text); }
  function toast(text) { $("#toast").textContent = text; $("#toast").hidden = false; clearTimeout(toastTimer); toastTimer = setTimeout(() => $("#toast").hidden = true, 6000); }
  async function api(path, body, method = body === undefined ? "GET" : "POST", headers = {}) {
    const response = await fetch("/api/teams" + path, { method, credentials: "same-origin", headers: { ...(body === undefined ? {} : { "Content-Type": "application/json" }), ...(state.demoToken ? { "X-Demo-Session": state.demoToken } : {}), ...headers }, body: body === undefined ? undefined : JSON.stringify(body) });
    const value = await response.json().catch(() => ({}));
    if (!response.ok) { const error = new Error(typeof value.detail === "string" ? value.detail : "Please check the fields and try again."); error.status = response.status; throw error; }
    if (method !== "GET") queueNotes();
    return value;
  }
  async function perform(button, fn) {
    button.disabled = true;
    button.setAttribute("aria-busy", "true");
    try { await fn(); } catch (error) { toast(error.message); } finally { button.disabled = false; button.removeAttribute("aria-busy"); }
  }
  async function download(path, name) {
    const response = await fetch("/api/teams" + path, { credentials: "same-origin", headers: state.demoToken ? { "X-Demo-Session": state.demoToken } : {} });
    if (!response.ok) throw new Error("Could not download this record. Refresh your role and retry.");
    const url = URL.createObjectURL(await response.blob());
    const link = h("a", { href: url, download: name }); link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  function field(label, control, hint) {
    const controlId = control.id || `field-${id()}`, hintId = `${controlId}-hint`;
    control.id = controlId;
    if (hint) control.setAttribute("aria-describedby", hintId);
    if (["INPUT", "SELECT", "TEXTAREA"].includes(control.tagName)) return h("div", { class: "field" }, h("label", { for: controlId }, label), control, hint && h("small", { id: hintId }, hint));
    control.setAttribute("role", "group"); control.setAttribute("aria-label", label);
    return h("div", { class: "field" }, h("strong", {}, label), control, hint && h("small", { id: hintId }, hint));
  }
  const input = (name, value = "", props = {}) => h("input", { name, value, autocomplete: "off", ...props });
  function select(name, values, value = "") {
    const el = h("select", { name, class: "lm-select" }, values.map(v => h("option", { value: Array.isArray(v) ? v[0] : v }, Array.isArray(v) ? v[1] : v)));
    if (value) el.value = value;
    return el;
  }
  function checkList(name, values, selected = []) {
    return h("div", { class: "check-list" }, values.map(([value, label]) => h("label", {}, input(name, value, { type: "checkbox", checked: selected.includes(value) }), label)));
  }
  function dialog(title, build, submit, label = "Save record") {
    const opener = focusSnapshot();
    const openerElement = document.activeElement;
    const modal = $("#modal"); modal.replaceChildren();
    const error = h("div", { class: "error", role: "alert", tabindex: "-1", hidden: true });
    const form = h("form", {}, ...build());
    const save = h("button", { type: "submit", class: "primary" }, label);
    form.append(error, h("div", { class: "row dialog-actions" }, btn("Cancel", () => modal.close()), save));
    form.addEventListener("submit", async event => {
      event.preventDefault(); save.disabled = true; error.hidden = true;
      const saveLabel = save.textContent; save.textContent = "Saving…"; save.setAttribute("aria-busy", "true");
      try { await submit(new FormData(form), form); modal.close(); } catch (e) { error.textContent = e.message; error.hidden = false; error.focus(); } finally { save.disabled = false; save.textContent = saveLabel; save.removeAttribute("aria-busy"); }
    });
    modal.append(h("div", { class: "dialog-head" }, h("h2", { id: "modal-title", tabindex: "-1" }, title), iconButton("Close dialog", "close", () => modal.close(), { id: "close-dialog" })), form);
    modal.addEventListener("close", () => {
      // Native close events can arrive after the user has already moved on.
      // Restore a removed opener, but never steal newly chosen keyboard focus.
      const active = document.activeElement;
      if (!active || active === document.body || active === openerElement || active === modal || modal.contains(active)) restoreFocus(opener, true);
    }, { once: true });
    modal.showModal();
    $("#modal-title").focus();
  }
  async function command(action, data) {
    const doc = await api(`/incidents/${state.doc.id}/commands`, { version: state.doc.version, request_id: id(), action, data });
    state.doc = doc; state.syncedAt = now(); state.syncError = false; renderIncident(); toast("Record saved");
  }
  function languageSelect() {
    const picker = select("staff_language", Object.entries(STAFF_LANGUAGES), state.lang);
    picker.id = "staff-language";
    return picker;
  }
  function setLanguage(value) {
    if (!STAFF_LANGUAGES[value] || value === state.lang) return;
    state.lang = value; tr = translator(state.lang); document.documentElement.lang = state.lang;
    try { localStorage.setItem("lastmile-staff-lang", state.lang); } catch { /* optional */ }
    if (state.lang !== "en") toast(tr("Interface partly translated"));
    if (state.doc) renderIncident(); else route();
  }
  function header() {
    return h("header", { class: "header" }, h("div", { class: "brand-group" },
      h("a", { class: "brand lm-brand", href: "/", translate: "no", lang: "en" },
        h("img", { class: "lm-brand__mark", src: "/static/response-mark.svg?v=coordination", alt: "", width: "46", height: "46" }),
        h("span", { class: "lm-brand__type" }, h("span", { class: "lm-brand__name" }, "Last", h("span", {}, "Mile")), " ", h("span", { class: "lm-brand__subtitle" }, "Disaster Coordination Portal"))),
      pill("Sri Lanka · Prototype")),
      state.me ? h("div", { class: "row account" }, notesButton(), !state.me.demo && has("administrator") && btn("Members", memberDialog, "quiet"), btn(state.me.demo ? "Leave workspace" : "Sign out", async () => { if (!state.me.demo) await api("/logout", {}); forgetRole(); sessionStorage.removeItem("lastmile-entered-incidents"); location.assign("/"); }, "quiet"), roleSwitch()) : state.demoMode && h("div", { class: "row account" }, btn("Choose organisation & role", () => enterWorkspace(), "primary")));
  }
  // Who you are acting as, and the way to change it, are one control in the top-right corner.
  function roleSwitch() {
    const who = h("span", { class: "role-switch-text" }, h("strong", {}, state.me.demo ? jobOf().title : state.me.name), h("small", {}, orgName(state.me.organisation), has("partner") ? " · limited access" : ""));
    if (!state.me.demo) return h("div", { class: "role-switch static" }, who);
    return h("button", { type: "button", id: "switch-role", class: "role-switch", title: "Switch role", onclick: e => perform(e.currentTarget, roleDialog) }, h("span", { class: "sr-only" }, "Switch role: "), who, actionIcon("chevron"));
  }
  // The bell: what is waiting for this person now, and what other bodies did that concerns their organisation.
  // Read items leave the list. What is read is remembered in this browser only, so people sharing a demo role never clear each other's.
  const notes = { for: null, items: [], waiting: [], at: null, open: false, read: new Set(), loading: null };
  let notesSoon;
  const queueNotes = () => { clearTimeout(notesSoon); notesSoon = setTimeout(() => loadNotes().catch(() => {}), 1200); };
  const readKey = () => `lastmile-notes-read:${state.me?.id}`;
  // A waiting item comes back when it changes (a new count, a newer step), so its key carries its title and time.
  const noteKey = (item, waiting) => waiting ? `waiting:${item.incident_id}:${item.kind}:${item.record || ""}:${item.title}:${item.at || ""}` : item.id;
  const mine = () => notes.for === state.me?.id;
  const unread = () => mine() ? { waiting: notes.waiting.filter(i => !notes.read.has(noteKey(i, true))), items: notes.items.filter(i => !notes.read.has(noteKey(i, false))) } : { waiting: [], items: [] };
  function loadRead() { try { const v = JSON.parse(localStorage.getItem(readKey()) || "[]"); return new Set(Array.isArray(v) ? v : []); } catch { return new Set(); } }
  function saveRead() {
    // Keep only keys still listed, so the record stays small.
    const listed = new Set([...notes.waiting.map(i => noteKey(i, true)), ...notes.items.map(i => noteKey(i, false))]);
    notes.read = new Set([...notes.read].filter(k => listed.has(k)));
    try { localStorage.setItem(readKey(), JSON.stringify([...notes.read])); } catch { /* Storage is optional. */ }
  }
  function markRead(keys) { keys.forEach(k => notes.read.add(k)); saveRead(); paintNotes(); }
  function loadNotes(announce = true) {
    if (!state.me) return Promise.resolve();
    if (notes.loading) return notes.loading;
    const who = state.me.id;
    notes.loading = api("/notifications").then(data => {
      if (state.me?.id !== who) return;
      const fresh = notes.for !== who;
      const before = fresh ? null : new Set([...notes.items.map(i => noteKey(i, false)), ...notes.waiting.map(i => noteKey(i, true))]);
      if (fresh) notes.read = loadRead();
      Object.assign(notes, { for: who, items: data.items, waiting: data.waiting, at: data.generated_at });
      const now = unread(), arrived = before ? [...now.waiting.map(i => [i, true]), ...now.items.map(i => [i, false])].filter(([i, w]) => !before.has(noteKey(i, w))).map(([i]) => i) : [];
      paintNotes();
      if (announce && arrived.length) {
        $("#notes-live").textContent = arrived.length === 1 ? `New notification: ${arrived[0].title}` : `${arrived.length} new notifications`;
        const bell = $("#notes-button"); bell?.classList.remove("ring"); void bell?.offsetWidth; bell?.classList.add("ring");
      }
    }).finally(() => { notes.loading = null; });
    return notes.loading;
  }
  function notesButton() {
    return h("button", { type: "button", id: "notes-button", class: "quiet notes-button", title: "Notifications", "aria-expanded": String(notes.open), "aria-controls": "notes-pane", "aria-label": "Notifications", onclick: () => notes.open ? closeNotes(true) : openNotes() }, navIcon("bell"));
  }
  function notesPane() {
    return h("section", { id: "notes-pane", class: "notes-pane", "aria-labelledby": "notes-title", hidden: !notes.open, onkeydown: e => { if (e.key === "Escape") closeNotes(true); } }, notes.open ? notesContent() : []);
  }
  function noteRow(item, waiting) {
    const key = noteKey(item, waiting), when = item.at ? (waiting ? `arrived ${ago(item.at)}` : ago(item.at)) : "";
    return h("li", { class: "note-row", "data-note": key },
      h("button", { type: "button", class: "note-item", onclick: () => { markRead([key]); closeNotes(false); openRecord(item); } },
        h("span", { class: "attention-icon", "aria-hidden": "true" }, kindIcon[item.kind]),
        h("span", { class: "note-text" }, h("strong", {}, item.title), item.detail && h("span", {}, item.detail), h("small", {}, [item.incident_title, when].filter(Boolean).join(" · ")))),
      h("button", { type: "button", class: "note-read", title: "Mark as read", "aria-label": `Mark as read: ${item.title}`, onclick: () => {
        // Keep keyboard focus in the list: on the next item, or the heading when the list is empty.
        const rows = [...$("#notes-pane").querySelectorAll(".note-row")], at = rows.findIndex(r => r.dataset.note === key);
        const next = (rows[at + 1] || rows[at - 1])?.dataset.note;
        markRead([key]);
        const target = next && [...$("#notes-pane").querySelectorAll(".note-row")].find(r => r.dataset.note === next);
        (target?.querySelector(".note-read") || $("#notes-title"))?.focus();
      } }, actionIcon("check")));
  }
  function notesContent() {
    const loaded = mine(), list = unread(), total = list.waiting.length + list.items.length;
    return [h("div", { class: "notes-head" }, h("h2", { id: "notes-title", tabindex: "-1" }, "Notifications"), h("small", { class: "muted", role: "status" }, loaded ? `Updated ${ago(notes.at)}` : "Loading…"),
        total > 0 && btn("Mark all read", () => { markRead([...list.waiting.map(i => noteKey(i, true)), ...list.items.map(i => noteKey(i, false))]); $("#notes-title").focus(); }, "quiet"),
        iconButton("Close notifications", "close", () => closeNotes(true))),
      loaded && !total && h("div", { class: "notes-empty" }, h("strong", {}, "You’re all caught up"), h("p", { class: "muted" }, "New requests, steps on your requests, shared reports and incidents your organisation is added to will appear here.")),
      list.waiting.length > 0 && [h("h3", {}, `Waiting for you (${list.waiting.length})`), h("ul", { class: "notes-list" }, list.waiting.map(i => noteRow(i, true)))],
      list.items.length > 0 && [h("h3", {}, `Latest (${list.items.length})`), h("ul", { class: "notes-list" }, list.items.map(i => noteRow(i, false)))],
      h("small", { class: "muted notes-foot" }, "Read items leave this list; what still waits for you stays on the Dashboard. Shown in the portal only. No SMS or email is sent.")].flat().filter(x => x instanceof Node);
  }
  // Repaint the badge and, when open, the pane, keeping keyboard focus on the same item.
  function paintNotes(pane = true) {
    const button = $("#notes-button");
    if (!button) return;
    const list = unread(), count = list.waiting.length + list.items.length;
    button.querySelectorAll(".badge").forEach(el => el.remove());
    if (count) button.append(h("span", { class: "badge", "aria-hidden": "true" }, count > 99 ? "99+" : String(count)));
    button.setAttribute("aria-label", count ? `Notifications, ${count} unread` : "Notifications");
    button.setAttribute("aria-expanded", String(notes.open));
    const panel = $("#notes-pane");
    if (!pane || !panel || !notes.open) return;
    const active = document.activeElement, row = active?.closest?.("#notes-pane .note-row")?.dataset.note, cls = active?.className;
    panel.replaceChildren(...notesContent());
    if (row) [...panel.querySelectorAll(".note-row")].find(el => el.dataset.note === row)?.querySelector(cls === "note-read" ? ".note-read" : ".note-item")?.focus();
  }
  function openNotes() {
    notes.open = true;
    const panel = $("#notes-pane"); panel.hidden = false; panel.replaceChildren(...notesContent());
    paintNotes(false); $("#notes-title").focus();
    if (!mine() || Date.now() - Date.parse(notes.at) > 15000) loadNotes(false).catch(e => toast(e.message));
  }
  function closeNotes(returnFocus) {
    if (!notes.open) return;
    notes.open = false;
    const panel = $("#notes-pane"); if (panel) { panel.hidden = true; panel.replaceChildren(); }
    paintNotes(false);
    if (returnFocus) $("#notes-button")?.focus();
  }
  // The path, not the target: a click on the badge or a row that a repaint has since replaced still counts as inside.
  document.addEventListener("click", e => { if (notes.open && !e.composedPath().some(el => el.id === "notes-pane" || el.id === "notes-button")) closeNotes(false); });
  // The sticky sidebar and chat sit below the sticky header; keep their offset equal to its real height.
  const headerSize = new ResizeObserver(([entry]) => { document.documentElement.style.setProperty("--header-h", Math.ceil(entry.borderBoxSize?.[0]?.blockSize ?? entry.target.offsetHeight) + "px"); queueFitChat(); });
  // Status maps carry their own remove(); bare Leaflet maps lose their layers first (see clearLayers).
  function dropMap(m) { try { if (m.eachLayer) { m.stop(); clearLayers(m); } m.remove(); } catch { /* already gone */ } }
  function shell(content) {
    if (map) { dropMap(map); map = null; }
    const active = state.doc ? "incidents" : new URLSearchParams(location.hash.slice(1)).get("view") || "dashboard";
    const item = (key, label) => btn([navIcon(key), h("span", { class: "nav-label" }, label)], () => { mobileNavOpen = false; updateNavigation(); return key === "dashboard" ? showIncidents() : goTool(key); }, "sidebar-item", { "aria-label": label, title: label, "aria-current": active === key ? "page" : null });
    const sidebar = h("aside", { class: "app-sidebar", "aria-label": "Application sidebar", onkeydown: e => { if (e.key === "Escape" && mobileNavigation.matches && mobileNavOpen) { mobileNavOpen = false; updateNavigation(); $("#toggle-navigation").focus(); } } },
      h("div", { class: "sidebar-heading" }, h("span", { class: "nav-label eyebrow" }, "Workspace"), btn(navIcon("collapse"), toggleNavigation, "sidebar-toggle", { id: "toggle-navigation", "aria-controls": "sidebar-menu" })),
      h("div", { id: "sidebar-menu", class: "sidebar-menu" }, h("nav", { "aria-label": "Main navigation" }, item("dashboard", "Dashboard"), item("incidents", "Incidents"), ...["requests", "tasks", "whowhat", "sources", "communications", "activity", "resources", "map"].map(key => item(key, toolNames[key])),
        h("a", { class: "sidebar-item", href: "/public", target: "_blank", rel: "noopener", title: "Public status page (opens in a new tab)", "aria-label": "Public status page (opens in a new tab)" }, navIcon("public"), h("span", { class: "nav-label" }, "Public status page"))),
        h("div", { class: "sidebar-bottom" }, btn([navIcon("activity"), h("span", { class: "nav-label" }, "How well the AI does")], aiQuality, "sidebar-item", { title: "How well the AI does", "aria-label": "How well the AI does" }),
          btn([navIcon("settings"), h("span", { class: "nav-label" }, "Workspace settings")], workspaceSettings, "sidebar-item", { id: "workspace-settings", title: "Workspace settings", "aria-label": "Workspace settings" }), h("small", { class: "sidebar-note" }, "Sri Lanka prototype"))));
    $("#app").replaceChildren(header(), state.me ? notesPane() : [], h("div", { class: "application-layout" }, sidebar, h("main", { id: "main", class: "shell", tabindex: "-1" }, content)));
    headerSize.disconnect(); headerSize.observe($(".header"));
    paintNotes();
    if (state.me && !mine()) loadNotes(false).catch(() => {});
    updateNavigation();
  }
  function navIcon(key) {
    const paths = { whowhat:"M4 5h16M4 12h10M4 19h7M17 15l3 3-3 3", requests:"M4 8h13M13 4l4 4-4 4M20 16H7M11 12l-4 4 4 4", dashboard:"M3 3h8v8H3zM13 3h8v5h-8zM13 10h8v11h-8zM3 13h8v8H3z", incidents:"M3 7h18v14H3zM3 7V3h7l3 4M7 12h10M7 16h7", sources:"M4 4h16v16H4zM8 8h8M8 12h8M8 16h5", resources:"m3 10 9-7 9 7M5 9v12h14V9M9 21v-7h6v7", map:"m3 5 6-2 6 2 6-2v16l-6 2-6-2-6 2zM9 3v16M15 5v16", tasks:"M8 3h8v4H8zM8 5H5v16h14V5h-3M8 12l2 2 5-5M8 18h7", communications:"M3 4h18v13H9l-6 4zM7 8h10M7 12h7", activity:"M12 7v5l3 2M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0", handover:"M5 3h10l4 4v14H5zM14 3v5h5M8 12h8M8 16h8", guide:"m9 5 7 7-7 7", warning:"m12 3 9 17H3zM12 9v4M12 16v1", public:"M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0M3 12h18M12 3c-5 5-5 13 0 18M12 3c5 5 5 13 0 18", bell:"M6 9a6 6 0 0 1 12 0c0 6 3 8 3 8H3s3-2 3-8M10 21h4", settings:"M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8M12 2v3M12 19v3M2 12h3M19 12h3M5 5l2 2M17 17l2 2M5 19l2-2M17 7l2-2", more:"M5 5h4v4H5zM15 5h4v4h-4zM5 15h4v4H5zM15 15h4v4h-4z", collapse:"M4 4h16v16H4zM9 4v16m7-12-3 4 3 4" };
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg"), path = document.createElementNS(svg.namespaceURI, "path");
    svg.setAttribute("viewBox", "0 0 24 24"); svg.setAttribute("aria-hidden", "true"); svg.classList.add("nav-icon"); path.setAttribute("d", paths[key] || paths.guide); svg.append(path); return svg;
  }
  function actionIcon(key) {
    const paths = {
      request: "M7 17 17 7M7 7h10v10",
      forward: "M5 12h14m-6-6 6 6-6 6",
      back: "M19 12H5m6-6-6 6 6 6",
      plus: "M12 5v14M5 12h14",
      check: "m5 12.5 4.5 4.5L19 7.5",
      chevron: "m6 9 6 6 6-6",
      refresh: "M3 12a9 9 0 0 1 15.36-6.36L21 8M21 3v5h-5M21 12a9 9 0 0 1-15.36 6.36L3 16M3 21v-5h5"
    };
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg"), path = document.createElementNS(svg.namespaceURI, "path");
    svg.setAttribute("viewBox", "0 0 24 24"); svg.setAttribute("aria-hidden", "true"); svg.setAttribute("focusable", "false"); svg.classList.add("action-icon"); path.setAttribute("d", paths[key]); svg.append(path); return svg;
  }
  const actionLabel = (label, icon, trailing = false) => trailing ? [label, actionIcon(icon)] : [actionIcon(icon), label];
  function setNavCollapsed(value) { navCollapsed = value; try { localStorage.setItem("lastmile-sidebar-collapsed", String(value)); } catch { /* Optional preference. */ } updateNavigation(); }
  function toggleNavigation() { if (mobileNavigation.matches) { mobileNavOpen = !mobileNavOpen; updateNavigation(); } else setNavCollapsed(!navCollapsed); }
  function updateNavigation() {
    const layout = $(".application-layout"), toggle = $("#toggle-navigation"), menu = $("#sidebar-menu"); if (!layout) return;
    layout.classList.toggle("nav-collapsed", navCollapsed); layout.classList.toggle("mobile-nav-open", mobileNavOpen);
    const expanded = mobileNavigation.matches ? mobileNavOpen : !navCollapsed;
    const label = mobileNavigation.matches ? expanded ? "Hide navigation" : "Show navigation" : expanded ? "Collapse sidebar" : "Expand sidebar";
    toggle.setAttribute("aria-label", label); toggle.title = label; toggle.setAttribute("aria-expanded", String(expanded)); menu.hidden = mobileNavigation.matches && !mobileNavOpen;
    requestAnimationFrame(() => map?.invalidateSize());
  }
  mobileNavigation.addEventListener("change", updateNavigation);
  // The recorded benchmark of the public-request reader, with its limits: evidence, not a promise.
  async function aiQuality() {
    const data = await fetch("/api/public/ai-quality", { cache: "no-store" }).then(r => r.ok ? r.json() : {}).catch(() => ({}));
    const rows = [["decision", "Right decision (file, hold, check, close)"], ["junk_caught", "Junk closed"], ["need", "Right need"], ["owner", "Right owning body"], ["people", "Right people count"], ["urgent_flagged", "Urgent cases flagged urgent"]];
    const table = (set, title) => {
      if (!set) return null;
      const readers = Object.keys(set.scores);
      return h("div", { class: "stack" }, h("h3", {}, `${title} · ${set.cases} cases`),
        h("table", { class: "ai-quality" }, h("thead", {}, h("tr", {}, h("th", { scope: "col" }, "Measure"), readers.map(r => h("th", { scope: "col" }, r)))),
          h("tbody", {}, rows.map(([key, label]) => h("tr", {}, h("th", { scope: "row" }, label), readers.map(r => { const m = set.scores[r][key]; return h("td", {}, m?.of ? `${m.correct}/${m.of} (${Math.round(m.rate * 100)}%)` : "–"); }))),
            h("tr", {}, h("th", { scope: "row" }, "Real requests wrongly closed (target 0)"), readers.map(r => h("td", {}, String(set.scores[r].real_requests_wrongly_closed)))))),
        h("small", { class: "muted" }, `Run ${dateTime(set.run_at)}`), set.rescored && h("small", { class: "muted" }, `Re-scored ${set.rescored.at}: ${set.rescored.why}`));
    };
    dialog("How well the AI does", () => [notice("Public requests read by Gemini and by the offline rules, on synthetic labelled messages in English, Sinhala, Tamil and Singlish. Code, not the model, decides safety: nothing with danger words or a plea for help is closed automatically, and counts are kept only if the message states them."),
      table(data.held_out, "Held-out set, run once with frozen rules") || notice("No benchmark recorded on this server."), table(data.development, "Development set (used to fix safety gaps)"),
      h("p", { class: "muted" }, "Limits: written by the builders, not real messages; Sinhala and Tamil need native review; no photos in these runs. Full report: docs/AI_EVALUATION.md in the repository.")], async () => {}, "Close");
  }
  function workspaceSettings() {
    dialog("Workspace settings", () => [notice("These preferences are saved on this browser."), field("Language", languageSelect(), "Labels and buttons. Records stay in the language they were written in."), field("Sidebar on desktop", select("sidebar", [["expanded", "Show labels"], ["collapsed", "Compact icon rail"]], navCollapsed ? "collapsed" : "expanded")), notice(state.me ? `Current organisation: ${orgName(state.me.organisation)}. ${state.me.demo ? "Select your role in the top-right corner to switch." : "Account permissions come from your membership."}` : "Choose your organisation and role when entering an incident.")], async data => { setNavCollapsed(data.get("sidebar") === "collapsed"); setLanguage(data.get("staff_language")); }, "Save preferences");
  }
  async function goTool(key) {
    mobileNavOpen = false;
    const target = "view=" + key;
    if (location.hash.slice(1) === target) return route();
    location.hash = target;
  }
  async function showIncidents() {
    if (!state.me && !state.demoMode) return authScreen();
    state.entryReady = false;
    if (location.hash) location.hash = "";
    else await route();
  }
  async function enterWorkspace(after = null, incidentId = null) {
    if (!state.demoMode) { if (!state.me) return authScreen(); return after ? after() : navigate(null); }
    dialog("Choose organisation & role", roleFields, async data => {
      await selectRole(data, incidentId, true);
      state.entryReady = true;
      if (incidentId) { rememberEntry(incidentId); if (new URLSearchParams(location.hash.slice(1)).get("incident") === incidentId) await route(); else await navigate(incidentId); }
      else if (after) await after();
      // Stay on the current page (Dashboard, Incidents, a tool), now with the chosen role.
      else await route();
    }, "Enter workspace");
  }
  async function openIncident(incidentId) {
    if (state.demoMode && !state.me) return enterWorkspace(null, incidentId);
    rememberEntry(incidentId); await navigate(incidentId);
  }
  // Any team opens incidents; observers follow them.
  const canOpen = () => state.me ? has("contributor", "agency_supervisor", "coordinator", "finance_approver", "communications_reviewer") && !has("partner") : state.demoMode;
  function startIncident() {
    const create = () => { if (!canOpen()) throw new Error("Observers follow incidents but do not open them. Switch to an acting role."); setTimeout(newIncident, 0); };
    return state.demoMode && !state.me ? enterWorkspace(create) : create();
  }
  async function startSample() {
    const create = async () => { if (!canOpen()) throw new Error("Observers follow incidents but do not open them. Switch to an acting role."); const doc = await api("/practice", {}); rememberEntry(doc.id); await navigate(doc.id); };
    return state.demoMode && !state.me ? enterWorkspace(create) : create();
  }
  async function authScreen(mode = "login") {
    const setup = await api("/setup");
    if (setup.demo && mode === "login") { state.catalogue = setup; state.demoMode = true; return route(); }
    let invitation = new URLSearchParams(location.hash.slice(1)).get("join") || "";
    if (invitation) { mode = "join"; history.replaceState(null, "", "/"); }
    if (setup.setup_required) mode = "setup";
    const title = mode === "setup" ? "Set up your team" : mode === "join" ? "Join your organisation" : "Welcome back";
    const error = h("div", { class: "error", role: "alert", hidden: true });
    const form = h("form", {}, mode !== "login" && field("Your name", input("name", "", { required: true, autocomplete: "name", maxlength: 80 })),
      field("Username", input("username", "", { required: true, autocomplete: "username", minlength: 3, maxlength: 40 })),
      field("Password", input("password", "", { type: "password", required: true, autocomplete: mode === "login" ? "current-password" : "new-password", minlength: mode === "login" ? 1 : 12, maxlength: 128 }), mode !== "login" && "Use at least 12 characters."),
      mode === "setup" && field("Server setup key", input("setupKey", "", { type: "password", autocomplete: "off" }), "Leave blank for a direct local preview without a configured key. Only the first administrator needs the server key remotely; members use invitation links."),
      mode === "join" && field("Invitation code", input("invitation", invitation, { required: true, autocomplete: "off" })), error,
      h("button", { type: "submit", class: "primary" }, mode === "login" ? "Sign in" : mode === "setup" ? "Create workspace" : "Create my account"));
    form.addEventListener("submit", async event => {
      event.preventDefault(); const save = form.querySelector("button"); save.disabled = true; error.hidden = true; const data = new FormData(form);
      try {
        const credentials = { username: data.get("username"), password: data.get("password") };
        if (mode !== "login") await api(mode === "setup" ? "/bootstrap" : "/join", { ...credentials, name: data.get("name"), invitation: data.get("invitation") || "" }, "POST", mode === "setup" ? { "X-Setup-Key": data.get("setupKey") } : {});
        await api("/login", credentials); await boot();
      } catch (e) { error.textContent = e.message; error.hidden = false; } finally { save.disabled = false; }
    });
    shell(h("div", { class: "auth stack" }, h("div", { class: "card stack" }, h("div", {}, h("div", { class: "eyebrow" }, "Incident workspace"), h("h1", {}, title), h("p", { class: "muted" }, "One shared incident. Each organisation contributes evidence and responds under its own account.")), form,
      h("div", { class: "row login-links" }, mode === "login" ? btn("I have an invitation", () => authScreen("join"), "quiet") : btn("Back to sign in", () => authScreen("login"), "quiet"))),
      notice("Prototype access is not verified government identity or financial delegation.")));
  }
  function choiceCard(name, value, title, detail, checked, extra = null) {
    const key = `${name}-${value}`;
    return h("label", { class: "choice-card" }, input(name, value, { type: "radio", required: true, checked, "aria-labelledby": `${key}-title`, "aria-describedby": `${key}-detail` }),
      h("span", { class: "choice-body" }, h("strong", { id: `${key}-title` }, title), h("small", { id: `${key}-detail` }, detail), extra));
  }
  function roleFields() {
    const active = activeOrgs(), inactive = state.catalogue.organisations.filter(o => !o.active);
    let org = active.find(o => o.id === state.me?.organisation) || active[0];
    const roleGrid = h("div", { class: "choice-grid roles" });
    const drawRoles = () => {
      // Keep the same job (e.g. Observer) when switching organisation if it exists there.
      const previous = roleGrid.querySelector("input:checked")?.value || state.me?.job_role;
      const jobs = org.roles.filter(r => !r.accounts_only), chosen = jobs.find(r => r.id === previous) || jobs[0];
      roleGrid.replaceChildren(...jobs.map(r => choiceCard("role", r.id, r.title, r.summary, r === chosen, h("ul", { class: "choice-tasks" }, r.tasks.map(t => h("li", {}, t))))));
    };
    const orgGrid = h("div", { class: "choice-grid orgs" }, active.map(o => choiceCard("organisation", o.id, o.name, o.scope, o === org)));
    orgGrid.addEventListener("change", event => { org = active.find(o => o.id === event.target.value); drawRoles(); });
    drawRoles();
    return [field("Organisation", orgGrid), field("Your role", roleGrid),
      inactive.length && h("section", { class: "inactive-orgs", "aria-labelledby": "inactive-orgs-title" }, h("strong", { id: "inactive-orgs-title" }, "Other agencies · not active in this demo"),
        h("ul", {}, inactive.map(o => h("li", {}, o.name)))),
      notice("Each tab keeps its own organisation and role. Everyone shares incident data; your role decides what you can act on. Prototype access is not verified agency sign-in.")];
  }
  async function selectRole(data, incidentId = state.doc?.id || null, entering = false) {
    const result = await api("/demo/select", { organisation: data.get("organisation"), role: data.get("role"), incident_id: incidentId });
    state.demoToken = result.token; sessionStorage.setItem("lastmile-demo-session", result.token); try { localStorage.setItem("lastmile-demo-role", result.token); } catch { /* Storage is optional. */ }
    state.chatDraft = ""; state.me = result.user;
    if (entering) { state.catalogue = await api("/me"); return; }
    if (state.doc) {
      state.catalogue = await api("/me");
      try { state.doc = await api(`/incidents/${state.doc.id}`); }
      catch (error) { if (error.status !== 403) throw error; const id = state.doc.id; state.doc = null; return outsiderPage(id); }
      state.chatDraft = drafts.get(draftKey()) || ""; renderIncident();
    }
    else await boot();
  }
  function roleDialog() { dialog("Choose your role", roleFields, data => selectRole(data), "Switch role"); }
  function roleBar() {
    const unread = (c().notifications || []).filter(n => !n.read_by.includes(state.me.id)).length;
    return h("section", { class: "role-chip", "aria-label": "Active organisation and role" }, h("span", { class: "role-who" }, h("strong", {}, jobOf().title), h("small", { title: orgName(state.me.organisation) }, orgShort(state.me.organisation))),
      btn(unread ? ["Shared with you", h("span", { class: "badge", "aria-hidden": "true" }, String(unread))] : "Shared with you", () => { state.tab = "notifications"; renderData(); }, "quiet" + (unread ? " has-new" : ""), unread ? { "aria-label": `Shared with you, ${unread} new` } : {}),
      btn("Access history", accessHistoryDialog, "quiet"));
  }
  async function memberDialog() {
    const members = await api("/members");
    const organisation = select("organisation", activeOrgs().map(o => [o.id, o.name])), job = select("job_role", []);
    const fillJobs = () => job.replaceChildren(...activeOrgs().find(o => o.id === organisation.value).roles.map(r => h("option", { value: r.id }, r.title)));
    organisation.addEventListener("change", fillJobs); fillJobs();
    dialog("Team members", () => [notice("Invite a separate account for each participant. Each role carries only its own permissions; administration alone does not grant finance approval."),
      ...members.map(m => h("div", { class: "row between" }, h("div", {}, m.name, h("small", {}, `${orgName(m.organisation)} · ${m.job_title} · ${m.active ? "active" : "inactive"}`)), m.id !== state.me.id && m.active && btn("Deactivate", async () => { await api(`/members/${m.id}/deactivate`, {}); $("#modal").close(); toast("Membership and sessions revoked"); }, "quiet danger"))),
      field("Organisation", organisation), field("Role", job),
      field("District scope", checkList("districts", [["*", "All Sri Lanka (national role)"], ...state.catalogue.districts.map(d => [d, d])], ["Nuwara Eliya"]))],
      async data => {
        const result = await api("/invitations", { organisation: data.get("organisation"), job_role: data.get("job_role"), districts: data.getAll("districts") });
        const link = `${location.origin}/#join=${encodeURIComponent(result.token)}`;
        // Open the invitation after the submitting dialog has closed.
        setTimeout(() => dialog("Private invitation · one use", () => [notice(result.notice + " Expires " + dateTime(result.expires_at)), field("Invitation link", input("link", link, { readonly: true })), h("small", {}, "Send this privately to your participant. No message was sent by LastMile.")], async () => {}, "Done"), 0);
      }, "Create invitation");
  }
  // Home dashboard: what needs this role now, shelter places, the map, every incident and recent activity.
  const dash = { query: "", filter: null, allShelters: false };
  const ago = value => { const minutes = Math.round((Date.now() - Date.parse(value)) / 60000); return minutes < 1 ? "just now" : minutes < 60 ? `${minutes} min ago` : minutes < 1440 ? `${Math.round(minutes / 60)} h ago` : dateTime(value); };
  const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;
  // The CSP forbids style attributes, so widths are set through the CSSOM.
  const bar = (cls, width) => { const el = h("i", { class: cls }); el.style.width = width; return el; };
  const openRecord = item => { location.hash = new URLSearchParams({ incident: item.incident_id, ...(item.tab ? { tab: item.tab } : {}), ...(item.record ? { record: item.record } : {}), pane: item.pane || "data" }); };
  const jump = id => () => document.getElementById(id)?.scrollIntoView({ behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "start" });
  const lkr = value => "LKR " + money(value).replace(/\.00$/, "");
  async function listIncidents() {
    state.doc = null; state.chatDraft = ""; clearInterval(pollTimer);
    document.title = "Dashboard · LastMile";
    const canCreate = canOpen();
    const preRole = state.demoMode && !state.me;
    const data = preRole ? null : await api("/dashboard");
    const records = preRole ? await api("/demo/catalogue") : [...data.incidents, ...(data.others || [])];
    if (data?.incident_types) state.incidentTypes = data.incident_types;
    const focus = focusSnapshot();
    const head = h("div", { class: "dash-head" }, h("div", {}, h("div", { class: "eyebrow" }, "Sri Lanka response workspace"), h("h1", {}, "Dashboard"),
        state.me ? h("p", { class: "dash-role" }, h("strong", {}, jobOf().title), ` · ${orgName(state.me.organisation)}${state.me.demo ? "" : ` · ${state.me.name}`}`, jobOf().summary && h("span", { class: "muted" }, jobOf().summary))
          : h("p", { class: "muted" }, "One shared picture, from the first report to the next shift.")),
      h("div", { class: "dash-actions" }, data && h("small", { class: "sync-status", role: "status" }, "Updated " + ago(data.generated_at)), data && btn("Refresh", () => listIncidents(), "quiet"), canCreate && btn("Try sample case", startSample), canCreate && btn("+ New incident", startIncident, "primary")));
    if (preRole) {
      const tiles = [[records.filter(r => r.status !== "closed").length, "Active incidents"], [records.reduce((n, r) => n + r.pending_reports, 0), "Reports awaiting review"], [records.reduce((n, r) => n + r.open_requests, 0), "Open requests"], [new Set(records.map(r => r.district)).size, "Districts represented"]];
      shell(h("div", { class: "stack dashboard" }, head, kpis(tiles.map(([value, label]) => [value, label, "dash-incidents"])),
        h("section", { class: "dash-card dash-callout" }, h("div", {}, h("h2", {}, "See what needs your attention"), h("p", { class: "muted" }, "Choose an organisation and role to see your tasks, shelter places and the live incident map.")), h("p", { class: "callout-hint" }, "Use ", h("strong", {}, "Choose organisation & role"), " at the top.")),
        incidentSection(records)));
      restoreFocus(focus);
      return;
    }
    const queue = has("coordinator") ? await api("/public-requests").catch(() => null) : null;
    const t = data.totals, s = data.shelters;
    const tiles = [[t.active_incidents, "Active incidents", "dash-incidents", t.districts ? `in ${plural(t.districts, "district")}` : "None open"],
      shelterTile(s),
      [t.pending_reports, "Reports awaiting review", "dash-attention", "Unconfirmed until reviewed"],
      [t.open_requests, "Open requests", "dash-incidents", `${plural(t.assignments_in_progress, "assignment")} in progress`],
      [lkr(data.finance.released), "Funding released", "dash-incidents", `of ${lkr(data.finance.requested)} requested`]];
    shell(h("div", { class: "stack dashboard" }, head, kpis(tiles),
      h("div", { class: "dash-grid" }, attentionSection(data.attention), shelterSection(s, data.incidents.filter(i => i.status !== "closed").map(i => i.district))),
      queue && publicQueueSection(queue, records),
      h("div", { class: "dash-grid map-row" }, h("section", { id: "dash-map-section", class: "dash-card", "aria-labelledby": "dash-map-title" }, h("div", { class: "dash-card-head" }, h("h2", { id: "dash-map-title" }, "Status map"), data.picture ? mapLayerButtons("dashMapLayer", () => dashCtl?.update(null, { layer: state.dashMapLayer })) : pill("Sri Lanka")),
          h("div", { id: "dashboard-map", class: "map dash-map" }),
          h("div", { id: "dash-map-legend" }),
          h("small", {}, "Numbered pins are active incidents; dots are safety centres. A blank area means nothing was reported, not that it is safe. ", h("a", { href: "#view=map" }, "Open the full status map"), " · ", h("a", { class: "with-icon", href: "/public", target: "_blank", rel: "noopener" }, actionLabel("Public page", "request", true)))),
        incidentSection(records)),
      notice("Figures reflect the portal records available to your organisation.")));
    restoreFocus(focus);
    drawDashboardMap(data).catch(() => { const el = $("#dashboard-map"); if (el) el.textContent = "Map could not load. Incident and shelter records remain available on this page."; });
    // Keep the picture current without disturbing someone typing or reading a popup.
    pollTimer = setInterval(() => { if (!document.hidden && !$("#modal").open && !document.activeElement?.matches("input, select, textarea") && !$(".leaflet-popup")) listIncidents().catch(() => {}); }, 30000);
  }
  // Confirmed free places when any shelter is confirmed; otherwise what owners last reported, said plainly.
  function shelterTile(s) {
    if (!s.count) return ["—", "Free shelter places", "dash-shelters", "No shelters in your incidents yet"];
    if (s.known) return [s.available, "Free shelter places", "dash-shelters", s.unknown ? `${plural(s.unknown, "shelter")} also need${s.unknown === 1 ? "s" : ""} a check` : `in ${plural(s.known, "confirmed shelter")}`];
    return [s.reported_free ?? "—", "Free places at last report", "dash-shelters", `Not confirmed · ${plural(s.unknown, "shelter")} need${s.unknown === 1 ? "s" : ""} a check`];
  }
  // Why a shelter's free places are not confirmed, in a few words.
  function shelterCheck(x) {
    const problems = [];
    if (/stale|future/i.test(x.reason || "")) problems.push(x.observed_at ? `last checked ${ago(x.observed_at)}` : "report out of date");
    if (x.status && x.status !== "accepting") problems.push(`status ${x.status}`);
    if (x.access && x.access !== "passable") problems.push(`access ${x.access}`);
    for (const k of ["water", "power"]) if (x[k] && x[k] !== "working") problems.push(`${k} ${x[k]}`);
    if (/must all be confirmed/.test(x.reason || "")) problems.push("some facts not reported yet");
    const text = problems.join(" · ") || x.reason || "needs an owner check";
    return text[0].toUpperCase() + text.slice(1);
  }
  // An incident resource's facts in the shape shelterCheck reads, and its free places at the owner's last report.
  const factValue = (a, k) => a.facts?.[k]?.value ?? null;
  const assetFacts = a => ({ reason: a.balance.reason, status: factValue(a, "status"), access: factValue(a, "access"), water: factValue(a, "water"), power: factValue(a, "power"),
    observed_at: Object.values(a.facts || {}).map(f => f?.observed_at).filter(Boolean).sort().at(-1) });
  const lastFree = a => { const cap = factValue(a, "capacity"), occ = factValue(a, "occupied"); return cap == null || occ == null ? null : Math.max(0, cap - occ - (factValue(a, "external_pending") || 0)); };
  function kpis(tiles) {
    return h("section", { class: "dash-kpis", "aria-label": "Incident statistics" }, tiles.map(([value, label, target, note]) => btn([h("strong", {}, value), h("span", {}, label), note && h("small", {}, note)], jump(target), "dash-kpi")));
  }
  // Line icons from the same set as the sidebar and the overview's arrows.
  const KIND_ICON = { ack: "sources", join: "incidents", assignment: "tasks", proposal: "communications", report: "sources", resource: "resources", finance: "requests", request: "tasks", notification: "communications", incident: "incidents", public: "public", suggestion: "communications" };
  const kindIcon = new Proxy({}, { get: (_, kind) => kind === "ask" ? actionIcon("request") : navIcon(KIND_ICON[kind] || "tasks") });
  function attentionSection(items) {
    const shown = items.slice(0, 7);
    return h("section", { id: "dash-attention", class: "dash-card", "aria-labelledby": "dash-attention-title" }, h("div", { class: "dash-card-head" }, h("h2", { id: "dash-attention-title" }, "Needs your attention"), items.length ? pill(String(items.length), "warn") : pill("Up to date", "green")),
      items.length ? h("ul", { class: "attention-list" }, shown.map(item => h("li", {}, h("button", { type: "button", class: "attention-item " + item.kind, onclick: () => openRecord(item) },
        h("span", { class: "attention-icon", "aria-hidden": "true" }, kindIcon[item.kind] || "•"), h("span", { class: "attention-text" }, h("strong", {}, item.title), h("span", {}, item.detail), h("small", {}, [item.incident_title, item.district, item.at && ago(item.at)].filter(Boolean).join(" · "))), h("span", { class: "chevron", "aria-hidden": "true" }, "›"))))) :
        h("div", { class: "dash-empty" }, h("strong", {}, "You’re up to date"), h("p", {}, jobOf().idle || "Nothing is waiting for your organisation and role. New reports, assignments and proposals will appear here.")),
      items.length > shown.length && h("small", {}, `${items.length - shown.length} more inside the incidents`));
  }
  function shelterRow(x) {
    const known = x.available != null && x.capacity > 0, last = x.available == null && x.reported_free != null && x.capacity > 0;
    const free = known ? x.available : x.reported_free, committed = known || last ? Math.max(0, x.capacity - (x.occupied || 0) - free) : 0;
    const pct = n => `${Math.max(0, Math.min(100, n / x.capacity * 100))}%`;
    const badge = x.available == null ? ["Needs a check", "warn"] : x.available === 0 ? ["Full", "danger"] : [`${x.available} free`, "green"];
    const ready = ["water", "power", "access"].filter(k => x[k] != null).map(k => { const ok = x[k] === "working" || x[k] === "passable"; return h("span", { class: "ready " + (ok ? "ok" : "bad") }, (ok ? "✓ " : "✕ ") + k[0].toUpperCase() + k.slice(1)); });
    return h("li", {}, h("button", { type: "button", class: "shelter-row", onclick: () => openRecord({ incident_id: x.incident_id, tab: "resources", record: x.id }) },
      h("span", { class: "shelter-name" }, h("strong", {}, x.name), pill(badge[0], badge[1])), h("small", {}, `${x.incident_title} · ${orgName(x.owner)}`),
      known || last ? h("span", { class: "meter" + (last ? " unconfirmed" : ""), role: "img", "aria-label": `${x.occupied} in use, ${committed} committed, ${free} free of ${x.capacity}${last ? " at the last report" : ""}` }, bar("used", pct(x.occupied || 0)), committed > 0 && bar("committed", pct(committed))) : h("span", { class: "meter unknown", "aria-hidden": "true" }),
      h("small", { class: "shelter-figures" }, known ? `${x.occupied} in use · ${committed} committed · ${x.available} free of ${x.capacity}` : last ? `Last report: ${x.occupied} in use, ${free} free of ${x.capacity}` : "No capacity reported yet"),
      x.available == null && h("small", { class: "shelter-check" }, shelterCheck(x)),
      h("span", { class: "ready-row" }, ready, x.observed_at && h("small", {}, "Checked " + ago(x.observed_at)))));
  }
  // Registered centres in the districts of open incidents: the planning reserve behind live free places.
  function listedCentres(districts) {
    const box = h("p", { class: "listed-centres muted", "aria-live": "polite" });
    const fill = () => {
      const near = (placesCache.centres || []).filter(r => !r.retired && districts.includes(state.areas?.byId.get(r.area)?.district));
      if (!near.length) { box.textContent = districts.length ? "No centres listed in Places & stock for these districts." : ""; return; }
      const places = near.reduce((n, r) => n + (r._derived?.capacity || 0), 0), unknown = near.filter(r => r._derived?.capacity == null).length;
      box.replaceChildren(`Listed in Places & stock for ${[...new Set(districts)].join(", ")}: ${plural(near.length, "centre")}, ${places.toLocaleString("en-GB")} places by standard${unknown ? ` (${unknown} not known)` : ""}. Not confirmed open. `,
        h("a", { href: "#" + new URLSearchParams({ view: "resources", list: "centres" }) }, "Open Places & stock"));
    };
    Promise.all([areas(), registry()]).then(fill).catch(() => {});
    return box;
  }
  function shelterSection(s, districts = []) {
    const rows = dash.allShelters ? s.items : s.items.slice(0, 4);
    const use = s.capacity ? Math.round((s.capacity - s.available) / s.capacity * 100) : null;
    return h("section", { id: "dash-shelters", class: "dash-card", "aria-labelledby": "dash-shelters-title" }, h("div", { class: "dash-card-head" }, h("h2", { id: "dash-shelters-title" }, "Shelter capacity"), s.count ? pill(plural(s.count, "shelter")) : null),
      s.count ? [h("div", { class: "shelter-hero" + (s.known ? "" : " unknown") }, h("strong", {}, s.known ? s.available : s.reported_free ?? "—"),
          h("span", {}, h("span", {}, s.known ? `free places across ${plural(s.known, "confirmed shelter")}` : "free places at the owners' last report, not confirmed"), use != null && h("small", {}, `${use}% of confirmed capacity in use or committed`),
            s.unknown > 0 && h("small", { class: "warn-text" }, s.known ? `${plural(s.unknown, "shelter")} need${s.unknown === 1 ? "s" : ""} a check and ${s.unknown === 1 ? "is" : "are"} not counted` : `${plural(s.unknown, "shelter")} need${s.unknown === 1 ? "s" : ""} a check before places can be confirmed`))),
        h("div", { class: "meter-legend", "aria-hidden": "true" }, h("span", {}, h("i", { class: "used" }), "In use"), h("span", {}, h("i", { class: "committed" }), "Committed / expected"), h("span", {}, h("i", { class: "free" }), "Free")),
        h("ul", { class: "shelter-list" }, rows.map(shelterRow)),
        s.items.length > 4 && btn(dash.allShelters ? "Show fewer shelters" : `Show all ${s.items.length} shelters`, () => { dash.allShelters = !dash.allShelters; return listIncidents(); }, "quiet")] :
        h("div", { class: "dash-empty" }, h("strong", {}, "No shelters confirmed yet"), h("p", {}, "Shelter owners confirm capacity, occupancy and readiness inside an incident. Unknown places are never estimated.")),
      listedCentres(districts));
  }
  function incidentSection(records) {
    const list = h("div", { class: "incident-list" }), count = h("small", { role: "status" });
    const search = input("dash-search", dash.query, { type: "search", placeholder: "Search incidents or districts…", "aria-label": "Search incidents" });
    const filters = h("div", { class: "segmented", role: "group", "aria-label": "Incident status" });
    const update = () => {
      // Until someone picks a status, show active incidents, or everything when none is active.
      const filter = dash.filter || (records.some(r => r.status !== "closed") ? "active" : "all");
      const query = dash.query.trim().toLocaleLowerCase();
      const rows = records.filter(r => (filter === "all" || (filter === "closed") === (r.status === "closed")) && `${r.title} ${r.district} ${r.phase || ""}`.toLocaleLowerCase().includes(query));
      count.textContent = `${rows.length} of ${plural(records.length, "incident")}`;
      filters.querySelectorAll("button").forEach(b => b.setAttribute("aria-pressed", String(b.dataset.key === filter)));
      list.replaceChildren(...(rows.length ? rows.map(r => {
        const total = r.requests ?? 0, done = r.requests_fulfilled ?? 0, mine = r.attention ?? r.queue ?? 0;
        if (r.member === false) return h("article", { class: "incident-row outsider" }, h("div", { class: "incident-row-main" }, h("div", { class: "row" }, pill(r.district), pill(r.status === "closed" ? "Closed" : "Active", r.status === "closed" ? "" : "green"), pill("Not part of it")),
            h("h3", {}, r.title), h("small", {}, `Led by ${orgShort(r.lead)} · Updated ${ago(r.updated_at)}`), bodyChips(r)), joinButton(r));
        return h("article", { class: "incident-row" }, h("div", { class: "incident-row-main" }, h("div", { class: "row" }, pill(r.district), pill(r.status === "closed" ? "Closed" : "Active", r.status === "closed" ? "" : "green"), r.phase && r.status !== "closed" && pill(r.phase), mine > 0 && pill(`${mine} need you`, "warn")),
            h("h3", {}, r.title),
            h("small", {}, [`${plural(r.reports, "report")}${r.pending_reports ? ` (${r.pending_reports} to review)` : ""}`, plural(r.open_requests, "open request"), r.shelters != null && (r.shelters ? r.shelters_unknown === r.shelters ? `${r.places_reported != null ? `${r.places_reported} free places at last report` : "Shelter places not reported yet"} (${plural(r.shelters, "shelter")} to check)` : `${r.places_available} free places${r.shelters_unknown ? `, ${r.shelters_unknown} more shelter${r.shelters_unknown === 1 ? "" : "s"} to check` : ""}` : "No shelters yet"), `Updated ${ago(r.updated_at)}`].filter(Boolean).join(" · ")),
            bodyChips(r),
            r.requests_fulfilled != null && total > 0 && h("span", { class: "progress" }, h("span", { class: "progress-track", role: "img", "aria-label": `${done} of ${total} requests fulfilled` }, bar("done", `${done / total * 100}%`)), h("small", {}, `${done}/${total} requests fulfilled`))),
          btn(actionLabel("Open incident", "forward", true), () => openIncident(r.id), "primary with-icon"));
      }) : [empty(records.length ? "No incidents match. Try another search or status." : "No incidents yet. Open an incident or try the sample case.")]));
    };
    filters.append(...[["active", "Active"], ["closed", "Closed"], ["all", "All"]].map(([key, label]) => btn(label, () => { dash.filter = key; update(); }, "", { "data-key": key })));
    search.addEventListener("input", () => { dash.query = search.value; update(); });
    update();
    return h("section", { id: "dash-incidents", class: "dash-card", "aria-label": "Saved incidents" }, h("div", { class: "dash-card-head" }, h("h2", {}, "Incidents"), count), h("div", { class: "incident-tools" }, search, filters), list);
  }
  // Incidents board: one card per incident with what it is, what needs this role and how the response is going.
  const board = { query: "", filter: null, sort: "attention" };
  const orgShort = id => ({ dmc: "DMC", district: "DDMCU", division: "DS", ndrsc: "NDRSC", water: "NWSDB", rda: "RDA" })[id] || orgName(id);
  const shortDate = value => new Intl.DateTimeFormat("en-GB", { timeZone: "Asia/Colombo", day: "numeric", month: "short" }).format(new Date(value));
  async function incidentBoard() {
    state.doc = null; state.chatDraft = ""; clearInterval(pollTimer);
    document.title = "Incidents · LastMile";
    const preRole = state.demoMode && !state.me, canCreate = canOpen();
    const boardData = preRole ? null : await api("/dashboard");
    const records = preRole ? await api("/demo/catalogue") : [...boardData.incidents, ...(boardData.others || [])];
    if (boardData?.incident_types) state.incidentTypes = boardData.incident_types;
    const focus = focusSnapshot();
    const open = records.filter(r => r.status !== "closed").length, waiting = records.reduce((n, r) => n + (r.attention || 0), 0);
    const head = h("div", { class: "dash-head" }, h("div", {}, h("div", { class: "eyebrow" }, "Sri Lanka response workspace"), h("h1", {}, "Incidents"),
        h("p", { class: "muted" }, [plural(open, "active incident"), records.length - open && `${records.length - open} closed`, !preRole && (waiting ? (waiting === 1 ? "1 item needs you" : `${waiting} items need you`) : "nothing waiting for you")].filter(Boolean).join(" · "))),
      h("div", { class: "dash-actions" }, btn("Refresh", () => incidentBoard(), "quiet"), canCreate && btn("Try sample case", startSample), canCreate && btn("+ New incident", startIncident, "primary")));
    const grid = h("div", { class: "incident-board" }), count = h("small", { role: "status" });
    const search = input("board-search", board.query, { type: "search", placeholder: "Search incidents or districts…", "aria-label": "Search incidents" });
    const filters = h("div", { class: "segmented", role: "group", "aria-label": "Incident status" });
    const sort = select("board-sort", [["attention", "Needs attention first"], ["updated", "Recently updated"], ["newest", "Newest first"], ["district", "District A–Z"]], board.sort);
    sort.setAttribute("aria-label", "Sort incidents");
    const time = value => value ? Date.parse(value) : 0;
    const order = { attention: null, updated: (a, b) => time(b.updated_at) - time(a.updated_at), newest: (a, b) => time(b.created_at) - time(a.created_at), district: (a, b) => a.district.localeCompare(b.district) || a.title.localeCompare(b.title) };
    const update = () => {
      const filter = board.filter || (open ? "active" : "all"), query = board.query.trim().toLocaleLowerCase();
      const rows = records.filter(r => (filter === "all" || (filter === "closed") === (r.status === "closed")) && `${r.title} ${r.district} ${r.phase || ""}`.toLocaleLowerCase().includes(query));
      if (order[board.sort]) rows.sort(order[board.sort]);
      count.textContent = `${rows.length} of ${plural(records.length, "incident")}`;
      filters.querySelectorAll("button").forEach(b => b.setAttribute("aria-pressed", String(b.dataset.key === filter)));
      grid.replaceChildren(...(rows.length ? rows.map(incidentCard) : [empty(records.length ? "No incidents match. Try another search or status." : "No incidents yet. Open an incident or try the sample case.")]));
    };
    filters.append(...[["active", "Active"], ["closed", "Closed"], ["all", "All"]].map(([key, label]) => btn(label, () => { board.filter = key; update(); }, "", { "data-key": key })));
    search.addEventListener("input", () => { board.query = search.value; update(); });
    sort.addEventListener("change", () => { board.sort = sort.value; update(); });
    shell(h("div", { class: "stack dashboard" }, head,
      preRole && h("section", { class: "dash-card dash-callout" }, h("div", {}, h("h2", {}, "See what each incident needs from you"), h("p", { class: "muted" }, "Choose an organisation and role to see your next step, shelter places and funding on each card.")), h("p", { class: "callout-hint" }, "Use ", h("strong", {}, "Choose organisation & role"), " at the top.")),
      h("div", { class: "incident-tools board-tools" }, search, filters, sort, count), grid));
    update(); restoreFocus(focus);
    pollTimer = setInterval(() => { if (!document.hidden && !$("#modal").open && !document.activeElement?.matches("input, select, textarea")) incidentBoard().catch(() => {}); }, 30000);
  }
  // Participating bodies as short chips: the lead first and marked, the viewer's body highlighted, stood-down bodies struck through.
  function bodyChips(r) {
    const active = r.active_participants || r.participants || [], down = r.stood_down || [];
    const ids = [...new Set([r.lead, ...active, ...down].filter(Boolean))].filter(id => state.catalogue?.organisations.some(o => o.id === id && o.active) || id === state.me?.organisation);
    if (!ids.length) return null;
    return h("ul", { class: "card-bodies", "aria-label": "Participating organisations" }, ids.map(id => h("li", { title: orgName(id) + (id === r.lead ? " · leads this incident" : down.includes(id) ? " · stood down" : ""), class: [id === state.me?.organisation && "mine", id === r.lead && "lead", down.includes(id) && "down"].filter(Boolean).join(" ") },
      orgShort(id), id === r.lead && h("span", { class: "lead-mark" }, " · lead"), id === state.me?.organisation && h("span", { class: "sr-only" }, " (your organisation)"), down.includes(id) && h("span", { class: "sr-only" }, " (stood down)"))));
  }
  function joinButton(r) {
    if (r.status === "closed") return h("small", { class: "muted" }, "Closed");
    if (r.my_join_request === "pending") return h("span", { class: "pill warn" }, "Join requested");
    if (!canOpen()) return h("small", { class: "muted" }, "Observers cannot join");
    return btn(r.my_join_request === "declined" ? "Ask to join again" : "Ask to join", () => joinDialog(r), "", { "aria-label": `Ask to join ${r.title}` });
  }
  function joinDialog(r) {
    dialog(`Ask to join · ${r.title}`, () => [notice(`${orgName(r.lead)} leads this incident with ${(r.active_participants || r.participants).map(orgShort).join(", ")}. A coordinator or supervisor from one of them will approve or decline.`),
      field("Why should your organisation join? (optional)", h("textarea", { name: "note", maxlength: 500, placeholder: "e.g. Our crew is already clearing the access road" }))],
      async data => { await api(`/incidents/${r.id}/join`, { note: data.get("note") || null }); toast("Request to join sent"); state.doc ? null : route(); }, "Send request");
  }
  async function outsiderPage(incidentId) {
    state.doc = null; clearInterval(pollTimer);
    const card = (await api("/incidents")).find(r => r.id === incidentId);
    shell(h("div", { class: "stack dashboard" }, h("div", { class: "dash-head" }, h("div", {}, h("div", { class: "eyebrow" }, "Incident"), h("h1", {}, card?.title || "Incident"))),
      h("section", { class: "dash-card stack outsider-card" }, h("h2", {}, `${orgName(state.me.organisation)} is not part of this incident`),
        card ? [h("p", { class: "muted" }, `${card.district} · led by ${orgName(card.lead)} · updated ${ago(card.updated_at)}`), bodyChips(card), h("p", {}, "Only the organisations taking part can open its records. Ask to join, or switch to a role in one of them."), h("div", { class: "row" }, joinButton(card), btn("Back to Dashboard", () => showIncidents(), "quiet"))]
          : h("p", {}, "This incident is outside your district scope or no longer exists."))));
  }
  function incidentCard(r) {
    const closed = r.status === "closed", titleId = `card-${r.id}-title`;
    if (r.member === false) return h("article", { class: "incident-card outsider" + (closed ? " closed" : ""), "aria-labelledby": titleId },
      h("div", { class: "row between card-top" }, h("div", { class: "row" }, pill(r.district), pill(closed ? "Closed" : "Active", closed ? "" : "green"), r.phase && !closed && pill(r.phase)), pill("Not part of it")),
      h("div", {}, h("h2", { id: titleId }, r.title), h("small", { class: "muted" }, [r.created_at && `Opened ${shortDate(r.created_at)}`, `Led by ${orgShort(r.lead)}`, `Updated ${ago(r.updated_at)}`].filter(Boolean).join(" · "))),
      h("p", { class: "card-idle" }, "Your organisation is not part of this incident. You can see who is involved; ask to join to open it."),
      bodyChips(r), h("div", { class: "row between card-foot" }, h("small", { class: "muted" }, r.my_join_request === "declined" ? "Last request declined" : ""), joinButton(r)));
    const stat = (label, value, note, extra = null) => h("div", { class: "card-stat" }, h("span", {}, label), h("strong", {}, value), note && h("small", {}, note), extra);
    const total = r.requests ?? 0, done = r.requests_fulfilled;
    const stats = [
      stat("Reports", r.reports ?? 0, r.pending_reports ? `${r.pending_reports} to review` : "All reviewed"),
      stat("Open requests", r.open_requests ?? 0, done != null && total ? `${done}/${total} fulfilled` : total ? `of ${total}` : "None yet",
        done != null && total > 0 && h("span", { class: "progress-track", role: "img", "aria-label": `${done} of ${total} requests fulfilled` }, bar("done", `${done / total * 100}%`))),
      // Unconfirmed availability is never shown as 0 free places: the last report is labelled as such.
      r.shelters !== undefined && stat("Shelter places", !r.shelters ? "—" : r.shelters_unknown === r.shelters ? r.places_reported ?? "—" : r.places_available,
        !r.shelters ? "No shelters yet" : r.shelters_unknown === r.shelters ? `free at last report · ${plural(r.shelters, "shelter")} to check` : r.shelters_unknown ? `free · ${r.shelters_unknown} more to check` : `free in ${plural(r.shelters, "shelter")}`),
      r.assignments_in_progress !== undefined && stat("Assignments", r.assignments_in_progress, "in progress"),
    ].filter(Boolean);
    const funding = r.finance && Number(r.finance.requested) > 0 && h("small", { class: "card-funding" }, `Funding: ${lkr(r.finance.released)} released of ${lkr(r.finance.requested)} requested`);
    const next = r.next ? h("button", { type: "button", class: "card-next", onclick: () => openRecord(r.next) }, h("span", { class: "attention-icon", "aria-hidden": "true" }, kindIcon[r.next.kind] || "•"),
        h("span", {}, h("small", {}, "Next for you"), h("strong", {}, r.next.title), r.attention > 1 && h("small", {}, `+${r.attention - 1} more`)), h("span", { class: "chevron", "aria-hidden": "true" }, "›"))
      : state.me && h("p", { class: "card-idle" }, closed ? "Closed · read-only record" : "Nothing waiting for your role");
    return h("article", { class: "incident-card" + (closed ? " closed" : "") + (r.attention ? " needs-you" : ""), "aria-labelledby": titleId },
      h("div", { class: "row between card-top" }, h("div", { class: "row" }, pill(r.district), pill(closed ? "Closed" : "Active", closed ? "" : "green"), r.phase && !closed && pill(r.phase)), r.attention > 0 && pill(`${r.attention} need${r.attention === 1 ? "s" : ""} you`, "warn")),
      h("div", {}, h("h2", { id: titleId }, r.title), h("small", { class: "muted" }, [r.created_at && `Opened ${shortDate(r.created_at)}`, `Updated ${ago(r.updated_at)}`].filter(Boolean).join(" · "))),
      next,
      h("div", { class: "card-stats" }, stats), funding,
      bodyChips(r),
      h("div", { class: "row between card-foot" }, h("small", { class: "muted" }, r.latest ? `${r.latest.label} · ${ago(r.latest.at)}` : ""), btn(actionLabel("Open incident", "forward", true), () => openIncident(r.id), "primary with-icon", { "aria-describedby": titleId })));
  }
  function mapLayerButtons(key, changed) {
    state[key] ||= "warnings";
    return h("div", { class: "segmented small", role: "group", "aria-label": "Map shows" }, [["warnings", "Warnings"], ["affected", "Affected"], ["services", "Services"]].map(([k, label]) =>
      h("button", { type: "button", "aria-pressed": String(state[key] === k), onclick: e => { state[key] = k; for (const b of e.currentTarget.parentNode.children) b.setAttribute("aria-pressed", String(b === e.currentTarget)); changed(); } }, label)));
  }
  let dashCtl = null;
  async function drawDashboardMap(data) {
    const container = $("#dashboard-map"); if (!container || !window.L) return;
    const picture = data.picture || { as_of: new Date().toISOString(), areas: {}, centres: [], warnings: [] }, vocabulary = data.vocabulary;
    if (!vocabulary) { container.remove(); return; }
    const legendSlot = $("#dash-map-legend");
    const showLegend = () => legendSlot?.replaceChildren(legend(state.dashMapLayer || "warnings", "all", vocabulary));
    const ctl = await drawStatusMap(container, picture, { layer: state.dashMapLayer || "warnings", vocab: vocabulary, fetchLayer: meta => api(`/incidents/${meta.incident_id}/layers/${meta.id}`), label: "Sri Lanka: official warnings, people affected or service status, with incidents and safety centres",
      onSelect: id => { state.nationalArea = id; goTool("map"); } });
    if (!ctl) return;
    dashCtl = { ...ctl, update: (p, o) => { ctl.update(p, o); showLegend(); } }; map = ctl; showLegend();
    const active = new Map(data.districts.map(d => [d.name, d.active_incidents]));
    for (const [district, count] of active) {
      const at = ctl.areas.index.districts.find(d => d.name === district)?.centre; if (!at) continue;
      const here = data.incidents.filter(i => i.district === district && i.status !== "closed");
      L.marker(at, { icon: L.divIcon({ className: "incident-pin", html: `<span>${Number(count)}</span>`, iconSize: [30, 30] }), title: `${district}: ${plural(count, "active incident")}`, alt: `${district}: ${plural(count, "active incident")}` }).addTo(ctl.map)
        .bindPopup(h("div", { class: "map-popup" }, h("strong", {}, district), here.map(i => h("p", {}, h("a", { href: "#" + new URLSearchParams({ incident: i.id }) }, i.title), h("br"), h("small", {}, `${plural(i.open_requests, "open request")} · ${i.attention} need you`)))));
    }
  }
  const suggestedBodies = { landslide: ["dmc", "district", "division", "ndrsc", "rda"], flood: ["dmc", "district", "division", "ndrsc", "water"], cyclone: ["dmc", "district", "division", "ndrsc", "water", "rda"], drought: ["dmc", "district", "division", "ndrsc", "water"], other: ["dmc", "district", "division"] };
  function newIncident() {
    const mine = state.me.organisation, types = state.incidentTypes || { hazards: { landslide: "Landslide", flood: "Flood", cyclone: "Cyclone or storm", drought: "Drought", other: "Other" }, suggested: suggestedBodies };
    const hazard = select("hazard", Object.entries(types.hazards), "landslide");
    const bodies = h("div", { class: "check-list" }, activeOrgs().map(o => h("label", {}, input("participants", o.id, { type: "checkbox", checked: o.id === mine, disabled: o.id === mine }), o.id === mine ? `${o.name} · leads` : o.name)));
    const suggest = () => { for (const box of bodies.querySelectorAll("input")) if (box.value !== mine) box.checked = (types.suggested[hazard.value] || []).includes(box.value); };
    hazard.addEventListener("change", suggest); suggest();
    dialog("Open an incident", () => [field("Incident name", input("title", "", { required: true, minlength: 3, maxlength: 120, placeholder: "e.g. Nuwara Eliya flood response…" })), field("Country", select("country", [["lk", "Sri Lanka"], ["in", "India · Coming soon"], ["bd", "Bangladesh · Coming soon"]], "lk")),
      h("div", { class: "field-row" }, field("District", select("district", state.catalogue.districts.filter(d => state.me.districts.includes("*") || state.me.districts.includes(d)), "Nuwara Eliya")), field("Type of incident", hazard)),
      field("Organisations to invite", bodies, "Suggested for this type; change as needed. Only invited organisations can open the incident. Others see it and can ask to join, and you can add more later."),
    ], async data => { const doc = await api("/incidents", { title: data.get("title"), district: data.get("district"), hazard: data.get("hazard"), participants: [mine, ...data.getAll("participants")] }); rememberEntry(doc.id); navigate(doc.id); }, "Open incident");
    $("#modal select[name=country]").querySelectorAll("option").forEach(option => { if (option.value !== "lk") option.disabled = true; });
  }
  async function navigate(incidentId) { if (incidentId) state.entryReady = true; location.hash = incidentId ? `incident=${incidentId}` : ""; if (incidentId === state.doc?.id) await refresh(); }
  async function route() {
    const epoch = ++routeEpoch, incidentId = new URLSearchParams(location.hash.slice(1)).get("incident");
    clearInterval(pollTimer);
    const view = new URLSearchParams(location.hash.slice(1)).get("view");
    if (!incidentId && view === "incidents") return incidentBoard();
    if (!incidentId && toolNames[view]) return showTool(view, epoch);
    if (!incidentId) { state.entryReady = false; return listIncidents(); }
    if (state.demoMode && !state.me) {
      await listIncidents(); return enterWorkspace(null, incidentId);
    }
    let doc;
    try { doc = await api(`/incidents/${encodeURIComponent(incidentId)}`); }
    catch (error) { if (error.status === 403 && epoch === routeEpoch) return outsiderPage(incidentId); throw error; }
    if (epoch !== routeEpoch) return;
    // The first time this tab enters an incident, its history records which role came in.
    const first = state.me.demo && !enteredIncidents.has(incidentId);
    const entered = first ? await api(`/incidents/${encodeURIComponent(incidentId)}/demo-enter`, {}) : doc;
    rememberEntry(incidentId);
    if (epoch !== routeEpoch) return;
    const params = new URLSearchParams(location.hash.slice(1));
    if (state.doc?.id !== entered.id) { storyFocus = ""; storyGroup = "all"; storyLimit = 20; }
    state.doc = entered; state.chatDraft = drafts.get(draftKey()) || ""; state.tab = params.get("tab") || "overview";
    // Links to evidence-based needs and assignments open their own view.
    if (state.tab === "requests" && /^(request|offer)_/.test(params.get("record") || "")) state.tab = "assignments"; state.collapsed = params.get("chat") === "collapsed"; state.mobileView = params.get("pane") === "data" ? "data" : "chat";
    state.syncedAt = now(); state.syncError = false; renderIncident();
    const currentTabs = $("#data-area wa-tab-group");
    if (currentTabs) await Promise.all([currentTabs.updateComplete, ...[...currentTabs.querySelectorAll("wa-tab-panel")].map(panel => panel.updateComplete)]);
    if (epoch !== routeEpoch) return;
    const recordId = params.get("record"), record = recordId && document.getElementById(recordId);
    if (record?.closest(".data-panel")) {
      const focusRecord = () => { if (record.isConnected) { record.tabIndex = -1; record.focus({ preventScroll: true }); record.scrollIntoView({ block: "center" }); } };
      // An entry dialog makes the workspace inert until it closes.
      if ($("#modal").open) $("#modal").addEventListener("close", focusRecord, { once: true }); else focusRecord();
    }
    lastPoll = Date.now();
    pollTimer = setInterval(() => {
      if (document.hidden || !state.doc || pollInFlight) return;
      const latest = c().chat.turns.at(-1);
      const running = state.busy || latest?.status === "running" && Date.now() - Date.parse(latest.at) < 120000;
      if (!running && Date.now() - lastPoll < 15000) return;
      lastPoll = Date.now(); pollInFlight = true;
      refresh(false).catch(() => { state.syncError = true; updateSync(); }).finally(() => pollInFlight = false);
    }, 2500);
  }
  async function showTool(key, epoch) {
    state.doc = null; state.chatDraft = "";
    document.title = `${toolNames[key]} · LastMile`;
    const heading = h("div", { class: "page-head row between" }, h("div", {}, h("div", { class: "eyebrow" }, "Across your incidents"), h("h1", {}, toolNames[key])), btn("Refresh", () => route(), "quiet"));
    if (!state.me) {
      shell(h("div", { class: "stack" }, heading, notice("Choose your organisation and role to browse shared incident records."), h("p", { class: "callout-hint" }, "Use ", h("strong", {}, "Choose organisation & role"), " at the top."))); return;
    }
    if (key === "map") return nationalMap(heading, epoch);
    if (key === "requests") return requestsPage(heading, epoch);
    if (key === "resources") return placesPage(heading, epoch);
    if (key === "whowhat") return whoWhatPage(heading, epoch);
    if (key === "communications") return communicationsPage(heading, epoch);
    if (key === "activity") return activityPage(heading, epoch);
    const content = h("div", { class: "stack", "aria-busy": "true" }, heading, h("p", { role: "status" }, "Loading your accessible incident records…")); shell(content);
    try {
      const docs = await incidentDocs(epoch); if (!docs) return;
      content.replaceChildren(heading, notice(`Shared saved records for ${orgName(state.me.organisation)}. Actions remain inside the incident under your current permissions. Snapshot loaded ${dateTime(now())}; use Refresh for newer updates.`), ...groupedRecords(key, docs));
      content.removeAttribute("aria-busy");
    } catch (error) {
      if (epoch !== routeEpoch) return;
      content.removeAttribute("aria-busy"); content.append(h("div", { class: "error", role: "alert" }, "Records could not be refreshed. " + error.message, btn("Try again", () => route())));
    }
  }
  // Activity: everything recorded in your incidents, newest first, by day; each line opens the incident's timeline.
  const activityView = { incident: "", query: "", limit: 100 };
  async function activityPage(heading, epoch) {
    const content = h("div", { class: "stack", "aria-busy": "true" }, heading, h("p", { role: "status" }, "Loading activity…")); shell(content);
    let data;
    try { data = await api("/activity"); }
    catch (error) { if (epoch === routeEpoch) { content.removeAttribute("aria-busy"); content.append(h("div", { class: "error", role: "alert" }, "Activity could not load. " + error.message, btn("Try again", () => route()))); } return; }
    if (epoch !== routeEpoch) return;
    const search = input("activity-search", activityView.query, { type: "search", placeholder: "Search what happened…", "aria-label": "Search activity" });
    const incident = select("activity-incident", [["", "All incidents"], ...data.incidents.map(i => [i.id, `${i.title} · ${i.district}`])], activityView.incident);
    incident.setAttribute("aria-label", "Incident");
    const count = h("small", { role: "status" }), list = h("div", { class: "stack activity-days" }), more = h("div", {});
    const day = at => { const d = new Date(at), today = new Date(); const key = d.toDateString(); return key === today.toDateString() ? "Today" : key === new Date(today - 864e5).toDateString() ? "Yesterday" : new Intl.DateTimeFormat("en-GB", { dateStyle: "full" }).format(d); };
    const time = at => new Intl.DateTimeFormat("en-GB", { hour: "2-digit", minute: "2-digit" }).format(new Date(at));
    const update = () => {
      const q = activityView.query.trim().toLocaleLowerCase();
      const rows = data.activity.filter(e => (!activityView.incident || e.incident_id === activityView.incident) && (!q || `${e.label} ${e.detail} ${e.incident_title} ${e.district}`.toLocaleLowerCase().includes(q)));
      const shown = rows.slice(0, activityView.limit), days = [];
      for (const e of shown) { const label = day(e.at); if (days.at(-1)?.[0] !== label) days.push([label, []]); days.at(-1)[1].push(e); }
      count.textContent = `${rows.length.toLocaleString("en-GB")} ${rows.length === 1 ? "entry" : "entries"}${activityView.incident ? "" : ` in ${plural(new Set(rows.map(e => e.incident_id)).size, "incident")}`}`;
      list.replaceChildren(...(days.length ? days.map(([label, events]) => h("section", { class: "card stack activity-day", "aria-label": label }, h("h2", {}, label),
        h("ol", { class: "activity-list" }, events.map(e => h("li", {}, h("button", { type: "button", onclick: () => openRecord({ incident_id: e.incident_id, tab: "timeline" }) },
          h("time", { datetime: e.at, title: dateTime(e.at) }, time(e.at)), h("span", {}, h("strong", {}, e.label), h("small", {}, [!activityView.incident && e.incident_title, e.detail].filter(Boolean).join(" · ")))))))))
        : [empty(q || activityView.incident ? "Nothing matches. Try another search or incident." : "Nothing recorded yet in the incidents you take part in.")]));
      more.replaceChildren(...(rows.length > shown.length ? [btn(`Show ${Math.min(100, rows.length - shown.length)} more`, () => { activityView.limit += 100; update(); }, "quiet")] : []));
    };
    search.addEventListener("input", () => { activityView.query = search.value; activityView.limit = 100; update(); });
    incident.addEventListener("change", () => { activityView.incident = incident.value; activityView.limit = 100; update(); });
    content.replaceChildren(heading, h("p", { class: "muted" }, "Everything recorded in the incidents you take part in: reports, requests, decisions, messages and changes, with who acted inside each incident's timeline."),
      h("div", { class: "filter-bar" }, search, incident, count), list, more);
    content.removeAttribute("aria-busy"); update();
  }
  // Every incident this person can open, four at a time; null when they moved to another page meanwhile.
  async function incidentDocs(epoch) {
    const cards = await api("/incidents"), docs = [];
    for (let i = 0; i < cards.length; i += 4) {
      const batch = await Promise.all(cards.slice(i, i + 4).map(card => api(`/incidents/${card.id}`)));
      if (epoch !== routeEpoch) return null; docs.push(...batch);
    }
    return epoch === routeEpoch ? docs : null;
  }
  // One collapsible group per incident; open incidents start expanded, closed ones collapsed, and a search opens every group with a match.
  function groupedRecords(key, docs) {
    const rows = docs.flatMap(doc => toolRows(key, doc));
    const search = input("tool-search", "", { type: "search", placeholder: "Search incident, organisation or record…" });
    const count = h("small", { role: "status" }), list = h("div", { class: "stack tool-records" }), opened = toolGroupsOpen[key] ||= {};
    const card = row => h("article", { class: "card stack" }, h("div", { class: "row between" }, h("h3", {}, row.title), pill(row.status, row.tone)), h("p", {}, row.detail), row.extra, btn("Open in incident", () => {
      location.hash = new URLSearchParams({ incident: row.doc.id, tab: row.tab, record: row.id, pane: "data" });
    }, "quiet"));
    const update = () => {
      const query = search.value.trim().toLocaleLowerCase(), matches = rows.filter(row => `${row.title} ${row.detail} ${row.doc.title} ${row.doc.coordination.district} ${row.status}`.toLocaleLowerCase().includes(query));
      const groups = docs.map(doc => [doc, matches.filter(row => row.doc === doc)]).filter(([, found]) => found.length).sort(([a], [b]) => (a.status === "closed") - (b.status === "closed"));
      count.textContent = `${query ? `${matches.length} of ` : ""}${plural(rows.length, TOOL_NOUN[key] || "record")} in ${plural(groups.length, "incident")}`;
      list.replaceChildren(...(groups.length ? groups.map(([doc, found]) => {
        const waiting = found.filter(row => row.waiting).length;
        return h("details", { class: "card tool-group", open: query ? true : opened[doc.id] ?? doc.status !== "closed", ontoggle: e => { if (!search.value.trim()) opened[doc.id] = e.currentTarget.open; } },
          h("summary", {}, h("h2", {}, doc.title), h("span", { class: "tool-group-meta" }, pill(doc.coordination.district), doc.status === "closed" && pill("Closed"), h("small", {}, plural(found.length, TOOL_NOUN[key] || "record")), waiting > 0 && pill(`${waiting} ${TOOL_WAITING[key]}`, "warn"))),
          h("div", { class: "cards" }, found.map(card)));
      }) : [empty(query ? "No matching records. Try another search." : TOOL_EMPTY[key] || "No records yet. Open an incident to add and review the evidence.")]));
    };
    search.addEventListener("input", update); update();
    return [field("Search shared records", search), count, list];
  }
  // Communications: public SMS, emails and press releases across incidents, with their templates and media recipients.
  const commsView = { tab: "messages" }, commsCache = {};
  const MESSAGE_KINDS = ["sms", "email", "press_release"], LANG_NAMES = { en: "English", si: "Sinhala", ta: "Tamil" };
  const MEDIA_TYPE = { tv: "Television", radio: "Radio", newspaper: "Newspaper", online: "Online news", agency: "News agency", government: "Government information" };
  async function commsLists() {
    if (!commsCache.loaded || Date.now() - commsCache.loaded > 300000) {
      const [templates, media, population] = await Promise.all(["templates", "media", "population"].map(n => api(`/reference/${n}`)));
      Object.assign(commsCache, { templates: templates.records.filter(r => !r.retired), media: media.records.filter(r => !r.retired), population: population.records.filter(r => !r.retired), loaded: Date.now() });
    }
    return commsCache;
  }
  // What a phone network bills: 160 characters a message (153 when split); 70 (67) once Sinhala or Tamil letters appear.
  function smsLength(text = "") {
    const unicode = /[^\x00-\x7f]/.test(text), single = unicode ? 70 : 160, multi = unicode ? 67 : 153;
    const parts = text.length <= single ? 1 : Math.ceil(text.length / multi);
    return `${text.length} characters · ${parts === 1 ? "1 SMS" : `${parts} SMS parts`}`;
  }
  async function communicationsPage(heading, epoch) {
    const content = h("div", { class: "stack", "aria-busy": "true" }, heading, h("p", { role: "status" }, "Loading communications…")); shell(content);
    const tab = commsView.tab;
    let docs = null, data = null;
    try {
      await areas();
      if (tab === "messages") { docs = await incidentDocs(epoch); commsLists().catch(() => {}); } else data = await api(`/reference/${tab}`);
    } catch (error) { if (epoch === routeEpoch) { content.removeAttribute("aria-busy"); content.append(h("div", { class: "error", role: "alert" }, "Communications could not load. " + error.message, btn("Try again", () => route()))); } return; }
    if (epoch !== routeEpoch || (tab === "messages" && !docs)) return;
    const go = key => { commsView.tab = key; Object.assign(placesView, { query: "", district: "", limit: 100, selected: null }); route(); };
    const tabs = h("div", { class: "segmented places-tabs", role: "group", "aria-label": "Communications" },
      [["messages", "Messages"], ["templates", "Templates"], ["media", "Media recipients"]].map(([key, label]) => btn(label, () => go(key), "", { "aria-pressed": String(tab === key) })));
    content.replaceChildren(heading,
      h("p", { class: "muted" }, "Public SMS, emails and press releases from the incidents you take part in, and the templates and media desks they start from. Ask for a message in an incident's chat, or use Prepare document in its Documents tab. A communications reviewer approves the exact text before it is sent."),
      tabs, ...(tab === "messages" ? groupedRecords("communications", docs) : placesList(data)));
    content.removeAttribute("aria-busy");
  }
  // Placeholders filled from the incident, as the server does: {incident} {district} {areas} {details} {date} {time} {hotline} {issuer}.
  function fillTemplate(text, details) {
    const at = new Date(c().replay_at || now());
    const values = { incident: state.doc.title, district: c().district, areas: `${c().district} District`, details: details.trim(), hotline: "117", issuer: orgName(state.me.organisation),
      date: at.toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "numeric" }), time: at.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" }) };
    return (text || "").replace(/\{(\w+)\}/g, (m, k) => values[k] ?? m).replace(/ {2,}/g, " ").trim();
  }
  function sentToLong(d) {
    const to = d.sent_to || {};
    if (to.areas?.length) return `${to.areas.map(nameOf).join("; ")}${d.reach ? ` (about ${d.reach.toLocaleString("en-GB")} people)` : ""}`;
    if (to.outlets?.length) return to.outlets.map(id => commsCache.media?.find(m => m.id === id)?.name || id).join(", ");
    return "no one";
  }
  async function sendDialog(d) {
    const lists = await commsLists(); await areas();
    if (d.kind === "sms") {
      const g = state.areas.index, home = c().district, homeId = g.districts.find(x => x.name === home)?.id;
      const chosen = d.planned?.areas?.length ? d.planned.areas : [homeId];
      const people = Object.fromEntries(lists.population.map(r => [r.area || r.id, r.population]));
      const reach = h("p", { class: "reach", role: "status" });
      const recount = box => {
        const picked = [...box.querySelectorAll("input[name=areas]:checked")].map(x => x.value);
        const whole = new Set(picked.filter(a => g.districts.some(x => x.id === a)).map(a => state.areas.byId.get(a)?.name));
        const counted = picked.filter(a => !whole.has(state.areas.byId.get(a)?.district)), known = counted.filter(a => people[a] != null);
        const total = known.reduce((n, a) => n + people[a], 0).toLocaleString("en-GB");
        reach.textContent = !picked.length ? "Choose at least one area." : `${plural(picked.length, "area")} · about ${total} people live in ${known.length === counted.length ? "them" : "the whole districts chosen (DS populations are not listed)"} · ${smsLength(d.text)}`;
      };
      dialog("Send SMS", () => {
        const box = h("div", { class: "stack" },
          checkList("areas", [[homeId, `Whole ${home} District`], ...g.divisions.filter(x => x.district === home).map(x => [x.id, `${x.name} DS`])], chosen),
          h("details", { class: "inline-details" }, h("summary", {}, "Other districts"), checkList("areas", g.districts.filter(x => x.name !== home).map(x => [x.id, `Whole ${x.name} District`]), chosen)));
        box.addEventListener("change", () => recount(box)); queueMicrotask(() => recount(box));
        return [h("div", { class: "message-text send-preview" }, d.text), field("Send to everyone in", box), reach,
          notice("Recorded as sent to every phone in the chosen areas. No SMS gateway is connected, so no phone receives it.")];
      }, async form => command("send_document", { document_id: d.id, hash: d.hash, areas: form.getAll("areas") }), "Send SMS");
      return;
    }
    const chosen = d.planned?.outlets?.length ? d.planned.outlets : lists.media.filter(m => [d.language, "all"].includes(m.language)).map(m => m.id);
    const what = MESSAGE_KIND[d.kind].toLowerCase();
    dialog(`Send ${what}`, () => {
      const list = checkList("outlets", lists.media.map(m => [m.id, `${m.name} · ${MEDIA_TYPE[m.type] || m.type} · ${m.language === "all" ? "all languages" : LANG_NAMES[m.language]}`]), chosen);
      const only = types => list.querySelectorAll("input").forEach(x => { x.checked = types.includes(lists.media.find(m => m.id === x.value)?.type); });
      const quick = h("div", { class: "row wrap quick-picks" }, btn("All", () => only(Object.keys(MEDIA_TYPE)), "quiet"), btn("None", () => only([]), "quiet"),
        btn("Television and radio", () => only(["tv", "radio"]), "quiet"), btn("Papers and online", () => only(["newspaper", "online", "agency"]), "quiet"));
      return [h("div", { class: "message-text send-preview" }, d.body), field("Send to", h("div", { class: "stack" }, quick, list), `Starts with the desks that publish in ${LANG_NAMES[d.language]}. Add or change desks under Communications › Media recipients.`),
        notice("Recorded as sent to the chosen newsrooms. No mail server is connected, so nothing is emailed.")];
    }, async form => command("send_document", { document_id: d.id, hash: d.hash, outlets: form.getAll("outlets") }), `Send ${what}`);
  }
  // Places & stock: standing lists that exist before any incident (centres, stock, hospitals, gauges, population, contacts, items).
  const placesView = { tab: "centres", query: "", district: "", retired: false, limit: 100, selected: null };
  const placesCache = {};
  const PLACE_TONE = { centres: r => r._derived?.capacity == null ? "#b7791f" : "#1967d2", stock: r => ({ ready: "#1e8e3e", in_use: "#1967d2", repair: "#b7791f" })[r.status] || "#5f6b7a", health: () => "#c5221f", gauges: r => GAUGE_STATE[r._derived?.state]?.[1] || "#0b7285" };
  const GAUGE_STATE = { major: ["Major flood", "#b3261e", "danger"], minor: ["Minor flood", "#e8710a", "warn"], alert: ["Alert", "#c9a100", "warn"], normal: ["Normal", "#188038", "green"], no_reading: ["No reading", "#0b7285", ""] };
  const gaugeText = d => d?.state && d.state !== "no_reading" ? `${GAUGE_STATE[d.state][0]} · ${d.level}${d.trend === "rising" ? " ↑ rising" : d.trend === "falling" ? " ↓ falling" : ""}${d.stale ? " · old reading" : ""}` : "No reading yet";
  const PLACE_LEGEND = { centres: [["#1967d2", "Capacity by standard known"], ["#b7791f", "Capacity not known"]], stock: [["#1e8e3e", "Ready"], ["#1967d2", "In use"], ["#b7791f", "Under repair"]], health: [["#c5221f", "Hospital"]], gauges: [["#b3261e", "Major flood"], ["#e8710a", "Minor flood"], ["#c9a100", "Alert"], ["#188038", "Normal"], ["#0b7285", "No reading"]] };
  async function placesPage(heading, epoch) {
    const params = new URLSearchParams(location.hash.slice(1));
    if (params.get("list")) { placesView.tab = params.get("list"); placesView.selected = params.get("record"); placesView.query = ""; placesView.district = ""; history.replaceState(null, "", "#view=resources"); }
    const content = h("div", { class: "stack", "aria-busy": "true" }, heading, h("p", { role: "status" }, "Loading places and stock…")); shell(content);
    const tab = placesView.tab;
    let summary, data;
    try {
      [summary, data] = await Promise.all([api("/reference"), tab === "incidents" ? null : api(`/reference/${tab}`), areas()]);
      if (tab === "stock" || tab === "items") placesCache.items = tab === "items" ? data.records : (await api("/reference/items")).records;
    } catch (error) { if (epoch === routeEpoch) { content.removeAttribute("aria-busy"); content.append(h("div", { class: "error", role: "alert" }, "Places could not load. " + error.message, btn("Try again", () => route()))); } return; }
    if (epoch !== routeEpoch) return;
    const go = key => { placesView.tab = key; placesView.query = ""; placesView.district = ""; placesView.limit = 100; placesView.selected = null; route(); };
    const tabs = h("div", { class: "segmented places-tabs", role: "group", "aria-label": "Lists" },
      [...summary.datasets.filter(d => d.group !== "communications").map(d => [d.id, `${d.label} (${d.active.toLocaleString("en-GB")})`]), ["incidents", "In incidents"]].map(([key, label]) => btn(label, () => go(key), "", { "aria-pressed": String(tab === key) })));
    content.replaceChildren(heading,
      h("p", { class: "muted" }, "Standing lists every incident draws on: where people can shelter, what each organisation holds, hospitals, river gauges, population and who to call. Every change is versioned with who made it and why."), tabs);
    content.removeAttribute("aria-busy");
    if (tab === "incidents") { content.append(await placesInIncidents(epoch)); return; }
    content.append(...placesList(data));
  }
  function placeValue(f, v, r) {
    if (v == null || v === "") return ["bool", "int", "number", "lat", "lon"].includes(f.type) ? "Not known" : "—";
    if (f.type === "bool") return v ? "Yes" : "No";
    if (f.options) return f.options[v] || v;
    if (f.type === "area") return v === "national" ? "National" : nameOf(v);
    if (f.type === "item") return placesCache.items?.find(i => i.id === v)?.name || v;
    if (f.type === "date") return new Intl.DateTimeFormat("en-GB", { dateStyle: "medium" }).format(new Date(v + "T00:00:00"));
    if (f.type === "int") return Number(v).toLocaleString("en-GB") + (f.unit ? " " + f.unit : "");
    if (f.type === "number") return v + (r?.unit && /level|flood/.test(f.key) ? " " + r.unit : "");
    return String(v);
  }
  function capacityText(d) {
    if (!d || d.capacity == null) return "Not known";
    const why = { design: "design capacity", space: "floor area", toilets: "toilets" }[d.limited_by];
    return `${d.capacity.toLocaleString("en-GB")} people · limited by ${why}${d.complete ? "" : " (some facts missing)"}`;
  }
  function placesList(data) {
    const spec = data.spec, ds = data.dataset;
    const fields = Object.fromEntries(spec.fields.map(f => [f.key, f]));
    const nameKey = ["name", "label", "district", "item"].find(k => fields[k]);
    const listed = spec.fields.filter(f => f.list && f.key !== nameKey);
    const districtOf = r => { const a = r.area && state.areas.byId.get(r.area); return a ? (a.district || a.name) : (r.district || ""); };
    const search = input("places-search", placesView.query, { type: "search", placeholder: `Search ${spec.label.toLowerCase()}…`, "aria-label": `Search ${spec.label}` });
    const district = select("places-district", [["", "All districts"], ...state.areas.index.districts.map(d => [d.name, d.name])], placesView.district);
    district.setAttribute("aria-label", "District");
    const retired = input("places-retired", "", { type: "checkbox", checked: placesView.retired });
    const count = h("small", { role: "status", class: "result-count" });
    const detail = h("section", { class: "place-detail", "aria-live": "polite", "aria-label": `Selected ${spec.singular}` });
    const tableBox = h("div", { class: "table-scroll" }), more = h("div", {});
    const mapBox = spec.map ? h("div", { id: "places-map", class: "map places-map", role: "region", "aria-label": `${spec.label} map` }) : null;
    let layer = null;
    const title = r => ds === "stock" ? `${r._derived.item_name} · ${r.location_name}` : placeValue(fields[nameKey], r[nameKey], r);
    const visible = () => {
      const query = placesView.query.trim().toLocaleLowerCase();
      return data.records.filter(r => (placesView.retired || !r.retired) && (!placesView.district || districtOf(r) === placesView.district || r.district === placesView.district)
        && (!query || `${title(r)} ${listed.map(f => placeValue(f, r[f.key], r)).join(" ")} ${districtOf(r)}`.toLocaleLowerCase().includes(query)));
    };
    const select_ = (recordId, { scroll = true } = {}) => {
      placesView.selected = recordId;
      const r = data.records.find(x => x.id === recordId);
      detail.replaceChildren(...(r ? [placeCard(data, r, title(r), () => select_(null))] : []));
      tableBox.querySelectorAll("tr[data-id]").forEach(tr => tr.classList.toggle("selected", tr.dataset.id === recordId));
      if (r && scroll) detail.scrollIntoView({ block: "nearest" });
    };
    const update = () => {
      const rows = visible(), shown = rows.slice(0, placesView.limit);
      count.textContent = `${rows.length.toLocaleString("en-GB")} of ${(data.partial ? data.total : data.records.filter(r => placesView.retired || !r.retired).length).toLocaleString("en-GB")} ${spec.label.toLowerCase()}${data.partial && !placesView.query && !placesView.district ? " · search or pick a district to see more" : ""}`;
      const head = [fields[nameKey].label, ...(ds === "gauges" ? ["Latest reading"] : []), ...listed.map(f => f.label), ...(ds === "centres" ? ["Places by standard"] : []), ...(spec.map ? ["Map"] : [])];
      tableBox.replaceChildren(rows.length ? h("table", { class: "status-table places-table" },
        h("caption", { class: "sr-only" }, `${spec.label}. Select a name for details.`),
        h("thead", {}, h("tr", {}, head.map(text => h("th", { scope: "col" }, text)))),
        h("tbody", {}, shown.map(r => h("tr", { "data-id": r.id, class: r.id === placesView.selected ? "selected" : "" },
          h("th", { scope: "row" }, h("button", { type: "button", class: "link-button", onclick: () => select_(r.id) }, title(r)), r.retired && pill("Retired", "muted")),
          ds === "gauges" && h("td", {}, r._derived?.state && r._derived.state !== "no_reading" ? pill(gaugeText(r._derived), GAUGE_STATE[r._derived.state][2]) : h("span", { class: "muted" }, "No reading yet")),
          listed.map(f => h("td", { class: ["int", "number"].includes(f.type) ? "num" : "" }, f.key === "quantity" ? `${Number(r.quantity).toLocaleString("en-GB")} ${r._derived.unit}` : placeValue(f, r[f.key], r))),
          ds === "centres" && h("td", {}, capacityText(r._derived)),
          spec.map && h("td", {}, r.latitude == null ? h("span", { class: "muted" }, "No location") : "Yes")))))
        : empty(data.records.length ? "Nothing matches. Try another search or district." : `No ${spec.label.toLowerCase()} yet.`));
      more.replaceChildren(...(rows.length > shown.length ? [btn(`Show ${Math.min(100, rows.length - shown.length)} more`, () => { placesView.limit += 100; update(); }, "quiet")] : []));
      if (layer) { layer.clearLayers(); drawPlaceMarkers(layer, rows, ds, title, select_); }
    };
    // GN divisions are too many to send at once: search and district filter ask the server.
    let serverTimer = null;
    const fromServer = () => {
      clearTimeout(serverTimer);
      serverTimer = setTimeout(async () => {
        const dId = state.areas.index.districts.find(d => d.name === placesView.district)?.id;
        const params = new URLSearchParams({ ...(dId ? { area: dId } : {}), ...(placesView.query.trim().length >= 2 ? { q: placesView.query.trim() } : {}) });
        try { const fresh = await api(`/reference/${ds}?${params}`); data.records = fresh.records; data.partial = fresh.partial; update(); } catch (error) { toast(error.message); }
      }, 250);
    };
    search.addEventListener("input", () => { placesView.query = search.value; placesView.limit = 100; if (ds === "gn") fromServer(); else update(); });
    district.addEventListener("change", () => { placesView.district = district.value; placesView.limit = 100; if (ds === "gn") fromServer(); else update(); });
    retired.addEventListener("change", () => { placesView.retired = retired.checked; update(); });
    const file = input("places-file", "", { type: "file", accept: ".csv,text/csv", hidden: true, "aria-label": "CSV file to import" });
    // Irrigation Department "Water Level & Rainfall" bulletin: latest levels and any revised thresholds.
    const bulletinFile = input("gauge-bulletin", "", { type: "file", accept: ".pdf,.txt,application/pdf,text/plain", hidden: true, "aria-label": "Gauge bulletin to import" });
    bulletinFile.addEventListener("change", async () => {
      const chosen = bulletinFile.files[0]; bulletinFile.value = ""; if (!chosen) return;
      const bytes = new Uint8Array(await chosen.arrayBuffer()); let binary = "";
      for (let i = 0; i < bytes.length; i += 32768) binary += String.fromCharCode(...bytes.subarray(i, i + 32768));
      try {
        const result = await api("/reference/gauges/bulletin", { version: data.version, file: { name: chosen.name, base64: btoa(binary) } });
        const b = result.bulletin;
        toast(`Bulletin of ${dateTime(b.issued_at)}: ${plural(b.readings, "reading")}${b.revised.length ? `; levels revised for ${b.revised.join(", ")}` : ""}${b.not_found.length ? `; not in bulletin: ${b.not_found.length}` : ""}`);
        route();
      } catch (error) { toast(error.message); }
    });
    file.addEventListener("change", async () => {
      const chosen = file.files[0]; file.value = ""; if (!chosen) return;
      const text = await chosen.text(), rows = Math.max(0, text.trim().split(/\r?\n/).length - 1);
      dialog(`Import into ${spec.label}`, () => [notice(`${plural(rows, "row")} from ${chosen.name}. A row with an id updates that record; a row without one adds a record. If any row has a problem, nothing is saved and every problem is listed.`),
        field("Reason (optional)", input("reason", "", { maxlength: 300, placeholder: "For example: monthly stock count" }))],
      async form => { const result = await api(`/reference/${ds}/import`, { version: data.version, csv: text, reason: form.get("reason") || "" }); toast(`Imported: ${result.imported.added} added, ${result.imported.updated} updated, ${result.imported.unchanged} unchanged`); route(); }, "Import");
    });
    const actions = h("div", { class: "row wrap" },
      data.can_add && btn(`Add ${spec.singular}`, () => placeEditDialog(data), "primary"),
      btn("Export CSV", () => download(`/reference/${ds}/export`, `lastmile-${ds}.csv`), "quiet"),
      data.can_add && btn("Blank template", () => download(`/reference/${ds}/export?template=true`, `lastmile-${ds}-template.csv`), "quiet"),
      data.can_add && btn("Import CSV", () => file.click(), "quiet"), file,
      ds === "gauges" && has("coordinator") && btn("Import bulletin", () => bulletinFile.click(), "quiet"), ds === "gauges" && bulletinFile);
    const keepers = spec.editors?.includes("communications_reviewer") ? "Coordinators and communications reviewers keep this list up to date." : "Coordinators keep this list up to date.";
    const whoCanEdit = data.can_add ? (has("coordinator") || !spec.owned ? "You can change every record in this list." : `You can add and change ${orgShort(state.me.organisation)}'s own records.`)
      : spec.owned ? "Organisation supervisors change their own records; coordinators change any." : keepers;
    const changeWord = { add: "Added", update: "Changed", retire: "Retired", restore: "Restored", import: "Imported", bulletin: "Bulletin read" };
    const historyBox = h("details", { class: "card places-history" }, h("summary", {}, `Change history (${data.history.length})`),
      data.history.length ? h("ol", { class: "history-list" }, data.history.map(c => h("li", {},
        h("strong", {}, `${changeWord[c.action] || c.action}: ${c.record_name}`),
        h("small", { class: "muted" }, ` · version ${c.version} · ${dateTime(c.at)} · ${c.by_name || c.by} (${orgShort(c.organisation)})`),
        c.action === "update" && c.fields?.length > 0 && h("small", {}, ` · ${c.fields.map(k => fields[k]?.label || k).join(", ")}`),
        c.reason && h("p", {}, `“${c.reason}”`))))
        : h("p", { class: "muted" }, "No changes since the starting list was loaded."));
    const standards = ds === "centres" && h("details", { class: "card standards-note" }, h("summary", {}, "How places by standard are worked out"),
      h("p", {}, `Each centre holds the fewest of: its design capacity; floor area ÷ ${data.standards.space_m2_per_person.value} m² per person; working toilets × ${data.standards.people_per_toilet.value} people per toilet. Water at that number is ${data.standards.water_l_per_person_day.value} L per person per day.`),
      h("p", { class: "muted" }, "Sphere Handbook minimum standards. A planning figure for choosing centres, not today's free places: live occupancy and admission stay in each incident, with evidence."));
    const legendBox = mapBox && h("ul", { class: "places-legend", "aria-label": "Map key" }, (PLACE_LEGEND[ds] || []).map(([colour, text]) => h("li", {}, h("span", { class: "swatch", "data-colour": colour, "aria-hidden": "true" }), text)));
    legendBox?.querySelectorAll(".swatch").forEach(el => el.style.background = el.dataset.colour);
    const out = [
      h("section", { class: "card stack", "aria-labelledby": "places-title" },
        h("div", { class: "row between wrap" }, h("div", { class: "row wrap" }, h("h2", { id: "places-title" }, spec.label), pill(`Version ${data.version}`)), actions),
        h("p", {}, spec.about), h("small", { class: "muted" }, whoCanEdit)),
      standards,
      h("div", { class: "filter-bar places-filters" }, search, (fields.area || fields.district) && district, h("label", { class: "check-inline" }, retired, "Show retired"), count),
      mapBox && h("div", { class: "card map-card stack" }, mapBox, legendBox),
      detail, tableBox, more, historyBox].filter(Boolean);
    queueMicrotask(async () => {
      update();
      if (placesView.selected) select_(placesView.selected, { scroll: false });
      if (mapBox) { const made = await drawPlacesMap(mapBox); if (made && map === made.instance) { layer = made.layer; update(); } }
    });
    return out;
  }
  async function drawPlacesMap(box) {
    if (!window.L || !box.isConnected) return null;
    if (map) { dropMap(map); map = null; }
    guardCanvas();
    const instance = L.map(box, { renderer: L.canvas({ padding: .3 }), scrollWheelZoom: false, zoomSnap: .25, minZoom: 6.5, zoomAnimation: false, fadeAnimation: false, markerZoomAnimation: false });
    map = instance;
    instance.attributionControl.setPrefix(false).addAttribution("Boundaries: geoBoundaries · reference only");
    instance.setView([7.85, 80.7], 7);
    L.geoJSON(state.areas.districts, { interactive: false, style: { color: "#8da6ca", weight: 1, fillColor: "#eef3fa", fillOpacity: .6 } }).addTo(instance);
    const layer = L.layerGroup().addTo(instance);
    // A hidden or unsized pane has no size yet: fit Sri Lanka once it has one.
    const sized = new ResizeObserver(() => { if (box.clientWidth && box.clientHeight && map === instance) { instance.invalidateSize(); instance.fitBounds([[5.9, 79.6], [9.85, 81.9]], { animate: false }); sized.disconnect(); } });
    sized.observe(box);
    return { instance, layer };
  }
  function drawPlaceMarkers(layer, rows, ds, title, onSelect) {
    const tone = PLACE_TONE[ds] || (() => "#1967d2");
    for (const r of rows) if (r.latitude != null && r.longitude != null)
      L.circleMarker([r.latitude, r.longitude], { radius: ds === "health" ? 4 : 7, weight: 1.5, color: "#fff", fillColor: tone(r), fillOpacity: .95 })
        .bindTooltip(title(r)).on("click", () => onSelect(r.id)).addTo(layer);
  }
  function placeCard(data, r, name, close) {
    const spec = data.spec, ds = data.dataset, d = r._derived || {};
    const rows = spec.fields.filter(f => !["latitude", "longitude"].includes(f.key)).map(f => [f.label, f.key === "quantity" ? `${Number(r.quantity).toLocaleString("en-GB")} ${d.unit}` : placeValue(f, r[f.key], r), f.multiline]);
    if (ds === "templates" && r.channel === "sms") rows.push(["Length before filling in", smsLength(r.body)]);
    if (r.latitude != null) rows.push(["Location", `${r.latitude.toFixed(4)}, ${r.longitude.toFixed(4)}`]);
    const reading = ds === "gauges" && r.reading && h("div", { class: "capacity-box" }, h("strong", {}, `Latest: ${gaugeText(d)}`),
      h("p", {}, `${r.reading.level} ${r.unit} at ${dateTime(r.reading.at)}${r.reading.previous != null ? ` (previous ${r.reading.previous} ${r.unit})` : ""}${r.reading.rain_mm != null ? ` · ${r.reading.rain_mm} mm rain in 12 h` : ""}.`),
      h("small", { class: "muted" }, `From ${r.reading.source?.name || "a bulletin"}. Alert ${r.alert_level}, minor flood ${r.minor_flood}, major flood ${r.major_flood} ${r.unit}.`));
    const capacity = ds === "centres" && h("div", { class: "capacity-box" }, h("strong", {}, `Places by standard: ${capacityText(d)}`),
      h("ul", {}, [["Design capacity", d.by?.design], ["By floor area", d.by?.space], ["By toilets", d.by?.toilets]].map(([label, value]) => h("li", { class: d.limited_by && value === d.capacity ? "limit" : "" }, `${label}: ${value == null ? "not known" : value.toLocaleString("en-GB") + " people"}`))),
      d.water_l_day != null && h("p", {}, `Water at that number: ${d.water_l_day.toLocaleString("en-GB")} L a day.`));
    return h("article", { class: "card stack place-card" },
      h("div", { class: "row between wrap" }, h("div", { class: "row wrap" }, h("h3", {}, name), r.retired && pill("Retired", "muted")), iconButton("Close details", "close", close)),
      r.retired && notice(`Retired ${r.retired_at ? dateTime(r.retired_at) : ""} by ${r.retired_by || "unknown"}: ${r.retired_reason || "no reason given"}.`),
      capacity, reading,
      h("dl", { class: "place-facts" }, rows.flatMap(([k, v, multiline]) => [h("dt", {}, k), h("dd", { class: multiline ? "multiline" : null }, v)])),
      h("div", { class: "row wrap" },
        r._can_edit && !r.retired && btn("Edit", () => placeEditDialog(data, r), "primary"),
        r._can_edit && !r.retired && btn("Retire", () => dialog(`Retire ${name}`, () => [notice("Retired records leave the active list but keep their history, and can be restored."), field("Why is it retired?", input("reason", "", { required: true, minlength: 3, maxlength: 300, placeholder: "For example: building handed back to the school" }))],
          async form => { await api(`/reference/${ds}/${r.id}/retire`, { version: data.version, reason: form.get("reason") }); toast("Record retired"); route(); }, "Retire record"), "quiet"),
        r._can_edit && r.retired && btn("Restore", async () => { await api(`/reference/${ds}/${r.id}/retire`, { version: data.version, restore: true }); toast("Record restored"); placesView.retired = false; route(); }, "quiet"),
        !r._can_edit && h("small", { class: "muted" }, spec.owned ? `Only ${orgShort(r.owner)} or a coordinator can change this record.` : spec.editors?.includes("communications_reviewer") ? "Coordinators and communications reviewers keep this list up to date." : "Coordinators keep this list up to date.")));
  }
  function placeAreaSelect(name, value, { national = false, divisionsOnly = false, required = false } = {}) {
    const g = state.areas.index;
    const el = h("select", { name, class: "lm-select", required: required || null }, h("option", { value: "" }, "Choose an area"), national && h("option", { value: "national" }, "National"),
      g.districts.map(d => h("optgroup", { label: d.name }, !divisionsOnly && h("option", { value: d.id }, `${d.name} District (whole district)`),
        g.divisions.filter(x => x.district === d.name).map(x => h("option", { value: x.id }, `${x.name} DS`)))));
    el.value = value || ""; return el;
  }
  function placeEditDialog(data, record = null) {
    const spec = data.spec, ds = data.dataset, coordinator = has("coordinator"), today = new Date().toISOString().slice(0, 10);
    const control = f => {
      const v = record ? record[f.key] : f.key === "owner" ? state.me.organisation : f.key === "fictional" ? true : null;
      const name = "f_" + f.key, text = v == null ? "" : String(v), required = f.required ? true : null;
      if (f.type === "bool") return select(name, [["", "Not known"], ["true", "Yes"], ["false", "No"]], text);
      if (f.type === "org" || f.type === "contact_org" || f.type === "holder") { const options = Object.entries(f.options).filter(([k]) => coordinator || k === state.me.organisation); return select(name, options, text || options[0][0]); }
      if (f.options) { const el = select(name, [...(f.required ? [["", "Choose…"]] : [["", "Not known"]]), ...Object.entries(f.options)], text); if (required) el.required = true; return el; }
      if (f.type === "area") return placeAreaSelect(name, text, { national: !!f.national, divisionsOnly: ["centres", "stock"].includes(ds), required: !!f.required });
      if (f.type === "item") { const el = select(name, [["", "Choose an item"], ...(placesCache.items || []).filter(i => !i.retired).map(i => [i.id, `${i.name} (${i.unit})`])], text); el.required = true; return el; }
      if (f.type === "date") return input(name, text, { type: "date", max: today });
      if (f.key === "gn") {
        // Suggest the GN divisions of the chosen DS division; free text stays allowed.
        const list = h("datalist", { id: "gn-options" }), box = input(name, text, { maxlength: String(f.max || 80), list: "gn-options" });
        const load = async area => { if (!area) return; try { const r = await api(`/reference/gn?area=${encodeURIComponent(area)}`); list.replaceChildren(...r.records.map(g => h("option", { value: g.name }, g.name_si || ""))); } catch { /* suggestions are optional */ } };
        queueMicrotask(() => { box.after(list); const areaBox = box.form?.elements.namedItem("f_area"); if (areaBox) { load(areaBox.value); areaBox.addEventListener("change", () => load(areaBox.value)); } });
        return box;
      }
      if (["int", "number", "lat", "lon"].includes(f.type)) return input(name, text, { type: "number", step: f.type === "int" ? "1" : "any", min: f.type === "int" ? "0" : null, inputmode: f.type === "int" ? "numeric" : "decimal", required });
      if (f.multiline) { const box = h("textarea", { name, rows: "7", maxlength: String(f.max || 150), required }); box.value = text; return box; }
      return input(name, text, { maxlength: String(f.max || 150), required });
    };
    dialog(record ? `Edit ${spec.singular}` : `Add ${spec.singular}`, () => [
      notice(spec.owned && !coordinator ? `Saved for ${orgName(state.me.organisation)}. Leave a fact blank when it is not known; never estimate.` : "Leave a fact blank when it is not known; never estimate."),
      ...spec.fields.map(f => field(f.label + (f.unit ? ` (${f.unit})` : "") + (f.required ? "" : " · optional"), control(f), f.hint)),
      field("What changed and why · optional", input("reason", "", { maxlength: "300", placeholder: "For example: toilets repaired after inspection" }))],
    async form => {
      const out = record ? { id: record.id } : {};
      for (const f of spec.fields) {
        const raw = form.get("f_" + f.key);
        out[f.key] = raw === "" || raw == null ? null : f.type === "bool" ? raw === "true" : ["int", "number", "lat", "lon"].includes(f.type) ? Number(raw) : raw;
      }
      const result = await api(`/reference/${ds}`, { version: data.version, record: out, reason: form.get("reason") || "" });
      placesView.selected = result.saved; toast(result.unchanged ? "Nothing changed" : record ? "Changes saved" : `${spec.singular[0].toUpperCase() + spec.singular.slice(1)} added`); route();
    }, record ? "Save changes" : `Add ${spec.singular}`);
  }
  async function placesInIncidents(epoch) {
    const box = h("div", { class: "stack" }, h("p", { role: "status" }, "Loading resources confirmed in your incidents…"));
    try {
      const cards = (await api("/incidents")).filter(card => card.version), docs = [];
      for (let i = 0; i < cards.length; i += 4) docs.push(...await Promise.all(cards.slice(i, i + 4).map(card => api(`/incidents/${card.id}`))));
      if (epoch !== routeEpoch) return box;
      const assets = docs.flatMap(doc => doc.coordination.assets.map(a => ({ a, doc })));
      box.replaceChildren(h("p", { class: "muted" }, "Resources an organisation has confirmed inside an incident, with evidence. A resource registered in Places & stock links back to its standing record."),
        assets.length ? h("div", { class: "cards tool-records" }, assets.map(({ a, doc }) => h("article", { class: "card stack" },
          h("div", { class: "row between" }, pill(doc.coordination.district), pill(a.kind)), h("h2", {}, a.name), h("small", {}, doc.title),
          h("p", {}, `${orgName(a.owner)} · ${a.balance.available == null ? "Availability needs a check" : a.balance.available + " planning balance"}. ${a.balance.reason}`),
          a.resource_key && /^(centre|stock)-/.test(a.resource_key) && h("a", { href: "#" + new URLSearchParams({ view: "resources", list: a.resource_key.startsWith("centre-") ? "centres" : "stock", record: a.resource_key }) }, "Open its standing record"),
          btn("Open in incident", () => { location.hash = new URLSearchParams({ incident: doc.id, tab: "resources", record: a.id, pane: "data" }); }, "quiet"))))
          : empty("No resources confirmed in your incidents yet."));
    } catch (error) { box.replaceChildren(h("div", { class: "error", role: "alert" }, "Incident resources could not load. " + error.message)); }
    return box;
  }
  // Who does what where (3W): accepted work and service restoration by organisation, sector and area.
  const whoWhatView = { by: "area", district: "" };
  const SECTOR_NAMES = { water: "Water", food: "Food & relief", shelter: "Shelter", transport: "Transport", rescue: "Rescue & teams", roads: "Roads & access", power: "Power", health: "Health", communications: "Phone & internet", other: "Other" };
  async function whoWhatPage(heading, epoch) {
    const content = h("div", { class: "stack", "aria-busy": "true" }, heading, h("p", { role: "status" }, "Loading who does what where…")); shell(content);
    let rows;
    try { [rows] = await Promise.all([api("/3w"), areas()]); }
    catch (error) { if (epoch === routeEpoch) { content.removeAttribute("aria-busy"); content.append(h("div", { class: "error", role: "alert" }, "Could not load. " + error.message, btn("Try again", () => route()))); } return; }
    if (epoch !== routeEpoch) return;
    const districtOf = id => state.areas.byId.get(id)?.district || state.areas.byId.get(id)?.name || "";
    const views = h("div", { class: "segmented", role: "group", "aria-label": "Group by" });
    const district = select("w3-district", [["", "All districts"], ...[...new Set(rows.map(r => districtOf(r.area)))].filter(Boolean).sort().map(d => [d, d])], whoWhatView.district);
    district.setAttribute("aria-label", "District");
    const body = h("div", { class: "stack" }), count = h("small", { role: "status" });
    const statusWord = r => r.kind === "service" ? (r.status === "restoring" ? "restoring" : "service down") : ({ accepted: "accepted", in_progress: "in progress", done: "delivered", confirmed: "confirmed" })[r.status];
    const line = r => h("li", {}, h("a", { href: "#" + new URLSearchParams({ incident: r.incident_id, tab: r.kind === "request" ? "requests" : "map", ...(r.kind === "request" ? { record: r.record_id, pane: "data" } : {}) }) },
      h("strong", {}, orgShort(r.organisation)), ` · ${SECTOR_NAMES[r.sector] || r.sector}`), ` · ${[r.what, r.amount].filter(Boolean).join(", ") || "—"} `, pill(statusWord(r), ["done", "confirmed"].includes(r.status) ? "green" : r.status === "disrupted" ? "danger" : "warn"),
      h("small", { class: "muted" }, ` ${r.for ? `for ${orgShort(r.for)} · ` : ""}${r.incident_title}`));
    const update = () => {
      const shown = rows.filter(r => !whoWhatView.district || districtOf(r.area) === whoWhatView.district);
      views.replaceChildren(...[["area", "By area"], ["org", "By organisation"], ["matrix", "Matrix"]].map(([k, label]) => btn(label, () => { whoWhatView.by = k; update(); }, "", { "aria-pressed": String(whoWhatView.by === k) })));
      count.textContent = `${shown.length} ${shown.length === 1 ? "activity" : "activities"} by ${plural(new Set(shown.map(r => r.organisation)).size, "organisation")} in ${plural(new Set(shown.map(r => r.area)).size, "area")}`;
      if (!shown.length) { body.replaceChildren(empty("No accepted work or service restoration recorded yet in the incidents your organisation takes part in.")); return; }
      if (whoWhatView.by === "matrix") {
        const orgs = [...new Set(shown.map(r => r.organisation))].sort(), sectors = [...new Set(shown.map(r => r.sector))].sort();
        body.replaceChildren(h("div", { class: "table-scroll" }, h("table", { class: "status-table w3-matrix" }, h("caption", { class: "sr-only" }, "Activities by organisation and sector"),
          h("thead", {}, h("tr", {}, h("th", { scope: "col" }, "Organisation"), sectors.map(k => h("th", { scope: "col" }, SECTOR_NAMES[k] || k)))),
          h("tbody", {}, orgs.map(o => h("tr", {}, h("th", { scope: "row" }, orgName(o)), sectors.map(k => { const n = shown.filter(r => r.organisation === o && r.sector === k); return h("td", { class: "num" }, n.length ? `${n.length} · ${plural(new Set(n.map(r => r.area)).size, "area")}` : "—"); })))))));
        return;
      }
      const key = whoWhatView.by === "area" ? r => r.area : r => r.organisation;
      const groups = [...new Set(shown.map(key))].map(k => [k, shown.filter(r => key(r) === k)]).sort((a, b) => b[1].length - a[1].length);
      body.replaceChildren(...groups.map(([k, items]) => h("section", { class: "dash-card stack", "aria-label": whoWhatView.by === "area" ? nameOf(k) : orgName(k) },
        h("h2", {}, whoWhatView.by === "area" ? nameOf(k) : orgName(k)), h("ul", { class: "w3-list" }, items.map(line)))));
    };
    district.addEventListener("change", () => { whoWhatView.district = district.value; update(); });
    const csv = () => {
      const cells = v => `"${String(v ?? "").replaceAll('"', '""')}"`;
      const text = ["organisation,sector,area,district,what,amount,status,for,incident,updated", ...rows.map(r => [orgName(r.organisation), SECTOR_NAMES[r.sector] || r.sector, nameOf(r.area), districtOf(r.area), r.what, r.amount, statusWord(r), r.for ? orgName(r.for) : "", r.incident_title, r.at].map(cells).join(","))].join("\n");
      const url = URL.createObjectURL(new Blob([text], { type: "text/csv" })); const link = h("a", { href: url, download: "lastmile-3w.csv" }); link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
    };
    content.replaceChildren(heading,
      h("p", { class: "muted" }, "Who is delivering what, and where: accepted requests and services being restored in the incidents your organisation takes part in. Use it to spot gaps and duplication."),
      h("div", { class: "filter-bar" }, views, district, btn("Export CSV", csv, "quiet"), count), body);
    content.removeAttribute("aria-busy");
    update();
  }
  // Requests across incidents: who asked whom, grouped by incident, with what your organisation must supply first.
  const requestView = { show: null, query: "" };
  async function requestsPage(heading, epoch) {
    const content = h("div", { class: "stack", "aria-busy": "true" }, heading, h("p", { role: "status" }, "Loading requests…")); shell(content);
    let groups;
    try { groups = await api("/requests"); }
    catch (error) { if (epoch === routeEpoch) { content.removeAttribute("aria-busy"); content.append(h("div", { class: "error", role: "alert" }, "Requests could not load. " + error.message, btn("Try again", () => route()))); } return; }
    if (epoch !== routeEpoch) return;
    const mine = state.me.organisation;
    // Your organisation supplies it and it is not delivered yet.
    const supplying = a => a.to === mine && ["asked", "recommended", "accepted", "in_progress"].includes(a.status);
    const yourSteps = a => (a.can || []).filter(step => !["comment", "cancel", "escalate"].includes(step));
    const needsYou = a => supplying(a) || yourSteps(a).length > 0;
    const all = groups.flatMap(g => g.asks.map(a => ({ ...a, group: g })));
    const counts = { attention: all.filter(a => askOpen(a) && needsYou(a)).length, to: all.filter(a => a.to === mine).length, from: all.filter(a => a.from === mine).length, open: all.filter(askOpen).length, all: all.length };
    // Each role starts on what needs it; the choice is kept while the role stays the same.
    const who = `${mine}/${state.me.job_role || state.me.id}`;
    if (requestView.who !== who) { requestView.who = who; requestView.show = null; }
    requestView.show ||= counts.attention ? "attention" : "open";
    const views = [["attention", "Needs you"], ["to", "To us"], ["from", "From us"], ["open", "All open"], ["all", "All"]];
    const filters = h("div", { class: "segmented", role: "group", "aria-label": "Show requests" });
    const search = input("request-search", requestView.query, { type: "search", placeholder: "Search request, incident or organisation…", "aria-label": "Search requests" });
    const list = h("div", { class: "stack request-groups" }), count = h("small", { role: "status" });
    const matches = a => ({ attention: askOpen(a) && needsYou(a), to: a.to === mine, from: a.from === mine, open: askOpen(a), all: true })[requestView.show];
    const order = { asked: 0, recommended: 1, accepted: 2, in_progress: 3, done: 4, confirmed: 5, declined: 6, cancelled: 7 };
    const row = a => {
      const supply = supplying(a), steps = yourSteps(a), titleId = `req-${a.id}`;
      return h("li", {}, h("button", { type: "button", class: "request-row" + (askOpen(a) && needsYou(a) ? " needs-you" : "") + (supply ? " supplying" : ""), "aria-describedby": titleId,
          onclick: () => { location.hash = new URLSearchParams({ incident: a.group.incident_id, tab: "requests", record: a.id, pane: "data" }); } },
        h("span", { class: "attention-icon", "aria-hidden": "true" }, kindIcon.ask),
        h("span", { class: "request-main" },
          h("strong", { id: titleId }, a.title),
          h("span", { class: "ask-route" }, h("span", { class: a.from === mine ? "mine" : "" }, orgShort(a.from)), h("span", { class: "route-arrow", "aria-hidden": "true" }, actionIcon("forward")), h("span", { class: "sr-only" }, " to "), h("span", { class: a.to === mine ? "mine" : "" }, orgShort(a.to))),
          h("small", { class: "muted" }, [askKindName(a.kind), askAmount(a), a.due_at && `needed by ${dateTime(a.due_at)}`, `sent ${ago(a.created_at)}`].filter(Boolean).join(" · ")),
          supply && h("span", { class: "supply-note" }, "Your organisation supplies this"),
          a.escalation && h("span", { class: "escalation-note" }, `Escalated to ${LADDER_NAME[a.escalation.to] || orgShort(a.escalation.to)} · ${a.escalation.reason}`),
          a.priority && a.priority !== "normal" && h("span", { class: "sr-only" }, ` ${PRIORITY[a.priority][0]} priority`),
          steps.length > 0 && h("span", { class: "step-note" }, `Your step: ${steps.map(step => stepLabel(a, step)).join(" or ")}`)),
        a.priority && a.priority !== "normal" && pill(PRIORITY[a.priority][0], PRIORITY[a.priority][1]), pill(askStatus(a), askTone(a)), h("span", { class: "chevron", "aria-hidden": "true" }, "›")));
    };
    const update = () => {
      const query = requestView.query.trim().toLocaleLowerCase();
      const visible = groups.map(g => ({ g, asks: g.asks.map(a => ({ ...a, group: g })).filter(a => matches(a) && `${a.title} ${g.title} ${g.district} ${orgName(a.from)} ${orgName(a.to)} ${orgShort(a.from)} ${orgShort(a.to)}`.toLocaleLowerCase().includes(query))
        .sort((x, y) => (needsYou(y) && askOpen(y)) - (needsYou(x) && askOpen(x)) || order[x.status] - order[y.status] || Date.parse(y.created_at) - Date.parse(x.created_at)) }))
        .filter(x => x.asks.length).sort((x, y) => y.asks.filter(a => askOpen(a) && needsYou(a)).length - x.asks.filter(a => askOpen(a) && needsYou(a)).length);
      filters.replaceChildren(...views.map(([key, label]) => btn(`${label} (${counts[key]})`, () => { requestView.show = key; update(); }, "", { "aria-pressed": String(requestView.show === key) })));
      const shown = visible.reduce((n, x) => n + x.asks.length, 0);
      count.textContent = `${plural(shown, "request")} in ${plural(visible.length, "incident")}`;
      list.replaceChildren(...(visible.length ? visible.map(({ g, asks }) => {
        const waiting = asks.filter(a => askOpen(a) && needsYou(a)).length;
        return h("section", { class: "dash-card request-group", "aria-labelledby": `group-${g.incident_id}` },
          h("div", { class: "row between wrap" }, h("div", { class: "row wrap" }, h("h2", { id: `group-${g.incident_id}` }, g.title), pill(g.district), g.status === "closed" && pill("Closed"), waiting > 0 && pill(`${waiting} need${waiting === 1 ? "s" : ""} you`, "warn")),
            h("a", { class: "button-link with-icon", href: "#" + new URLSearchParams({ incident: g.incident_id, tab: "requests" }) }, actionLabel("Open incident requests", "forward", true))),
          h("ul", { class: "request-rows" }, asks.map(row)));
      }) : [empty(all.length ? (requestView.show === "attention" ? "Nothing needs your organisation right now. Try All open." : "No requests match. Try another view or search.") : "No requests yet in the incidents your organisation takes part in. Send one from an incident with ↗ New request.")]));
    };
    search.addEventListener("input", () => { requestView.query = search.value; update(); });
    content.replaceChildren(heading,
      h("p", { class: "muted" }, `Requests between organisations in the incidents ${orgName(mine)} takes part in. Highlighted requests are ones your organisation must supply or act on. Select one to open it in its incident.`),
      h("div", { class: "incident-tools board-tools" }, search, filters, count), list);
    content.removeAttribute("aria-busy");
    update();
  }
  // Situation map: one national picture across every incident this role can see.
  async function nationalMap(heading, epoch) {
    const content = h("div", { class: "stack", "aria-busy": "true" }, heading, h("p", { role: "status" }, "Loading the national picture…")); shell(content);
    let data;
    try { [data] = await Promise.all([api("/dashboard"), areas()]); }
    catch (error) { if (epoch === routeEpoch) { content.removeAttribute("aria-busy"); content.append(h("div", { class: "error", role: "alert" }, "The map could not load. " + error.message, btn("Try again", () => route()))); } return; }
    if (epoch !== routeEpoch) return;
    const pic = data.picture, v = data.vocabulary;
    state.nationalLayer ||= "warnings"; state.nationalSector ||= "all";
    const fmt = { dateTime, orgName, link: r => h("a", { class: "with-icon", href: "#" + new URLSearchParams({ incident: r.incident_id, tab: "map" }) }, actionLabel(`Open ${r.incident_title}`, "forward", true)) };
    const detail = h("aside", { id: "area-detail", class: "area-detail", "aria-live": "polite", "aria-label": "Selected area" });
    const show = id => { state.nationalArea = id; detail.replaceChildren(id ? areaCard(state.areas, pic, id, v, fmt) : h("div", { class: "area-empty" }, h("strong", {}, "Choose an area"), h("p", {}, "Select a DS division on the map or in the list below."))); };
    const legendSlot = h("div", {});
    const sector = select("national_sector", [["all", "All services (worst)"], ...SECTOR_ORDER.map(k => [k, v.sectors[k]])], state.nationalSector);
    sector.setAttribute("aria-label", "Service shown");
    let ctl = null;
    const refresh = () => { sector.hidden = state.nationalLayer !== "services"; legendSlot.replaceChildren(legend(state.nationalLayer, state.nationalSector, v)); ctl?.update(null, { layer: state.nationalLayer, sector: state.nationalSector }); };
    sector.addEventListener("change", () => { state.nationalSector = sector.value; refresh(); });
    const live = pic.warnings.filter(w => !w.ended_at && (!w.valid_until || Date.parse(w.valid_until) > Date.parse(pic.as_of))).sort((a, b) => WARNING[b.level].rank - WARNING[a.level].rank);
    const severity = { working: 0, unknown: 1, restoring: 2, disrupted: 3 };
    const problem = Object.entries(pic.areas).map(([id, s]) => ({ id, s, rank: (s.warnings[0] ? WARNING[s.warnings[0].level].rank * 10 : 0) + Math.max(0, ...Object.values(s.services).map(r => severity[r.level])) }))
      .filter(x => x.rank >= 2).sort((a, b) => b.rank - a.rank || nameOf(a.id).localeCompare(nameOf(b.id)));
    const mapBox = h("div", { id: "status-map", class: "map status-map tall" });
    // Reference layers from Places & stock, drawn over the status colours.
    state.nationalOverlays ||= { centres: false, health: false, gauges: true };
    const overlayKeys = [["centres", "Listed centres"], ["health", "Hospitals"], ["gauges", "River gauges"]];
    const overlayLayers = {};
    const drawOverlays = async () => {
      if (!ctl?.map) return;
      for (const [key] of overlayKeys) {
        overlayLayers[key]?.remove(); overlayLayers[key] = null;
        if (!state.nationalOverlays[key]) continue;
        placesCache["list_" + key] ||= (await api(`/reference/${key}`)).records;
        if (!ctl?.map || !mapBox.isConnected || map !== ctl) return;  // the map went away while the list loaded
        const layer = L.layerGroup(), tone = PLACE_TONE[key];
        for (const r of placesCache["list_" + key]) if (!r.retired && r.latitude != null)
          L.circleMarker([r.latitude, r.longitude], { radius: key === "health" ? 3.5 : key === "gauges" ? 6 : 5, weight: 1.5, color: "#fff", fillColor: tone(r), fillOpacity: .95 })
            .bindTooltip(key === "gauges" ? `${r.name} (${r.river}) · ${gaugeText(r._derived)}` : key === "centres" ? `${r.name} · ${capacityText(r._derived)}` : r.name)
            .on("click", () => { location.hash = new URLSearchParams({ view: "resources", list: key, record: r.id }); }).addTo(layer);
        overlayLayers[key] = layer.addTo(ctl.map);
      }
    };
    const overlayBox = h("div", { class: "overlay-toggles", role: "group", "aria-label": "Reference layers" }, h("span", { class: "muted" }, "Also show:"),
      overlayKeys.map(([key, label]) => h("label", { class: "check-inline" }, input("overlay_" + key, "", { type: "checkbox", checked: state.nationalOverlays[key], onchange: e => { state.nationalOverlays[key] = e.currentTarget.checked; drawOverlays().catch(error => toast(error.message)); } }), label)));
    content.replaceChildren(heading,
      h("section", { class: "card stack map-card", "aria-labelledby": "national-map-title" },
        h("div", { class: "row between wrap" }, h("h2", { id: "national-map-title" }, "Sri Lanka status"), h("a", { class: "button-link with-icon", href: "/public", target: "_blank", rel: "noopener" }, actionLabel("Public page", "request", true))),
        h("div", { class: "map-toolbar" }, mapLayerButtons("nationalLayer", refresh), sector), overlayBox,
        h("div", { class: "map-layout" }, mapBox, detail), legendSlot,
        h("small", { class: "muted" }, `${plural(data.incidents.filter(i => i.status !== "closed").length, "open incident")} you can see · picture as of ${dateTime(pic.as_of)}. Reference boundaries only; a blank area means nothing was reported.`)),
      h("section", { class: "card stack", "aria-labelledby": "national-warnings-title" }, h("h2", { id: "national-warnings-title" }, `Official warnings in force (${live.length})`),
        live.length ? h("ul", { class: "warning-list" }, live.map(w => h("li", { class: `warning-item ${w.level}` }, h("div", { class: "row between wrap" }, h("span", { class: `warning-chip ${w.level}` }, h("span", { "aria-hidden": "true" }, WARNING[w.level].icon), `${v.warning_levels[w.level]} · ${v.hazards[w.hazard]}`), h("strong", {}, v.issuers[w.issuer])),
          h("p", {}, w.advice), h("small", { class: "muted" }, `${w.areas.map(nameOf).join("; ")} · `, h("a", { href: "#" + new URLSearchParams({ incident: w.incident_id, tab: "map" }) }, w.incident_title)))))
          : h("p", { class: "muted" }, "No official warning recorded in your incidents.")),
      h("section", { class: "card stack", "aria-labelledby": "problem-areas-title" }, h("h2", { id: "problem-areas-title" }, `Areas needing attention (${problem.length})`),
        problem.length ? h("ul", { class: "problem-list" }, problem.map(({ id, s }) => h("li", {}, h("button", { type: "button", class: "link-button", onclick: () => { show(id); ctl?.select(id, { zoom: true }); mapBox.scrollIntoView({ block: "nearest" }); } }, nameOf(id)),
          s.warnings[0] && h("span", { class: `warning-chip ${s.warnings[0].level}` }, h("span", { "aria-hidden": "true" }, WARNING[s.warnings[0].level].icon), v.hazards[s.warnings[0].hazard]),
          SECTOR_ORDER.filter(k => ["disrupted", "restoring"].includes(s.services[k]?.level)).map(k => h("span", { class: `status-chip ${s.services[k].level}` }, h("span", { class: "status-icon", "aria-hidden": "true" }, STATUS[s.services[k].level].icon), `${v.sectors[k]}: ${v.levels[s.services[k].level]}`)))))
          : h("p", { class: "muted" }, "No area has a warning or a service problem reported.")));
    content.removeAttribute("aria-busy");
    show(state.nationalArea || null); refresh();
    ctl = await drawStatusMap(mapBox, pic, { layer: state.nationalLayer, sector: state.nationalSector, vocab: v, selected: state.nationalArea, zoom: true, fetchLayer: meta => api(`/incidents/${meta.incident_id}/layers/${meta.id}`), label: "Sri Lanka: warnings, people affected and services by DS division", onSelect: show });
    map = ctl;
    if (ctl && state.nationalArea) ctl.select(state.nationalArea, { zoom: true });
    drawOverlays().catch(() => {});
  }
  const toolGroupsOpen = {}, TOOL_NOUN = { sources: "report", tasks: "record", communications: "message" };
  const TOOL_WAITING = { sources: "to review", tasks: "open", communications: "not sent" };
  const TOOL_EMPTY = { tasks: "No needs or assignments yet. Inside an incident, a need is recorded from a report and assigned to the body that can meet it.", communications: "No messages yet. Inside an incident, ask in the chat (for example: create an SMS for Kothmale saying go to the temple hall) or use Prepare document." };
  // An assignment is open until its outcome is reviewed; it can be a place (a safety centre), a thing (a bowser) or a team.
  const ASSIGNMENT_STATE = { offered: ["Waiting for acceptance", "open"], clarification: ["Question asked", "open"], accepted: ["Accepted", "open"], mobilised: ["On the way", "open"], reported_complete: ["Reported done", "open"], completed: ["Done", "done"], rejected: ["Declined", "closed"], cancelled: ["Cancelled", "closed"], expired: ["Expired", "closed"] };
  const MESSAGE_KIND = { sms: "SMS", email: "Email", press_release: "Press release", situation: "Situation report", handover: "Shift handover", letter: "Letter" };
  const MESSAGE_STATE = { draft: ["Draft", "warn"], approved_exercise: ["Approved", ""], sent: ["Sent", "green"], retracted: ["Retracted", ""], handover_accepted: ["Handover received", "green"], issued: ["Issued", "green"] };
  function toolRows(key, doc) {
    const coord = doc.coordination;
    const row = (record, title, detail, tab, extra = {}) => ({ doc, id: record.id, title, detail, status: record.status || record.kind || "Unknown", tab, ...extra });
    if (key === "sources") return doc.sources.map(s => row(s, s.title, `${orgName(s.organisation)} · observed ${dateTime(s.observed_at)}`, "sources", { waiting: s.status === "reported" }));
    if (key === "tasks") {
      const need = id => coord.requests.find(r => r.id === id), asset = id => coord.assets.find(a => a.id === id);
      const assignments = coord.offers.map(o => {
        const r = need(o.request_id), a = asset(o.asset_id), [label, phase] = ASSIGNMENT_STATE[o.status] || [o.status, "open"];
        return row(o, a?.name || o.note || r?.title || "Assignment", `Assignment · ${o.quantity} ${r?.unit || ""} for “${r?.title || "a need"}” · ${orgName(o.recipient)}${o.due_at ? ` · due ${dateTime(o.due_at)}` : ""}`, "requests",
          { status: label, tone: phase === "done" ? "green" : phase === "open" ? "warn" : "", waiting: phase === "open", order: phase === "open" ? 0 : 2 });
      });
      const needs = coord.requests.map(r => row(r, r.title, `Need · ${r.quantity ?? "Unknown"} ${r.unit} · recorded by ${orgName(r.organisation)}`, "requests", { status: r.status === "open" ? "Open need" : r.status.replaceAll("_", " "), order: r.status === "open" ? 1 : 3 }));
      return [...assignments, ...needs].sort((a, b) => a.order - b.order);
    }
    if (key === "communications") return coord.documents.filter(d => d.kind !== "handover").map(d => {
      const [label, tone] = MESSAGE_STATE[d.status] || [d.status.replaceAll("_", " "), ""];
      const preview = (d.text || "").replace(/\s*\n+\s*/g, " ");
      if (d.kind === "letter") return row(d, `Letter: ${d.subject}`, `${d.reference} · ${orgName(d.issuer)} to ${d.to.map(orgShort).join(", ")} · issued ${dateTime(d.issued_at)}`, "documents",
        { status: label, tone, extra: h("p", { class: "message-text" }, preview.length > 220 ? preview.slice(0, 220) + "…" : preview) });
      return row(d, d.subject || (d.template_name ? `${MESSAGE_KIND[d.kind]}: ${d.template_name}` : MESSAGE_KIND[d.kind] || d.kind), `${MESSAGE_KIND[d.kind] || d.kind} · ${orgName(d.issuer)} · ${d.status === "sent" ? `sent ${dateTime(d.sent_at)} to ${sentTo(d)}` : `prepared ${dateTime(d.cutoff)} · not sent`}`, "documents",
        { status: label, tone, waiting: d.status !== "sent" && d.status !== "retracted" && d.kind !== "situation", extra: preview && h("p", { class: "message-text" }, preview.length > 220 ? preview.slice(0, 220) + "…" : preview) });
    });
    return [];
  }
  function sentTo(d) {
    const to = d.sent_to || d.planned || {};
    if (to.areas?.length) return `${to.areas.length > 2 ? plural(to.areas.length, "area") : to.areas.map(nameOf).join(" and ")}${d.reach ? ` · about ${d.reach.toLocaleString("en-GB")} people` : ""}`;
    if (to.outlets?.length) return plural(to.outlets.length, "media recipient");
    return "no one yet";
  }
  async function drawDirectoryMap(docs) {
    const container = $("#directory-map"); if (!container || !window.L) return;
    if (!state.boundaries) { const response = await fetch("/static/data/sri-lanka-districts.geojson"); if (!response.ok) throw new Error("Map unavailable"); state.boundaries = await response.json(); }
    if (!container.isConnected) return;
    const instance = L.map(container, { scrollWheelZoom: false, attributionControl: false }); map = instance;
    const boundaries = L.geoJSON(state.boundaries, { style: { color: "#8da6ca", weight: 1, fillColor: "#e1ebf9", fillOpacity: .65 } }).addTo(instance);
    instance.fitBounds(boundaries.getBounds(), { padding: [10, 10] });
    for (const doc of docs) for (const asset of doc.coordination.assets) if (asset.latitude != null && asset.longitude != null) L.circleMarker([asset.latitude, asset.longitude], { radius: 8, color: asset.balance.available == null ? "#b7791f" : "#1967d2", fillOpacity: .8 }).addTo(instance).bindPopup(h("div", {}, h("strong", {}, asset.name), h("p", {}, doc.title), h("p", {}, orgName(asset.owner)), h("a", { href: "#" + new URLSearchParams({ incident: doc.id, tab: "resources", record: asset.id, pane: "data" }) }, "Open resource in incident")));
  }
  async function refresh(always = true) {
    if (!state.doc) return;
    const incidentId = state.doc.id, identity = state.demoToken || state.me.id;
    let doc;
    try { doc = await api(`/incidents/${incidentId}`); }
    catch (error) { state.syncError = true; updateSync(); throw error; }
    if (state.doc?.id !== incidentId || identity !== (state.demoToken || state.me.id)) return;
    state.syncedAt = now(); state.syncError = false; updateSync();
    if (doc.version < state.doc.version || doc.version === state.doc.version && Date.parse(doc.updated_at) < Date.parse(state.doc.updated_at)) return;
    if (always || JSON.stringify(doc) !== JSON.stringify(state.doc)) {
      const active = document.activeElement;
      // Keep drafts/focus while another organisation updates the incident.
      if (!always && active?.closest("#modal")) return;
      if (!always && state.doc.version === doc.version) {
        const sharedMetadataChanged = JSON.stringify(c().notifications) !== JSON.stringify(doc.coordination.notifications) || JSON.stringify(c().role_history) !== JSON.stringify(doc.coordination.role_history);
        state.doc = doc; if (sharedMetadataChanged) renderData(); else renderChatHistory(); return;
      }
      state.doc = doc;
      if (!always && active?.id === "chat-message") { renderData(); renderChatHistory(); } else renderIncident();
    }
  }
  function renderIncident() {
    const doc = state.doc;
    if (!doc) return;
    const focus = focusSnapshot();
    const previousMessages = $("#messages"), messageScroll = previousMessages && previousMessages.scrollHeight - previousMessages.scrollTop - previousMessages.clientHeight > 70 ? previousMessages.scrollTop : null;
    document.title = `${doc.title} · LastMile`;
    // One compact bar: identity and freshness on the left, role and view controls on the right.
    const content = h("div", { class: "stack incident-shell" }, h("div", { class: "incident-bar" },
        h("div", { class: "incident-identity" }, h("a", { class: "back-link", href: "#", "aria-label": "Back to Dashboard", title: "Back to Dashboard" }, actionIcon("back")),
          h("div", { class: "incident-title" }, h("h1", {}, doc.title), h("div", { class: "incident-meta" }, pill(c().district), orgButton(), pill(doc.status === "closed" ? "Closed" : c().phase || "response"), h("span", { id: "sync-status", class: "sync-status", role: "status", "aria-live": "polite" }, syncLabel()), btn(actionIcon("refresh"), () => refresh(), "quiet icon-btn", { "aria-label": "Refresh", title: "Refresh now" }), h("small", { class: "incident-version", title: "Saved version" }, `v${doc.version}`)))),
        h("div", { class: "incident-controls" }, roleBar(),
          btn(state.collapsed ? "Show chat" : "Collapse chat", () => { state.collapsed = !state.collapsed; if (!state.collapsed) state.mobileView = "chat"; renderIncident(); }, "compact", { id: "toggle-chat", "aria-controls": "incident-workspace", "aria-expanded": String(!state.collapsed) }))),
      h("div", { class: "mobile-pane-switch", role: "group", "aria-label": "Workspace view" }, btn("Chat", () => { state.mobileView = "chat"; state.collapsed = false; renderIncident(); }, "", { id: "mobile-chat", "aria-pressed": String(state.mobileView === "chat" && !state.collapsed) }), btn("Live data", () => { state.mobileView = "data"; renderIncident(); }, "", { id: "mobile-data", "aria-pressed": String(state.mobileView === "data" || state.collapsed) })),
      h("div", { id: "incident-workspace", class: `workspace${state.collapsed ? " collapsed" : ""} mobile-${state.mobileView}` }, !state.collapsed && chatPanel(), h("section", { id: "data-area", class: "stack", "aria-label": "Incident data" })));
    shell(content); fitChat(); renderData();
    if (messageScroll != null && $("#messages")) $("#messages").scrollTop = messageScroll;
    restoreFocus(focus);
    rememberView();
  }
  function chatPanel() {
    // Enter sends; Shift+Enter (or an IME composition) keeps typing.
    const text = h("textarea", { id: "chat-message", name: "text", rows: 1, required: true, maxlength: 20000, placeholder: "Ask, describe an update, or attach a report…", oninput: e => { state.chatDraft = e.target.value; drafts.set(draftKey(), state.chatDraft); }, onkeydown: e => { if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); e.currentTarget.form.requestSubmit(); } } }, state.chatDraft);
    const sendIcon = document.createElementNS("http://www.w3.org/2000/svg", "svg"), sendPath = document.createElementNS(sendIcon.namespaceURI, "path");
    sendIcon.setAttribute("viewBox", "0 0 24 24"); sendIcon.setAttribute("aria-hidden", "true"); sendPath.setAttribute("d", "M12 19V5M5.5 11.5 12 5l6.5 6.5"); sendIcon.append(sendPath);
    const form = h("form", { class: "chat-form" }, h("label", { class: "sr-only", for: "chat-message" }, "Ask or describe the next step"), text,
      h("div", { class: "composer-bar" }, iconButton("Upload a report", "plus", contributeDialog, { id: "upload-report", class: "attach-button", title: "Attach a PDF, text file or pasted report", disabled: state.busy || state.doc.status === "closed" || !has("contributor", "agency_supervisor", "coordinator", "finance_approver") }),
        h("button", { type: "button", class: "composer-request with-icon", title: "Ask another organisation for help", onclick: () => askDialog(), disabled: state.busy || !canAct("contributor", "agency_supervisor", "coordinator") }, actionLabel("Request", "request")), h("small", { class: "composer-hint" }, "Enter to send · Shift+Enter for a new line"), h("button", { type: "submit", class: "primary send-button", "aria-label": "Send message", title: "Send (Enter)", disabled: state.busy, "aria-busy": String(state.busy) }, sendIcon)));
    form.addEventListener("submit", async event => { event.preventDefault(); await sendChat(); });
    return h("aside", { class: "chat", "aria-label": "Incident chat" }, h("div", { class: "chat-head" }, h("span", { class: "assistant-mark", "aria-hidden": "true" }, "✦"), h("div", {}, h("h2", {}, "Incident assistant"), h("small", {}, state.catalogue.model === "fake" ? "Offline test driver" : "Gemini · Google ADK"))), h("div", { id: "messages", class: "messages" }, h("div", { id: "chat-log", role: "log", "aria-label": "Conversation", "aria-live": "polite", "aria-relevant": "additions" }), h("div", { id: "chat-activity", role: "group", "aria-label": "Coordinator activity" }), h("div", { id: "chat-proposals" })), h("div", { class: "composer-dock" }, h("div", { id: "chat-status", class: "chat-status", role: "status", "aria-live": "polite" }), form));
  }
  // Keep the chat's bottom (and so the composer) on the visible screen: it sticks below the top edge
  // and its height is whatever remains between its current top and the bottom of the viewport.
  let chatFrame = 0;
  function fitChat() {
    chatFrame = 0;
    const chat = $(".chat"), log = $("#messages");
    if (!chat?.offsetParent) return;
    const following = log && log.scrollHeight - log.scrollTop - log.clientHeight < 70;
    const top = Math.max(chat.parentElement.getBoundingClientRect().top, parseFloat(getComputedStyle(chat).top) || 0);
    chat.style.setProperty("--chat-top", Math.round(top) + "px");
    if (following) log.scrollTop = log.scrollHeight;
  }
  const queueFitChat = () => { if (!chatFrame) chatFrame = requestAnimationFrame(fitChat); };
  addEventListener("scroll", queueFitChat, { passive: true });
  addEventListener("resize", queueFitChat);
  // ---------------------------------------------------------------- plans: several linked changes on one card
  const isStepRef = v => Boolean(v && typeof v === "object" && "$step" in v);
  const PLAN_HIDDEN = new Set(["template_id", "language", "recipients", "public_request_id"]);
  const PLAN_CHOICES = { priority: ["urgent", "high", "normal"], level: ["working", "restoring", "disrupted", "unknown"] };
  const planOpen = p => p.status === "pending" && p.current && Date.parse(p.expires_at) > Date.now();
  function planValue(key, value) {
    if (isStepRef(value)) return `From step ${value.$step.slice(1)}`;
    if (key === "areas" && Array.isArray(value)) return value.map(nameOf).join("; ");
    if (key === "outlets" && Array.isArray(value)) return plural(value.length, "media recipient");
    if ((key === "to" || key === "recipients") && Array.isArray(value)) return value.map(orgName).join("; ");
    if (key === "to") return orgName(value);
    if (key === "area" || key === "delivery_area") return nameOf(value);
    if (value && typeof value === "object") return JSON.stringify(value);
    return String(value ?? "Unknown");
  }
  function planNext(p) {
    const next = [];
    if (p.steps.some(s => s.action === "document" && MESSAGE_KINDS.includes(s.data.kind))) next.push("Then a communications reviewer approves each message in Documents before it can be sent.");
    if (p.steps.some(s => s.action === "ask")) next.push("Then the receiving organisation answers the request.");
    return next;
  }
  function planCard(p) {
    const open = planOpen(p), blocks = (p.checks || []).filter(x => x.level === "block");
    const usedLater = s => p.steps.some(o => o.id !== s.id && JSON.stringify(o.data).includes(`"$step":"${s.id}"`));
    const call = (path, body = {}, done = "") => async () => { state.doc = await api(`/incidents/${state.doc.id}/plans/${p.id}/${path}`, body); renderIncident(); if (done) toast(done); };
    const steps = h("ol", { class: "plan-steps" }, p.steps.map((s, i) => h("li", { class: "plan-step" },
      h("div", { class: "plan-step-head" }, h("strong", {}, s.summary),
        open && h("div", { class: "row plan-step-tools" }, btn("Edit", () => editPlanStep(p, s), "quiet small", { "aria-label": `Edit step ${i + 1}` }),
          p.steps.length > 1 && !usedLater(s) && btn("Remove", call("edit", { hash: p.hash, changes: [{ id: s.id, remove: true }] }, "Step removed; plan checked again"), "quiet small", { "aria-label": `Remove step ${i + 1}` }))),
      h("dl", { class: "proposal-fields" }, Object.entries(s.data).filter(([k]) => !PLAN_HIDDEN.has(k)).map(([k, v]) => h("div", {}, h("dt", {}, k.replaceAll("_", " ")), h("dd", { class: ["text", "body", "note", "details"].includes(k) ? "multiline" : null }, planValue(k, v))))),
      (p.checks || []).filter(x => x.step === s.id).map(x => h("p", { class: "plan-check", role: "note" }, x.message)),
      (s.human_fields || []).length ? h("small", { class: "muted" }, `You edited: ${s.human_fields.map(f => f.replaceAll("_", " ")).join(", ")}`) : null)));
    const many = p.steps.length > 1;
    return h("div", { class: "proposal plan-card", "data-plan": p.id },
      pill(p.origin !== "event" ? "Review required" : p.prepared_by_ai ? "AI suggestion" : "Suggestion", "warn"), h("h3", {}, p.title),
      p.trigger && h("p", { class: "plan-why" }, p.trigger.why),
      h("p", { class: "muted" }, many ? `${p.steps.length} steps · applied together, or not at all` : "One change · nothing applies until you approve it"),
      steps,
      (p.checks || []).filter(x => !x.step).map(x => notice(x.message)),
      planNext(p).map(t => h("p", { class: "plan-next" }, t)),
      !open ? notice("The incident changed or this plan expired. Check it again against the latest records.") : null,
      h("details", {}, h("summary", {}, "Exact steps & review reference"), h("pre", {}, JSON.stringify(p.steps.map(s => ({ step: s.id, action: s.action, data: s.data })), null, 2)),
        h("small", {}, `Version ${p.version} · ${p.hash.slice(0, 12)} · ${p.origin === "event" && !p.prepared_by_ai ? "prepared automatically from the records, no AI model" : "prepared with AI" + ((p.models || []).length ? ": " + p.models.map(m => `${m.role} ${m.model}`).join(", ") : "")}`)),
      h("div", { class: "row wrap plan-actions" },
        open ? btn(many ? "Apply all steps" : "Apply change", call("approve", { hash: p.hash, request_id: id() }, many ? "All steps applied" : "Change applied"), "primary", { disabled: blocks.length > 0 })
          : btn("Check again", call("refresh", {}, "Plan checked against the latest records"), "primary"),
        btn("Dismiss", call("dismiss", { reason: "" }, "Plan dismissed"), "quiet")));
  }
  function editPlanStep(p, s) {
    const editable = Object.entries(s.data).filter(([k, v]) => !isStepRef(v) && !PLAN_HIDDEN.has(k) && (typeof v === "string" || typeof v === "number"));
    const label = k => k.replaceAll("_", " ").replace(/^./, x => x.toUpperCase());
    const control = (k, v) => PLAN_CHOICES[k] ? select(k, PLAN_CHOICES[k], v)
      : ["text", "body", "note", "details"].includes(k) ? h("textarea", { name: k, rows: k === "body" ? 10 : 4 }, String(v))
      : input(k, String(v), typeof v === "number" ? { type: "number", min: "0", step: "1" } : {});
    dialog(`Edit step ${s.id.slice(1)}`, () => [h("p", { class: "muted" }, s.summary), ...editable.map(([k, v]) => field(label(k), control(k, v)))], async form => {
      const data = {};
      for (const [k, v] of editable) { const raw = form.get(k); const next = typeof v === "number" ? Number(raw) : String(raw); if (next !== v) data[k] = next; }
      if (!Object.keys(data).length) return;
      state.doc = await api(`/incidents/${state.doc.id}/plans/${p.id}/edit`, { hash: p.hash, changes: [{ id: s.id, data }] });
      renderIncident(); toast("Plan checked again with your changes");
    }, "Check and save");
  }
  function renderChatHistory() {
    const el = $("#messages"); if (!el) return;
    const focus = focusSnapshot(), nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 70, initial = !el.dataset.loaded;
    const chat = c().chat;
    const log = $("#chat-log"), activity = $("#chat-activity"), proposals = $("#chat-proposals");
    if (!chat.messages.length && !log.children.length) log.append(h("div", { class: "chat-welcome" }, h("span", { class: "assistant-mark", "aria-hidden": "true" }, "✦"), h("h3", {}, "One incident. A shared picture."), h("p", {}, "Add a report with +, describe an update, or ask for the next step."), h("div", { class: "suggestions" }, btn("What needs attention?", () => sendChat("What needs attention?"), "quiet"), btn("Show my organisation’s tasks", () => sendChat("Show the assignments and next actions for my organisation."), "quiet"))));
    if (chat.messages.length && !log.querySelector("[data-message]")) log.replaceChildren();
    for (const m of chat.messages) if (![...log.children].some(n => n.dataset.message === m.id)) log.append(h("div", { class: "bubble " + m.role, "data-message": m.id }, h("span", { class: "message-author" }, m.role === "user" ? "You" : "Assistant"), h("div", {}, m.role === "user" ? m.text : readable(m.text))));
    const latest = chat.turns.at(-1);
    const interrupted = latest?.status === "running" && Date.now() - Date.parse(latest.at) >= 120000;
    // Associate old records by turn order; new records have explicit turn IDs.
    const replies = new Map(), users = new Map();
    let legacyIndex = -1;
    for (const message of chat.messages) {
      if (message.role === "user") legacyIndex++;
      const turnId = message.turn_id || chat.turns[legacyIndex]?.id;
      if (turnId) (message.role === "user" ? users : replies).set(turnId, message);
    }
    for (const turn of chat.turns) {
      const reply = replies.get(turn.id);
      const replyNode = reply && [...log.children].find(n => n.dataset.message === reply.id);
      const currentTurn = turn.status === "running" && Date.now() - Date.parse(turn.at) >= 120000 ? { ...turn, status: "interrupted" } : turn;
      let view = activityViews.get(turn.id);
      if (!view || view.status !== currentTurn.status) { view = { status: currentTurn.status, open: currentTurn.status === "running" }; activityViews.set(turn.id, view); }
      let display = [...log.children].find(n => n.dataset.activityTurn === turn.id);
      if (!display) {
        display = h("lm-work-activity", { "data-activity-turn": turn.id, "onactivity-toggle": event => { const v = activityViews.get(event.detail.id); if (v) v.open = event.detail.open; } });
      }
      display.turn = currentTurn; display.expanded = view.open;
      if (replyNode && display.nextElementSibling !== replyNode) log.insertBefore(display, replyNode);
      else if (!replyNode) {
        const user = users.get(turn.id), userNode = user && [...log.children].find(n => n.dataset.message === user.id);
        if (userNode && userNode.nextElementSibling !== display) userNode.after(display);
        else if (!userNode && display.parentElement !== log) log.append(display);
      }
    }
    activity.replaceChildren();
    const status = state.busy && latest?.status !== "running" ? "Saving your message…" : latest?.status === "running" && Date.now() - Date.parse(latest.at) < 120000 ? latest.steps.at(-1)?.label || "Reading your message…" : interrupted ? "Turn interrupted · send a focused question" : latest?.status === "failed" ? "Could not finish · your records are saved" : "Changes need your review before they apply";
    if ($("#chat-status").textContent !== status) $("#chat-status").textContent = status;
    const proposalKey = JSON.stringify([chat.proposals, c().plans, state.doc.version]);
    if (proposals.dataset.key === proposalKey) { if (initial || nearBottom) el.scrollTop = el.scrollHeight; el.dataset.loaded = "true"; return; }
    proposals.dataset.key = proposalKey; proposals.replaceChildren();
    for (const p of chat.proposals.filter(p => p.status === "pending")) {
      const stale = p.version !== state.doc.version || Date.parse(p.expires_at) < Date.now();
      const readable = (key, value) => key === "areas" && Array.isArray(value) ? value.map(nameOf).join(", ") : key === "outlets" && Array.isArray(value) ? plural(value.length, "media recipient") : value;
      const fields = Object.entries(p.data).filter(([key]) => key !== "template_id").map(([key, value]) => [key, readable(key, value)]).map(([key, value]) => h("div", {}, h("dt", {}, key.replaceAll("_", " ")), h("dd", { class: key === "text" ? "multiline" : null }, value && typeof value === "object" ? h("pre", {}, JSON.stringify(value, null, 2)) : String(value ?? "Unknown"))));
      const title = p.action === "document" && MESSAGE_KINDS.includes(p.data.kind) ? `Draft ${MESSAGE_KIND[p.data.kind]}` : p.action.replaceAll("_", " ");
      proposals.append(h("div", { class: "proposal" }, pill("Review required", "warn"), h("h3", {}, title), h("dl", { class: "proposal-fields" }, fields),
        h("details", {}, h("summary", {}, "Exact payload & review reference"), h("pre", {}, JSON.stringify(p.data, null, 2)), h("small", {}, `Version ${p.version} · ${p.hash.slice(0, 12)}`)),
        stale ? notice("The incident changed or this proposal expired. Ask for a fresh proposal.") : btn("Review and apply change", async () => { state.doc = await api(`/incidents/${state.doc.id}/proposals/${p.id}/approve`, { hash: p.hash, request_id: id() }); renderIncident(); toast("Exact proposal applied"); }, "primary")));
    }
    for (const p of (c().plans || []).filter(p => p.status === "pending" && p.origin === "chat")) proposals.append(planCard(p));
    if (initial || nearBottom) el.scrollTop = el.scrollHeight;
    el.dataset.loaded = "true"; restoreFocus(focus);
  }
  async function sendChat(text = state.chatDraft, mode = "conversation") {
    if (state.busy || !text.trim()) return;
    const key = draftKey(), isDraft = text === state.chatDraft;
    state.busy = true;
    if (isDraft) { state.chatDraft = ""; drafts.delete(key); }
    const incidentId = state.doc.id;
    renderIncident();
    try { const doc = await api(`/incidents/${incidentId}/chat`, { text, request_id: id(), mode, language: state.lang }); if (state.doc?.id === incidentId) state.doc = doc; }
    catch (e) { if (isDraft) { drafts.set(key, text); if (state.doc?.id === incidentId) state.chatDraft = text; } toast(e.message); }
    finally { state.busy = false; if (state.doc?.id === incidentId) renderIncident(); }
  }
  async function analyseReport(sourceId) {
    state.collapsed = false; state.mobileView = "chat";
    await sendChat(`Analyze report source ${sourceId}. Summarize its reported facts and uncertainties. Identify both the evidence-review owner and the candidate operational sector body from the participant directory, pending review. Prepare appropriate updates for my role only if supported. Do not accept evidence or apply changes automatically.`);
  }
  function renderData() {
    const area = $("#data-area"); if (!area) return;
    const focus = focusSnapshot();
    if (map) { dropMap(map); map = null; mapCtl = null; }
    const unread = (c().notifications || []).filter(n => !n.read_by.includes(state.me.id)).length;
    // Three everyday views; older, specialist records stay one click away under More.
    const main = [["overview", "Overview"], ["map", "Map & status"], ["requests", `Requests (${(c().asks || []).filter(askOpen).length})`], ["sources", `Reports (${state.doc.sources.length})`]];
    const more = [["resources", `Shelters & resources (${c().assets.length})`], ...(has("partner") ? [] : [["finance", "Funding records"]]), ["documents", "Documents"], ["notifications", `Shared with you (${unread})`], ["timeline", "Activity"], ["assignments", "Needs & assignments"]];
    if (state.tab === "situation") state.tab = "overview";
    const extra = more.find(([key]) => key === state.tab);
    const tabs = extra ? [...main, extra] : main;
    if (!tabs.some(([key]) => key === state.tab)) state.tab = "overview";
    const activate = async key => {
      state.tab = key; renderData();
      const target = document.getElementById(`tab-${key}`);
      await target.updateComplete;
      if (target.isConnected) target.focus({ preventScroll: true });
    };
    const tabGroup = createDataTabs({ tabs, active: state.tab, onSelect: activate, renderPanel: key => [({ overview: overviewPanel, map: mapPanel, sources: sourcesPanel, resources: resourcesPanel, requests: asksPanel, assignments: () => h("div", { class: "stack" }, notice("Evidence-based needs and receiving-body assignments. Each step needs reviewed evidence; most coordination now uses Requests."), requestsPanel(), operationPanel("requests")), finance: financePanel, documents: documentsPanel, notifications: notificationsPanel, timeline: timelinePanel })[key](), !['overview', 'requests', 'assignments'].includes(key) && operationPanel(key)] });
    const menu = createViewMenu({ items: more, active: state.tab, onSelect: key => activate(key).catch(error => toast(error.message)) });
    const sendAllowed = canAct("contributor", "agency_supervisor", "coordinator");
    area.replaceChildren(h("div", { class: "data-toolbar" }, h("div", { class: "row" }, sendAllowed && btn(actionLabel("New request", "request"), () => askDialog(), "primary with-icon"), canAct("contributor", "agency_supervisor", "coordinator", "finance_approver") && btn(actionLabel("Add report", "plus"), contributeDialog, "with-icon")), menu), tabGroup);
    renderChatHistory();
    restoreFocus(focus); rememberView();
    if (state.tab === "overview" || state.tab === "map") Promise.all([tabGroup.updateComplete, ...[...tabGroup.querySelectorAll("wa-tab-panel")].map(panel => panel.updateComplete)]).then(() => {
      if (tabGroup.isConnected && state.tab === "overview") return drawMap();
      if (tabGroup.isConnected && state.tab === "map") return drawAreaMap();
    }).catch(e => { const el = $("#incident-map"); if (el) el.textContent = "Map could not load. Resource locations remain available in the resource records."; });
  }
  // Map & status: official warnings, people affected and service status for every area.
  const vocab = () => c().area_vocabulary;
  const figureOrgs = ["dmc", "district", "division", "ndrsc"];
  const canPublish = () => canAct("coordinator", "agency_supervisor", "communications_reviewer");
  const shortLevel = { working: "Working", restoring: "Restoring", disrupted: "Not working", unknown: "Not known" };
  const statusChip = (level, compact = false) => h("span", { class: `status-chip ${level || "none"}` }, h("span", { class: "status-icon", "aria-hidden": "true" }, level ? STATUS[level].icon : "–"), h("span", { class: compact ? "chip-word" : "" }, level ? (compact ? shortLevel[level] : vocab().levels[level]) : "No report"));
  const warningChip = w => h("span", { class: `warning-chip ${w.level}` }, h("span", { "aria-hidden": "true" }, WARNING[w.level].icon), `${w.level[0].toUpperCase()}${w.level.slice(1)} · ${vocab().hazards[w.hazard]}`);
  const activeWarning = w => !w.ended_at && Date.parse(w.issued_at) <= Date.parse(c().picture.as_of) && (!w.valid_until || Date.parse(w.valid_until) > Date.parse(c().picture.as_of));
  const publicBadge = record => record.public ? pill("Public", "green") : pill("Internal");
  const toIso = value => value ? new Date(value).toISOString() : null;
  const localNow = () => { const d = new Date(); d.setMinutes(d.getMinutes() - d.getTimezoneOffset()); return d.toISOString().slice(0, 16); };
  async function areas() { state.areas ||= await loadAreas(); return state.areas; }
  const nameOf = id => state.areas ? areaName(state.areas, id) : id;
  const districtId = (name = c().district) => state.areas?.index.districts.find(d => d.name === name)?.id;
  function areaSelect(name, value, { divisionsOnly = false } = {}) {
    const index = state.areas.index, home = c().district;
    const group = district => h("optgroup", { label: `${district} District` }, !divisionsOnly && h("option", { value: districtId(district) }, `Whole ${district} District`),
      index.divisions.filter(d => d.district === district).map(d => h("option", { value: d.id }, `${d.name} DS`)));
    const el = h("select", { name, class: "lm-select", required: true }, group(home), index.districts.filter(d => d.name !== home).map(d => group(d.name)));
    el.value = value || (divisionsOnly ? index.divisions.find(d => d.district === home)?.id : districtId());
    return el;
  }
  const sourceOptions = () => [["", "No linked report"], ...state.doc.sources.filter(s => s.status !== "rejected").map(s => [s.id, `${s.title} · ${orgName(s.organisation)}`])];
  const publicToggle = (checked = false) => canPublish() && h("label", { class: "check-line" }, input("public", "yes", { type: "checkbox", checked }), "Show on the public status page");
  async function areaStatusDialog(preset = {}) {
    await areas(); const v = vocab();
    dialog("Update area status", () => [
      field("Area", areaSelect("area", preset.area)),
      h("div", { class: "field-row" }, field("Service", select("sector", SECTOR_ORDER.map(k => [k, v.sectors[k]]), preset.sector || "water")),
        field("Status", select("level", LEVEL_ORDER.map(k => [k, `${STATUS[k].icon} ${v.levels[k]}`]), preset.level || "disrupted"))),
      field("What people should know (optional)", h("textarea", { name: "note", maxlength: 300, placeholder: "e.g. Piped supply off; bowsers at the temple from 2 pm" })),
      field("Expected back (optional)", input("expected_by", "", { type: "datetime-local" }), "Only for a service that is being restored."),
      h("details", { class: "inline-details" }, h("summary", {}, "Add a map point (optional)"), h("p", { class: "muted" }, "For a blocked road, a broken pipe or a closed bridge. Copy the latitude and longitude from the report or a map app."),
        h("div", { class: "field-row" }, field("Latitude", input("latitude", "", { inputmode: "decimal", placeholder: "7.04" })), field("Longitude", input("longitude", "", { inputmode: "decimal", placeholder: "80.62" })))),
      field("Linked report (optional)", select("source_id", sourceOptions())),
      publicToggle(),
      notice("Record what the responsible body reported. A status older than 24 hours is marked as possibly out of date."),
    ], async data => {
      const lat = data.get("latitude"), lng = data.get("longitude");
      await command("area_status", { area: data.get("area"), sector: data.get("sector"), level: data.get("level"), note: data.get("note") || null, expected_by: toIso(data.get("expected_by")),
        latitude: lat ? Number(lat) : null, longitude: lng ? Number(lng) : null, source_id: data.get("source_id") || null, public: data.get("public") === "yes" });
      state.mapArea = data.get("area").startsWith("ds-") ? data.get("area") : state.mapArea;
    }, "Save status");
  }
  async function warningDialog() {
    await areas(); const v = vocab(), home = c().district;
    const divisions = state.areas.index.divisions.filter(d => d.district === home);
    dialog("Record an official warning", () => [
      notice("Copy the warning exactly as the issuing authority published it, and link the report that holds the bulletin. This workspace records warnings; it never issues them."),
      h("div", { class: "field-row" }, field("Issued by", select("issuer", Object.entries(v.issuers), "nbro")), field("Hazard", select("hazard", Object.entries(v.hazards), "landslide"))),
      field("Level", select("level", ["red", "amber", "yellow"].map(k => [k, `${WARNING[k].icon} ${v.warning_levels[k]}`]), "amber")),
      field("Areas named in the bulletin", checkList("areas", [[districtId(), `Whole ${home} District`], ...divisions.map(d => [d.id, `${d.name} DS`])])),
      h("details", { class: "inline-details" }, h("summary", {}, "Other districts"), checkList("areas", state.areas.index.districts.filter(d => d.name !== home).map(d => [d.id, `Whole ${d.name} District`]))),
      h("div", { class: "field-row" }, field("Issued at", input("issued_at", localNow(), { type: "datetime-local", required: true })), field("Valid until (optional)", input("valid_until", "", { type: "datetime-local" }))),
      field("Advice from the bulletin", h("textarea", { name: "advice", required: true, maxlength: 600, placeholder: "e.g. Residents should be alert and ready to move to a safety centre." })),
      field("Report holding the bulletin", select("source_id", sourceOptions().slice(1))),
      publicToggle(true),
    ], async data => {
      const chosen = data.getAll("areas");
      if (!chosen.length) throw new Error("Choose at least one area named in the bulletin.");
      if (!data.get("source_id")) throw new Error("Add the bulletin as a report first, then link it here.");
      await command("warning", { issuer: data.get("issuer"), hazard: data.get("hazard"), level: data.get("level"), areas: chosen, issued_at: toIso(data.get("issued_at")), valid_until: toIso(data.get("valid_until")),
        advice: data.get("advice"), source_id: data.get("source_id"), public: data.get("public") === "yes" });
    }, "Record warning");
  }
  async function figuresDialog(area) {
    await areas(); const v = vocab();
    dialog("Record affected figures", () => [
      notice("Use the DMC situation-report figures for one DS division. Leave a figure blank if it is not known; blank is never treated as zero."),
      h("div", { class: "field-row" }, field("DS division", areaSelect("area", area, { divisionsOnly: true })), field("Figures as of", input("as_of", localNow(), { type: "datetime-local", required: true }))),
      h("div", { class: "figure-grid" }, Object.entries(v.figures).map(([k, label]) => field(label, input(k, "", { type: "number", min: 0, step: 1, inputmode: "numeric" })))),
      field("Linked report (optional)", select("source_id", sourceOptions())),
      publicToggle(),
    ], async data => {
      const values = Object.fromEntries(Object.keys(v.figures).filter(k => data.get(k) !== "").map(k => [k, Number(data.get(k))]));
      await command("area_figures", { area: data.get("area"), as_of: toIso(data.get("as_of")), source_id: data.get("source_id") || null, public: data.get("public") === "yes", ...values });
      state.mapArea = data.get("area");
    }, "Save figures");
  }
  function endWarning(w) {
    dialog("End this warning", () => [notice(`${vocab().issuers[w.issuer]} · ${vocab().warning_levels[w.level]} · ${vocab().hazards[w.hazard]}`), field("Reason", h("textarea", { name: "reason", required: true, minlength: 4, maxlength: 300, placeholder: "e.g. NBRO lifted the warning at 18:00" }))],
      async data => command("warning_end", { warning_id: w.id, reason: data.get("reason") }), "End warning");
  }
  const publishButton = record => canPublish() && btn(record.public ? "Hide from public page" : "Show on public page", () => command("publish", { record_id: record.id, public: !record.public }), "quiet small");
  function areaDetail(id) {
    const v = vocab(), s = areaState(c().picture, id);
    if (!id) return h("div", { class: "area-empty" }, h("strong", {}, "Choose an area"), h("p", {}, "Select a DS division on the map or in the table below to see its warnings, services and figures."));
    const figures = s.figures ? Object.entries(v.figures).filter(([k]) => s.figures.values[k] != null) : [];
    return h("div", { class: "stack area-detail-body" },
      h("div", {}, h("h3", { id: "area-detail-title", tabindex: "-1" }, nameOf(id)), h("small", { class: "muted" }, "Reference boundary; check local names with the Divisional Secretariat.")),
      h("section", { "aria-label": "Warnings in force" }, h("h4", {}, "Warnings in force"), s.warnings.length ? h("ul", { class: "plain-list" }, s.warnings.map(w => h("li", {}, warningChip(w), h("p", {}, w.advice), h("small", { class: "muted" }, `${v.issuers[w.issuer]} · issued ${dateTime(w.issued_at)}${w.valid_until ? ` · until ${dateTime(w.valid_until)}` : ""}`))))
        : h("p", { class: "muted" }, "No warning recorded. That is not an all-clear.")),
      h("section", { "aria-label": "Services" }, h("h4", {}, "Services"), h("ul", { class: "service-list" }, SECTOR_ORDER.map(k => { const r = s.services[k]; return h("li", {},
        h("div", { class: "row between" }, h("strong", {}, v.sectors[k]), statusChip(r?.level)),
        r && r.note && h("p", {}, r.note),
        r && h("small", { class: "muted" }, `${orgName(r.organisation)} · ${ago(r.at)}${r.expected_by ? ` · expected back ${dateTime(r.expected_by)}` : ""}${r.stale ? " · may be out of date" : ""}${r.area !== id ? " · district-wide" : ""}`),
        r && h("div", { class: "row" }, publicBadge(r), publishButton(r))); }))),
      h("section", { "aria-label": "Affected figures" }, h("h4", {}, "Affected"), figures.length ? h("dl", { class: "figure-list" }, figures.map(([k, label]) => [h("dt", {}, label), h("dd", {}, s.figures.values[k].toLocaleString())])) : h("p", { class: "muted" }, "No figures reported."),
        s.figures && h("small", { class: "muted" }, `As of ${dateTime(Object.values(s.figures.as_of).sort().at(-1))}`), s.figures && h("div", { class: "row" }, publicBadge(s.figures), publishButton(s.figures))),
      acting() && h("div", { class: "row" }, btn("Update a service here", () => areaStatusDialog({ area: id }), "primary"), figureOrgs.includes(state.me.organisation) && btn("Record figures", () => figuresDialog(id))));
  }
  function statusTable() {
    const v = vocab(), pic = c().picture, home = c().district;
    const ids = new Set([...state.areas.index.divisions.filter(d => d.district === home).map(d => d.id), ...Object.keys(pic.areas)]);
    const severity = { working: 0, unknown: 1, restoring: 2, disrupted: 3 };
    const rank = id => { const s = areaState(pic, id); return [-(s.warnings[0] ? WARNING[s.warnings[0].level].rank : 0), -Math.max(0, ...Object.values(s.services).map(r => severity[r.level])), nameOf(id)]; };
    const rows = [...ids].sort((a, b) => { const x = rank(a), y = rank(b); return x[0] - y[0] || x[1] - y[1] || x[2].localeCompare(y[2]); });
    const short = { power: "Power", water: "Water", roads: "Roads", health: "Health", communications: "Phone", shelter: "Centres", food: "Food" };
    return h("div", { class: "table-scroll" }, h("table", { class: "status-table" }, h("caption", { class: "sr-only" }, "Status by area: warnings, services and people affected"),
      h("thead", {}, h("tr", {}, h("th", { scope: "col" }, "Area"), h("th", { scope: "col" }, "Warning"), SECTOR_ORDER.map(k => h("th", { scope: "col", title: v.sectors[k] }, short[k])), h("th", { scope: "col" }, "People affected"))),
      h("tbody", {}, rows.map(id => { const s = areaState(pic, id); return h("tr", { class: state.mapArea === id ? "selected" : "", "data-area": id },
        h("th", { scope: "row" }, h("button", { type: "button", class: "link-button", onclick: () => selectArea(id, true) }, nameOf(id).replace(`, ${home}`, ""))),
        h("td", {}, s.warnings[0] ? warningChip(s.warnings[0]) : h("span", { class: "muted" }, "None")),
        SECTOR_ORDER.map(k => h("td", {}, statusChip(s.services[k]?.level, true))),
        h("td", { class: "num" }, s.figures?.values.people_affected != null ? s.figures.values.people_affected.toLocaleString() : h("span", { class: "muted" }, "—"))); }))));
  }
  function selectArea(id, zoom = false) {
    state.mapArea = id;
    mapCtl?.select(id, { zoom });
    const slot = $("#area-detail"); if (slot) { slot.replaceChildren(areaDetail(id)); if (zoom) $("#area-detail-title")?.focus({ preventScroll: true }); }
    for (const row of document.querySelectorAll(".status-table tbody tr")) row.classList.toggle("selected", row.dataset.area === id);
    if (zoom) $("#status-map")?.scrollIntoView({ block: "nearest", behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
  }
  function mapPanel() {
    if (!state.areas) { areas().then(() => { if (state.tab === "map" && state.doc) renderData(); }).catch(() => { const s = $("#map-loading"); if (s) s.textContent = "The map could not load. Status records remain in the activity history."; }); return h("p", { id: "map-loading", class: "muted" }, "Loading map…"); }
    const v = vocab(), pic = c().picture;
    const warnings = pic.warnings.filter(activeWarning).sort((a, b) => WARNING[b.level].rank - WARNING[a.level].rank);
    const legendSlot = h("div", { id: "map-legend" }, legend(state.mapLayer, state.mapSector, v));
    const sector = select("map_sector", [["all", "All services (worst)"], ...SECTOR_ORDER.map(k => [k, v.sectors[k]])], state.mapSector);
    sector.setAttribute("aria-label", "Service shown"); sector.hidden = state.mapLayer !== "services";
    const refreshLayer = () => { mapCtl?.update(null, { layer: state.mapLayer, sector: state.mapSector }); legendSlot.replaceChildren(legend(state.mapLayer, state.mapSector, v)); sector.hidden = state.mapLayer !== "services"; };
    sector.addEventListener("change", () => { state.mapSector = sector.value; refreshLayer(); });
    const layers = h("div", { class: "segmented", role: "group", "aria-label": "Map shows" }, [["warnings", "Warnings"], ["affected", "People affected"], ["services", "Services"]].map(([k, label]) =>
      h("button", { type: "button", "aria-pressed": String(state.mapLayer === k), onclick: e => { state.mapLayer = k; for (const b of e.currentTarget.parentNode.children) b.setAttribute("aria-pressed", String(b === e.currentTarget)); refreshLayer(); } }, label)));
    const toggle = (key, label) => h("label", { class: "check-line" }, input(key, "yes", { type: "checkbox", checked: state[key] !== false, onchange: e => { state[key] = e.currentTarget.checked; mapCtl?.update(null, { centres: state.mapCentres !== false, points: state.mapPoints !== false }); } }), label);
    return h("div", { class: "stack" },
      h("section", { class: "card stack map-card", "aria-labelledby": "map-title" },
        h("div", { class: "row between wrap" }, h("h2", { id: "map-title" }, "Map & status"), h("div", { class: "row wrap" },
          acting() && btn("+ Area status", () => areaStatusDialog({ area: state.mapArea || undefined }), "primary"),
          canAct("coordinator") && btn("+ Official warning", warningDialog),
          acting() && figureOrgs.includes(state.me.organisation) && btn("+ Affected figures", () => figuresDialog(state.mapArea?.startsWith("ds-") ? state.mapArea : undefined)),
          acting() && btn("+ Map layer", layerDialog),
          h("a", { class: "button-link with-icon", href: "/public", target: "_blank", rel: "noopener" }, actionLabel("Public page", "request", true)))),
        h("div", { class: "map-toolbar" }, layers, sector, toggle("mapCentres", "Safety centres"), toggle("mapPoints", "Reported problems"), (c().picture.layers || []).length > 0 && toggle("mapShapes", "Map layers")),
        h("div", { class: "map-layout" }, h("div", { id: "status-map", class: "map status-map" }), h("aside", { id: "area-detail", class: "area-detail", "aria-live": "polite", "aria-label": "Selected area" }, areaDetail(state.mapArea))),
        legendSlot,
        h("small", { class: "muted" }, "DS-division outlines are reference boundaries (OCHA / Survey Department 2020), not certified warning zones. A blank area means nothing was reported, not that it is safe.")),
      layersCard(),
      h("section", { class: "card stack", "aria-labelledby": "warnings-title" }, h("h2", { id: "warnings-title" }, `Official warnings in force (${warnings.length})`),
        warnings.length ? h("ul", { class: "warning-list" }, warnings.map(w => h("li", { class: `warning-item ${w.level}` },
          h("div", { class: "row between wrap" }, h("span", { class: "row wrap" }, warningChip(w), h("strong", {}, v.issuers[w.issuer])), h("span", { class: "row" }, publicBadge(w), publishButton(w), canAct("coordinator") && btn("End", () => endWarning(w), "quiet small"))),
          h("p", {}, w.advice), h("small", { class: "muted" }, `${w.areas.map(nameOf).join("; ")} · issued ${dateTime(w.issued_at)}${w.valid_until ? ` · valid until ${dateTime(w.valid_until)}` : " · no expiry given"}`))))
          : h("p", { class: "muted" }, "No official warning recorded for this incident. Record one from an NBRO, Met, Irrigation or DMC bulletin.")),
      h("section", { class: "card stack", "aria-labelledby": "area-table-title" }, h("div", { class: "row between wrap" }, h("h2", { id: "area-table-title" }, "Status by area"), h("small", { class: "muted" }, `${c().district} District first · ✓ working · ◐ being restored · ✕ not working · ? not known`)), statusTable()));
  }
  const LAYER_KIND_NAMES = { flood_extent: "Observed flood extent", road_closure: "Road closures", landslide_area: "Landslide area", other: "Other" };
  function layersCard() {
    const layers = c().map_layers || [], live = layers.filter(x => !x.removed_at);
    if (!layers.length) return null;
    const swatch = kind => { const el = h("span", { class: "swatch", "aria-hidden": "true" }); el.style.background = (LAYER_STYLE[kind] || LAYER_STYLE.other).fillColor || LAYER_STYLE[kind].color; el.style.borderColor = (LAYER_STYLE[kind] || LAYER_STYLE.other).color; return el; };
    return h("section", { class: "card stack", "aria-labelledby": "layers-title" }, h("h2", { id: "layers-title" }, `Map layers (${live.length})`),
      h("ul", { class: "layer-list" }, layers.map(x => h("li", { class: x.removed_at ? "removed" : "" },
        h("div", { class: "row between wrap" }, h("span", { class: "row wrap" }, swatch(x.kind), h("strong", {}, x.name), pill(LAYER_KIND_NAMES[x.kind] || x.kind), x.removed_at ? pill("Removed", "muted") : publicBadge(x)),
          !x.removed_at && h("span", { class: "row" }, publishButton(x), (x.organisation === state.me.organisation || has("coordinator")) && acting() && btn("Remove", () => dialog(`Remove ${x.name}`, () => [field("Why remove it?", h("textarea", { name: "reason", required: true, minlength: 3, maxlength: 300, placeholder: "e.g. Superseded by the 14:00 extent" }))],
            async data => command("map_layer_remove", { layer_id: x.id, reason: data.get("reason") }), "Remove layer"), "quiet small"))),
        h("small", { class: "muted" }, `${x.issuer} · observed ${dateTime(x.observed_at)} · ${plural(x.features, "feature")} · added by ${orgShort(x.organisation)}${x.removed_at ? ` · removed: ${x.removed_reason}` : ""}`)))),
      h("small", { class: "muted" }, "Layers show what their producer observed at that time. They are not forecasts or certified hazard zones."));
  }
  function layerDialog() {
    let geojson = null;
    const file = input("layer_file", "", { type: "file", accept: ".geojson,.json,application/geo+json,application/json", required: true });
    const info = h("small", { "aria-live": "polite" });
    file.addEventListener("change", async () => {
      geojson = null; info.textContent = "";
      const chosen = file.files[0]; if (!chosen) return;
      if (chosen.size > 2500000) { info.textContent = "This file is over 2.5 MB. Simplify it first (for example in QGIS or mapshaper)."; return; }
      try { geojson = JSON.parse(await chosen.text()); info.textContent = `${plural(geojson.features?.length || 0, "feature")} read.`; const nameBox = file.form?.elements.namedItem("name"); if (nameBox && !nameBox.value) nameBox.value = chosen.name.replace(/\.(geo)?json$/i, ""); }
      catch { info.textContent = "This is not valid GeoJSON."; }
    });
    dialog("Add a map layer", () => [
      notice("Upload a layer someone else produced, such as a Copernicus EMS or UNOSAT flood extent, or RDA road closures. GeoJSON in longitude/latitude (WGS84)."),
      field("GeoJSON file", file), info,
      field("Name", input("name", "", { required: true, minlength: 3, maxlength: 120 })),
      h("div", { class: "field-row" }, field("Shows", select("kind", Object.entries(LAYER_KIND_NAMES), "flood_extent")), field("Produced by", input("issuer", "", { required: true, maxlength: 80, placeholder: "e.g. Copernicus EMS" }))),
      field("Observed at", input("observed_at", localNow(), { type: "datetime-local", required: true })),
      field("Linked report (optional)", select("source_id", sourceOptions())),
      publicToggle(),
    ], async data => {
      if (!geojson) throw new Error("Choose a valid GeoJSON file.");
      await command("map_layer", { name: data.get("name"), kind: data.get("kind"), issuer: data.get("issuer"), observed_at: toIso(data.get("observed_at")), geojson, source_id: data.get("source_id") || null, public: data.get("public") === "yes" });
    }, "Add layer");
  }
  async function drawAreaMap() {
    const container = $("#status-map"); if (!container) return;
    const incidentId = state.doc.id;
    mapCtl = await drawStatusMap(container, c().picture, { layer: state.mapLayer, sector: state.mapSector, focus: c().district, selected: state.mapArea, vocab: vocab(), zoom: false,
      fetchLayer: meta => api(`/incidents/${incidentId}/layers/${meta.id}`), shapes: state.mapShapes !== false,
      centres: state.mapCentres !== false, points: state.mapPoints !== false, label: `Map of ${c().district} District: warnings, people affected and services`, onSelect: id => selectArea(id) });
    map = mapCtl;
  }
  // Organisation-to-organisation requests: the core collaboration flow.
  const askOpen = a => !["confirmed", "declined", "cancelled"].includes(a.status);
  const askKinds = [["supplies", "Relief supplies"], ["water", "Water"], ["shelter", "Shelter places"], ["transport", "Transport"], ["team", "People / team"], ["road", "Road clearing / access"], ["power", "Power"], ["funding", "Funding (LKR)"], ["other", "Other"]];
  const askKindName = kind => (askKinds.find(([k]) => k === kind) || [kind, kind])[1];
  // Who usually handles each kind; the sender can always pick another body.
  const askOwner = { supplies: "ndrsc", water: "water", shelter: "division", transport: "district", team: "dmc", road: "rda", power: "dmc", other: "dmc" };
  const askStatus = a => ({ asked: "Asked", recommended: "Recommended", accepted: a.kind === "funding" ? "Approved" : "Accepted", in_progress: "In progress", done: a.kind === "funding" ? "Released" : "Done", confirmed: "Confirmed", declined: "Declined", cancelled: "Cancelled" })[a.status] || a.status;
  const askTone = a => ({ asked: "warn", recommended: "warn", done: "green", confirmed: "green", declined: "danger" })[a.status] || "";
  const stepLabel = (a, step) => ({ recommend: "Recommend", accept: a.kind === "funding" ? "Approve" : "Accept", decline: a.kind === "funding" && a.status === "asked" ? "Return" : "Decline", start: "Start work", done: a.kind === "funding" ? "Mark released" : "Mark done", confirm: a.kind === "funding" ? "Confirm received" : "Confirm delivered", reopen: "Not done yet", cancel: "Cancel request", comment: "Add update", escalate: "Escalate", redirect: "Send to another body" })[step];
  const stepNeedsNote = new Set(["decline", "reopen", "cancel", "comment", "escalate", "redirect"]);
  const PRIORITY = { urgent: ["Urgent", "danger", "answer within 1 hour"], high: ["High", "warn", "answer within 4 hours"], normal: ["Normal", "", "answer within 12 hours"] };
  const LADDER_NAME = { division: "Divisional Secretariat", district: "District (DDMCU)", dmc: "DMC" };
  // Registered stock and centres (Places & stock), loaded once and refreshed every five minutes.
  async function registry() {
    if (!placesCache.loaded || Date.now() - placesCache.loaded > 300000) {
      const [centres, stock, items] = await Promise.all(["centres", "stock", "items"].map(n => api(`/reference/${n}`)));
      Object.assign(placesCache, { centres: centres.records, stock: stock.records, items: items.records, loaded: Date.now() });
    }
    return placesCache;
  }
  function whenRegistry(redraw) {
    if (placesCache.loaded || placesCache.loading) return;
    placesCache.loading = true;
    registry().then(redraw).catch(() => {}).finally(() => { placesCache.loading = false; });
  }
  const KIND_CATEGORIES = { water: ["water"], supplies: ["food", "shelter", "hygiene", "health"], transport: ["transport"], team: ["rescue"], road: ["roads"], power: ["equipment"] };
  // What an organisation holds on record for this kind of request, nearest district first.
  function stockOnRecord(kind, org, district) {
    if (!placesCache.loaded) return null;
    const districtOf = id => state.areas?.byId.get(id)?.district;
    if (kind === "shelter") {
      const centres = placesCache.centres.filter(r => !r.retired && (!org || r.owner === org) && r._derived?.capacity != null).map(r => ({ r, near: districtOf(r.area) === district }))
        .sort((a, b) => b.near - a.near || b.r._derived.capacity - a.r._derived.capacity);
      if (!centres.length) return null;
      const near = centres.filter(x => x.near);
      return `On record: ${plural(centres.length, "registered centre")}${near.length ? `, ${near.length} in ${district} (${near.reduce((n, x) => n + x.r._derived.capacity, 0).toLocaleString("en-GB")} places by standard)` : ""}. Live free places come from this incident.`;
    }
    const cats = KIND_CATEGORIES[kind]; if (!cats) return null;
    const itemOf = id => placesCache.items.find(i => i.id === id) || {};
    const lines = placesCache.stock.filter(r => !r.retired && r.status === "ready" && (!org || r.owner === org) && cats.includes(itemOf(r.item).category));
    if (!lines.length) return org ? `${orgShort(org)} has no ready stock of this kind on record.` : null;
    const byItem = new Map();
    for (const r of lines) { const key = r.item; byItem.set(key, [...(byItem.get(key) || []), r]); }
    const parts = [...byItem.entries()].slice(0, 3).map(([item, rows]) => {
      rows.sort((a, b) => (districtOf(b.area) === district) - (districtOf(a.area) === district) || b.quantity - a.quantity);
      const total = rows.reduce((n, r) => n + r.quantity, 0), first = rows[0];
      return `${itemOf(item).name}: ${total.toLocaleString("en-GB")} ${itemOf(item).unit} ready (${first.quantity.toLocaleString("en-GB")} at ${first.location_name.replace(" · fictional", "")}${districtOf(first.area) === district ? ", in this district" : ""})`;
    });
    return `On record${org ? ` for ${orgShort(org)}` : ""}: ${parts.join("; ")}.`;
  }
  const askAmount = a => a.kind === "funding" ? lkr(a.amount) : a.quantity != null ? `${a.quantity} ${a.unit}` : "Quantity not given";
  function askStep(a, step) {
    if (!stepNeedsNote.has(step)) return command("ask_update", { ask_id: a.id, step });
    const title = { decline: "Why are you declining?", reopen: "What is still missing?", cancel: "Why cancel this request?", comment: "Add an update", escalate: "Escalate this request", redirect: "Send this request to another body" }[step];
    const targets = pv().active.filter(o => ![a.to, a.from].includes(o));
    dialog(title, () => [notice(`${a.title} · ${orgName(a.from)} → ${orgName(a.to)}`),
      step === "escalate" && h("p", { class: "muted" }, `It moves one level up: ${["division", "district", "dmc"].filter(o => o !== a.to && o !== a.from).map(o => LADDER_NAME[o]).join(" → ")}. They can send it to another body.`),
      step === "redirect" && field("Send to", select("to", targets.map(o => [o, orgName(o)])), stockOnRecord(a.kind, null, c().district) || undefined),
      field(step === "comment" ? "Update" : "Reason", h("textarea", { name: "note", required: true, minlength: 3, maxlength: 1000, placeholder: step === "escalate" ? "e.g. No answer and 40 people are waiting" : step === "redirect" ? "e.g. NDRSC has bottled water in this district" : "" }))],
      async data => command("ask_update", { ask_id: a.id, step, note: data.get("note"), ...(step === "redirect" ? { to: data.get("to") } : {}) }), stepLabel(a, step));
  }
  function askDialog(preset = {}) {
    const mine = state.me.organisation, others = pv().active.filter(o => o !== mine && activeOrgs().some(x => x.id === o));
    const outside = pv().can_manage ? activeOrgs().map(o => o.id).filter(o => o !== mine && !others.includes(o)) : [];
    const kind = select("kind", askKinds.filter(([k]) => k !== "funding" || !has("partner")), preset.kind || "supplies");
    const to = select("to", [...others.map(o => [o, orgName(o)]), ...outside.map(o => [o, `${orgName(o)} · not in this incident yet (adds them)`])], preset.to || ([...others, ...outside].includes(askOwner[preset.kind || "supplies"]) ? askOwner[preset.kind || "supplies"] : others[0]));
    let chosen = !!preset.to;
    to.addEventListener("change", () => { chosen = true; });
    const amount = input("amount", "", { inputmode: "decimal", placeholder: "e.g. 20000" });
    const toField = field("To", to), countRow = h("div", { class: "field-row" }, field("Quantity (optional)", input("quantity", preset.quantity ?? "", { type: "number", min: 1, max: 1000000, step: 1 })), field("Unit", input("unit", preset.unit || "", { placeholder: "e.g. bowsers, packs, people", maxlength: 40 })));
    const amountField = field("Amount (LKR)", amount, "Goes to NDRSC: a coordinator recommends it, then their Finance Approver approves and releases it.");
    const priority = select("priority", Object.entries(PRIORITY).map(([k, [label, , hint]]) => [k, `${label} · ${hint}`]), preset.priority || "normal");
    const home = c().district, divisions = state.areas ? state.areas.index.divisions.filter(d => d.district === home) : [];
    const deliveryArea = select("delivery_area", [["", "Not set"], ...divisions.map(d => [d.id, `${d.name} DS`])], preset.delivery_area || "");
    const place = select("place_key", [["", "Not a registered centre"]], "");
    const placeText = input("delivery_place", preset.delivery_place || "", { maxlength: 150, placeholder: "e.g. Kotmale practice hall, main gate" });
    const stockHint = h("small", { class: "stock-hint", "aria-live": "polite" });
    const fillPlaces = () => {
      const centres = (placesCache.centres || []).filter(r => !r.retired && (!deliveryArea.value || r.area === deliveryArea.value) && state.areas?.byId.get(r.area)?.district === home);
      place.replaceChildren(h("option", { value: "" }, centres.length ? "Not a registered centre" : "No registered centre here"), ...centres.map(r => h("option", { value: r.id }, r.name)));
    };
    place.addEventListener("change", () => { const r = (placesCache.centres || []).find(x => x.id === place.value); if (r) { placeText.value = r.name.replace(" · fictional", ""); deliveryArea.value = r.area; } });
    deliveryArea.addEventListener("change", fillPlaces);
    const sync = () => {
      const funding = kind.value === "funding";
      toField.hidden = funding; countRow.hidden = funding; amountField.hidden = !funding; amount.required = funding;
      if (!funding && !chosen && others.includes(askOwner[kind.value])) to.value = askOwner[kind.value];
      stockHint.textContent = funding ? "" : stockOnRecord(kind.value, to.value, home) || "";
    };
    kind.addEventListener("change", sync);
    to.addEventListener("change", sync);
    Promise.all([registry(), areas()]).then(() => {
      if (deliveryArea.options.length === 1) deliveryArea.append(...state.areas.index.divisions.filter(d => d.district === home).map(d => h("option", { value: d.id }, `${d.name} DS`)));
      if (preset.delivery_area) deliveryArea.value = preset.delivery_area;
      fillPlaces(); sync();
    }).catch(() => {});
    dialog("New request", () => {
      const parts = [notice(`From ${orgName(mine)}. The receiving organisation's lead accepts or declines; you confirm when it is delivered. Unanswered requests escalate: Division → District → DMC.`), field("What kind of help?", kind), toField, stockHint,
        field("What exactly is needed?", input("title", preset.title || "", { required: true, minlength: 3, maxlength: 200, placeholder: "e.g. Two water bowsers for Kotmale hall" })), countRow, amountField,
        h("div", { class: "field-row" }, field("Priority", priority), field("Needed by (optional)", input("due_at", "", { type: "datetime-local" }))),
        h("div", { class: "field-row" }, field("Deliver to (optional)", deliveryArea), field("Registered centre (optional)", place)),
        field("Delivery place (optional)", placeText),
        field("Note (optional)", h("textarea", { name: "note", maxlength: 1000, placeholder: "People affected, access, who receives it…" }, preset.note || "")),
        state.doc.sources.length > 0 && field("Link a report (optional)", select("source_id", [["", "No linked report"], ...state.doc.sources.map(s => [s.id, `${s.title} · ${orgName(s.organisation)}`])], preset.source_id || ""))];
      sync(); return parts;
    }, async data => {
      const funding = data.get("kind") === "funding", quantity = data.get("quantity"), due = data.get("due_at");
      await command("ask", { kind: data.get("kind"), title: data.get("title"), note: data.get("note") || "", priority: data.get("priority"),
        ...(data.get("delivery_area") ? { delivery_area: data.get("delivery_area") } : {}), ...(data.get("delivery_place") ? { delivery_place: data.get("delivery_place") } : {}), ...(data.get("place_key") ? { place_key: data.get("place_key") } : {}), ...(funding ? { amount: String(data.get("amount")).replaceAll(",", "") } : { to: data.get("to"), ...(quantity ? { quantity: Number(quantity), unit: data.get("unit") || "units" } : {}) }),
        ...(due ? { due_at: new Date(due).toISOString() } : {}), ...(data.get("source_id") ? { source_id: data.get("source_id") } : {}) });
      state.tab = "requests"; renderData();
    }, "Send request");
  }
  function askCard(a) {
    const latest = a.history.at(-1), steps = (a.can || []).filter(s => s !== "comment"), primary = new Set(["accept", "recommend", "start", "done", "confirm"]);
    const overdue = a.due_at && askOpen(a) && a.status !== "done" && Date.parse(a.due_at) < Date.now();
    return h("article", { class: `ask-card ${a.status}${(a.can || []).some(s => primary.has(s)) ? " your-turn" : ""}`, id: a.id, "aria-labelledby": `${a.id}-title` },
      h("div", { class: "row between" }, h("span", { class: "row" }, pill(askKindName(a.kind)), a.priority && a.priority !== "normal" && pill(PRIORITY[a.priority][0], PRIORITY[a.priority][1])), pill(askStatus(a), askTone(a))),
      h("h4", { id: `${a.id}-title` }, a.title),
      a.escalation && h("p", { class: "escalation-note", role: "note" }, h("strong", {}, `Escalated to ${LADDER_NAME[a.escalation.to] || orgShort(a.escalation.to)}`), ` · ${a.escalation.reason}`),
      h("p", { class: "ask-route" }, h("span", { title: orgName(a.from) }, orgShort(a.from)), h("span", { class: "route-arrow", "aria-hidden": "true" }, actionIcon("forward")), h("span", { class: "sr-only" }, " to "), h("span", { title: orgName(a.to) }, orgShort(a.to))),
      h("small", {}, [askAmount(a), a.due_at && `${overdue ? "Overdue · was needed" : "Needed"} by ${dateTime(a.due_at)}`, `Sent ${ago(a.created_at)}`].filter(Boolean).join(" · ")),
      (a.delivery_place || a.delivery_area) && h("small", { class: "ask-delivery" }, `Deliver to ${[a.delivery_place, a.delivery_area && nameOf(a.delivery_area)].filter(Boolean).join(", ")}`),
      latest.note && h("p", { class: "ask-note" }, `“${latest.note}”`, h("small", {}, ` — ${orgShort(latest.organisation)}, ${ago(latest.at)}`)),
      a.kind !== "funding" && ["asked", "accepted"].includes(a.status) && (a.to === state.me.organisation || (a.can || []).includes("redirect")) && h("small", { class: "stock-hint", "data-kind": a.kind, "data-to": (a.can || []).includes("redirect") ? "" : a.to }, stockOnRecord(a.kind, (a.can || []).includes("redirect") ? null : a.to, c().district) || ""),
      a.source_id && btn(`Report: ${sourceName(a.source_id)}`, () => { state.tab = "sources"; renderData(); document.getElementById(a.source_id)?.scrollIntoView({ block: "center" }); }, "quiet ask-link"),
      (steps.length > 0 || (a.can || []).includes("comment")) && h("div", { class: "row ask-actions" }, steps.map(step => btn(stepLabel(a, step), () => askStep(a, step), primary.has(step) || step === "redirect" ? "primary" : ["decline", "cancel"].includes(step) ? "quiet danger" : step === "escalate" ? "quiet" : "")), (a.can || []).includes("comment") && btn("Add update", () => askStep(a, "comment"), "quiet")),
      h("details", { class: "ask-history" }, h("summary", {}, `History (${a.history.length})`), h("ol", {}, a.history.map(x => h("li", {}, h("strong", {}, `${orgShort(x.organisation)} · ${x.step === "asked" ? "sent" : stepLabel(a, x.step) || x.step}`), h("small", {}, ` ${dateTime(x.at)}`), x.note && h("p", {}, x.note))))));
  }
  function asksPanel() {
    const mine = state.me.organisation, all = c().asks || [];
    // Fill the stock lines in place when Places & stock arrives; never redraw the board (focus stays where it is).
    whenRegistry(() => { for (const el of document.querySelectorAll(".ask-card .stock-hint[data-kind]")) el.textContent = state.doc ? stockOnRecord(el.dataset.kind, el.dataset.to || null, c().district) || "" : ""; });
    const scoped = all.filter(a => state.askView === "to" ? a.to === mine : state.askView === "from" ? a.from === mine : true);
    const columns = [["Asked", ["asked", "recommended"]], ["Accepted", ["accepted"]], ["In progress", ["in_progress"]], ["Done", ["done", "confirmed"]]];
    const closed = scoped.filter(a => ["declined", "cancelled"].includes(a.status));
    const views = h("div", { class: "segmented", role: "group", "aria-label": "Show requests" }, [["all", "All"], ["to", "To us"], ["from", "From us"]].map(([key, label]) => btn(label, () => { state.askView = key; renderData(); }, "", { "aria-pressed": String(state.askView === key) })));
    const legacy = c().requests.length + c().offers.length;
    return h("div", { class: "stack" },
      h("div", { class: "card stack" }, h("div", { class: "row between" }, h("div", {}, h("h2", {}, "Requests between organisations"), h("small", {}, "Ask another body for help. Their lead accepts or declines, they deliver, you confirm.")), views),
        !all.length ? empty("No requests yet. Use New request to ask another organisation for supplies, water, transport, road clearing or funding.") :
        h("div", { class: "ask-board" }, columns.map(([label, statuses], i) => { const items = scoped.filter(a => statuses.includes(a.status)); return h("section", { class: "ask-column", "aria-labelledby": `ask-col-${i}` }, h("h3", { id: `ask-col-${i}` }, label, h("span", { class: "count" }, String(items.length))), items.length ? items.map(askCard) : h("p", { class: "ask-empty" }, "None")); })),
        closed.length > 0 && h("details", { class: "form-disclosure" }, h("summary", {}, `Declined or cancelled (${closed.length})`), h("div", { class: "ask-closed" }, closed.map(askCard)))),
      legacy > 0 && h("div", { class: "card" }, btn(actionLabel(`Earlier needs & assignments (${legacy})`, "forward", true), () => { state.tab = "assignments"; renderData(); }, "quiet with-icon")));
  }
  // What this role should do in this incident, in priority order.
  // Who takes part: lead, invited, joined, stood down; requests to join; add or stand down.
  const pv = () => c().participation_view || { organisations: c().participants.map(o => ({ organisation: o, status: "active" })), join_requests: [], active: c().participants, can_stand_down: [] };
  function orgButton() {
    const pending = pv().join_requests.filter(r => r.status === "pending").length;
    return btn([`${pv().active.length} organisations`, pending && pv().can_manage ? h("span", { class: "badge", "aria-hidden": "true" }, String(pending)) : null], organisationsDialog, "quiet org-button",
      { "aria-label": `${pv().active.length} organisations${pending && pv().can_manage ? `, ${pending} asking to join` : ""}` });
  }
  const viaText = o => ({ created: "Opened this incident", invited: `Invited by ${orgShort(o.by_org)}`, joined: `Joined · approved by ${orgShort(o.by_org)}`, request: `Added with a request from ${orgShort(o.by_org)}`, returned: `Brought back by ${orgShort(o.by_org)}`, original: "Taking part" })[o.via] || "Taking part";
  function organisationsDialog() {
    const view = pv(), lead = view.lead || "dmc", outside = activeOrgs().filter(o => !view.active.includes(o.id));
    const pending = view.join_requests.filter(r => r.status === "pending");
    dialog("Organisations in this incident", () => [
      h("ul", { class: "org-list" }, view.organisations.map(o => h("li", { class: o.status === "stood_down" ? "down" : "" },
        h("div", {}, h("strong", {}, orgName(o.organisation)), " ", o.organisation === lead ? pill("Lead", "green") : o.status === "stood_down" ? pill("Stood down") : pill("Taking part"),
          h("small", { class: "muted" }, o.status === "stood_down" ? `Stood down ${o.stood_down_at ? ago(o.stood_down_at) : ""} · ${o.reason}` : `${viaText(o)}${o.at ? ` · ${ago(o.at)}` : ""}${o.reason ? ` · ${o.reason}` : ""}`)),
        view.can_stand_down.includes(o.organisation) && btn("Stand down", () => { $("#modal").close(); setTimeout(() => standDownDialog(o.organisation), 0); }, "quiet small")))),
      pending.length > 0 && h("section", { class: "stack", "aria-labelledby": "join-title" }, h("h3", { id: "join-title" }, `Asking to join (${pending.length})`),
        h("ul", { class: "org-list" }, pending.map(r => h("li", { id: r.id }, h("div", {}, h("strong", {}, orgName(r.organisation)), h("small", { class: "muted" }, `${ago(r.at)}${r.note ? ` · “${r.note}”` : ""}`)),
          view.can_manage && h("div", { class: "row" }, btn("Approve", async () => { await command("join_decision", { request_id: r.id, decision: "approve" }); $("#modal").close(); }, "primary small"),
            btn("Decline", () => { $("#modal").close(); setTimeout(() => declineJoinDialog(r), 0); }, "quiet small")))))),
      view.can_manage && outside.length > 0 ? field("Add an organisation", select("organisation", [["", "Choose an organisation"], ...outside.map(o => [o.id, o.name])]), "It can open the incident straight away. This grants no account or new role.")
        : !view.can_manage && notice("A coordinator or supervisor of a participating organisation adds bodies and answers join requests."),
      view.can_manage && outside.length > 0 && field("Why (optional)", input("reason", "", { maxlength: 300, placeholder: "e.g. Bowsers needed at the hall" })),
    ], async data => { if (data.get("organisation")) await command("participant", { organisation: data.get("organisation"), reason: data.get("reason") || null }); }, view.can_manage && outside.length ? "Add organisation" : "Done");
  }
  function standDownDialog(org) {
    dialog(`Stand down ${orgName(org)}`, () => [notice("It keeps read access and its history, but can no longer act here. A lead can add it again later."), field("Reason", h("textarea", { name: "reason", required: true, minlength: 4, maxlength: 300, placeholder: "e.g. Road cleared; nothing left for RDA" }))],
      async data => command("stand_down", { organisation: org, reason: data.get("reason") }), "Stand down");
  }
  function declineJoinDialog(r) {
    dialog(`Decline ${orgName(r.organisation)}`, () => [field("Reason", h("textarea", { name: "reason", required: true, minlength: 4, maxlength: 300 }))],
      async data => command("join_decision", { request_id: r.id, decision: "decline", reason: data.get("reason") }), "Decline");
  }
  function myWork() {
    const items = [], go = (tab, id, pane = "data") => () => focusRecord(tab, id, pane);
    if (pv().can_manage) for (const r of pv().join_requests.filter(r => r.status === "pending")) items.push(["join", `${orgShort(r.organisation)} asks to join`, r.note || "Approve or decline", organisationsDialog]);
    for (const a of (c().asks || []).filter(askOpen)) {
      const can = new Set(a.can || []), what = `${a.title} · ${orgShort(a.from)} → ${orgShort(a.to)}`;
      if (can.has("recommend")) items.push(["finance", "Recommend or return a funding request", what, go("requests", a.id)]);
      else if (can.has("accept")) items.push(["ask", a.kind === "funding" ? "Funding awaits your approval" : `New request from ${orgShort(a.from)}`, what, go("requests", a.id)]);
      else if (can.has("confirm")) items.push(["ask", "Confirm a delivered request", what, go("requests", a.id)]);
      else if (can.has("start") || can.has("done")) items.push(["ask", a.kind === "funding" ? "Release approved funding" : "Deliver what your organisation accepted", what, go("requests", a.id)]);
    }
    for (const s of state.doc.sources) if (s.status === "reported" && s.author !== state.me.id && (canAct("coordinator") || canAct("agency_supervisor") && s.organisation === state.me.organisation)) items.push(["report", "Review a report", `${s.title} · ${orgShort(s.organisation)}`, go("sources", s.id)]);
    // Older records keep the same rules as the Dashboard.
    if (canAct("coordinator")) for (const r of c().requests) if (["open", "needs_clarification"].includes(r.status) && !c().offers.some(o => o.request_id === r.id && ["offered", "accepted", "mobilised", "reported_complete"].includes(o.status))) items.push(["request", "Earlier need without an assignment", r.title, go("assignments", r.id)]);
    for (const f of c().finance) {
      const owner = c().schemes.find(x => x.id === f.scheme_id)?.owner;
      if (canAct("coordinator") && f.status === "requested" && f.requester !== state.me.id) items.push(["finance", "Recommend or return a funding record", f.purpose, go("finance", f.id)]);
      else if (canAct("finance_approver") && f.status === "recommended" && owner === state.me.organisation && ![f.requester, f.recommender].includes(state.me.id)) items.push(["finance", "Funding record awaits your decision", f.purpose, go("finance", f.id)]);
    }
    for (const id of c().my_queue || []) { const o = c().offers.find(x => x.id === id); if (o && canAct("agency_supervisor")) items.push(["assignment", "Earlier assignment for your organisation", `${o.quantity} · ${o.note}`, go("assignments", o.id)]); }
    const proposals = (c().chat.proposals || []).filter(p => p.status === "pending" && p.version === state.doc.version).length + (c().plans || []).filter(p => p.origin === "chat" && planOpen(p)).length;
    if (proposals) items.push(["proposal", `${proposals} change${proposals === 1 ? "" : "s"} from the chat to review`, "Check the exact change before it applies.", go(state.tab, null, "chat")]);
    const checks = (c().public_requests || []).filter(r => r.status === "verify" || r.similar_to);
    if (checks.length && canAct("coordinator")) { const answered = checks.filter(r => r.answer).at(-1); items.push(["public", `${plural(checks.length, "public request")} to check`, answered ? `${answered.code} answered: ${answered.answer.text}` : checks.at(-1).reading?.summary || checks.at(-1).text, go("overview", "public-requests")]); }
    const suggested = (c().plans || []).filter(p => p.origin === "event" && p.status === "pending");
    if (suggested.length) items.push(["proposal", `${plural(suggested.length, "suggestion")} to review`, suggested[0].title, go("overview", "ai-suggestions")]);
    for (const n of pendingAcks()) items.push(["ack", `Acknowledge a report from ${orgShort(n.from)}`, sourceName(n.source_id), go("sources", n.source_id)]);
    const unread = (c().notifications || []).filter(n => !n.read_by.includes(state.me.id) && !pendingAcks().includes(n)).length;
    if (unread) items.push(["notification", `${unread} unread shared report${unread === 1 ? "" : "s"}`, "Another body asked for your attention.", go("notifications")]);
    return items;
  }
  // Show a tab and move focus to one of its records once the prebuilt tab panel has rendered.
  async function focusRecord(tab, id, pane = "data") {
    state.tab = tab; state.mobileView = pane; renderIncident();
    const tabs = $("#data-area wa-tab-group");
    if (tabs) await Promise.all([tabs.updateComplete, ...[...tabs.querySelectorAll("wa-tab-panel")].map(panel => panel.updateComplete)]);
    const el = id && document.getElementById(id); if (!el) return;
    for (let d = el.closest("details"); d; d = d.parentElement?.closest("details")) d.open = true;
    el.tabIndex = -1; el.focus({ preventScroll: true }); el.scrollIntoView({ block: "center" });
  }
  // ---------------------------------------------------------------- public requests: what people asked for, read by the AI
  const NEED_LABEL = { rescue: "Rescue", medical: "Medical help", shelter: "Shelter", food: "Food", water: "Drinking water", power: "Electricity", road: "Road access", communications: "Phone and internet", other: "Other help" };
  const PUBLIC_STATE = { filed: ["In this incident", ""], verify: ["Needs a check", "warn"], hold: ["Waiting for a decision", "warn"], closed: ["Closed", ""], received: ["Being read", ""] };
  async function photoUrl(path) {
    const response = await fetch("/api/teams" + path, { credentials: "same-origin", headers: state.demoToken ? { "X-Demo-Session": state.demoToken } : {} });
    if (!response.ok) throw new Error("Photo unavailable");
    return URL.createObjectURL(await response.blob());
  }
  function photoStrip(r) {
    if (!(r.photos || []).length) return null;
    const strip = h("div", { class: "public-photos" });
    for (const p of r.photos) {
      const img = h("img", { alt: `Photo ${p.n + 1} sent with ${r.code}`, width: 120, height: 90, loading: "lazy" });
      strip.append(img);
      photoUrl(`/public-requests/${r.id}/photos/${p.n}`).then(url => { img.src = url; }).catch(() => { img.alt = "Photo unavailable"; });
    }
    return strip;
  }
  function publicCard(r, actions = []) {
    const reading = r.reading || {}, [label, tone] = PUBLIC_STATE[r.status] || [r.status, ""];
    const ask = (state.doc?.coordination?.asks || []).find(a => a.public_request_id === r.id);
    const by = r.model?.model && r.model.model !== "offline-rules" ? `Read by ${r.model.model}` : "Read by the offline rules (no AI model)";
    return h("article", { class: "record public-card", id: r.id },
      h("div", { class: "row between" }, h("h3", {}, `${NEED_LABEL[reading.need] || "Request"} · ${r.area_label}`), pill(label, tone)),
      h("small", { class: "muted" }, `${r.code} · ${ago(r.at)}${reading.urgency === "high" ? " · urgent" : ""}${reading.people ? ` · ${reading.people} people` : ""}`),
      r.similar > 0 && h("p", { class: "plan-why" }, `${plural(r.similar, "other report")} confirmed as the same problem.`),
      r.group_of && h("p", { class: "muted" }, `Same problem as ${codeOf(r.group_of)}, confirmed by a coordinator. Its request covers this one.`),
      r.similar_to && h("p", { class: "plan-why" }, `Possibly the same problem as ${codeOf(r.similar_to)}: same need, same area, within a day. If it is, that request covers this one; if not, this one gets its own suggestion.`),
      h("blockquote", { class: "public-words" }, r.text),
      photoStrip(r),
      reading.summary && h("p", { class: "public-summary" }, h("strong", {}, "Summary: "), reading.summary),
      reading.photo_note && h("p", { class: "muted" }, h("strong", {}, "Photos: "), reading.photo_note),
      (r.decision?.reason) && h("p", { class: "plan-why" }, r.decision.reason),
      r.answer && h("p", {}, h("strong", {}, "Their answer: "), r.answer.text),
      (r.reading_notes || []).map(n => h("p", { class: "muted" }, n)),
      r.phone && h("p", {}, h("strong", {}, "Phone: "), h("a", { href: `tel:${r.phone.replace(/\s/g, "")}` }, r.phone)),
      ask && h("p", {}, h("strong", {}, "Request: "), `${ask.title} → ${orgName(ask.to)} · ${ask.status.replaceAll("_", " ")}`),
      h("small", { class: "muted" }, by),
      actions.length > 0 && h("div", { class: "row wrap" }, actions));
  }
  const publicStatus = (r, status, reason, done) => async () => { await api(`/public-requests/${r.id}/status`, { status, reason }); toast(done); };
  function closeDialog(r, after) {
    dialog(`Close ${r.code}`, () => [notice("Closed requests stay in Closed by AI on the Dashboard and can be reopened. The person sees the reason."),
      field("Reason", h("textarea", { name: "reason", rows: 2, maxlength: 300, required: true }, "Not something this portal handles."))],
      async data => { await api(`/public-requests/${r.id}/status`, { status: "closed", reason: data.get("reason") }); toast("Request closed"); await after(); }, "Close request");
  }
  // In the incident: what the public asked for here, with the AI's reading.
  function publicSection() {
    const items = (c().public_requests || []).filter(r => r.status !== "closed");
    if (!items.length) return null;
    const refresh = async () => { state.doc = await api(`/incidents/${state.doc.id}`); renderIncident(); };
    return h("section", { id: "public-requests", class: "card stack", "aria-labelledby": "public-title" },
      h("div", { class: "row between" }, h("h2", { id: "public-title" }, "From the public"), pill(String(items.length))),
      h("p", { class: "muted" }, "Requests people sent from the public status page. The suggested request for each is under Suggestions."),
      items.toReversed().map(r => publicCard(r, !canAct("coordinator") ? [] : r.status === "verify" ? [
        btn("Mark as genuine", async () => { await publicStatus(r, "filed", "Checked by a coordinator", "Marked as genuine")(); await refresh(); }, "primary"),
        btn("Close", () => closeDialog(r, refresh), "quiet")] : r.similar_to ? [
        btn("Same problem", async () => { await api(`/public-requests/${r.id}/similar`, { same: true }); toast(`Joined to ${codeOf(r.similar_to)}`); await refresh(); }),
        btn("Different place or need", async () => { await api(`/public-requests/${r.id}/similar`, { same: false }); toast("Kept separate; its own suggestion follows"); await refresh(); }, "primary")] : [])));
  }
  function codeOf(id) { return (state.doc?.coordination?.public_requests || []).find(x => x.id === id)?.code || "an earlier report"; }
  // On the Dashboard, for coordinators: requests with no incident, requests to check, and what the AI closed.
  function publicQueueSection(queue, records) {
    const waiting = [...queue.hold, ...queue.verify];
    if (!waiting.length && !queue.closed.length) return null;
    const open = records.filter(i => i.status !== "closed");
    const refresh = () => listIncidents();
    const actions = r => {
      const district = r.area_label.split(", ").at(-1).replace(" District", "");
      const here = open.filter(i => i.district === district), others = open.filter(i => i.district !== district);
      const choice = select("incident", [...here, ...others].map(i => [i.id, `${i.title} · ${i.district}`]));
      choice.setAttribute("aria-label", `Incident for ${r.code}`);
      return [open.length > 0 && h("div", { class: "row public-file" }, choice, btn("Add to incident", async () => { await api(`/public-requests/${r.id}/file`, { incident_id: choice.value }); toast("Added to the incident"); await refresh(); })),
        btn("Open new incident", () => openFromPublic(r, district), "primary"),
        btn("Close", () => closeDialog(r, refresh), "quiet")];
    };
    return h("section", { id: "dash-public", class: "dash-card stack", "aria-labelledby": "dash-public-title" },
      h("div", { class: "dash-card-head" }, h("h2", { id: "dash-public-title" }, "Public requests waiting"), pill(String(waiting.length), waiting.length ? "warn" : "green")),
      waiting.length ? h("p", { class: "muted" }, "No open incident covers these yet, or they need a check. Add each to an incident, open one, or close it.") : h("p", { class: "muted" }, "Nothing waiting."),
      waiting.map(r => publicCard(r, actions(r))),
      queue.closed.length > 0 && h("details", { class: "closed-by-ai" }, h("summary", {}, `Closed automatically (${queue.closed.length}) · check and reopen if needed`),
        queue.closed.toReversed().map(r => publicCard(r, [btn("Reopen", async () => { await publicStatus(r, "hold", "Reopened by a coordinator", "Reopened")(); await refresh(); }, "quiet")]))));
  }
  function openFromPublic(r, district) {
    const need = r.reading?.need || "other", owner = { power: "edl", water: "water", road: "rda", food: "ndrsc" }[need];
    const types = state.incidentTypes?.hazards || {};
    const choices = (state.catalogue?.organisations || []).filter(o => o.active && o.id !== state.me.organisation);
    dialog("Open an incident from this request", () => [notice(`The request (${r.code}) goes into the new incident, with a suggested request for the right body.`),
      field("Title", input("title", `${NEED_LABEL[need] || "Help"} · ${district}`, { required: true, maxlength: 120 })),
      Object.keys(types).length > 0 && field("Incident type", select("hazard", Object.entries(types))),
      field("Organisations", checkList("participants", choices.map(o => [o.id, o.name]), [owner, "division", "district"].filter(Boolean)))],
      async data => {
        const doc = await api(`/public-requests/${r.id}/open`, { title: data.get("title"), hazard: data.get("hazard") || null, participants: data.getAll("participants") });
        location.hash = new URLSearchParams({ incident: doc.id });
      }, "Open incident");
  }
  // Plans prepared when something happened (a warning, a report, an overdue request, a disagreement), for this role.
  function suggestionsSection() {
    const mine = (c().plans || []).filter(p => p.origin === "event" && p.status === "pending");
    if (!mine.length || state.doc.status === "closed") return null;
    return h("section", { id: "ai-suggestions", class: "card stack", "aria-labelledby": "suggestions-title" },
      h("div", { class: "row between" }, h("h2", { id: "suggestions-title" }, "Suggestions"), pill(String(mine.length), "warn")),
      h("p", { class: "muted" }, "Prepared when something happened in this incident, for your organisation and role. Check each one; nothing applies until you approve it."),
      mine.map(planCard));
  }
  function overviewPanel() {
    const items = state.doc.status === "closed" ? [] : myWork(), shelters = c().assets.filter(a => a.kind === "shelter");
    const known = shelters.filter(a => a.balance.available != null), pending = state.doc.sources.filter(s => s.status === "reported").length;
    const open = (c().asks || []).filter(askOpen).length;
    const metric = (value, label, tab) => btn([h("strong", {}, value), " ", h("span", {}, label), tab && h("span", { class: "metric-arrow", "aria-hidden": "true" }, actionIcon("request"))], () => { if (tab) { state.tab = tab; renderData(); } }, "metric");
    return h("div", { class: "stack" },
      h("section", { class: "card stack", "aria-labelledby": "needs-you-title" }, h("div", { class: "row between" }, h("h2", { id: "needs-you-title" }, "Needs you"), items.length ? pill(String(items.length), "warn") : pill("Up to date", "green")),
        state.doc.status === "closed" ? notice("This incident is closed. Records stay readable; a coordinator can reopen it with a reason.") :
        items.length ? h("ul", { class: "attention-list" }, items.map(([kind, title, detail, onclick]) => h("li", {}, h("button", { type: "button", class: "attention-item " + kind, onclick }, h("span", { class: "attention-icon", "aria-hidden": "true" }, kindIcon[kind] || "•"), h("span", { class: "attention-text" }, h("strong", {}, title), h("span", {}, detail)), h("span", { class: "chevron", "aria-hidden": "true" }, "›")))))
          : h("div", { class: "dash-empty" }, h("strong", {}, "You’re up to date"), h("p", {}, jobOf().idle || "Nothing here needs your organisation and role right now."))),
      h("div", { class: "metrics" }, metric(open, "Open requests", "requests"), metric(pending, "Reports to review", "sources"), metric(!shelters.length ? "—" : known.length ? known.reduce((n, a) => n + a.balance.available, 0) : shelters.reduce((n, a) => n + (lastFree(a) ?? 0), 0), known.length || !shelters.length ? "Free shelter places" : "Free places at last report", "resources"), metric(c().participants.length, "Organisations", null)),
      shelters.length > 0 && h("section", { class: "card stack", "aria-labelledby": "shelters-title" }, h("h2", { id: "shelters-title" }, "Shelters"), h("ul", { class: "shelter-list" }, shelters.map(a => h("li", { class: "overview-shelter" }, h("span", {}, h("strong", {}, a.name), h("small", {}, a.balance.available == null ? `${orgName(a.owner)} · ${lastFree(a) != null ? `${lastFree(a)} free at last report · ` : ""}${shelterCheck(assetFacts(a))}` : `${orgName(a.owner)} · ${a.balance.reason}`)), pill(a.balance.available == null ? "Needs a check" : a.balance.available === 0 ? "Full" : `${a.balance.available} free`, a.balance.available == null ? "warn" : a.balance.available === 0 ? "danger" : "green"))))),
      publicSection(),
      suggestionsSection(),
      areaSummaryCard(),
      has("coordinator") && h("details", { class: "card incident-admin", open: state.adminOpen, ontoggle: e => { state.adminOpen = e.currentTarget.open; } }, h("summary", {}, "Incident management"), operationPanel("situation")));
  }
  function areaSummaryCard() {
    const pic = c().picture, states = Object.values(pic.areas);
    const warnings = pic.warnings.filter(activeWarning), down = states.filter(s => Object.values(s.services).some(r => r.level === "disrupted")).length;
    const people = pic.totals?.people_affected ?? states.reduce((n, s) => n + (s.figures?.values.people_affected || 0), 0);
    const open = () => { state.tab = "map"; renderData(); };
    return h("section", { class: "card stack", "aria-labelledby": "area-summary-title" }, h("div", { class: "row between wrap" }, h("h2", { id: "area-summary-title" }, "Area status"), btn(actionLabel("Open Map & status", "forward", true), open, "quiet with-icon")),
      h("div", { class: "metrics compact" }, [[warnings.length, "Warnings in force"], [down, "Areas with a service down"], [people ? people.toLocaleString() : "—", "People affected (reported)"]].map(([value, label]) => h("div", { class: "metric static" }, h("strong", {}, value), " ", h("span", {}, label)))),
      h("div", { id: "incident-map", class: "map" }),
      h("small", { class: "muted" }, "Official warnings by DS division. Select an area to open its status. A blank area means nothing was reported, not that it is safe."));
  }
  async function drawMap() {
    const container = $("#incident-map"); if (!container || !container.clientWidth) return;
    await areas();
    mapCtl = await drawStatusMap(container, c().picture, { layer: "warnings", focus: c().district, vocab: vocab(), label: `Official warnings in ${c().district} District`,
      onSelect: id => { state.mapArea = id; state.tab = "map"; renderData(); } });
    map = mapCtl;
  }
  function acceptedOptions() { return state.doc.sources.filter(s => s.status === "accepted").map(s => [s.id, `${s.title} · ${orgName(s.organisation)}`]); }
  function sourceField() { return field("Accepted evidence", select("source_id", [["", "Choose reviewed evidence"], ...acceptedOptions()]), "If missing, add a report and have a different reviewer accept it first."); }
  function contributeDialog() {
    const fileInput = input("file", "", { type: "file", accept: ".pdf,.txt", "aria-describedby": "attachment-status" });
    const titleInput = input("title", "", { required: true, maxlength: 150, placeholder: "e.g. Kotmale water supply update…" });
    const textInput = h("textarea", { name: "text", placeholder: "Paste the original message, including its location and facts…", maxlength: 60000 });
    const fileStatus = h("small", { id: "attachment-status", role: "status" }, "Text PDF or UTF-8 TXT · up to 2 MB / 30 PDF pages. Choose a file or paste text below.");
    fileInput.addEventListener("change", () => {
      const file = fileInput.files[0];
      if (file && !titleInput.value.trim()) titleInput.value = file.name.replace(/\.[^.]+$/, "").replaceAll("_", " ");
      textInput.disabled = !!file;
      fileStatus.textContent = file ? `${file.name} selected. The original file will be analysed. Add commentary later in chat.` : "Choose a PDF/TXT file or paste the original message below.";
    });
    const mine = state.me.organisation, others = c().participants.filter(o => o !== mine);
    dialog("Add a report", () => [notice(`Shared with every participating organisation and attributed to ${orgName(mine)}. A reviewer from another account checks it before it counts as evidence.`),
      field("Attach the original (optional)", fileInput), fileStatus, field("What was reported?", textInput), field("Report title", titleInput),
      field("Ask to acknowledge", checkList("recipients", others.map(o => [o, orgName(o)]), others.includes("dmc") ? ["dmc"] : []), "Each selected organisation must acknowledge it. Everyone can read the report either way; nothing is sent by SMS or email."),
      field("Message to them (optional)", h("textarea", { name: "message", maxlength: 1000, rows: 2, placeholder: "e.g. Please check water at the hall before 18:00" })),
      h("details", { class: "form-disclosure" }, h("summary", {}, "Source timing · change for an older report"), h("div", { class: "stack" }, field("Observation time", input("observed_at", c().replay_at || now(), { required: true }), "Defaults to the current incident time. Replace it with the actual observation time for older reports (ISO format with timezone)."), field("Publication time (optional)", input("published_at", ""))))],
      async data => {
        const file = data.get("file"), original = data.get("text") || "";
        if (!file?.size && original.trim().length < 10) { textInput.setAttribute("aria-invalid", "true"); throw new Error("Attach a PDF/TXT report or paste at least 10 characters of the original message."); }
        textInput.removeAttribute("aria-invalid");
        const payload = { title: data.get("title"), text: original, observed_at: data.get("observed_at"), published_at: data.get("published_at") || null, recipients: data.getAll("recipients"), ...(data.get("message") ? { message: data.get("message") } : {}) };
        if (file?.size) { if (file.size > 2000000) throw new Error("Maximum attachment size is 2 MB. Choose a smaller PDF/TXT report."); const bytes = new Uint8Array(await file.arrayBuffer()); let binary = ""; for (const b of bytes) binary += String.fromCharCode(b); payload.attachment = { name: file.name, base64: btoa(binary) }; }
        await command("contribute", payload);
        const source = state.doc.sources.at(-1);
        $("#modal").close();
        await analyseReport(source.id);
      }, "Upload and analyse");
  }
  function notifyDialog(source) {
    const asked = new Set((source.acknowledgements || []).map(a => a.organisation)), others = c().participants.filter(o => o !== state.me.organisation && !asked.has(o));
    dialog("Ask to acknowledge", () => [notice(`Everyone can already read “${source.title}”. Each selected organisation is asked to acknowledge it; this does not copy, approve or assign it.`),
      others.length ? field("Organisations", checkList("recipients", others.map(o => [o, orgName(o)]))) : notice("Every participating organisation has already been asked."),
      field("Message", h("textarea", { name: "note", required: true, maxlength: 1000 }, "Please review this shared report and acknowledge it."))],
      async data => { if (!data.getAll("recipients").length) throw new Error("Choose at least one organisation."); await command("notify_report", { source_id: source.id, recipients: data.getAll("recipients"), note: data.get("note") }); }, "Ask to acknowledge");
  }
  const acting = () => canAct("contributor", "agency_supervisor", "coordinator", "finance_approver", "communications_reviewer");
  // Notifications for this organisation that still need its acknowledgement.
  const pendingAcks = () => acting() ? (c().notifications || []).filter(n => n.ack_required && !(n.acks || {})[state.me.organisation]) : [];
  const acknowledge = n => command("acknowledge", { notification_id: n.id });
  function notificationsPanel() {
    const mine = state.me.organisation;
    return h("div", { class: "card stack" }, h("h2", {}, "Reports shared with your organisation"), notice("Reports stay visible to every participating body. A notification asks your organisation to acknowledge a report; it does not assign work."), !(c().notifications || []).length && empty("No notifications for your organisation yet."),
      (c().notifications || []).toReversed().map(n => {
        const ack = (n.acks || {})[mine], read = n.read_by.includes(state.me.id);
        return h("article", { class: "record" }, h("div", { class: "row between" }, h("strong", {}, orgName(n.from)), n.ack_required ? pill(ack ? "Acknowledged" : "Needs acknowledgement", ack ? "green" : "warn") : pill(read ? "Read" : "New", read ? "" : "warn")), h("p", {}, n.note), h("small", {}, `${sourceName(n.source_id)} · ${dateTime(n.at)}${ack ? ` · acknowledged ${dateTime(ack.at)}` : ""}`),
          h("div", { class: "row" }, btn("Open shared report", async () => { if (!read && !n.ack_required) await command("read_notification", { notification_id: n.id }); await focusRecord("sources", n.source_id); }), n.ack_required && !ack && acting() && btn("Acknowledge", () => acknowledge(n), "primary"), !n.ack_required && !read && btn("Mark read", () => command("read_notification", { notification_id: n.id }), "quiet")));
      }));
  }
  const reportTimes = [["all", "Any time"], ["1", "Last hour"], ["6", "Last 6 hours"], ["24", "Last 24 hours"], ["168", "Last 7 days"]];
  const colomboDay = value => new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Colombo" }).format(new Date(value));
  function reportBucket(s, reference) {
    const at = Date.parse(s.observed_at);
    if (reference - at < 3600000) return "Last hour";
    if (colomboDay(at) === colomboDay(reference)) return "Earlier today";
    if (colomboDay(at) === colomboDay(reference - 86400000)) return "Yesterday";
    return "Older";
  }
  function reportCard(s) {
    const mine = state.me.organisation, mineAck = pendingAcks().find(n => n.source_id === s.id), acks = s.acknowledgements || [];
    const status = { reported: ["Awaiting review", "warn"], accepted: ["Accepted", "green"], rejected: ["Rejected", "danger"], issued: ["Issued letter", ""] }[s.status] || [s.status, ""];
    return h("article", { class: "record report-card" + (mineAck ? " your-turn" : ""), id: s.id, "aria-labelledby": `${s.id}-title` },
      h("div", { class: "row between" }, h("h3", { id: `${s.id}-title` }, s.title), pill(status[0], status[1])),
      h("small", { class: "report-meta" }, h("strong", { title: orgName(s.organisation) }, orgShort(s.organisation)), ` · Observed ${dateTime(s.observed_at)}${Date.now() - Date.parse(s.observed_at) < 86400000 ? ` · ${ago(s.observed_at)}` : ""}`),
      acks.length > 0 && h("div", { class: "ack-list", role: "group", "aria-label": "Acknowledgements" }, h("span", {}, "Acknowledged by"),
        acks.map(a => h("span", { class: "ack-chip " + (a.acknowledged_at ? "done" : "waiting"), title: a.acknowledged_at ? `${orgName(a.organisation)} acknowledged ${dateTime(a.acknowledged_at)}${a.note ? ` · ${a.note}` : ""}` : `${orgName(a.organisation)} has not acknowledged yet` },
          `${orgShort(a.organisation)} ${a.acknowledged_at ? "✓" : "· waiting"}`, h("span", { class: "sr-only" }, a.acknowledged_at ? " acknowledged" : "")))),
      mineAck && h("div", { class: "ack-ask" }, h("span", {}, h("strong", {}, `${orgShort(mineAck.from)} asks ${orgShort(mine)} to acknowledge`), mineAck.note && h("small", {}, ` “${mineAck.note}”`)), btn("Acknowledge", () => acknowledge(mineAck), "primary")),
      h("details", {}, h("summary", {}, s.status === "issued" ? "Read the letter" : "Read original evidence"), h("pre", { class: "source-text" }, s.text), h("small", {}, `SHA256 ${s.hash}`)),
      h("div", { class: "row report-actions" }, btn("Analyse report", () => analyseReport(s.id), "quiet", { disabled: state.busy }),
        acting() && s.status !== "issued" && /situation report|water level|landslide|warning|level\s*[123]|flood/i.test(s.text) && btn("Read figures & warnings", () => readBulletinDialog(s), "quiet"), state.doc.status !== "closed" && acting() && btn("Ask to acknowledge", () => notifyDialog(s), "quiet"),
        s.attachment && btn(`Download original: ${s.attachment.name}`, () => download(`/incidents/${state.doc.id}/sources/${s.id}/attachment`, s.attachment.name), "quiet"),
        s.status === "reported" && s.author !== state.me.id && (canAct("coordinator") || canAct("agency_supervisor") && s.organisation === mine) && [btn("Accept evidence", () => command("review_source", { source_id: s.id, decision: "accepted" }), "primary"), btn("Reject", () => command("review_source", { source_id: s.id, decision: "rejected" }), "quiet danger")]),
      s.attachment && h("small", {}, s.attachment.extraction));
  }
  // Turn an official bulletin in a report into figures and warnings, after a person checks each row.
  async function readBulletinDialog(s) {
    await areas();
    const read = await api(`/incidents/${state.doc.id}/sources/${s.id}/read`), v = vocab(), home = c().district;
    const figuresAllowed = ["dmc", "district", "division", "ndrsc"].includes(state.me.organisation);
    const gaugeRows = read.gauges.rows.filter(r => r.warning);
    const landslide = /landslide/i.test(s.text);
    const sections = [];
    if (read.sitrep.rows.length) sections.push(h("fieldset", { class: "bulletin-section" }, h("legend", {}, `Situation report figures${read.sitrep.as_of ? ` · as at ${dateTime(read.sitrep.as_of)}` : ""}`),
      !figuresAllowed && notice("Figures are recorded by the DMC, district, divisional or NDRSC offices."),
      h("div", { class: "check-list" }, read.sitrep.rows.map(r => h("label", {}, input("sitrep", r.area, { type: "checkbox", checked: r.district === home, disabled: !figuresAllowed }),
        `${r.district}: ${r.figures.people_affected.toLocaleString("en-GB")} people affected, ${r.figures.deaths} deaths, ${r.figures.missing} missing, ${r.figures.people_in_centres.toLocaleString("en-GB")} in ${r.figures.safety_centres} centres`))),
      read.sitrep.problems.length > 0 && h("small", { class: "muted" }, "Not read: " + read.sitrep.problems.join("; "))));
    if (read.warnings.levels.length) sections.push(h("fieldset", { class: "bulletin-section" }, h("legend", {}, "Warnings named in the text"),
      h("div", { class: "field-row" }, field("Issued by", select("w_issuer", Object.entries(v.issuers), landslide ? "nbro" : "dmc")), field("Hazard", select("w_hazard", Object.entries(v.hazards), landslide ? "landslide" : "flood"))),
      read.warnings.levels.map(l => h("div", {}, h("strong", {}, `${WARNING[l.level].icon} ${v.warning_levels[l.level]}`), h("div", { class: "check-list" }, l.areas.map(a => h("label", {}, input("w_" + l.level, a, { type: "checkbox", checked: true }), nameOf(a)))))),
      field("Advice from the bulletin", h("textarea", { name: "w_advice", maxlength: 600, placeholder: "Copy the issuer's advice exactly." })),
      field("Valid until (optional)", input("w_valid_until", "", { type: "datetime-local" }))));
    if (gaugeRows.length) sections.push(h("fieldset", { class: "bulletin-section" }, h("legend", {}, `River gauges above alert level${read.gauges.issued_at ? ` · ${dateTime(read.gauges.issued_at)}` : ""}`),
      h("div", { class: "check-list" }, gaugeRows.map(r => h("label", {}, input("gauge", r.gauge_id, { type: "checkbox", checked: !!r.area && state.areas.byId.get(r.area)?.district === home, disabled: !r.area }),
        `${r.name} (${r.river}): ${r.level} ${r.unit}, ${r.state === "major" ? "major flood" : r.state === "minor" ? "minor flood" : "alert"}${r.trend ? `, ${r.trend}` : ""} → ${WARNING[r.warning].icon} ${v.warning_levels[r.warning]} for ${r.area ? nameOf(r.area) : "no known area"}`))),
      h("small", { class: "muted" }, "Each ticked gauge records an Irrigation Department flood warning for the DS division it is in.")));
    if (!sections.length) { toast("No figures, warnings or gauge levels were recognised in this report."); return; }
    dialog(`Read: ${s.title}`, () => [notice("Check every row against the original before adding it. Each record links back to this report."), ...sections], async data => {
      let added = 0;
      for (const area of data.getAll("sitrep")) {
        const row = read.sitrep.rows.find(r => r.area === area);
        await command("area_figures", { area, as_of: read.sitrep.as_of || s.observed_at, source_id: s.id, ...row.figures }); added++;
      }
      for (const level of ["red", "amber", "yellow"]) {
        const chosen = data.getAll("w_" + level); if (!chosen.length) continue;
        if (!data.get("w_advice")) throw new Error("Copy the advice from the bulletin before adding warnings.");
        await command("warning", { issuer: data.get("w_issuer"), hazard: data.get("w_hazard"), level, areas: chosen, issued_at: s.observed_at, valid_until: toIso(data.get("w_valid_until")), advice: data.get("w_advice"), source_id: s.id, public: canPublish() }); added++;
      }
      const picked = gaugeRows.filter(r => data.getAll("gauge").includes(r.gauge_id));
      for (const level of ["red", "amber", "yellow"]) {
        const rows = picked.filter(r => r.warning === level); if (!rows.length) continue;
        await command("warning", { issuer: "irrigation", hazard: "flood", level, areas: [...new Set(rows.map(r => r.area))], issued_at: read.gauges.issued_at || s.observed_at,
          advice: rows.map(r => `${r.river} at ${r.name}: ${r.level} ${r.unit} (${r.state === "major" ? "major flood" : r.state === "minor" ? "minor flood" : "alert"} level${r.trend ? `, ${r.trend}` : ""}).`).join(" ") + " Follow Irrigation Department and DMC instructions.",
          source_id: s.id, public: canPublish() }); added++;
      }
      toast(added ? `Added ${plural(added, "record")} from ${s.title}` : "Nothing was ticked");
    }, "Add ticked records");
  }
  function sourcesPanel() {
    const sources = state.doc.sources, reference = c().replay_at ? Date.parse(c().replay_at) : Date.now();
    const orgs = [...new Set(sources.map(s => s.organisation))];
    const search = input("report_search", state.reportQuery, { id: "report-search", type: "search", placeholder: "Search reports…", "aria-label": "Search reports" });
    const org = select("report_org", [["all", "All organisations"], ...orgs.map(o => [o, orgName(o)])], orgs.includes(state.reportOrg) ? state.reportOrg : "all"); org.id = "report-org-filter"; org.setAttribute("aria-label", "Organisation");
    const time = select("report_time", reportTimes, state.reportTime); time.id = "report-time-filter"; time.setAttribute("aria-label", "Time");
    const status = select("report_status", [["all", "Any review state"], ["reported", "Awaiting review"], ["accepted", "Accepted"], ["rejected", "Rejected"]], state.reportStatus); status.id = "report-status-filter"; status.setAttribute("aria-label", "Review state");
    const waiting = pendingAcks().length;
    const group = h("div", { class: "segmented", role: "group", "aria-label": "Group reports by" }, [["org", "By organisation"], ["time", "By time"]].map(([key, label]) => btn(label, () => { state.reportGroup = key; update(); }, "", { "data-key": key })));
    const count = h("div", { class: "result-count", role: "status", "aria-live": "polite" }), list = h("div", { class: "report-groups" });
    const clear = btn("Clear filters", () => { search.value = ""; org.value = "all"; time.value = "all"; status.value = "all"; state.reportMine = false; update(); search.focus(); }, "quiet clear-filters");
    const update = () => {
      state.reportQuery = search.value; state.reportOrg = org.value; state.reportTime = time.value; state.reportStatus = status.value;
      clear.hidden = !(search.value || org.value !== "all" || time.value !== "all" || status.value !== "all" || state.reportMine);
      group.querySelectorAll("button").forEach(b => b.setAttribute("aria-pressed", String(b.dataset.key === state.reportGroup)));
      const query = search.value.trim().toLocaleLowerCase(), hours = Number(time.value);
      const matches = sources.filter(s => (org.value === "all" || s.organisation === org.value) && (time.value === "all" || reference - Date.parse(s.observed_at) <= hours * 3600000) && (status.value === "all" || s.status === status.value) && (!state.reportMine || pendingAcks().some(n => n.source_id === s.id)) && `${s.title} ${orgName(s.organisation)} ${s.text}`.toLocaleLowerCase().includes(query))
        .sort((a, b) => Date.parse(b.observed_at) - Date.parse(a.observed_at));
      count.textContent = `${matches.length} of ${plural(sources.length, "report")}`;
      const groups = new Map();
      for (const s of matches) { const key = state.reportGroup === "time" ? reportBucket(s, reference) : s.organisation; if (!groups.has(key)) groups.set(key, []); groups.get(key).push(s); }
      list.replaceChildren(...(matches.length ? [...groups].map(([key, items], i) => h("section", { class: "report-group", "aria-labelledby": `report-group-${i}` }, h("h3", { id: `report-group-${i}`, class: "report-group-title" }, state.reportGroup === "time" ? key : orgName(key), h("span", { class: "count" }, String(items.length))), items.map(reportCard)))
        : [empty(sources.length ? "No matching reports. Try another search, organisation or time, or clear the filters." : "No reports yet. Any organisation can add one: attach the original message or document.")]));
    };
    for (const control of [org, time, status]) control.addEventListener("change", update);
    search.addEventListener("input", update);
    const panel = h("div", { class: "card stack" }, h("div", { class: "panel-title" }, h("h2", {}, "Reports"), h("small", {}, "Any organisation can add one and ask others to acknowledge it.")),
      waiting > 0 && h("div", { class: "ack-banner" }, h("span", {}, h("strong", {}, `${plural(waiting, "report")} waiting for ${orgShort(state.me.organisation)}’s acknowledgement`)), btn(state.reportMine ? "Show all reports" : "Show only these", () => { state.reportMine = !state.reportMine; update(); }, "quiet")),
      // Nothing to filter until the first report arrives.
      sources.length > 0 && h("div", { class: "filter-bar report-filters", role: "search", "aria-label": "Filter reports" }, search, org, time, status, group, count, clear), list);
    update(); return panel;
  }
  function assetDialog(asset = null) {
    dialog(asset ? "Refresh owner-confirmed resource" : "Confirm an agency resource", () => [notice("Only your organisation can confirm its resources. Every value needs an exact excerpt from accepted evidence. Shelter/transport balances require all readiness and count inputs."), !asset && registeredPicker(), !asset && field("Resource name", input("name", "", { required: true })), !asset && field("Resource kind", select("kind", ["shelter", "transport", "team", "road", "power", "water", "supplies", "other"])), !asset && field("Owner shared resource ID (optional)", input("resource_key", ""), "Use the same owner-confirmed ID in linked incidents. Do not guess identity from names."), sourceField(), !asset && h("div", { class: "field-row" }, field("Latitude (optional)", input("latitude", "", { type: "number", step: "any" })), field("Longitude (optional)", input("longitude", "", { type: "number", step: "any" }))),
      ...["capacity", "occupied", "external_pending", "status", "access", "water", "power"].map(key => h("div", { class: "field-row" }, field(key.replaceAll("_", " "), input(key, "", { placeholder: { status: "accepting / paused / closed / unknown", access: "passable / restricted / blocked / unknown", water: "working / restricted / unavailable / unknown", power: "working / restricted / unavailable / unknown" }[key] || "Whole number; blank = unknown" })), field("Exact evidence quote", input(key + "_quote", "", { maxlength: 2000 })))), asset && field("Arrivals included in this new occupancy snapshot", checkList("reconcile_offer_ids", c().offers.filter(o => o.asset_id === asset.id && o.status === "completed" && !o.reconciled).map(o => [o.id, `${o.quantity} · ${o.id}`])))],
      async data => {
        const facts = {}; for (const key of ["capacity", "occupied", "external_pending", "status", "access", "water", "power"]) if (data.get(key) !== "") facts[key] = { value: ["capacity", "occupied", "external_pending"].includes(key) ? Number(data.get(key)) : data.get(key), quote: data.get(key + "_quote") };
        await command("confirm_asset", { ...(asset ? { asset_id: asset.id, reconcile_offer_ids: data.getAll("reconcile_offer_ids") } : { name: data.get("name"), kind: data.get("kind"), resource_key: data.get("resource_key") || null, latitude: data.get("latitude") === "" ? null : Number(data.get("latitude")), longitude: data.get("longitude") === "" ? null : Number(data.get("longitude")) }), source_id: data.get("source_id"), facts });
      }, "Confirm facts");
  }
  // Fill the resource form from a standing record in Places & stock; facts still need this incident's evidence.
  function registeredPicker() {
    const picker = select("registered", [["", "Loading your registered places…"]]);
    const kindOf = { water: "water", roads: "road", transport: "transport", rescue: "team" };
    Promise.all([api("/reference/centres"), api("/reference/stock"), api("/reference/items")]).then(([centres, stock, items]) => {
      const mine = r => !r.retired && r.owner === state.me.organisation, itemOf = id => items.records.find(i => i.id === id) || {};
      const options = [...centres.records.filter(mine).map(r => [r.id, r.name, "shelter", r]), ...stock.records.filter(mine).map(r => [r.id, `${itemOf(r.item).name} · ${r.location_name}`, kindOf[itemOf(r.item).category] || "supplies", r])];
      picker.replaceChildren(h("option", { value: "" }, options.length ? "None: type the resource below" : "Nothing registered for your organisation"), ...options.map(([value, label]) => h("option", { value }, label)));
      picker.onchange = () => {
        const chosen = options.find(o => o[0] === picker.value), form = picker.form; if (!chosen || !form) return;
        const [value, label, kind, r] = chosen, set = (key, v) => { const el = form.elements.namedItem(key); if (el) el.value = v ?? ""; };
        set("name", label); set("kind", kind); set("resource_key", value); set("latitude", r.latitude); set("longitude", r.longitude);
      };
    }).catch(() => picker.replaceChildren(h("option", { value: "" }, "Registered places could not load; type the resource below")));
    return field("Registered place or stock (optional)", picker, "Picks the name, kind, location and shared ID from Places & stock. Capacity and readiness still need evidence from this incident.");
  }
  function resourcesPanel() {
    return h("div", { class: "card stack" }, h("div", { class: "row between" }, h("h2", {}, "Owner-confirmed resources"), canAct("agency_supervisor") && btn("Confirm resource", () => assetDialog(), "primary")), !c().assets.length && empty("No confirmed resources. A shelter list alone does not establish current capacity or admission."), c().assets.map(a => h("article", { class: "record", id: a.id }, h("div", { class: "row between" }, h("h3", {}, a.name), pill(a.kind)), h("p", {}, `${orgName(a.owner)} · ${a.balance.available === null ? "Availability needs a check" : a.balance.available + " available in planning balance"}`), notice(a.balance.reason), h("details", {}, h("summary", {}, "Read confirmed facts & provenance"), Object.entries(a.facts).map(([k, v]) => h("p", {}, `${k.replaceAll("_", " ")}: ${v.value} · ${v.quote} · ${sourceName(v.source_id)} · ${dateTime(v.observed_at)}`))), canAct("agency_supervisor") && a.owner === state.me.organisation && btn("Refresh facts", () => assetDialog(a)))));
  }
  function requestDialog() {
    dialog("Record a supported request", () => [field("What is needed?", input("title", "", { required: true, maxlength: 200 })), field("Request kind", select("kind", ["shelter", "transport", "team", "road", "power", "water", "supplies", "other"])), h("div", { class: "field-row" }, field("Quantity (blank if unknown)", input("quantity", "", { type: "number", min: 1, max: 1000000, step: 1 })), field("Unit", input("unit", "people", { required: true }), "Shelter allocations use people. Do not convert family counts.")), sourceField()],
      async data => command("request", { title: data.get("title"), kind: data.get("kind"), quantity: data.get("quantity") === "" ? null : Number(data.get("quantity")), unit: data.get("unit"), source_id: data.get("source_id") }), "Record request");
  }
  function offerDialog(request) {
    dialog("Propose a receiving-body assignment", () => [notice(`${request.title} · ${request.quantity ?? "Unknown"} ${request.unit}. This creates an offer; it reserves nothing until the receiving supervisor accepts.`), field("Confirmed resource", select("asset_id", [["", "No resource (general assignment)"], ...c().assets.filter(a => a.kind === request.kind).map(a => [a.id, `${a.name} · ${a.balance.available ?? "Unknown"} balance`])])), field("Receiving organisation (general assignment)", select("recipient", c().participants.map(o => [o, orgName(o)]))), field("Quantity", input("quantity", request.quantity ?? "", { type: "number", required: true, min: 1, step: 1 })), field("Assignment note", h("textarea", { name: "note", required: true }, "Please verify current conditions before accepting."))],
      async data => command("offer", { request_id: request.id, asset_id: data.get("asset_id") || null, recipient: data.get("recipient"), quantity: Number(data.get("quantity")), note: data.get("note") }), "Create reviewed offer");
  }
  function outcomeDialog(offer) {
    dialog("Report the outcome with evidence", () => [notice("Use new, accepted receiving-body evidence observed after acceptance. Reporting completion does not mark the outcome as reviewed."), sourceField()], async data => command("progress", { offer_id: offer.id, state: "reported_complete", source_id: data.get("source_id") }), "Submit outcome evidence");
  }
  function requestsPanel() {
    return h("div", { class: "stack" }, h("div", { class: "card stack" }, h("div", { class: "row between" }, h("h2", {}, "Needs & requests"), canAct("contributor", "agency_supervisor", "coordinator") && btn("Record request", requestDialog, "primary")), !c().requests.length && empty("Review a report, then record its supported need. Unknown quantities stay unknown."), c().requests.map(r => h("article", { class: "record", id: r.id }, h("h3", {}, r.title), h("p", {}, `${r.quantity ?? "Unknown"} ${r.unit} · ${orgName(r.organisation)} · ${sourceName(r.source_id)}`), canAct("coordinator") && btn("Propose assignment", () => offerDialog(r))))),
      h("div", { class: "card stack" }, h("h2", {}, "Receiving-body assignments"), !c().offers.length && empty("No assignments yet. Offers, acceptance and reviewed completion are separate states."), c().offers.toReversed().map(o => {
        const owner = o.recipient === state.me.organisation && canAct("agency_supervisor"), active = Date.parse(o.expires_at) > (c().replay_at ? Date.parse(c().replay_at) : Date.now());
        return h("article", { class: "record", id: o.id }, h("div", { class: "row between" }, h("h3", {}, `${o.quantity} → ${orgName(o.recipient)}`), pill(!active && ["offered", "accepted", "mobilised"].includes(o.status) ? "expired" : o.status, o.status === "completed" ? "green" : "warn")), h("p", {}, o.note), h("small", {}, `Reservation/offer expiry ${dateTime(o.expires_at)} · ${o.outcome_source_id ? "Outcome: " + sourceName(o.outcome_source_id) : "Outcome unconfirmed"}`),
          h("div", { class: "row" }, owner && o.status === "offered" && active && ["accepted", "rejected", "clarification"].map(decision => btn(decision === "accepted" ? "Accept assignment" : decision === "rejected" ? "Decline" : "Need clarification", () => command("respond", { offer_id: o.id, decision, note: decision }), decision === "accepted" ? "primary" : "")), owner && o.status === "accepted" && active && btn("Record mobilisation", () => command("progress", { offer_id: o.id, state: "mobilised" })), owner && ["accepted", "mobilised"].includes(o.status) && active && btn("Report outcome", () => outcomeDialog(o)), canAct("coordinator") && o.status === "reported_complete" && btn("Review outcome as complete", () => command("review_outcome", { offer_id: o.id, source_id: o.outcome_source_id }), "primary"), (owner || canAct("coordinator")) && ["offered", "accepted", "mobilised"].includes(o.status) && btn("Cancel assignment", () => command("progress", { offer_id: o.id, state: "cancelled" }), "quiet danger")));
      })));
  }
  function financeRequestDialog() {
    dialog("Request funding", () => [notice("Select the funding scheme and accepted supporting evidence. Payment and government delegation integrations are not connected."), field("Scheme", select("scheme_id", c().schemes.map(s => [s.id, `${s.id === "fictional-relief-v1" ? "Relief fund" : s.name} · ${s.pool_key ? "linked pool" : "per incident"} · LKR ${money(s.budget)}`]))), field("Purpose", input("purpose", "", { required: true })), field("Requested LKR", input("amount", "", { required: true, inputmode: "decimal", placeholder: "25000.00" })), sourceField()], async data => { const scheme = c().schemes.find(s => s.id === data.get("scheme_id")); await command("finance_request", { purpose: data.get("purpose"), amount: data.get("amount"), source_id: data.get("source_id"), head: scheme.head, scheme_id: scheme.id }); }, "Record funding request");
  }
  function financeActionDialog(f, action) {
    dialog(`Record ${action} funds`, () => [notice(`${f.purpose} · Requested LKR ${money(f.requested)} · Approved ${f.approved ?? "Unknown"}. Separate evidence states are never added into a fictitious total.`), field("LKR amount", input("amount", action === "approved" ? f.requested : "", { required: true, inputmode: "decimal" })), action !== "approved" && sourceField()], async data => command("finance_action", { finance_id: f.id, state: action, amount: data.get("amount"), ...(action !== "approved" ? { source_id: data.get("source_id") } : {}) }), `Record ${action}`);
  }
  function financePanel() {
    return h("div", { class: "card stack" }, h("div", { class: "row between" }, h("h2", {}, "Finance evidence register"), canAct("contributor", "agency_supervisor", "coordinator") && btn("Request funding", financeRequestDialog, "primary")), notice("Track requests, approvals and supporting evidence separately. Payments and procurement are not connected. Administrator access does not grant financial approval authority."), !c().finance.length && empty("No funding requests recorded yet. All bodies share this register; financial actions require the designated role."), c().finance.map(f => h("article", { class: "record", id: f.id }, h("div", { class: "row between" }, h("h3", {}, f.purpose), pill(f.status)), h("p", {}, `${orgName(f.organisation)} · ${f.head} · ${f.scheme_id}`), h("div", { class: "table-wrap" }, h("table", {}, h("thead", {}, h("tr", {}, ["Requested", "Approved", "Committed", "Released", "Spent"].map(t => h("th", {}, t)))), h("tbody", {}, h("tr", {}, ["requested", "approved", "committed", "released", "spent"].map(k => h("td", {}, f[k] == null ? "Unknown" : `LKR ${money(f[k])}`)))))), h("div", { class: "row" }, canAct("coordinator") && f.status === "requested" && btn("Recommend", () => command("finance_action", { finance_id: f.id, state: "recommended" })), canAct("finance_approver") && c().schemes.find(s => s.id === f.scheme_id)?.owner === state.me.organisation && f.status === "recommended" && btn("Review approval amount", () => financeActionDialog(f, "approved")), f.status === "approved" && f.approved && canAct("finance_approver") && c().schemes.find(s => s.id === f.scheme_id)?.owner === state.me.organisation && btn("Record release evidence", () => financeActionDialog(f, "released")), f.status === "approved" && f.approved && canAct("agency_supervisor") && f.organisation === state.me.organisation && ["committed", "spent"].map(s => btn(`Record ${s} evidence`, () => financeActionDialog(f, s)))), f.evidence.length > 0 && h("details", {}, h("summary", {}, "Financial evidence trail"), f.evidence.map(e => h("p", {}, `${e.state}: LKR ${money(e.amount)} · ${sourceName(e.source_id)} · ${dateTime(e.at)}`))))));
  }
  async function documentDialog() {
    let lists; try { lists = await commsLists(); } catch { lists = { templates: [], media: [], population: [] }; }
    const homeId = state.areas?.index.districts.find(x => x.name === c().district)?.id || `district-${c().district.toLowerCase().replaceAll(" ", "-")}`;
    dialog("Prepare a document", () => {
      const kind = select("kind", [["sms", "SMS"], ["email", "Email"], ["press_release", "Press / media release"], ["situation", "Situation report"], ["handover", "Shift handover"]], "sms");
      const language = select("language", Object.entries(LANG_NAMES), "en"), template = select("template_id", []);
      const details = h("textarea", { name: "details", rows: "3", maxlength: "1000", placeholder: "For example: Water is over the Kotmale road. Go to the temple hall." });
      const subject = input("subject", "", { maxlength: "200" }), text = h("textarea", { name: "text", rows: "6", maxlength: "6000" });
      const length = h("small", { class: "muted", role: "status" });
      const subjectField = field("Subject", subject);
      const messageBox = h("div", { class: "stack" }, field("Language", language), field("Template", template), field("Details to put in", details, "Goes where the template says {details}."), subjectField,
        field("Message", text, "Edit freely. The reviewer approves exactly this text."), length);
      const reportBox = h("div", { class: "stack" }, field("Audience / receiving team", input("audience", "Incident participants")),
        notice("Generated from the reviewed reports at this moment. Downloading or approving does not send it."));
      let edited = false;
      const count = () => { length.textContent = kind.value === "sms" ? smsLength(text.value) : `${text.value.length.toLocaleString("en-GB")} characters`; };
      const fill = () => {
        const t = lists.templates.find(x => x.id === template.value);
        if (edited) return;
        text.value = t ? fillTemplate(t.body, details.value) : details.value.trim(); subject.value = t ? fillTemplate(t.subject, details.value) : ""; count();
      };
      const sync = () => {
        const message = MESSAGE_KINDS.includes(kind.value), options = lists.templates.filter(t => t.channel === kind.value && t.language === language.value);
        messageBox.hidden = !message; reportBox.hidden = message; subjectField.hidden = kind.value === "sms";
        const submit = messageBox.closest("form")?.querySelector("button[type=submit]");
        if (submit) submit.textContent = message ? "Prepare" : "Generate snapshot";
        template.replaceChildren(...[...options.map(t => [t.id, t.name]), ["", "No template: write it yourself"]].map(([v, label]) => h("option", { value: v }, label)));
        template.value = options[0]?.id || ""; edited = false; fill();
      };
      kind.addEventListener("change", sync); language.addEventListener("change", sync);
      template.addEventListener("change", () => { edited = false; fill(); });
      details.addEventListener("input", fill);
      text.addEventListener("input", () => { edited = true; count(); });
      queueMicrotask(sync);
      return [field("Document type", kind), messageBox, reportBox];
    }, async data => {
      const kind = data.get("kind"), language = data.get("language");
      if (!MESSAGE_KINDS.includes(kind)) return command("document", { kind, audience: data.get("audience"), language: "en" });
      return command("document", { kind, language, template_id: data.get("template_id") || null, details: data.get("details"), text: data.get("text"), subject: kind === "sms" ? null : data.get("subject") || null,
        ...(kind === "sms" ? { areas: [homeId] } : { outlets: lists.media.filter(m => [language, "all"].includes(m.language)).map(m => m.id) }) });
    }, "Prepare");
  }
  function messageRecord(d) {
    const [label, tone] = MESSAGE_STATE[d.status] || [d.status.replaceAll("_", " "), ""], ours = d.issuer === state.me.organisation;
    return h("article", { class: "record message-record", id: d.id },
      h("div", { class: "row between" }, h("h3", {}, d.subject ? `${MESSAGE_KIND[d.kind]}: ${d.subject}` : MESSAGE_KIND[d.kind]), pill(label, tone)),
      h("p", { class: "message-text" }, d.text),
      h("small", { class: "muted" }, [d.kind === "sms" && smsLength(d.text), LANG_NAMES[d.language], d.template_name && `from “${d.template_name}”`, `${orgName(d.issuer)} · prepared ${dateTime(d.cutoff)}`].filter(Boolean).join(" · ")),
      d.status === "sent" && h("p", {}, h("strong", {}, "Sent "), `${dateTime(d.sent_at)} to ${sentToLong(d)}.`, d.kind !== "email" && [" ", h("a", { href: "/public", target: "_blank", rel: "noopener" }, "Shown on the public status page")]),
      h("div", { class: "row" },
        canAct("communications_reviewer") && ours && d.status === "draft" && btn("Approve", () => command("approve_document", { document_id: d.id, hash: d.hash }), "primary"),
        canAct("coordinator", "communications_reviewer") && ours && d.status === "approved_exercise" && btn("Send…", () => sendDialog(d), "primary"),
        btn("Download", () => download(`/incidents/${state.doc.id}/documents/${d.id}`, `${d.kind}.txt`), "quiet")),
      h("small", { class: "muted" }, ({ draft: "Waiting for a communications reviewer to approve this exact text.", approved_exercise: "Approved. Choose who receives it, then send.", sent: "Recorded as sent. No SMS gateway or mail server is connected, so nothing left the portal.", retracted: "Retracted." })[d.status] || ""));
  }
  // Official letters between organisations: issued by the person who applies them, shared for acknowledgement.
  function letterRecord(d) {
    const ours = d.issuer === state.me.organisation;
    const shares = (d.shared || []).map(x => {
      const acks = state.doc.sources.find(s => s.id === x.source_id)?.acknowledgements || [];
      return h("li", {}, `${dateTime(x.at)} · `, acks.map(a => h("span", { class: "ack-chip " + (a.acknowledged_at ? "done" : "waiting") }, `${orgShort(a.organisation)} ${a.acknowledged_at ? "✓" : "· waiting"}`, h("span", { class: "sr-only" }, a.acknowledged_at ? " acknowledged" : " not acknowledged yet"))));
    });
    return h("article", { class: "record letter-record", id: d.id },
      h("div", { class: "row between" }, h("h3", {}, `Letter: ${d.subject}`), pill("Issued", "green")),
      h("small", { class: "muted" }, `${d.reference} · ${orgName(d.issuer)} to ${d.to.map(orgName).join("; ")} · issued ${dateTime(d.issued_at)} by ${d.signed_by.name}`),
      h("p", { class: "message-text" }, d.text),
      shares.length ? h("div", { class: "letter-shares" }, h("strong", {}, "Shared"), h("ul", {}, shares))
        : h("small", { class: "muted" }, "Not shared yet. Sharing puts it in the recipients’ Reports and asks each to acknowledge it."),
      h("div", { class: "row" }, btn("Open letter", () => letterView(d), "quiet"), ours && acting() && btn("Share…", () => shareLetterDialog(d), "primary"),
        btn("Download", () => download(`/incidents/${state.doc.id}/documents/${d.id}`, `letter-${d.reference.replaceAll("/", "-")}.txt`), "quiet")));
  }
  function letterView(d) {
    dialog(`Letter ${d.reference}`, () => [h("div", { class: "letter-sheet" }, h("pre", { class: "letter-text" }, d.body)),
      h("small", { class: "muted" }, "Issued in the coordination portal. Exercise record; nothing was posted or emailed.")], async () => { window.print(); }, "Print");
  }
  function shareLetterDialog(d) {
    const done = new Set((d.shared || []).flatMap(x => x.recipients)), others = c().participants.filter(o => o !== state.me.organisation);
    dialog(`Share letter ${d.reference}`, () => [notice("The letter goes into each organisation’s Reports and each is asked to acknowledge it. A letter is your organisation’s statement, not reviewed evidence."),
      field("Organisations", checkList("recipients", others.map(o => [o, orgName(o) + (done.has(o) ? " (already shared)" : "")]), d.to.filter(o => !done.has(o)))),
      field("Message", h("textarea", { name: "note", maxlength: 1000 }, `Letter ${d.reference}: please acknowledge receipt.`))],
      async data => { if (!data.getAll("recipients").length) throw new Error("Choose at least one organisation."); await command("share_document", { document_id: d.id, recipients: data.getAll("recipients"), note: data.get("note") }); }, "Share letter");
  }
  async function letterDialog() {
    const templates = (await commsLists()).templates.filter(t => t.channel === "letter");
    const others = c().participants.filter(o => o !== state.me.organisation);
    dialog("Write a letter", () => [notice("Issued from your organisation when you save it. Share it afterwards to ask others to acknowledge."),
      field("To", checkList("to", others.map(o => [o, orgName(o)]))),
      templates.length > 1 && field("Template", select("template_id", templates.map(t => [t.id, t.name]))),
      field("Subject", input("subject", "", { required: true, maxlength: 200 })),
      field("Letter text", h("textarea", { name: "text", rows: 10, required: true, maxlength: 6000 }), "The letterhead, reference, date and signature come from the template.")],
      async data => { if (!data.getAll("to").length) throw new Error("Choose at least one organisation."); await command("document", { kind: "letter", to: data.getAll("to"), subject: data.get("subject"), text: data.get("text"), ...(data.get("template_id") ? { template_id: data.get("template_id") } : {}) }); }, "Issue letter");
  }
  function documentsPanel() {
    return h("div", { class: "card stack" }, h("div", { class: "row between" }, h("h2", {}, "Documents & communications"), h("div", { class: "row" }, acting() && !canAct("partner") && btn("Write letter", letterDialog, "quiet"), canAct("coordinator", "communications_reviewer") && btn("Prepare document", documentDialog, "primary"))), !c().documents.length && empty("Write a letter to other organisations, a public SMS, email or press release from a template, or generate a situation report or handover after reviewing the reports."), c().documents.toReversed().map(d => d.kind === "letter" ? letterRecord(d) : MESSAGE_KINDS.includes(d.kind) && d.text != null ? messageRecord(d) : h("article", { class: "record", id: d.id }, h("div", { class: "row between" }, h("h3", {}, d.kind.replaceAll("_", " ")), pill(({ approved_exercise: "Approved draft", handover_accepted: "Handover received", draft: "Draft", retracted: "Retracted" })[d.status] || d.status.replaceAll("_", " "))), h("p", {}, `${d.audience} · ${orgName(d.issuer)} · cutoff ${dateTime(d.cutoff)}`), h("details", {}, h("summary", {}, "Review exact document"), h("pre", { class: "source-text" }, d.body), h("small", {}, `SHA256 ${d.hash}`)), h("div", { class: "row" }, btn("Download document", () => download(`/incidents/${state.doc.id}/documents/${d.id}`, `${d.kind}-exercise.txt`), "quiet"), canAct("communications_reviewer") && d.status === "draft" && d.incident_version === state.doc.version && btn("Approve exact snapshot", () => command("approve_document", { document_id: d.id, hash: d.hash }), "primary"), d.kind === "handover" && d.status === "approved_exercise" && d.prepared_by !== state.me.id && canAct("coordinator", "agency_supervisor") && btn("Accept handover", () => command("accept_handover", { document_id: d.id, hash: d.hash }))), h("small", {}, "Delivery: not sent"))));
  }

  function timelinePanel() {
    const incidentId = state.doc.id, version = state.doc.version, actor = state.me.id;
    const key = `${incidentId}:${version}:${storyFocus}`;
    const panel = h("section", { class: "card stack story-panel", "aria-label": "Incident progression" }, h("div", { class: "row between" }, h("h2", {}, "Incident progression")), h("small", { class: "muted" }, "Reports → requests → receiving teams → reviewed outcomes"));
    const chat = c().chat, turn = chat.turns.filter(t => t.purpose === "history").at(-1);
    const message = turn && chat.messages.find(m => m.turn_id === turn.id && m.role === "assistant");
    panel.firstElementChild.append(btn(message ? "Refresh AI summary" : "Summarise progression", () => sendChat("Summarise how this incident progressed from reports to requests, receiving-team actions and reviewed outcomes. Include funding, handover, unsent communications and remaining uncertainty. Read only.", "history"), "quiet", { disabled: state.busy || turn?.status === "running" && Date.now() - Date.parse(turn.at) < 120000 }));
    const summary = h("section", { class: "story-summary", "aria-label": "AI progression summary", hidden: !message && turn?.status !== "running" },
      turn?.status === "running" ? h("p", { role: "status" }, "Reading the saved progression…") : message ? h("details", {}, h("summary", {}, `Read ${turn.runtime === "adk-offline" ? "recorded" : "AI"} summary · snapshot version ${turn.snapshot_version}${turn.snapshot_version !== version ? " · update available" : ""}`), h("p", { class: "story-narrative" }, readable(message.text)), h("small", {}, turn.status !== "completed" ? "Summary could not finish. Retry when ready." : `${dateTime(message.at)} · Read-only summary; review the linked records.`)) : null);
    const content = h("div", { class: "stack", "aria-busy": "true" }, h("p", { role: "status" }, "Loading the saved workflow…"));
    panel.append(content);
    const show = story => {
      if (!panel.isConnected || state.doc?.id !== incidentId || state.doc.version !== version || state.me.id !== actor) return;
      const choices = [["", "All workstreams"], ...story.requests.map(r => [r.id, r.title])];
      const focus = select("story_request", choices, storyFocus); focus.id = "story-request";
      focus.addEventListener("change", () => { storyFocus = focus.value; storyLimit = 20; renderData(); });
      const flow = h("lm-incident-flow", { "onopen-story-record": e => openStoryRecord(e.detail.id, e.detail.tab) }); flow.story = story;
      const group = select("story_group", [["all", "All milestones"], ["decisions", "Decisions & handover"], ["requests", "Requests & assignments"], ["outcomes", "Reviewed outcomes & receipts"], ["finance", "Funding"], ["communications", "Communications"], ["reports", "Contributions"]], storyGroup); group.id = "story-group";
      group.addEventListener("change", () => { storyGroup = group.value; storyLimit = 20; renderData(); });
      const entries = story.milestones.filter(m => storyGroup === "all" || m.group === storyGroup);
      const visible = entries.slice(-storyLimit);
      const timeline = h("ol", { class: "story-timeline", "aria-label": "Operational milestones" }, visible.map(m => h("li", { class: "story-milestone" }, h("span", { class: "milestone-dot", "aria-hidden": "true" }), h("div", {}, h("small", {}, m.at ? `${dateTime(m.at)} · ${m.time_label}` : "Time not recorded"), h("h3", {}, m.title), m.organisation_name && h("p", { class: "muted" }, m.organisation_name), m.detail && h("p", {}, m.detail), m.record_id && btn("Open record", () => openStoryRecord(m.record_id, m.tab), "quiet")))));
      content.replaceChildren(field("Workflow focus", focus), summary, flow, h("div", { class: "row between" }, h("h3", {}, "Key milestones"), field("Milestone type", group)), h("small", { role: "status" }, `${visible.length} of ${entries.length} milestones · oldest to newest${entries.length > visible.length ? " · showing most recent" : ""}`), ...(entries.length > visible.length ? [btn("Show earlier milestones", () => { storyLimit += 20; renderData(); }, "quiet")] : []), entries.length ? timeline : empty("No milestones match this view."));
      content.removeAttribute("aria-busy");
    };
    // The graph module is lazy; ordinary chat/map navigation pays no layout cost.
    const load = async () => {
      const cached = storyCache.get(key);
      const [story] = await Promise.all([cached || api(`/incidents/${incidentId}/story${storyFocus ? "?request_id=" + encodeURIComponent(storyFocus) : ""}`), import('/static/vendor/components/lastmile-story.js')]);
      if (storyCache.size > 24) storyCache.clear(); storyCache.set(key, story); show(story);
    };
    queueMicrotask(() => load().catch(error => { if (panel.isConnected) { content.removeAttribute("aria-busy"); content.replaceChildren(h("p", { role: "alert" }, "Workflow could not load. " + error.message), btn("Retry workflow", () => renderData())); } }));
    return panel;
  }
  function openStoryRecord(recordId, tab) {
    location.hash = new URLSearchParams({ incident: state.doc.id, tab: tab || "sources", record: recordId, pane: "data" });
  }
  function accessHistoryDialog() {
    const rows = (c().role_history || []).slice(-30).toReversed();
    dialog("Workspace access history", () => [h("p", {}, "Role selections are access metadata. They do not change operational decisions."), ...rows.map(r => h("p", {}, `${dateTime(r.at)} · ${orgName(r.organisation)} · ${r.role.replaceAll("_", " ")}`))], async () => {}, "Close");
  }
  function reasonField(optional = false) { return field("Reason / receiving note", h("textarea", { name: "reason", required: !optional, minlength: 8, maxlength: 1000 })); }
  function lifecycleDialog() {
    const closed = state.doc.status === "closed";
    dialog(closed ? "Reopen this incident" : "Incident lifecycle", () => [notice(`Current state: ${state.doc.status} · ${c().phase || "response"}. Closing requires cleared blockers and a current approved, received handover.`), !closed && field("Action", select("action", [["phase", "Change phase"], ["close", "Close incident"]])), !closed && field("Phase", select("phase", ["monitoring", "response", "recovery"], c().phase)), reasonField()],
      async data => { const action = closed ? "reopen" : data.get("action"); await command(action, { ...(action === "phase" ? { phase: data.get("phase") } : {}), reason: data.get("reason") }); }, closed ? "Reopen with reason" : "Apply lifecycle change");
  }
  function participantDialog() {
    dialog("Include another organisation", () => [notice("This adds incident participation. It does not create an account or grant new roles; an administrator handles invitations separately."), field("Organisation", select("organisation", activeOrgs().filter(o => !c().participants.includes(o.id)).map(o => [o.id, o.name])))], async data => command("participant", { organisation: data.get("organisation") }), "Include organisation");
  }
  function replayClockDialog() {
    dialog("Historical replay clock", () => [notice("Start a replay only before loading sources/resources. Then advance monotonically. This tests original historical dates; it does not claim the app managed that event. Account expiry and audit timestamps stay real."), field("As of (ISO time with timezone)", input("as_of", c().replay_at || "2025-11-28T12:00:00+00:00", { required: true })), reasonField()], async data => command("replay_clock", { as_of: data.get("as_of"), reason: data.get("reason") }), "Set labelled replay time");
  }
  function schemeDialog() {
    dialog("Register a funding scheme", () => [notice("Your finance role can register a source-backed scheme for its organisation. An explicit pool ID links its budget across incidents. Policy evidence must state both amounts; government financial integration is not connected."), field("Scheme ID", input("id", "fictional-pilot-v1", { required: true })), field("Scheme name", input("name", "Pilot relief fund", { required: true })), field("Budget LKR", input("budget", "10000.00", { required: true })), field("Approval limit LKR", input("limit", "5000.00", { required: true })), field("Expenditure head", input("head", "EXERCISE-POOL", { required: true })), field("Owner's shared pool ID (optional)", input("pool_key", "")), sourceField()], async data => command("finance_scheme", { id: data.get("id"), name: data.get("name"), budget: data.get("budget"), limit: data.get("limit"), head: data.get("head"), pool_key: data.get("pool_key") || null, source_id: data.get("source_id") }), "Register policy");
  }
  function claimDialog() {
    dialog("Record a source-backed area update", () => [sourceField(), field("Topic", select("topic", ["weather", "flood", "landslide", "road", "power", "water", "health", "displacement", "other"])), field("Location as written in source", input("location", "", { required: true })), field("Field / indicator", input("field", "", { required: true, placeholder: "e.g. water service status" })), field("Literal reported value", input("value", "", { required: true })), field("Unit", input("unit", "status", { required: true }), "Retain the source unit. Do not convert families to people."), field("Exact source quote", h("textarea", { name: "quote", required: true })), field("Valid until (optional ISO time)", input("valid_until", ""))],
      async data => command("claim", { topic: data.get("topic"), location: data.get("location"), field: data.get("field"), value: data.get("value"), unit: data.get("unit"), source_id: data.get("source_id"), quote: data.get("quote"), valid_until: data.get("valid_until") || null }), "Record attributed update");
  }
  function resolveClaimDialog(claim) {
    const competitors = (c().claims || []).filter(x => x.id !== claim.id && ["recorded", "current", "disputed"].includes(x.status) && ["topic", "location", "field", "unit"].every(k => x[k].toLowerCase() === claim[k].toLowerCase()));
    dialog("Resolve conflicting reports", () => [notice(`Selected: ${claim.location} · ${claim.field}: ${claim.value} ${claim.unit} · ${sourceName(claim.source_id)}. Original reports will remain in history.`), field("Superseded competing claims", checkList("supersedes", competitors.map(x => [x.id, `${x.value} ${x.unit} · ${sourceName(x.source_id)}`]), competitors.map(x => x.id))), reasonField()], async data => command("resolve_claim", { claim_id: claim.id, supersedes: data.getAll("supersedes"), reason: data.get("reason") }), "Record reviewed resolution");
  }
  function requestOperationsDialog() {
    dialog("Receipts, clarification and recovery ownership", () => [field("Request", select("request_id", c().requests.map(r => [r.id, `${r.title} · ${r.status} · remaining ${r.remaining ?? "Unknown"} ${r.unit}`]))), field("Action", select("action", [["receipt", "Confirm partial supply receipt"], ["clarify", "Clarify quantity from accepted evidence"], ["withdraw", "Withdraw remaining request"], ["transfer", "Offer recovery responsibility transfer"], ["accept_transfer", "Accept receiving responsibility"]])), field("Quantity (receipt or clarification)", input("quantity", "", { type: "number", min: 1, step: 1 })), field("Unit (receipt)", input("unit", "packs")), sourceField(), field("Exact receipt quote", input("quote", "")), field("Receiving organisation (transfer)", select("recipient", c().participants.map(o => [o, orgName(o)]))), field("Recovery follow-up reference", input("reference", "")), reasonField(true)],
      async data => { const request_id = data.get("request_id"), action = data.get("action"), reason = data.get("reason"); if (action === "receipt") return command("receipt", { request_id, quantity: Number(data.get("quantity")), unit: data.get("unit"), source_id: data.get("source_id"), quote: data.get("quote") }); if (action === "accept_transfer") return command("accept_transfer", { request_id, reason }); return command("request_update", { request_id, reason, state: { clarify: "open", withdraw: "withdrawn", transfer: "transfer_pending" }[action], ...(action === "clarify" ? { quantity: Number(data.get("quantity")), source_id: data.get("source_id") } : action === "transfer" ? { recipient: data.get("recipient"), reference: data.get("reference") } : {}) }); }, "Record reviewed action");
  }
  function assignmentDetailsDialog() {
    dialog("Assignment dependencies and deadline", () => [field("Assignment", select("offer_id", c().offers.map(o => [o.id, `${o.quantity} → ${orgName(o.recipient)} · ${o.status} · ${o.id}`]))), field("Required completed assignments", checkList("depends_on", c().offers.map(o => [o.id, `${o.id} · ${orgName(o.recipient)} · ${o.status}`]))), field("Due at (optional ISO time)", input("due_at", "")), field("Action", select("action", [["assignment_details", "Set before acceptance"], ["escalate", "Record overdue escalation"]])), reasonField()], async data => command(data.get("action"), { offer_id: data.get("offer_id"), reason: data.get("reason"), ...(data.get("action") === "assignment_details" ? { due_at: data.get("due_at") || null, depends_on: data.getAll("depends_on") } : {}) }), "Record coordination action");
  }
  function financeOperationsDialog() {
    dialog("Financial review and reconciliation", () => [notice("Reconciliation requires supported commitments, releases and spending. A positive cash balance needs a verified return record."), field("Funding record", select("finance_id", c().finance.map(f => [f.id, `${f.purpose} · ${f.status}`]))), field("Action", select("action", [["finance_reconcile", "Review final reconciliation"], ["returned", "Return pending request"], ["rejected", "Reject request"], ["cancelled", "Cancel unused approval"]])), sourceField(), field("Remaining balance (LKR)", input("balance", "0.00", { inputmode: "decimal" })), field("Exact reconciliation quote", input("quote", "")), reasonField(true)], async data => command(data.get("action") === "finance_reconcile" ? "finance_reconcile" : "finance_decision", { finance_id: data.get("finance_id"), ...(data.get("action") === "finance_reconcile" ? { source_id: data.get("source_id"), quote: data.get("quote"), balance: data.get("balance") } : { decision: data.get("action"), reason: data.get("reason") }) }), "Record financial review");
  }
  function reviseDocumentDialog(item) {
    dialog("Create a new revision", () => [notice("The approved original stays immutable. The new draft needs its own exact review. All selected source IDs must appear in the text. This does not send anything."), field("Document wording", h("textarea", { name: "body", required: true, maxlength: 12000 }, item.body)), field("Supporting accepted sources", checkList("source_ids", acceptedOptions(), state.doc.sources.filter(s => s.status === "accepted" && item.body.includes(s.id)).map(s => s.id))), reasonField()], async data => command("revise_document", { document_id: item.id, body: data.get("body"), source_ids: data.getAll("source_ids"), reason: data.get("reason") }), "Create reviewed draft proposal");
  }
  function operationPanel(tab) {
    const active = state.doc.status !== "closed";
    if (tab === "situation") return h("div", { class: "card stack operation-panel" }, h("div", { class: "row between" }, h("h2", {}, "Incident coordination"), pill(active ? `${state.doc.status} · ${c().phase || "response"}` : "Closed")), c().replay_at && notice(`Historical exercise clock: ${c().replay_at}. Original dates are preserved; account/audit time stays real.`), h("div", { class: "row" }, has("coordinator") && btn(active ? "Manage lifecycle" : "Reopen incident", lifecycleDialog), active && has("coordinator") && activeOrgs().some(o => !c().participants.includes(o.id)) && btn("Include organisation", participantDialog), active && has("coordinator") && btn("Historical replay clock", replayClockDialog), active && has("coordinator", "contributor", "agency_supervisor") && btn("Record area update", claimDialog)), h("details", {}, h("summary", {}, `${(c().closure_blockers || []).length} outstanding closure checks`), (c().closure_blockers || []).length ? c().closure_blockers.map(b => h("p", {}, `${b.kind} · ${b.id}: ${b.note}`)) : h("p", {}, "Work checks are clear. Closing still requires recovery phase and a current approved, received handover.")), (c().claims || []).map(x => h("article", { class: "record" }, h("div", { class: "row between" }, h("h3", {}, `${x.location} · ${x.field}`), pill(`${x.status}${x.expired ? " · expired" : ""}`, x.status === "disputed" ? "warn" : "")), h("p", {}, `${x.value} ${x.unit} · ${orgName(x.organisation)}`), h("small", {}, `${x.quote} · ${sourceName(x.source_id)} · ${dateTime(x.observed_at)}`), active && has("coordinator") && x.status === "disputed" && btn("Resolve with evidence", () => resolveClaimDialog(x)))));
    if (tab === "requests") return h("div", { class: "card stack operation-panel" }, h("h2", {}, "Fulfilment and follow-up"), active && has("coordinator", "agency_supervisor") && h("div", { class: "row" }, btn("Record receipt or request update", requestOperationsDialog), has("coordinator") && btn("Set dependencies or escalate", assignmentDetailsDialog)), c().requests.map(r => h("p", {}, `${r.title}: ${r.status} · fulfilled ${r.fulfilled ?? 0} · remaining ${r.remaining ?? "Unknown"} ${r.unit}${r.transfer_recipient ? ` · receiving ${orgName(r.transfer_recipient)} · ${r.transfer_reference}` : ""}`)), c().offers.filter(o => o.overdue || o.blocked_by?.length).map(o => notice(`${o.id}: ${o.overdue ? "Overdue. " : ""}${o.blocked_by?.length ? "Waiting for reviewed completion of " + o.blocked_by.join(", ") : ""}`)), (c().receipts || []).map(r => h("small", {}, `Receipt ${r.quantity} ${r.unit} · ${r.source_id}`)));
    if (tab === "finance") return h("div", { class: "card stack operation-panel" }, h("h2", {}, "Financial decision trail"), active && has("finance_approver") && btn("Register funding scheme", schemeDialog), active && has("coordinator", "finance_approver") && btn("Return, reject or reconcile funding", financeOperationsDialog), c().finance.filter(f => f.reconciliation || f.decision_reason).map(f => h("p", {}, `${f.purpose}: ${f.status} · ${f.decision_reason || `balance LKR ${money(f.reconciliation.balance)} · ${sourceName(f.reconciliation.source_id)}`}`)));
    if (tab === "documents") return !c().documents.some(d => d.kind !== "handover" && d.text == null) ? null : h("div", { class: "card stack operation-panel" }, h("h2", {}, "Revisions and corrections"), notice("Changes need a separate exact review. AI does not certify facts or translation accuracy."), c().documents.filter(d => d.kind !== "handover" && d.text == null).map(d => h("div", { class: "row between record" }, h("span", {}, `${d.kind} · ${d.status} · ${d.id}`), active && has("coordinator", "communications_reviewer") && btn("Create new revision", () => reviseDocumentDialog(d)), active && has("communications_reviewer") && d.status === "approved_exercise" && btn("Retract draft", () => dialog("Record retraction", () => [reasonField()], async data => command("retract_document", { document_id: d.id, reason: data.get("reason") }), "Record retraction"), "quiet danger"))));
    return null;
  }
  async function boot() {
    try {
      const setup = await api("/setup"); state.demoMode = setup.demo;
      if (setup.demo && !state.demoToken) { state.me = null; state.catalogue = setup; return route(); }
      state.catalogue = await api("/me"); state.me = state.catalogue.user; await route();
    }
    catch (error) { if (error.status === 401 || error.status === 403 && state.demoToken) { state.demoToken = ""; forgetRole(); state.me = null; await authScreen(); } else { shell(h("div", { class: "error" }, error.message, btn("Try again", boot))); } }
  }
  // A link followed while the page is still starting waits for the saved role, so it never asks again.
  window.addEventListener("hashchange", () => { booted.finally(() => { if (state.me || state.demoMode) route().catch(e => toast(e.message)); }); });
  window.addEventListener("pagehide", () => clearInterval(pollTimer));
  window.addEventListener("beforeunload", event => { if ([...drafts.values()].some(text => text.trim())) { event.preventDefault(); event.returnValue = ""; } });
  $(".skip").addEventListener("click", event => { event.preventDefault(); $("#main")?.focus(); $("#main")?.scrollIntoView({ block: "start" }); });
  document.addEventListener("visibilitychange", () => { if (!document.hidden && state.me && state.doc) refresh(false).catch(() => {}); });
  setInterval(() => { if (state.me && !document.hidden) loadNotes().catch(() => {}); }, 30000);
  document.addEventListener("visibilitychange", () => { if (!document.hidden && state.me && (!notes.at || Date.now() - Date.parse(notes.at) > 10000)) loadNotes().catch(() => {}); });
  document.body.append(h("div", { id: "notes-live", class: "sr-only", role: "status", "aria-live": "polite" }));
  const booted = boot();
})();
