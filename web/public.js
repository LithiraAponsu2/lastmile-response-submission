/* LastMile Alert public danger map. No frameworks; every piece of data is rendered with textContent. */
(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const params = new URLSearchParams(location.search);
  const ss = {
    get(k) { try { return sessionStorage.getItem(k) || ""; } catch { return ""; } },
    set(k, v) { try { v ? sessionStorage.setItem(k, v) : sessionStorage.removeItem(k); } catch { /* fine */ } },
  };
  const state = { region: null, lang: params.get("lang") || ss.get("pub_lang") || "en", sit: null, mapUrl: null,
                  session: ss.get("pub_session"), me: null };

  function h(tag, props = {}, ...kids) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(props)) {
      if (v === false || v == null) continue;
      if (k === "class") el.className = v;
      else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
      else el.setAttribute(k, v === true ? "" : v);
    }
    for (const kid of kids.flat()) if (kid != null && kid !== false) el.append(typeof kid === "object" ? kid : document.createTextNode(String(kid)));
    return el;
  }
  function toast(msg, err = false) {
    const t = $("#toast"); t.textContent = msg; t.className = "toast" + (err ? " err" : ""); t.hidden = false;
    clearTimeout(toast._t); toast._t = setTimeout(() => { t.hidden = true; }, 6000);
  }
  function notice(msg) { const n = $("#notice"); n.textContent = msg; n.hidden = !msg; }
  async function api(path, opts = {}) {
    const headers = Object.assign({}, opts.headers || {});
    if (opts.body) headers["Content-Type"] = "application/json";
    if (state.session) headers["X-Session"] = state.session;
    const r = await fetch(path, Object.assign({}, opts, { headers }));
    let data = null; try { data = await r.json(); } catch { /* svg or empty */ }
    if (!r.ok) throw new Error((data && (data.detail || data.error)) || r.statusText);
    return data;
  }
  const ui = (k) => (state.region.ui[state.lang] || state.region.ui.en)[k] || k;
  const name = (a) => (a.names && (a.names[state.lang] || a.names.en)) || a.id;
  const OFFSET = "+05:30";   // the region's clock (Sri Lanka); the picker shows local time

  function atIso() {
    const v = $("#at").value;
    return v ? `${v}:00${OFFSET}` : "";
  }
  function setAt(iso) {   // "2025-11-27T22:00:00+05:30" -> "2025-11-27T22:00"
    $("#at").value = iso ? iso.slice(0, 16) : "";
  }

  // ---------------------------------------------------------------- boot
  async function boot() {
    const regions = await api("/api/public/regions");
    state.region = regions.find(region => region.id === "lk");
    if (!state.region.languages[state.lang]) state.lang = "en";
    $("#langs").replaceChildren(...Object.entries(state.region.languages).map(([code, label]) =>
      h("button", { type: "button", class: "badge lang", "aria-pressed": String(code === state.lang),
                    onclick: () => { state.lang = code; ss.set("pub_lang", code); renderAll(); } }, label)));
    if (params.get("at")) setAt(params.get("at"));
    $("#at").addEventListener("change", refresh);
    $("#now").addEventListener("click", () => { setAt(""); refresh(); });
    $("#latest").addEventListener("click", () => { setAt(state.sit.latest_sent); refresh(); });
    $("#area").addEventListener("change", showArea);
    $("#checkRef").addEventListener("click", checkRef);
    $("#subscribe").addEventListener("click", subscribe);
    $("#signin").addEventListener("click", signin);
    $("#save").addEventListener("click", save);
    $("#deleteMe").addEventListener("click", deleteMe);
    $("#subLang").addEventListener("change", (e) => { e.target.dataset.touched = "1"; });
    fillForm();
    try { $("#consentText").textContent = (await api("/api/public/consent")).text; } catch { /* optional */ }
    await handleLinks();
    await refresh();
    setInterval(() => { refresh().catch(error => notice(error.message)); }, 60000);
    if (params.get("ref")) { $("#ref").value = params.get("ref"); checkRef(); }
  }

  async function handleLinks() {   // links from emails: ?confirm= ?signin= ?unsubscribe=
    try {
      if (params.get("confirm")) {
        await api("/api/public/confirm", { method: "POST", body: JSON.stringify({ token: params.get("confirm") }) });
        notice("Subscription confirmed. You will get warning emails for your areas.");
      } else if (params.get("signin")) {
        const r = await api("/api/public/session", { method: "POST", body: JSON.stringify({ token: params.get("signin") }) });
        state.session = r.session; ss.set("pub_session", r.session);
        notice("Signed in. You can change or delete your subscription below.");
      } else if (params.get("unsubscribe")) {
        await api("/api/public/unsubscribe", { method: "POST", body: JSON.stringify({ token: params.get("unsubscribe") }) });
        notice("Unsubscribed. Your email address has been deleted.");
      }
    } catch (e) { notice(e.message); }
    if (params.get("confirm") || params.get("signin") || params.get("unsubscribe")) history.replaceState(null, "", "/alerts");
    if (state.session) {
      try { state.me = await api("/api/public/me"); } catch { state.session = ""; ss.set("pub_session", ""); }
    }
    renderMe();
  }

  // ---------------------------------------------------------------- data
  async function refresh() {
    const at = atIso();
    state.sit = await api(`/api/public/situation?region=${encodeURIComponent(state.region.id)}` + (at ? `&at=${encodeURIComponent(at)}` : ""));
    renderAll();
  }
  async function renderAll() {
    document.documentElement.lang = state.lang;
    $("#title").textContent = `${ui("map_title")} — ${state.region.name}`;
    $("#findTitle").textContent = ui("find_area");
    $("#inForceTitle").textContent = ui("in_force");
    for (const b of document.querySelectorAll("#langs .lang")) b.setAttribute("aria-pressed", String(b.textContent === state.region.languages[state.lang]));
    const ex = $("#exerciseBanner"); ex.hidden = !state.sit.exercise; ex.textContent = ui("exercise_banner");
    $("#latest").hidden = !state.sit.latest_sent;
    fillAreas();
    fillForm();
    renderMe();
    renderAlerts();
    showArea();
    await renderMap();
  }
  async function renderMap() {
    const at = atIso();
    const r = await fetch(`/api/public/map.svg?region=${encodeURIComponent(state.region.id)}&lang=${state.lang}` + (at ? `&at=${encodeURIComponent(at)}` : ""));
    if (state.mapUrl) URL.revokeObjectURL(state.mapUrl);
    state.mapUrl = URL.createObjectURL(await r.blob());
    $("#map").src = state.mapUrl;
    if (!window.L) return;
    const query = `region=${encodeURIComponent(state.region.id)}` + (at ? `&at=${encodeURIComponent(at)}` : "");
    const generation = renderMap.generation = (renderMap.generation || 0) + 1;
    try {
      const [districts, warnings] = await Promise.all([
        state.districts ? Promise.resolve(state.districts) : api("/static/data/sri-lanka-districts.geojson"),
        api(`/api/public/map.geojson?${query}`),
      ]);
      if (generation !== renderMap.generation) return;
      state.districts = districts;
      if (!state.map) {
        state.map = L.map("public-map", { scrollWheelZoom: false, preferCanvas: true, minZoom: 6, maxZoom: 15 });
        const reference = L.geoJSON(districts, { style: { color: "#a7bacc", weight: .8, fillColor: "#fafcfe", fillOpacity: 1 },
          onEachFeature: (feature, layer) => layer.bindTooltip(h("span", {}, feature.properties.shapeName, " · reference boundary")) }).addTo(state.map);
        state.map.fitBounds(reference.getBounds(), { padding: [15, 15] });
      }
      if (state.warningLayer) state.warningLayer.remove();
      state.warningLayer = L.geoJSON(warnings, { style: feature => ({ color: feature.properties.colour, weight: 2, fillOpacity: .45,
        dashArray: feature.properties.approximate_geometry ? "5 5" : null }),
        onEachFeature: (feature, layer) => layer.bindTooltip(h("span", {}, feature.properties.name[state.lang] || feature.properties.name.en,
          " · ", feature.properties.label[state.lang] || feature.properties.label.en,
          feature.properties.approximate_geometry ? " · approximate geometry" : "", warnings.exercise ? " · Exercise" : "")) }).addTo(state.map);
      state.map.invalidateSize();
    } catch (error) { notice("Interactive geography could not load. Use the static map alternative. " + error.message); }
  }
  const levelInfo = (lv) => state.sit.levels[String(lv)];
  const areaById = (id) => state.region.areas.find((a) => a.id === id);

  function renderAlerts() {
    const ul = $("#alerts");
    if (!state.sit.alerts.length) { ul.replaceChildren(h("li", { class: "none" }, ui("no_alerts"))); return; }
    ul.replaceChildren(...state.sit.alerts.map((a) => {
      const top = Math.max(...Object.values(a.place_levels));
      const li = h("li", { class: top >= 3 ? "block" : "warn" },
        h("strong", {}, a.headline[state.lang] || a.headline.en || a.event[state.lang] || ""),
        h("div", {}, Object.entries(a.place_levels).sort((x, y) => y[1] - x[1])
          .map(([pid, lv]) => `${name(areaById(pid) || { id: pid })}: ${levelInfo(lv).label[state.lang] || levelInfo(lv).label.en}`).join(" · ")),
        h("div", { class: "hint" }, `${ui("valid_until")}: ${a.expires_text[state.lang] || a.expires || "-"} · ${ui("reference")}: ${a.reference}` + (a.status !== "Actual" ? ` · ${ui("exercise_tag")}` : "")));
      return li;
    }));
  }
  function fillAreas() {
    const sel = $("#area"), cur = sel.value;
    const districts = state.region.areas.filter((a) => !a.parent);
    sel.replaceChildren(h("option", { value: "" }, "—"), ...districts.flatMap((d) => [
      h("option", { value: d.id }, name(d)),
      ...state.region.areas.filter((a) => a.parent === d.id).map((a) => h("option", { value: a.id }, `  · ${name(a)}`))]));
    sel.value = cur;
  }
  async function showArea() {
    const id = $("#area").value, box = $("#areaResult");
    if (!id) { box.replaceChildren(); return; }
    const at = atIso();
    const r = await api(`/api/public/area?region=${encodeURIComponent(state.region.id)}&id=${encodeURIComponent(id)}` + (at ? `&at=${encodeURIComponent(at)}` : ""));
    if (!r.level) {
      const subs = Object.entries(r.sub_areas);
      box.replaceChildren(h("p", { class: "ok" }, ui("no_warning_here")),
        subs.length ? h("p", { class: "hint" }, subs.map(([pid, lv]) => `${name(areaById(pid))}: ${levelInfo(lv).label[state.lang] || levelInfo(lv).label.en}`).join(" · ")) : null);
      return;
    }
    const L = levelInfo(r.level);
    box.replaceChildren(
      h("p", { class: "levelchip", style: null }, h("span", { class: "swatch", "data-colour": L.colour }), L.label[state.lang] || L.label.en),
      h("p", {}, h("strong", {}, `${ui("what_to_do")}: `), L.advice[state.lang] || L.advice.en || ""),
      h("p", { class: "hint" }, `${ui("reference")}: ${r.references.join(", ")}`));
    for (const sw of box.querySelectorAll(".swatch")) sw.style.background = sw.dataset.colour;  // CSSOM, not inline style text
  }
  function checkRef() {
    const ref = $("#ref").value.trim().toUpperCase();
    const hit = state.sit.alerts.find((a) => a.reference === ref);
    $("#refResult").textContent = !ref ? "" : hit
      ? `Genuine: ${ref} is a warning in force (${hit.status}). ${hit.headline[state.lang] || hit.headline.en || ""}`
      : `${ref} is not among the warnings in force at this time. If you received it as a warning, treat it with care and check official channels (call 117).`;
  }

  // ---------------------------------------------------------- email form
  function fillForm() {   // re-run on every language switch; keeps what the user already chose
    const ticked = new Set(chosenAreas()), lvl = $("#minLevel").value, langSel = $("#subLang");
    const chosenLang = langSel.dataset.touched ? langSel.value : state.lang;
    langSel.replaceChildren(...Object.entries(state.region.languages).map(([c, l]) => h("option", { value: c }, l)));
    langSel.value = chosenLang;
    $("#minLevel").replaceChildren(...state.region.levels.map((l) => h("option", { value: String(l.level) }, l.label[state.lang] || l.label.en)));
    const districts = state.region.areas.filter((a) => !a.parent);
    $("#areaChecks").replaceChildren(...districts.map((d) => h("div", { class: "district" },
      h("label", { class: "check" }, h("input", { type: "checkbox", value: d.id, name: "areas" }), " ", name(d)),
      ...state.region.areas.filter((a) => a.parent === d.id).map((a) =>
        h("label", { class: "check sub" }, h("input", { type: "checkbox", value: a.id, name: "areas" }), " ", name(a))))));
    for (const i of document.querySelectorAll("#areaChecks input")) i.checked = ticked.has(i.value);
    if (lvl) $("#minLevel").value = lvl;
  }
  const chosenAreas = () => [...document.querySelectorAll("#areaChecks input:checked")].map((i) => i.value);
  function prefs() {
    return { areas: chosenAreas(), lang: $("#subLang").value, min_level: Number($("#minLevel").value), include_exercises: $("#drills").checked };
  }
  async function subscribe() {
    try {
      const r = await api("/api/public/subscribe", { method: "POST", body: JSON.stringify(Object.assign(
        { email: $("#email").value.trim(), region: state.region.id, consent: $("#consent").checked }, prefs())) });
      toast(r.detail);
    } catch (e) { toast(e.message, true); }
  }
  async function signin() {
    try {
      const r = await api("/api/public/signin", { method: "POST", body: JSON.stringify({ email: $("#signinEmail").value.trim() }) });
      toast(r.detail);
    } catch (e) { toast(e.message, true); }
  }
  function renderMe() {
    const signedIn = !!state.me;
    $("#manage").hidden = !signedIn; $("#save").hidden = !signedIn; $("#deleteMe").hidden = !signedIn;
    $("#subscribe").hidden = signedIn; $("#signinBox").hidden = signedIn;
    $("#email").disabled = signedIn;
    for (const el of [$("#consent").parentElement]) el.hidden = signedIn;
    if (!signedIn) return;
    $("#manageWho").textContent = `Signed in as ${state.me.email}. Consent recorded ${state.me.consent.at || ""}.`;
    $("#email").value = state.me.email;
    $("#subLang").value = state.me.lang; $("#minLevel").value = String(state.me.min_level); $("#drills").checked = state.me.include_exercises;
    for (const i of document.querySelectorAll("#areaChecks input")) i.checked = state.me.areas.includes(i.value);
  }
  async function save() {
    try { state.me = await api("/api/public/me", { method: "PUT", body: JSON.stringify(prefs()) }); toast("Saved."); }
    catch (e) { toast(e.message, true); }
  }
  async function deleteMe() {
    try {
      const r = await api("/api/public/me", { method: "DELETE" });
      state.me = null; state.session = ""; ss.set("pub_session", ""); renderMe(); toast(r.detail);
    } catch (e) { toast(e.message, true); }
  }

  // no inline handlers anywhere (the CSP blocks them): Enter in the form must not submit and reload the page
  document.getElementById("subForm").addEventListener("submit", (e) => e.preventDefault());
  boot().catch((e) => toast("Could not load the map: " + e.message, true));
})();
