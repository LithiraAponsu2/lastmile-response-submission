import { LitElement, html, css, svg, nothing } from 'lit';
import '@awesome.me/webawesome/dist/components/details/details.js';
import '@awesome.me/webawesome/dist/components/spinner/spinner.js';
import '@awesome.me/webawesome/dist/components/tab-group/tab-group.js';
import '@awesome.me/webawesome/dist/components/dropdown/dropdown.js';
import '@awesome.me/webawesome/dist/components/tooltip/tooltip.js';
import { registerIconLibrary } from '@awesome.me/webawesome/dist/components/icon/library.js';
import { icons as systemIcons } from '@awesome.me/webawesome/dist/components/icon/library.system.js';

// Keep prebuilt controls' icons on the same origin under the existing CSP.
registerIconLibrary('system', {
  resolver: (name, _family, variant = 'solid') => {
    const collection = typeof systemIcons[variant]?.[name] === 'string' ? variant : 'regular';
    const icon = typeof systemIcons[collection]?.[name] === 'string' ? name : 'circle-question';
    return `/static/vendor/components/icons/${collection}/${icon}.svg`;
  },
  mutator: svg => { if (!svg.hasAttribute('fill')) svg.setAttribute('fill', 'currentColor'); }
});

// A thin DOM adapter around the prebuilt tab group. Web Awesome owns tab
// activation, roving keyboard focus, panel relationships and overflow controls.
export function createDataTabs({ tabs, active, onSelect, renderPanel }) {
  const group = document.createElement('wa-tab-group');
  group.className = 'incident-tabs';
  group.active = active;
  group.activation = 'manual';
  for (const [key, label] of tabs) {
    const tab = document.createElement('wa-tab');
    tab.id = `tab-${key}`;
    tab.panel = key;
    tab.slot = 'nav';
    tab.active = key === active;
    // Reflect immediately so focus restoration also works before Lit's update.
    tab.setAttribute('tabindex', key === active ? '0' : '-1');
    tab.textContent = label;
    const panel = document.createElement('wa-tab-panel');
    panel.id = `panel-${key}`;
    panel.name = key;
    panel.active = key === active;
    panel.tabIndex = 0;
    panel.className = 'data-panel';
    if (key === active) panel.append(...renderPanel(key).filter(Boolean));
    group.append(tab, panel);
  }
  group.addEventListener('wa-tab-show', event => {
    if (event.target === group && event.detail.name !== active) onSelect(event.detail.name);
  });
  group.updateComplete.then(() => {
    // The component exposes its tablist as the `tabs` part; keep its name in
    // the same shadow tree rather than creating a second outer tablist.
    group.shadowRoot.querySelector('[part="tabs"]').setAttribute('aria-label', 'Incident data sections');
  });
  return group;
}

// The prebuilt popup uses the browser's top layer, escaping clipped cards.
// Web Awesome owns positioning, viewport flipping, dismissal and arrow keys.
export function createViewMenu({ items, active, onSelect }) {
  const dropdown = document.createElement('wa-dropdown');
  dropdown.className = 'more-views';
  dropdown.placement = 'bottom-end';
  dropdown.distance = 6;
  const trigger = document.createElement('button');
  trigger.type = 'button';
  trigger.slot = 'trigger';
  trigger.className = 'more-trigger';
  trigger.textContent = 'More';
  // Native buttons supply Enter/Space; add the menu-button arrow shortcut.
  trigger.addEventListener('keydown', event => {
    if (!dropdown.open && ['ArrowDown', 'ArrowUp'].includes(event.key)) {
      event.preventDefault();
      if (event.key === 'ArrowUp') dropdown.addEventListener('wa-after-show', () => dropdown.querySelector('wa-dropdown-item:last-child')?.focus(), { once: true });
      dropdown.open = true;
    }
  });
  dropdown.append(trigger);
  for (const [key, label] of items) {
    const item = document.createElement('wa-dropdown-item');
    item.value = key;
    item.textContent = label;
    if (key === active) item.setAttribute('aria-current', 'page');
    dropdown.append(item);
  }
  dropdown.addEventListener('wa-select', event => onSelect(event.detail.item.value));
  dropdown.updateComplete.then(() => {
    dropdown.shadowRoot.querySelector('[part="menu"]').setAttribute('aria-label', 'More incident views');
  });
  return dropdown;
}

const glyph = name => svg`<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d=${name === 'close' ? 'M6 6l12 12M18 6L6 18' : 'M12 5v14M5 12h14'} /></svg>`;

class IconButton extends LitElement {
  static properties = { label: {}, icon: {}, disabled: { type: Boolean, reflect: true } };
  static styles = css`
    :host { display:inline-flex; width:44px; height:44px; flex:0 0 44px; }
    button { display:grid; place-items:center; box-sizing:border-box; width:44px; height:44px; padding:0; margin:0; border:1px solid #d6e2f5; border-radius:50%; background:#e8f0fe; color:#1967d2; cursor:pointer; }
    button:hover { background:#d6e5fc; } button:focus-visible { outline:3px solid #174ea6; outline-offset:3px; }
    button:disabled { opacity:.55; cursor:not-allowed; } svg { display:block; width:22px; height:22px; fill:none; stroke:currentColor; stroke-width:1.8; stroke-linecap:round; }
    :host([icon=close]) button { background:transparent; border-color:transparent; color:#445774; }
  `;
  focus(options) { this.updateComplete.then(() => this.renderRoot.querySelector('button')?.focus(options)); }
  render() { return html`<button type="button" aria-label=${this.label || 'Action'} ?disabled=${this.disabled}>${glyph(this.icon)}</button>`; }
}
customElements.define('lm-icon-button', IconButton);

class WorkActivity extends LitElement {
  static properties = { turn: { attribute: false }, expanded: { type: Boolean } };
  async firstUpdated() {
    const details = this.renderRoot.querySelector('wa-details');
    if (!details) return;
    await details.updateComplete;
    // Web Awesome 3.14.0 references "header" without assigning that ID.
    // Keep the region's accessible name in the component's own shadow tree.
    const header = details.shadowRoot.querySelector('[part="header"]');
    if (header && !header.id) header.id = 'header';
  }
  static styles = css`
    :host { display:block; min-width:0; font:13px/1.5 "Segoe UI",Arial,sans-serif; color:#34435d;
      --wa-color-text-normal:#34435d; --wa-color-text-quiet:#53647d; --wa-color-surface-default:transparent;
      --wa-panel-border-width:0; --wa-panel-border-style:solid; --wa-panel-border-radius:12px;
      --wa-space-m:0; --wa-border-radius-m:12px; --wa-focus-ring:2px solid #174ea6; --wa-focus-ring-offset:3px; --wa-transition-normal:120ms; }
    wa-details::part(base) { border:0; background:transparent; }
    wa-details::part(header) { min-height:48px; padding:8px 4px; gap:10px; border-radius:12px; }
    wa-details::part(header):hover { background:#f1f5fb; }
    wa-details::part(icon) { rotate:none; transform:none; }
    wa-details::part(content) { padding:6px 0 0; } wa-details::part(summary) { width:100%; min-width:0; }
    .summary { display:flex; align-items:center; gap:10px; min-width:0; }
    .summary-copy { display:grid; gap:2px; flex:1; min-width:0; overflow-wrap:anywhere; }
    strong { font-size:12px; font-weight:600; color:#53647d; }
    .meta { font-size:11px; color:#53647d; }
    .running { color:#1967d2; } .failed { color:#a3211b; }
    .marker { display:grid; place-items:center; width:30px; height:30px; flex:0 0 30px; border-radius:10px; background:#edf2fa; color:#53647d; }
    .marker.running { color:#1967d2; background:#e8f0fe; }
    .marker.failed { background:#fce8e6; color:#a3211b; }
    svg { display:block; width:18px; height:18px; fill:none; stroke:currentColor; stroke-width:1.8; stroke-linecap:round; stroke-linejoin:round; }
    .chevron { width:20px; height:20px; color:#53647d; flex-shrink:0; }
    .chevron[slot=collapse-icon] { transform:rotate(180deg); }
    wa-spinner { font-size:18px; --indicator-color:#1967d2; --track-color:#c9dcfb; --track-width:2px; }
    .work-log { padding:4px 12px; border:1px solid #e2e8f1; border-radius:14px; background:#fff; }
    .log-heading { display:flex; justify-content:space-between; gap:10px; padding:10px 0; font-size:10px; font-weight:650; letter-spacing:.6px; text-transform:uppercase; color:#53647d; }
    .log-count { font-weight:500; letter-spacing:0; text-transform:none; }
    ol { list-style:none; padding:0; margin:0; }
    .step { display:grid; grid-template-columns:20px minmax(0,1fr) auto; align-items:center; gap:9px; min-height:44px; padding:10px 0; border-top:1px solid #edf1f6; }
    .step-label { font-size:12px; overflow-wrap:anywhere; }
    .step-icon { color:#53647d; } .completed .step-icon { color:#188038; } .running .step-icon { color:#1967d2; } .failed .step-icon { color:#a3211b; }
    .step-status { font-size:10px; color:#53647d; background:#f3f6fa; padding:3px 7px; border-radius:6px; }
    .running .step-status { color:#174ea6; background:#e8f0fe; } .failed .step-status { color:#a3211b; background:#fce8e6; }
    .interrupted .step-icon { color:#8a4f00; } .interrupted .step-status { color:#775000; background:#fef3d7; }
    .failure { margin:10px 2px 2px; font-size:12px; color:#8c201a; }
    .reduced { display:none; }
    @media(prefers-reduced-motion:reduce) { wa-spinner { display:none; } .reduced { display:block; } wa-details { --show-duration:0ms; --hide-duration:0ms; } }
    @media(max-width:360px) { .step { gap:7px; } .step-status { padding:3px 5px; } }
  `;
  toggle(event) {
    if (event.target.localName !== 'wa-details') return;
    this.expanded = event.type === 'wa-show';
    this.dispatchEvent(new CustomEvent('activity-toggle', { detail: { id: this.turn.id, open: this.expanded }, bubbles:true, composed:true }));
  }
  render() {
    const t = this.turn;
    if (!t) return nothing;
    const running = t.status === 'running', failed = ['failed','interrupted'].includes(t.status);
    const steps = t.steps || [];
    const title = running ? 'Assistant is working' : failed ? 'Request needs attention' : 'Assistant finished';
    const detail = running ? steps.at(-1)?.label || 'Reading your message…' : `${steps.length} ${steps.length === 1 ? 'operation' : 'operations'} · ${this.expanded ? 'Hide' : 'View'} details`;
    const icon = kind => svg`<svg viewBox="0 0 24 24" aria-hidden="true"><path d=${kind === 'completed' ? 'm5 12 4 4L19 6' : ['failed','interrupted'].includes(kind) ? 'M12 7v6m0 4h.01M12 3 2 21h20Z' : 'M4 7h9m4 0h3M4 12h3m4 0h9M4 17h9m4 0h3'} /></svg>`;
    return html`<wa-details .open=${!!this.expanded} @wa-show=${this.toggle} @wa-hide=${this.toggle}>
      <span slot="summary" class="summary">
        <span class=${`marker${running ? ' running' : failed ? ' failed' : ''}`} aria-hidden="true">${running ? html`<wa-spinner></wa-spinner><span class="reduced">${icon('running')}</span>` : icon(failed ? t.status : 'completed')}</span>
        <span class="summary-copy"><strong class=${failed ? 'failed' : running ? 'running' : ''}>${title}</strong><span class="meta">${detail}</span></span>
      </span>
      <svg slot="expand-icon" class="chevron" viewBox="0 0 24 24" aria-hidden="true"><path d="m6 9 6 6 6-6" /></svg>
      <svg slot="collapse-icon" class="chevron" viewBox="0 0 24 24" aria-hidden="true"><path d="m6 9 6 6 6-6" /></svg>
      <div class="work-log"><div class="log-heading">Activity<span class="log-count">${steps.length} recorded</span></div>
      <ol aria-label="Recorded assistant operations">${steps.map(s => {
        const status = failed && s.status === 'running' ? 'interrupted' : s.status;
        return html`<li class="step ${status}"><span class="step-icon" aria-hidden="true">${icon(status)}</span><span class="step-label">${s.label}</span><span class="step-status">${({completed:'Done',running:'Running',failed:'Failed',interrupted:'Interrupted'})[status] || status}</span></li>`;
      })}</ol>
      ${!steps.length ? html`<p class="meta">Waiting for the first recorded operation…</p>` : nothing}</div>
      ${failed ? html`<p class="failure">Your saved records remain available. Retry a focused question.</p>` : nothing}
    </wa-details>`;
  }
}
customElements.define('lm-work-activity', WorkActivity);
