import { LitElement, html, css, svg, nothing } from 'lit';
import { ref } from 'lit/directives/ref.js';
import { graphlib, layout } from '@dagrejs/dagre';
// wa-tooltip is registered by the always-loaded lastmile-ui.js bundle; importing it here would define wa-popup twice.
const statusText=value=>({approved_exercise:'approved draft',handover_accepted:'handover received'}[value]||value.replaceAll('_',' '));

// Loaded only when incident history is opened. Dagre owns branch/merge layout;
// nodes remain native buttons and the milestone list is the text alternative.
class IncidentFlow extends LitElement {
  static properties = { story: { attribute: false }, selected: { state: true }, zoom: { state: true } };
  static styles = css`
    :host{display:block;min-width:0;color:#26354e;font:inherit}
    *{box-sizing:border-box}button{font:inherit;color:inherit;cursor:pointer;touch-action:manipulation}
    .toolbar{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin:0 0 12px}.toolbar span{margin-right:auto;font-size:12px;color:#53647d}
    .control{min-width:44px;min-height:44px;border:1px solid #d9e2f2;border-radius:12px;background:white;padding:8px 12px}
    button:hover{border-color:#1967d2;background:#f4f8ff}button:focus-visible,.viewport:focus-visible{outline:3px solid #1967d2;outline-offset:3px}
    .viewport{overflow:auto;max-height:580px;min-height:240px;border:1px solid #e3e9f4;border-radius:18px;background-color:#f8faff;background-image:radial-gradient(#d7e1f0 .8px,transparent .8px);background-size:18px 18px;overscroll-behavior:contain}
    .extent{position:relative}.canvas{position:absolute;left:0;top:0;transform-origin:0 0}
    svg{position:absolute;inset:0;overflow:visible;pointer-events:none}.edge{fill:none;stroke:#9aacc7;stroke-width:1.5;stroke-linejoin:round}.edge.selected{stroke:#1967d2;stroke-width:2.5}.edge.pending{stroke-dasharray:5 5}.edge.dependency{stroke:#a46b13;stroke-dasharray:3 4}
    .anchor{position:absolute;width:206px;height:88px} .node{position:relative;width:206px;height:88px;padding:11px 13px;background:white;border:1px solid #d9e3f3;border-radius:15px;box-shadow:0 3px 9px #203b6510;text-align:left;display:flex;flex-direction:column;justify-content:center;gap:4px}
    .node[aria-pressed=true]{border:2px solid #1967d2;background:#edf4ff}.node.incident{background:#eaf2ff;border-color:#b8cff5}.node.assignment{border-color:#b9cff7;background:#f5f9ff}.node.finance{border-color:#f3d98f;background:#fffbee}.node.document{border-color:#d4c5f0;background:#faf7ff}
    .node strong{font-size:13px;line-height:1.35;overflow:hidden;display:-webkit-box;-webkit-box-orient:vertical;-webkit-line-clamp:2;overflow-wrap:anywhere}
    .node small{font-size:11px;color:#53647d;text-transform:capitalize}.dot{width:6px;height:6px;background:#1967d2;border-radius:50%;display:inline-block;margin-right:5px}.done .dot{background:#188038}
    .inspector{margin-top:14px;border:1px solid #e0e7f3;border-radius:16px;padding:16px;background:white;min-height:110px}.inspector h3{font-size:15px;margin:0 0 6px}.inspector p{font-size:13px;margin:6px 0;overflow-wrap:anywhere}.details{max-height:180px;overflow:auto}.open{margin-top:10px;color:#1967d2;font-weight:600}
    .legend{font-size:12px;color:#53647d;margin:10px 0 0;line-height:1.6}.empty{padding:24px;color:#53647d}
    wa-tooltip{--max-width:300px}wa-tooltip::part(tooltip){background:#26354e;color:white;border-radius:12px;padding:12px 14px;font-size:12px;line-height:1.5;box-shadow:0 8px 24px #26354e25}wa-tooltip::part(body){background:#26354e;color:white;border-radius:12px}wa-tooltip::part(base__arrow){background:#26354e}wa-tooltip p{margin:4px 0}
    @media(prefers-reduced-motion:reduce){*{scroll-behavior:auto}}
  `;
  constructor(){super();this.story=null;this.selected='';this.zoom=1;this.graph=null;this.fitNext=true;}
  willUpdate(changed){
    if(changed.has('story') && this.story){
      const g=new graphlib.Graph({multigraph:true});g.setGraph({rankdir:'LR',ranksep:66,nodesep:24,marginx:24,marginy:24});g.setDefaultEdgeLabel(()=>({}));
      this.story.nodes.forEach(n=>g.setNode(n.id,{width:206,height:88}));
      this.story.edges.forEach((e,i)=>g.setEdge(e.from,e.to,{...e},String(i)));
      layout(g);this.graph=g;
      if(!this.story.nodes.some(n=>n.id===this.selected))this.selected='';
      this.fitNext=true;
    }
  }
  updated(){if(this.fitNext && this.graph){this.fitNext=false;this.fit();this.updateComplete.then(()=>{const box=this.renderRoot.querySelector('.viewport'),start=this.graph.node('incident:start');if(box&&start)box.scrollTop=Math.max(0,start.y*this.zoom-box.clientHeight/2);});}}
  fit(){const box=this.renderRoot.querySelector('.viewport');if(box && this.graph)this.zoom=Math.max(.5,Math.min(1,(box.clientWidth-12)/this.graph.graph().width));}
  inspect(n){this.selected=n.id;}
  render(){
    if(!this.story||!this.graph)return html`<p class="empty">No workflow records yet.</p>`;
    const g=this.graph,{width,height}=g.graph(),active=this.story.nodes.find(n=>n.id===this.selected);
    return html`<div class="toolbar"><span>${this.story.nodes.length} points · ${this.story.edges.length} connections</span><button class="control" aria-label="Zoom out" @click=${()=>this.zoom=Math.max(.5,this.zoom-.15)}>−</button><button class="control" aria-label="Fit workflow" @click=${()=>this.fit()}>Fit</button><button class="control" aria-label="Zoom in" @click=${()=>this.zoom=Math.min(1.8,this.zoom+.15)}>+</button></div>
      <div class="viewport" tabindex="0" role="region" aria-label="Incident workflow diagram. Scroll to explore; use Tab to inspect points.">
        <div class="extent" ${ref(el=>{if(el)Object.assign(el.style,{width:width*this.zoom+'px',height:height*this.zoom+'px'});})}><div class="canvas" ${ref(el=>{if(el)Object.assign(el.style,{width:width+'px',height:height+'px',transform:`scale(${this.zoom})`});})}>
          <svg width=${width} height=${height} aria-hidden="true"><defs><marker id="flow-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="#8095b4"/></marker></defs>
            ${g.edges().map(e=>{const x=g.edge(e),on=x.from===this.selected||x.to===this.selected;return svg`<path class="edge ${x.kind} ${on?'selected':''}" d=${x.points.map((p,i)=>(i?'L':'M')+p.x+','+p.y).join(' ')} marker-end="url(#flow-arrow)"/>`;})}
          </svg>
          ${this.story.nodes.map((n,i)=>{const p=g.node(n.id),done=['completed','fulfilled','reconciled','handover_accepted','closed'].includes(n.status);return html`<div id=${'flow-anchor-'+i} class="anchor" ${ref(el=>{if(el)Object.assign(el.style,{left:p.x-103+'px',top:p.y-44+'px'});})}><button id=${'flow-point-'+i} class="node ${n.kind} ${done?'done':''}" data-node=${n.id} aria-pressed=${String(n.id===this.selected)} @pointerenter=${()=>this.inspect(n)} @focus=${()=>this.inspect(n)} @click=${()=>this.inspect(n)}><strong>${n.title}</strong> <small><span class="dot"></span>${statusText(n.status)}</small></button></div><wa-tooltip id=${'flow-tip-'+i} for=${'flow-anchor-'+i} placement="top" show-delay="200"><strong>${n.title}</strong><p>${n.organisation_name} · ${statusText(n.status)}</p>${n.details.filter(Boolean).slice(0,3).map(d=>html`<p>${d}</p>`)}</wa-tooltip>`;})}
        </div></div>
      </div>
      <p class="legend">Solid: saved connection · Dashed: pending outcome or dependency. Hover, focus or tap a point for details. Choose a request above for its individual stages.</p>
      <section class="inspector" aria-label="Workflow point details">${active?html`<h3>${active.title}</h3><p>${active.organisation_name} · ${statusText(active.status)}</p><div class="details">${active.details.filter(Boolean).map(d=>html`<p>${d}</p>`)}</div>${active.record_id?html`<button class="control open" @click=${()=>this.dispatchEvent(new CustomEvent('open-story-record',{detail:{id:active.record_id,tab:active.tab},bubbles:true,composed:true}))}>Open record</button>`:nothing}`:html`<h3>Follow the story</h3><p>Inspect a point to see the organisation, status and supporting records. Branches reconnect when outcomes return to coordination.</p>`}</section>`;
  }
}
customElements.define('lm-incident-flow',IncidentFlow);
