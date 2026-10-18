# Shared interface components

The current workspace imports `interface.js` through the checked-in Lit/Web Awesome bundle and loads `select.css` directly. Keep untrusted content as text, with native form semantics wherever possible.

## Dropdown: `lm-select`

Use the same skin for every single-select field:

```html
<link rel="stylesheet" href="/static/components/select.css">
<label for="organisation">Organisation</label>
<select class="lm-select" id="organisation" name="organisation">
  <option value="dmc">DMC / Emergency Operations Centre</option>
  <option value="water">NWSDB / water provider</option>
</select>
```

`web/teams.js`'s shared `select(name, options, value)` factory applies this class to every workspace dropdown, including role, district, country, filters, resources, evidence, funding and document actions. Attach labels and hints with the existing `field()` helper. Set disabled options with `option.disabled`; read/write `.value` and use ordinary input/change events and FormData. No mirrored hidden input, replacement permissions or new dropdown JavaScript is required.

The control has a soft filled surface, subtle inset edge, rounded corners and a compact chevron. Selection in the menu uses a blue tint and checkmark. Pointer focus has a soft edge; keyboard focus retains a visible outline. Open menus have a 16px radius, shadow, spaced 44px option rows, wrapping labels and a bounded scrolling area. Disabled, invalid, reduced-motion and forced-colour states are included.

In browsers supporting `appearance: base-select`, the native picker can be styled in the browser's top layer, including over a modal. Unsupported browsers retain the styled closed control and their OS/native menu. That fallback may render its open list differently. [Chrome's platform documentation](https://developer.chrome.com/blog/a-customizable-select) explains the progressive enhancement and unchanged select interfaces. No native mobile OS sheet is used when base-select is supported. No new runtime package or CDN is introduced.

Custom properties `--select-surface`, `--select-hover`, `--select-selected` and `--select-accent` provide the visual tokens. Add a new variation only when its semantics need it; don't duplicate the picker CSS in individual dialogs.

## Incident tabs: prebuilt Web Awesome group

The incident **More** control uses `createViewMenu({ items, active, onSelect })` from the same bundle. Supply `[key, label]` pairs and the current view key. Web Awesome's dropdown and items own top-layer placement, viewport flipping, scrolling, Escape/outside dismissal and item keyboard navigation. The adapter labels the menu and adds ArrowUp/ArrowDown opening to its native trigger. Keep its popup outside ordinary clipping and use `wa-select` through the adapter rather than duplicating click handlers or menu positioning. Decorative action SVGs in `teams.js` share `.action-icon`; pair them with a visible label or an explicit accessible name.

`createDataTabs()` in `interface.js` is a thin DOM adapter around the installed Web Awesome 3.14.0 `wa-tab-group`, `wa-tab` and `wa-tab-panel`. Pass `[key, label]` pairs, the active key, an onSelect callback and renderPanel returning DOM nodes. The prebuilt component owns keyboard movement/manual activation, selected state, panel relationships and overflow controls. The adapter keeps stable `tab-*`/`panel-*` IDs, names the exposed tablist and lets the existing app retain URL/focus state after data refresh.

Keep the containing data area block-sized and shrinkable. The incident shell uses a `minmax(0,1fr)` column, avoiding a mobile min-content overflow. Tabs are 44px high in a compact header; neither the tab header nor its buttons should be an auto-stretched grid row. Data content and the sticky chat's height are independent. Render the selected panel before initializing Leaflet or moving focus to a deep-linked record.

System icons are generated locally from the pinned Web Awesome package by `build-ui.mjs`, preserving their embedded Font Awesome attribution. The system icon resolver uses these same-origin SVGs so the prebuilt scroll arrows work under the existing same-origin CSP. `npm run check:ui` checks the module, package licences and generated icons. No UI CDN or CSP relaxation is needed.

## Assistant activity and chevrons

`lm-work-activity` uses the prebuilt `wa-details` and `wa-spinner` for disclosure and running feedback. Its summary is a compact neutral row with state and recorded operation count; the expanded body is a flat operations list with text statuses. It accepts only the existing public turn/step metadata. The app owns expanded-state persistence and completion auto-collapse; the component never applies proposals or fabricates progress. Interrupted/failed turns do not keep a stale Running row visible. Reduced motion swaps the spinner for a static SVG.

Disclosure chevrons use a centred 20px line SVG with the same path as `chevron-down.svg`. The shared dropdown CSS and sidebar More tools reuse that local asset, rotating it on open. Keep the prebuilt/native hit targets and keyboard behaviour; do not substitute Unicode v/caret glyphs or duplicate a second toggle button.

## Lit components

`lm-incident-flow` lives in the separately built/lazy-loaded `story.js` module. Set `.story` to the authenticated story projection. Dagre owns graph layout; native buttons own focus/click and the prebuilt Web Awesome tooltip owns hover/focus context. The tooltip targets a non-button wrapper to preserve the native button name. Dynamic positions use CSSOM property updates (`ref`), not initial inline style attributes, preserving CSP. `open-story-record` emits an existing record ID/tab, never an action or an invented link. The app supplies focus filters and a milestone list as a text alternative. Hover details are also available by focus/tap in the persistent inspector. Bundle and upstream licence checks cover both entries.

`lm-icon-button` supplies labelled 44px SVG buttons; `lm-work-activity` presents actual saved public steps with a disclosure. Change their source in `interface.js`, then run `npm run build:ui` and `npm run check:ui`. Dropdown CSS changes do not require rebuilding that JavaScript bundle.
